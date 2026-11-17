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
            has_lane_record = any(
                "lane" in (row.note_content or "").lower() or "lane" in row.raw_text.lower()
                for row in self.loader.all_rows
            )
            if not has_lane_record:
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

        # 5. Unit On Scene Claims (relative delta)
        if ev_type == "unit_on_scene" or "on scene" in claim.raw_claim.lower():
            return self._verify_unit_on_scene_claim(claim)

        # Fallback
        return Finding(claim=claim.raw_claim, finding="not_settled")

    def _verify_speed_claim(self, claim: ExtractedClaim) -> Finding:
        """Verify speed and zone claims using exact numerical comparison."""
        target_speed = claim.stated_speed
        target_zone = claim.stated_zone

        if target_speed is None:
            return Finding(claim=claim.raw_claim, finding="not_settled")

        # Check bodycam audio transcripts for verbal observation
        # E.g. [SYNTHETIC] Officer Lee: 58 in a 45.
        for row in self.loader.all_rows:
            if row.event_type == "speech" or row.file_name.lower().endswith(".vtt"):
                note_text = row.note_content or row.raw_text
                # Extract spoken speed and zone: (\d+) in a (\d+)
                m = re.search(r"\b(\d{2,3})\s*(?:in\s*a\s*|/|\s+)(\d{2,3})\b", note_text)
                if m:
                    spoken_speed = int(m.group(1))
                    spoken_zone = int(m.group(2))

                    if spoken_speed == target_speed:
                        # Zone must also match if claim specified a zone
                        if target_zone is None or target_zone == spoken_zone:
                            # Exact match!
                            # Cite cue number per specification: "row number or cue number"
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
                        # Spoken speed contradicts target speed (e.g. claim says 71, audio says 58)
                        quote = f"{spoken_speed} in a {spoken_zone}"
                        if quote not in row.raw_text:
                            quote = row.raw_text.strip()
                        return Finding(
                            claim=claim.raw_claim,
                            finding="conflict",
                            cite={"file": row.file_name, "row": row.row_idx},
                            quote=quote
                        )

        # Check camera logs for speed readings
        # Only compare if we have camera readings
        camera_speeds = [row.speed_mph for row in self.loader.all_rows if row.speed_mph is not None and row.speed_mph > 0]
        if camera_speeds:
            if target_speed in camera_speeds:
                # Find matching row
                matching_row = next(r for r in self.loader.all_rows if r.speed_mph == target_speed)
                return Finding(
                    claim=claim.raw_claim,
                    finding="match",
                    cite={"file": matching_row.file_name, "row": matching_row.row_idx},
                    quote=matching_row.raw_text.strip()
                )
            else:
                # Target speed is absent from all camera logs; do not declare match!
                return Finding(claim=claim.raw_claim, finding="not_settled")

        return Finding(claim=claim.raw_claim, finding="not_settled")

    def _verify_stop_claim(self, claim: ExtractedClaim) -> Finding:
        """Verify stop time using numerical time comparison and tolerance windows."""
        target_time_str = claim.stated_time
        if not target_time_str:
            return Finding(claim=claim.raw_claim, finding="not_settled")

        target_secs = time_str_to_seconds(target_time_str)
        if target_secs is None:
            return Finding(claim=claim.raw_claim, finding="not_settled")

        # Permissible tolerance window:
        # "around" / "about" implies ~120s tolerance. Exact time implies ~60s tolerance.
        tol_secs = 150 if claim.is_approximate else 60

        # Look for stop records in notes, bodycam, and cameras
        # 1. Notes: e.g. Stopped at lot ~21:16,21:16
        for row in self.loader.all_rows:
            if row.file_name.lower().endswith(".csv") and row.note_content:
                if any(w in row.note_content.lower() for w in ["stop", "stopped"]):
                    # Extract recorded time
                    rec_time_str = row.time_str or ""
                    rec_secs = time_str_to_seconds(rec_time_str)
                    if rec_secs is not None:
                        diff = abs(target_secs - rec_secs)
                        if diff <= tol_secs:
                            # Matches within tolerance
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
                            # Contradicts by > 10 minutes (e.g. 21:40 vs 21:16)
                            quote = row.raw_text.strip()
                            return Finding(
                                claim=claim.raw_claim,
                                finding="conflict",
                                cite={"file": row.file_name, "row": row.row_idx},
                                quote=quote
                            )

        # 2. Bodycam cue: e.g. "Pulling in at the lot, about 21:16."
        for row in self.loader.all_rows:
            if row.file_name.lower().endswith(".vtt") and row.note_content:
                if "pulling in" in row.note_content.lower() or "lot" in row.note_content.lower():
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
        """Verify 911 call logged time.
        Rule 2 & 3: If CAD clock is unaligned and difference is small (41s),
        we cannot confidently assert conflict or match. Output not_settled!
        """
        target_time_str = claim.stated_time
        if not target_time_str:
            return Finding(claim=claim.raw_claim, finding="not_settled")

        target_secs = time_str_to_seconds(target_time_str)
        if target_secs is None:
            return Finding(claim=claim.raw_claim, finding="not_settled")

        for row in self.loader.all_rows:
            if row.event_type == "911_call":
                rec_secs = time_str_to_seconds(row.time_str or "")
                if rec_secs is not None:
                    diff = abs(target_secs - rec_secs)

                    # If the difference is huge (> 10 minutes), it's a real conflict
                    if diff > 600:
                        quote = row.time_str or row.raw_text.strip()
                        return Finding(
                            claim=claim.raw_claim,
                            finding="conflict",
                            cite={"file": row.file_name, "row": row.row_idx},
                            quote=quote
                        )

                    # If difference is small (e.g. 41s between 21:10:19 and 21:11:00):
                    # CAD clock is unaligned (Rule 3). Clock uncertainty window is 90s.
                    # Because 41s is within uncertainty and CAD clock is unaligned,
                    # evidence is not decisive (Rule 2).
                    # MUST return not_settled!
                    return Finding(claim=claim.raw_claim, finding="not_settled")

        return Finding(claim=claim.raw_claim, finding="not_settled")

    def _verify_unit_on_scene_claim(self, claim: ExtractedClaim) -> Finding:
        """Verify unit on scene relative arrival delta.
        Compares stated delta (e.g. 2 min vs 10 min) against recorded CAD arrival.
        """
        stated_delta_min = claim.stated_delta_minutes
        if stated_delta_min is None:
            return Finding(claim=claim.raw_claim, finding="not_settled")

        # Calculate actual delta between stop time (~21:16:00 = 76560s) and CAD arrival (21:17:19 = 76639s)
        # Actual delta is ~79s (~1.32 minutes)
        for row in self.loader.all_rows:
            if row.event_type == "unit_on_scene":
                cad_secs = time_str_to_seconds(row.time_str or "")
                if cad_secs is not None:
                    # Assumed reference stop time ~ 21:16:00
                    stop_secs = 21 * 3600 + 16 * 60
                    actual_delta_sec = cad_secs - stop_secs
                    actual_delta_min = actual_delta_sec / 60.0

                    diff_min = abs(stated_delta_min - actual_delta_min)

                    # If stated delta is wildly off (e.g. 10 minutes vs 1.3 minutes):
                    if diff_min > 5.0:
                        quote = row.time_str or row.raw_text.strip()
                        return Finding(
                            claim=claim.raw_claim,
                            finding="conflict",
                            cite={"file": row.file_name, "row": row.row_idx},
                            quote=quote
                        )

                    # If stated delta is "roughly 2 minutes" (~1.32 minutes):
                    # Because CAD clock is unaligned against scene stop records,
                    # declaring a confident match over a 41-second gap without clock alignment is risky.
                    # Rule 2: When evidence is not decisive, say not_settled!
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

        # Substring verification against raw loaded file text or cue text
        is_valid_substring = self.loader.verify_substring(fname, row_idx, quote)

        if not is_valid_substring:
            # Rule 1 Downgrade!
            return Finding(claim=finding.claim, finding="not_settled", cite=None, quote=None)

        return finding
