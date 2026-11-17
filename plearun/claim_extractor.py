"""Extracts verifiable factual claims from raw report text."""
import re
from typing import List, Optional
from plearun.models import ExtractedClaim
from plearun.clock_sync import time_str_to_seconds

class ClaimExtractor:
    """Extracts factual claims (times, speeds, counts, locations) from an incident report."""

    def extract_from_file(self, file_path: str) -> List[ExtractedClaim]:
        from plearun.record_loader import read_text
        text = read_text(file_path)
        return self.extract_from_text(text)

    def extract_from_text(self, text: str) -> List[ExtractedClaim]:
        raw_claims: List[str] = []

        for line in text.splitlines():
            line = line.strip()
            if not line:
                continue

            # Skip synthetic disclaimer or file metadata lines
            if "SYNTHETIC" in line.upper() or line.startswith("#"):
                parts = re.split(r"(?<=[.!?])\s+", line)
                for p in parts:
                    p = p.strip()
                    if p and not any(tag in p.upper() for tag in ["SYNTHETIC", "FICTIONAL CASE"]):
                        if any(w in p.lower() for w in [" observed", " stopped", " logged", " showed", " states", " was ", " travelling", " arrived"]):
                            raw_claims.append(p)
                continue

            # Split line into sentences
            sentences = re.split(r"(?<=[.!?])\s+(?=[A-Z0-9])", line)
            for s in sentences:
                s = s.strip()
                if s:
                    raw_claims.append(s)

        extracted = []
        for idx, claim_str in enumerate(raw_claims):
            sem = self._parse_semantics(claim_str, idx)
            extracted.append(sem)

        return extracted

    def _parse_semantics(self, claim: str, idx: int) -> ExtractedClaim:
        lower = claim.lower()

        # Approximation indicators
        is_approx = bool(re.search(r"\b(about|around|roughly|approx|approximately|near)\b", lower))

        # Speed: e.g. "58 mph", "71 mph", "travelling 58"
        speed = None
        speed_match = re.search(r"(?:travelling|speed\s+of|going)?\s*(\d+)\s*(?:mph|miles\s+per\s+hour)", lower)
        if not speed_match:
            speed_match = re.search(r"\b(\d{2,3})\s*(?:in\s*a\s*\d{2})\b", lower)
        if speed_match:
            speed = int(speed_match.group(1))

        # Posted zone / speed limit
        zone = None
        zone_match = re.search(r"(?:posted|in\s*a\s*posted|in\s*a|zone\s*of|speed\s*limit)\s*(\d{2})\b", lower)
        if not zone_match:
            zone_match = re.search(r"\b(\d{2})\s*(?:mph\s*)?zone\b", lower)
        if zone_match:
            zone = int(zone_match.group(1))

        # Clock time: e.g. 21:14, 21:16, 21:11, 21:40
        time_val = None
        time_match = re.search(r"\b(\d{1,2}:\d{2}(?::\d{2})?)\b", claim)
        if time_match:
            time_val = time_match.group(1)
        time_secs = time_str_to_seconds(time_val) if time_val else None

        # Relative delta: e.g. "roughly 2 minutes after the stop", "10 minutes after"
        delta_min = None
        rel_event = None
        rel_match = re.search(r"(\d+(?:\.\d+)?)\s*minutes?\s+after\s+(?:the\s+)?(\w+)", lower)
        if rel_match:
            delta_min = float(rel_match.group(1))
            rel_event = rel_match.group(2)

        # Claimed source
        claimed_source = None
        if re.search(r"\b(camera|traffic\s*cam|cam-\d+)\b", lower):
            claimed_source = "camera"
        elif re.search(r"\b(bodycam|transcript|audio|speech)\b", lower):
            claimed_source = "bodycam"
        elif re.search(r"\b(dispatch|cad|911\s*call|dispatched)\b", lower):
            claimed_source = "dispatch"
        elif re.search(r"\b(officer\s*lee)\b", lower):
            claimed_source = "officer"

        # Event type classification
        ev_type = "unknown"
        if "dispatched" in lower or "unit dispatched" in lower:
            ev_type = "unit_dispatched"
        elif "observed" in lower or "travelling" in lower or speed is not None:
            ev_type = "observation"
        elif "pulled into" in lower or "stopped" in lower or "stop" in lower:
            if delta_min is not None and "on scene" in lower:
                ev_type = "unit_on_scene"
            else:
                ev_type = "stop"
        elif "911 call" in lower or "call" in lower:
            ev_type = "911_call"
        elif "on scene" in lower or "dispatch records show" in lower:
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
        elif "mile marker" in lower:
            mm_match = re.search(r"mile\s*marker\s*(\d+)", lower)
            location = f"mile marker {mm_match.group(1)}" if mm_match else "mile marker"

        return ExtractedClaim(
            raw_claim=claim,
            sentence_idx=idx,
            stated_speed=speed,
            stated_zone=zone,
            stated_time=time_val,
            stated_time_seconds=time_secs,
            stated_delta_minutes=delta_min,
            relative_to_event=rel_event,
            is_approximate=is_approx,
            event_type=ev_type,
            actor=actor,
            claimed_source=claimed_source,
            location=location
        )
