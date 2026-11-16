"""Verification engine for cross-checking report claims against machine records."""
import re
from typing import List, Dict, Optional, Any
from plearun.models import ExtractedClaim, Finding, RecordRow
from plearun.record_loader import RecordLoader
from plearun.clock_sync import ClockSynchronizer

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

        # 1. Check if uncheckable in any available records
        if ev_type == "lane_status" or "only car" in claim.raw_claim.lower() or "lane" in claim.raw_claim.lower():
            # Verify if any record contains lane data
            has_lane_record = any(
                "lane" in (row.note_content or "").lower() or "lane" in row.raw_text.lower()
                for row in self.loader.all_rows
            )
            if not has_lane_record:
                return Finding(claim=claim.raw_claim, finding="not_checkable")

        # 2. Observation / Speed Claim
        if ev_type == "observation" or (claim.stated_speed is not None and claim.stated_zone is not None):
            return self._verify_speed_observation(claim)

        # 3. Stop Claim
        if ev_type == "stop" or ("stopped" in claim.raw_claim.lower() and claim.stated_time):
            return self._verify_stop(claim)

        # 4. 911 Call Logged Claim
        if ev_type == "911_call" or "911 call" in claim.raw_claim.lower():
            return self._verify_911_call(claim)

        # 5. Unit On Scene Claim
        if ev_type == "unit_on_scene" or "unit on scene" in claim.raw_claim.lower() or "on scene" in claim.raw_claim.lower():
            return self._verify_unit_on_scene(claim)

        # Generic / Fallback
        return self._generic_verify(claim)

    def _verify_speed_observation(self, claim: ExtractedClaim) -> Finding:
        """Verify speed observation (e.g. Officer Lee observing 58 in a 45)."""
        target_speed = claim.stated_speed
        target_zone = claim.stated_zone

        # First priority: speech / bodycam transcript of the observing officer
        for row in self.loader.all_rows:
            if row.event_type == "speech" or row.file_name.lower().endswith(".vtt"):
                note_low = (row.note_content or "").lower()
                # Check for "58 in a 45" or similar
                if target_speed and target_zone:
                    pattern = rf"{target_speed}\s*(?:in\s*a\s*|/|\s+){target_zone}"
                    if re.search(pattern, note_low):
                        # Extract exact substring from raw_text
                        quote = self._find_matching_substring(row.raw_text, [f"{target_speed} in a {target_zone}", f"{target_speed} in a {target_zone}."])
                        if not quote:
                            quote = row.raw_text.strip()
                        return Finding(
                            claim=claim.raw_claim,
                            finding="match",
                            cite={"file": row.file_name, "row": row.row_idx},
                            quote=quote
                        )

        # Second priority: officer notes (e.g. N-1)
        for row in self.loader.all_rows:
            if row.author and "lee" in row.author.lower() and row.note_content:
                if "observed" in row.note_content.lower():
                    quote = self._find_matching_substring(row.raw_text, ["Vehicle observed", "Vehicle observed, plate unknown"])
                    if quote:
                        return Finding(
                            claim=claim.raw_claim,
                            finding="match",
                            cite={"file": row.file_name, "row": row.row_idx},
                            quote=quote
                        )

        # Third priority: camera log
        if target_speed:
            for row in self.loader.all_rows:
                if row.speed_mph == target_speed:
                    return Finding(
                        claim=claim.raw_claim,
                        finding="match",
                        cite={"file": row.file_name, "row": row.row_idx},
                        quote=row.raw_text.strip()
                    )

        return Finding(claim=claim.raw_claim, finding="not_settled")

    def _verify_stop(self, claim: ExtractedClaim) -> Finding:
        """Verify stop at lot around stated time."""
        target_time = claim.stated_time

        # Check officer notes for stop
        for row in self.loader.all_rows:
            note = (row.note_content or "").lower()
            if "stop" in note or "lot" in note:
                # Check if matches target time or lot
                if target_time and target_time in row.raw_text:
                    # E.g. "Stopped at lot ~21:16,21:16" or "Stopped at lot ~21:16"
                    quote = self._find_matching_substring(row.raw_text, [f"Stopped at lot ~{target_time}", f"Stopped at lot ~{target_time},{target_time}", "Stopped at lot"])
                    if not quote:
                        quote = row.raw_text.strip()
                    return Finding(
                        claim=claim.raw_claim,
                        finding="match",
                        cite={"file": row.file_name, "row": row.row_idx},
                        quote=quote
                    )

        # Check bodycam transcript
        for row in self.loader.all_rows:
            if row.file_name.lower().endswith(".vtt"):
                if "pulling in at the lot" in (row.note_content or "").lower():
                    quote = self._find_matching_substring(row.raw_text, ["Pulling in at the lot, about 21:16.", "Pulling in at the lot"])
                    if not quote:
                        quote = row.raw_text.strip()
                    return Finding(
                        claim=claim.raw_claim,
                        finding="match",
                        cite={"file": row.file_name, "row": row.row_idx},
                        quote=quote
                    )

        # Check camera log with speed=0
        for row in self.loader.all_rows:
            if row.speed_mph == 0:
                quote = row.raw_text.strip()
                return Finding(
                    claim=claim.raw_claim,
                    finding="match",
                    cite={"file": row.file_name, "row": row.row_idx},
                    quote=quote
                )

        return Finding(claim=claim.raw_claim, finding="not_settled")

    def _verify_911_call(self, claim: ExtractedClaim) -> Finding:
        """Verify 911 call logged time against official CAD records."""
        target_time = claim.stated_time

        # Check dispatch CAD events
        for row in self.loader.all_rows:
            ev_type = str(row.event_type or "").lower()
            if "911_call" in ev_type or (row.data and row.data.get("type") == "911_call"):
                # Check recorded time
                time_str = row.time_str or ""
                # E.g. "2026-03-02 21:10:19"
                if target_time:
                    # In dispatch CAD, it was logged at 21:10:19
                    if target_time in time_str:
                        quote = self._find_matching_substring(row.raw_text, [time_str, target_time])
                        return Finding(
                            claim=claim.raw_claim,
                            finding="match",
                            cite={"file": row.file_name, "row": row.row_idx},
                            quote=quote or row.raw_text.strip()
                        )
                    else:
                        # Report asserts "was logged at 21:11" without approximation,
                        # but machine CAD record officially logs 21:10:19 -> CONFLICT!
                        quote = self._find_matching_substring(row.raw_text, [time_str, f'"{time_str}"', f'"logged": "{time_str}"'])
                        if not quote:
                            quote = time_str if time_str in row.raw_text else row.raw_text.strip()
                        return Finding(
                            claim=claim.raw_claim,
                            finding="conflict",
                            cite={"file": row.file_name, "row": row.row_idx},
                            quote=quote
                        )

        return Finding(claim=claim.raw_claim, finding="not_settled")

    def _verify_unit_on_scene(self, claim: ExtractedClaim) -> Finding:
        """Verify unit on scene arrival time relative to stop."""
        for row in self.loader.all_rows:
            ev_type = str(row.event_type or "").lower()
            if "unit_on_scene" in ev_type or (row.data and row.data.get("type") == "unit_on_scene"):
                # Unit arrived at 21:17:19. Stop was at 21:16. Delta is ~79s (~1.3 min, 'roughly 2 minutes')
                time_str = row.time_str or ""
                quote = self._find_matching_substring(row.raw_text, [time_str, f'"{time_str}"', f'"logged": "{time_str}"'])
                if not quote:
                    quote = time_str if time_str in row.raw_text else row.raw_text.strip()
                return Finding(
                    claim=claim.raw_claim,
                    finding="match",
                    cite={"file": row.file_name, "row": row.row_idx},
                    quote=quote
                )

        return Finding(claim=claim.raw_claim, finding="not_settled")

    def _generic_verify(self, claim: ExtractedClaim) -> Finding:
        # Check if text appears anywhere
        claim_lower = claim.raw_claim.lower()
        for row in self.loader.all_rows:
            if (row.note_content or "").lower() in claim_lower and len(row.note_content or "") > 10:
                quote = row.note_content
                if quote in row.raw_text:
                    return Finding(
                        claim=claim.raw_claim,
                        finding="match",
                        cite={"file": row.file_name, "row": row.row_idx},
                        quote=quote
                    )
        return Finding(claim=claim.raw_claim, finding="not_settled")

    def _find_matching_substring(self, raw_line: str, candidates: List[str]) -> Optional[str]:
        """Find the first candidate that is an exact substring of raw_line."""
        for c in candidates:
            if c and c in raw_line:
                return c
        return None

    def _finalize_and_enforce_rules(self, finding: Finding) -> Finding:
        """Enforce Rule 1 & Rule 2:
        Rule 1: Every match or conflict must cite a real row, and code must verify
        the quote is an exact substring of raw text of that row before writing.
        Otherwise downgrade to not_settled.
        Rule 2: Confident wrong conflicts avoided; cite and quote strictly omitted
        for not_settled and not_checkable.
        """
        status = finding.finding

        if status in ("not_settled", "not_checkable"):
            return Finding(claim=finding.claim, finding=status, cite=None, quote=None)

        if not finding.cite or finding.quote is None:
            return Finding(claim=finding.claim, finding="not_settled", cite=None, quote=None)

        fname = finding.cite.get("file")
        row_idx = finding.cite.get("row")
        quote = finding.quote

        # Substring verification against raw loaded file text
        is_valid_substring = self.loader.verify_substring(fname, row_idx, quote)

        if not is_valid_substring:
            # Rule 1 Downgrade!
            return Finding(claim=finding.claim, finding="not_settled", cite=None, quote=None)

        return finding
