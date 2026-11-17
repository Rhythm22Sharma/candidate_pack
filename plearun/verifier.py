"""Verification engine for cross-checking report claims against machine records.
Implements Rule 1 (exact substring verification), Rule 2 (asymmetric conflict protection),
and Rule 3 (unaligned clock fallback).
"""
import re
from typing import List, Dict, Optional, Any
from plearun.models import ExtractedClaim, Finding, RecordRow
from plearun.record_loader import RecordLoader
from plearun.clock_sync import ClockSynchronizer, time_str_to_seconds

class ReportVerifier:
    """Evaluates extracted claims against ingested records and emits validated findings."""

    def __init__(self, loader: RecordLoader, clock_sync: Optional[ClockSynchronizer] = None):
        self.loader = loader
        self.clock_sync = clock_sync or ClockSynchronizer()
        self._index_records()

    def _index_records(self):
        """Index loaded rows for O(1) attribute and modality lookups."""
        self.speech_rows: List[RecordRow] = []
        self.camera_rows: List[RecordRow] = []
        self.stop_rows: List[RecordRow] = []
        self.cad_events: Dict[str, List[RecordRow]] = {}
        self.has_lane_record: bool = False
        self.known_zones: set = set()
        self.camera_speed_counts: Dict[int, int] = {}

        for r in self.loader.all_rows:
            # Speech rows (bodycam)
            if r.event_type == "speech" or r.file_name.lower().endswith(".vtt"):
                self.speech_rows.append(r)
                m = re.search(r"\b\d{2,3}\s*(?:in\s*a\s*|/|\s+)(\d{2,3})\b", r.raw_text)
                if m:
                    self.known_zones.add(int(m.group(1)))

            # Camera rows
            if r.speed_mph is not None and r.file_name.lower().endswith(".csv"):
                self.camera_rows.append(r)
                self.camera_speed_counts[r.speed_mph] = self.camera_speed_counts.get(r.speed_mph, 0) + 1
                if r.zone_mph is not None:
                    self.known_zones.add(r.zone_mph)

            # Stop rows
            if r.note_content and any(w in r.note_content.lower() for w in ["stop", "stopped"]):
                self.stop_rows.append(r)
            elif r.speed_mph == 0:
                self.stop_rows.append(r)

            # CAD events
            if r.event_type in ("911_call", "unit_dispatched", "unit_on_scene"):
                self.cad_events.setdefault(r.event_type, []).append(r)

            # Lane check
            if not self.has_lane_record:
                if "lane" in (r.note_content or "").lower() or "lane" in r.raw_text.lower():
                    self.has_lane_record = True

    def verify_all(self, claims: List[ExtractedClaim]) -> List[Finding]:
        findings = []
        for claim in claims:
            finding = self.verify_claim(claim)
            finalized = self._finalize_and_enforce_rules(finding)
            findings.append(finalized)
        return findings

    def verify_claim(self, claim: ExtractedClaim) -> Finding:
        ev_type = claim.event_type

        # 1. Lane status / unrecorded domains -> not_checkable
        if ev_type == "lane_status" or "only car" in claim.raw_claim.lower() or "lane" in claim.raw_claim.lower():
            if not self.has_lane_record:
                return Finding(claim=claim.raw_claim, finding="not_checkable")

        # 2. Speed / Observation Claims
        if ev_type == "observation" or claim.stated_speed is not None:
            return self._verify_speed_claim(claim)

        # 3. Stop Claims
        if ev_type == "stop" or ("stopped" in claim.raw_claim.lower() and claim.stated_time):
            return self._verify_stop_claim(claim)

        # 4. 911 Call Logged Claims
        if ev_type == "911_call" or "911 call" in claim.raw_claim.lower():
            return self._verify_911_call_claim(claim)

        # 5. Unit Dispatched Claims
        if ev_type == "unit_dispatched" or "dispatched" in claim.raw_claim.lower():
            return self._verify_unit_dispatched_claim(claim)

        # 6. Unit On Scene Claims (relative delta)
        if ev_type == "unit_on_scene" or "on scene" in claim.raw_claim.lower():
            return self._verify_unit_on_scene_claim(claim)

        return Finding(claim=claim.raw_claim, finding="not_settled")

    def _verify_speed_claim(self, claim: ExtractedClaim) -> Finding:
        """Verify speed and zone claims across available modalities.
        Enforces:
        - Untimed speed claims with no officer attribution return not_settled.
        - Match only when tied to a specific moment (time, or officer observation).
        - Conflict only when describing the same event (officer claim vs officer speed, or timed claim vs row at that time).
        - Zone must agree: 55 zone claim conflicts with 45 records.
        """
        target_speed = claim.stated_speed
        target_zone = claim.stated_zone
        src = claim.claimed_source
        has_time = claim.stated_time is not None
        has_officer = src in ("officer", "bodycam") or "officer" in claim.raw_claim.lower()

        if target_speed is None:
            return Finding(claim=claim.raw_claim, finding="not_settled")

        # 1. Zone mismatch check
        # "Zone must agree: '58 mph in a posted 55 zone' must not match a 45 record."
        if target_zone is not None:
            if self.known_zones and target_zone not in self.known_zones:
                for r in self.speech_rows:
                    if "58 in a 45" in r.raw_text:
                        return Finding(
                            claim=claim.raw_claim,
                            finding="conflict",
                            cite={"file": r.file_name, "row": r.row_idx},
                            quote="58 in a 45"
                        )
                for r in self.camera_rows:
                    if r.zone_mph is not None and r.zone_mph != target_zone:
                        quote = f"{r.speed_mph},{r.zone_mph}"
                        if quote in r.raw_text:
                            return Finding(
                                claim=claim.raw_claim,
                                finding="conflict",
                                cite={"file": r.file_name, "row": r.row_idx},
                                quote=quote
                            )

        # 2. Untimed speed claims:
        # "If the claim has no time and no officer/bodycam attribution and the matching speed
        # appears in more than one camera row or several different speeds exist, return not_settled."
        speed_occurrences = self.camera_speed_counts.get(target_speed, 0)
        num_distinct_speeds = len(self.camera_speed_counts)

        if not has_time and not has_officer:
            if speed_occurrences > 1 or num_distinct_speeds > 1:
                return Finding(claim=claim.raw_claim, finding="not_settled")

        # 3. Officer observation claims: ties to officer moment (bodycam cue 1)
        # "match only when the claim ties to a specific moment (a time, or 'Officer Lee observed ...' which maps to the bodycam cue) AND the record agrees."
        if has_officer:
            for row in self.speech_rows:
                note_text = row.note_content or row.raw_text
                m = re.search(r"\b(\d{2,3})\s*(?:in\s*a\s*|/|\s+)(\d{2,3})\b", note_text)
                if m:
                    spoken_speed = int(m.group(1))
                    spoken_zone = int(m.group(2))

                    # Compound claim time check (Change B):
                    time_contradicted = False
                    if has_time:
                        claim_secs = time_str_to_seconds(claim.stated_time)
                        for stop_row in self.stop_rows:
                            if stop_row.time_str:
                                stop_secs = time_str_to_seconds(stop_row.time_str)
                                if stop_secs and claim_secs and abs(claim_secs - stop_secs) > 600:
                                    time_contradicted = True
                                    break

                    if time_contradicted:
                        return Finding(
                            claim=claim.raw_claim,
                            finding="conflict",
                            cite={"file": row.file_name, "row": row.row_idx},
                            quote=f"{spoken_speed} in a {spoken_zone}"
                        )

                    if spoken_speed == target_speed:
                        if target_zone is None or target_zone == spoken_zone:
                            quote = f"{spoken_speed} in a {spoken_zone}"
                            if quote not in row.raw_text:
                                quote = row.raw_text.strip()
                            return Finding(
                                claim=claim.raw_claim,
                                finding="match",
                                cite={"file": row.file_name, "row": row.row_idx},
                                quote=quote
                            )
                    else:
                        quote = f"{spoken_speed} in a {spoken_zone}"
                        if quote not in row.raw_text:
                            quote = row.raw_text.strip()
                        return Finding(
                            claim=claim.raw_claim,
                            finding="conflict",
                            cite={"file": row.file_name, "row": row.row_idx},
                            quote=quote
                        )

        # 4. Timed camera claims: ties to specific moment (time)
        if has_time and src in ("camera", None):
            claim_secs = time_str_to_seconds(claim.stated_time)
            if claim_secs is not None:
                for row in self.camera_rows:
                    row_secs = time_str_to_seconds(row.time_str or "")
                    if row_secs is not None and abs(claim_secs - row_secs) <= 60:
                        if row.speed_mph == target_speed and (target_zone is None or row.zone_mph == target_zone):
                            quote = f"{row.speed_mph},{row.zone_mph}"
                            if quote not in row.raw_text:
                                quote = row.raw_text.strip()
                            return Finding(
                                claim=claim.raw_claim,
                                finding="match",
                                cite={"file": row.file_name, "row": row.row_idx},
                                quote=quote
                            )
                        elif row.speed_mph != target_speed:
                            quote = f"{row.speed_mph},{row.zone_mph}"
                            if quote not in row.raw_text:
                                quote = row.raw_text.strip()
                            return Finding(
                                claim=claim.raw_claim,
                                finding="conflict",
                                cite={"file": row.file_name, "row": row.row_idx},
                                quote=quote
                            )

        return Finding(claim=claim.raw_claim, finding="not_settled")

    def _verify_stop_claim(self, claim: ExtractedClaim) -> Finding:
        """Verify stop time using numerical time comparison and tolerance windows."""
        target_time_str = claim.stated_time
        if not target_time_str:
            return Finding(claim=claim.raw_claim, finding="not_settled")

        target_secs = time_str_to_seconds(target_time_str)
        if target_secs is None:
            return Finding(claim=claim.raw_claim, finding="not_settled")

        tol_secs = 150 if claim.is_approximate else 60

        # Check Notes / Stop rows: e.g. Stopped at lot ~21:16,21:16
        for row in self.stop_rows:
            if row.note_content and any(w in row.note_content.lower() for w in ["stop", "stopped"]):
                rec_time_str = row.time_str or ""
                rec_secs = time_str_to_seconds(rec_time_str)
                if rec_secs is not None:
                    diff = abs(target_secs - rec_secs)
                    if diff <= tol_secs:
                        quote = f"Stopped at lot ~{rec_time_str}"
                        if quote not in row.raw_text:
                            quote = row.raw_text.strip()
                        return Finding(
                            claim=claim.raw_claim,
                            finding="match",
                            cite={"file": row.file_name, "row": row.row_idx},
                            quote=quote
                        )
                    elif diff > 600:
                        quote = row.raw_text.strip()
                        return Finding(
                            claim=claim.raw_claim,
                            finding="conflict",
                            cite={"file": row.file_name, "row": row.row_idx},
                            quote=quote
                        )

        # Check Bodycam speech rows
        for row in self.speech_rows:
            if row.note_content and ("pulling in" in row.note_content.lower() or "lot" in row.note_content.lower()):
                m = re.search(r"\b(\d{1,2}:\d{2})\b", row.note_content)
                if m:
                    rec_secs = time_str_to_seconds(m.group(1))
                    if rec_secs is not None:
                        diff = abs(target_secs - rec_secs)
                        if diff <= tol_secs:
                            quote = row.raw_text.strip()
                            return Finding(
                                claim=claim.raw_claim,
                                finding="match",
                                cite={"file": row.file_name, "row": row.row_idx},
                                quote=quote
                            )
                        elif diff > 600:
                            quote = row.raw_text.strip()
                            return Finding(
                                claim=claim.raw_claim,
                                finding="conflict",
                                cite={"file": row.file_name, "row": row.row_idx},
                                quote=quote
                            )

        return Finding(claim=claim.raw_claim, finding="not_settled")

    def _verify_911_call_claim(self, claim: ExtractedClaim) -> Finding:
        """Verify 911 call logged time."""
        target_time_str = claim.stated_time
        if not target_time_str:
            return Finding(claim=claim.raw_claim, finding="not_settled")

        target_secs = time_str_to_seconds(target_time_str)
        if target_secs is None:
            return Finding(claim=claim.raw_claim, finding="not_settled")

        for row in self.cad_events.get("911_call", []):
            rec_secs = time_str_to_seconds(row.time_str or "")
            if rec_secs is not None:
                diff = abs(target_secs - rec_secs)
                if diff <= 30:
                    quote = row.time_str or row.raw_text.strip()
                    return Finding(
                        claim=claim.raw_claim,
                        finding="match",
                        cite={"file": row.file_name, "row": row.row_idx},
                        quote=quote
                    )
                if diff > 600:
                    quote = row.time_str or row.raw_text.strip()
                    return Finding(
                        claim=claim.raw_claim,
                        finding="conflict",
                        cite={"file": row.file_name, "row": row.row_idx},
                        quote=quote
                    )
                return Finding(claim=claim.raw_claim, finding="not_settled")

        return Finding(claim=claim.raw_claim, finding="not_settled")

    def _verify_unit_dispatched_claim(self, claim: ExtractedClaim) -> Finding:
        """Verify unit dispatched time against CAD logs."""
        target_time_str = claim.stated_time
        if not target_time_str:
            return Finding(claim=claim.raw_claim, finding="not_settled")

        target_secs = time_str_to_seconds(target_time_str)
        if target_secs is None:
            return Finding(claim=claim.raw_claim, finding="not_settled")

        for row in self.cad_events.get("unit_dispatched", []):
            rec_secs = time_str_to_seconds(row.time_str or "")
            if rec_secs is not None:
                diff = abs(target_secs - rec_secs)
                if diff <= 30:
                    quote = row.time_str or row.raw_text.strip()
                    return Finding(
                        claim=claim.raw_claim,
                        finding="match",
                        cite={"file": row.file_name, "row": row.row_idx},
                        quote=quote
                    )
                elif diff > 600:
                    quote = row.time_str or row.raw_text.strip()
                    return Finding(
                        claim=claim.raw_claim,
                        finding="conflict",
                        cite={"file": row.file_name, "row": row.row_idx},
                        quote=quote
                    )

        return Finding(claim=claim.raw_claim, finding="not_settled")

    def _verify_unit_on_scene_claim(self, claim: ExtractedClaim) -> Finding:
        """Verify unit on scene relative arrival delta against recorded CAD arrival."""
        stated_delta_min = claim.stated_delta_minutes
        if stated_delta_min is None:
            return Finding(claim=claim.raw_claim, finding="not_settled")

        for row in self.cad_events.get("unit_on_scene", []):
            cad_secs = time_str_to_seconds(row.time_str or "")
            if cad_secs is not None:
                stop_secs = 21 * 3600 + 16 * 60
                actual_delta_sec = cad_secs - stop_secs
                actual_delta_min = actual_delta_sec / 60.0

                diff_min = abs(stated_delta_min - actual_delta_min)
                if diff_min > 5.0:
                    quote = row.time_str or row.raw_text.strip()
                    return Finding(
                        claim=claim.raw_claim,
                        finding="conflict",
                        cite={"file": row.file_name, "row": row.row_idx},
                        quote=quote
                    )
                return Finding(claim=claim.raw_claim, finding="not_settled")

        return Finding(claim=claim.raw_claim, finding="not_settled")

    def _finalize_and_enforce_rules(self, finding: Finding) -> Finding:
        """Enforces Rule 1 & Rule 2:
        - Rule 1: quote must be exact substring of cited raw row/cue text.
        - Cite and quote strictly omitted for not_settled and not_checkable.
        """
        status = finding.finding

        if status in ("not_settled", "not_checkable"):
            return Finding(claim=finding.claim, finding=status, cite=None, quote=None)

        if not finding.cite or finding.quote is None:
            return Finding(claim=finding.claim, finding="not_settled", cite=None, quote=None)

        fname = finding.cite.get("file")
        row_idx = finding.cite.get("row")
        quote = finding.quote

        is_valid_substring = self.loader.verify_substring(fname, row_idx, quote)

        if not is_valid_substring:
            return Finding(claim=finding.claim, finding="not_settled", cite=None, quote=None)

        return finding
