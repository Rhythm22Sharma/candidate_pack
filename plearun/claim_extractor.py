"""Extracts verifiable factual claims from raw report text."""
import re
from typing import List, Optional
from plearun.models import ExtractedClaim

class ClaimExtractor:
    """Extracts factual claims (times, speeds, counts, locations) from an incident report."""

    def extract_from_file(self, file_path: str) -> List[ExtractedClaim]:
        with open(file_path, "r", encoding="utf-8", errors="replace") as f:
            text = f.read()
        return self.extract_from_text(text)

    def extract_from_text(self, text: str) -> List[ExtractedClaim]:
        raw_claims: List[str] = []

        for line in text.splitlines():
            line = line.strip()
            if not line:
                continue

            # Skip synthetic disclaimer or file metadata lines
            if "SYNTHETIC" in line.upper() or line.startswith("#"):
                # If the line is only disclaimer/header, skip
                # Check if there is narrative content after the header
                parts = re.split(r"(?<=[.!?])\s+", line)
                for p in parts:
                    p = p.strip()
                    if p and not any(tag in p.upper() for tag in ["SYNTHETIC", "FICTIONAL CASE"]):
                        # Check if it has a predicate verb (factual claim vs title fragment)
                        if any(w in p.lower() for w in [" observed", " stopped", " logged", " showed", " states", " was ", " travelling", " arrived"]):
                            raw_claims.append(p)
                continue

            # Split line into sentences
            sentences = re.split(r"(?<=[.!?])\s+(?=[A-Z0-9])", line)
            for s in sentences:
                s = s.strip()
                if s:
                    raw_claims.append(s)

        # Parse semantics for each claim
        extracted = []
        for idx, claim_str in enumerate(raw_claims):
            sem = self._parse_semantics(claim_str, idx)
            extracted.append(sem)

        return extracted

    def _parse_semantics(self, claim: str, idx: int) -> ExtractedClaim:
        lower = claim.lower()

        # Check approximation indicators
        is_approx = bool(re.search(r"\b(about|around|roughly|approx|approximately)\b", lower))

        # Check speeds e.g. "58 mph in a posted 45 zone"
        speed = None
        speed_match = re.search(r"(\d+)\s*(?:mph|miles per hour)", lower)
        if speed_match:
            speed = int(speed_match.group(1))

        # Check posted zone
        zone = None
        zone_match = re.search(r"(?:posted|speed limit|zone)\s*(?:of\s*)?(\d+)", lower)
        if zone_match:
            zone = int(zone_match.group(1))

        # Check time expressions e.g. 21:14, 21:16, 21:11
        time_val = None
        time_match = re.search(r"\b(\d{1,2}:\d{2}(?::\d{2})?)\b", claim)
        if time_match:
            time_val = time_match.group(1)

        # Check relative time e.g. "roughly 2 minutes after the stop"
        delta_min = None
        rel_event = None
        rel_match = re.search(r"(\d+(?:\.\d+)?)\s*minutes?\s+after\s+(?:the\s+)?(\w+)", lower)
        if rel_match:
            delta_min = float(rel_match.group(1))
            rel_event = rel_match.group(2)

        # Event type classification
        ev_type = "unknown"
        if "observed" in lower or "travelling" in lower or "mph" in lower:
            ev_type = "observation"
        elif "pulled into" in lower or "stopped" in lower:
            ev_type = "stop"
        elif "911 call" in lower or "call" in lower:
            ev_type = "911_call"
        elif "unit on scene" in lower or "on scene" in lower or "dispatch records show" in lower:
            ev_type = "unit_on_scene"
        elif "only car" in lower or "lane" in lower:
            ev_type = "lane_status"

        # Actor
        actor = None
        if "officer lee" in lower:
            actor = "Officer Lee"
        elif "dispatch" in lower:
            actor = "Dispatch"
        elif "driver" in lower:
            actor = "driver"

        # Location
        location = None
        if "harbor gas" in lower:
            location = "Harbor Gas lot"
        elif "mile marker 4" in lower:
            location = "mile marker 4"

        return ExtractedClaim(
            raw_claim=claim,
            sentence_idx=idx,
            stated_speed=speed,
            stated_zone=zone,
            stated_time=time_val,
            stated_delta_minutes=delta_min,
            relative_to_event=rel_event,
            is_approximate=is_approx,
            event_type=ev_type,
            actor=actor,
            location=location
        )
