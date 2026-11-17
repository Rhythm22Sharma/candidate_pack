"""Data models for Plearun verification pipeline."""
from dataclasses import dataclass, field
from datetime import datetime
from typing import Optional, Dict, Any, List

@dataclass
class RecordRow:
    """Represents a single raw row/line or cue from an ingested record file."""
    file_name: str
    row_idx: int                          # 1-indexed file line number (or cue number for VTT)
    raw_text: str                         # Exact string from original file line or cue text
    cue_idx: Optional[int] = None         # 1-indexed WebVTT cue number if applicable
    timestamp: Optional[datetime] = None  # Normalized UTC datetime if parseable
    time_str: Optional[str] = None        # Original raw timestamp string
    device: Optional[str] = None          # Device identifier e.g. CAM-03, CAM-07, L-12, etc.
    author: Optional[str] = None          # Note author or speaker e.g. Officer Lee, Dispatch
    event_type: Optional[str] = None      # speed_reading, 911_call, unit_on_scene, note, speech
    speed_mph: Optional[int] = None
    zone_mph: Optional[int] = None
    note_content: Optional[str] = None
    is_adversarial: bool = False
    data: Dict[str, Any] = field(default_factory=dict)

@dataclass
class LoadedRecordFile:
    """Represents a parsed record file with raw lines and cues cached for quote verification."""
    file_name: str
    file_path: str
    format: str                           # 'csv', 'json', 'vtt', 'txt'
    encoding: str
    raw_lines: List[str]                  # Exact lines from splitlines()
    cues: Dict[int, str] = field(default_factory=dict)  # cue_idx -> raw cue text
    rows: List[RecordRow] = field(default_factory=list)

@dataclass
class ExtractedClaim:
    """Represents a factual claim extracted from the incident report."""
    raw_claim: str                        # Exact claim as written in the report
    sentence_idx: int
    stated_speed: Optional[int] = None
    stated_zone: Optional[int] = None
    stated_time: Optional[str] = None                 # e.g. '21:14', '21:16', '21:11'
    stated_time_seconds: Optional[int] = None         # Seconds from midnight (e.g. 21:14 -> 76440)
    stated_delta_minutes: Optional[float] = None      # e.g. 2.0 minutes
    relative_to_event: Optional[str] = None           # e.g. 'stop'
    is_approximate: bool = False                      # about, around, roughly
    event_type: Optional[str] = None                  # 'observation', 'stop', '911_call', 'unit_on_scene', 'lane_status'
    actor: Optional[str] = None                       # 'Officer Lee', 'driver', etc.
    location: Optional[str] = None                    # 'mile marker 4', 'Harbor Gas lot'

@dataclass
class Finding:
    """Output finding according to Plearun schema."""
    claim: str
    finding: str                                      # 'match' | 'conflict' | 'not_settled' | 'not_checkable'
    cite: Optional[Dict[str, Any]] = None             # {"file": str, "row": int}
    quote: Optional[str] = None

    def to_dict(self) -> Dict[str, Any]:
        result: Dict[str, Any] = {
            "claim": self.claim,
            "finding": self.finding
        }
        if self.finding in ("match", "conflict"):
            if self.cite:
                result["cite"] = self.cite
            if self.quote is not None:
                result["quote"] = self.quote
        return result
