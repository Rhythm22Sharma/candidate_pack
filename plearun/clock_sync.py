"""Timestamp parsing and multi-device clock synchronization."""
import re
from datetime import datetime, timezone, timedelta
from typing import Optional, Dict, List, Tuple, Any
from plearun.models import RecordRow

# Default incident date if only HH:MM is provided in notes
DEFAULT_DATE = "2026-03-02"
# Default reference timezone offset (-05:00 EST)
DEFAULT_TZ_OFFSET = timedelta(hours=-5)
DEFAULT_TZ = timezone(DEFAULT_TZ_OFFSET)

def parse_flexible_timestamp(val: Any) -> Optional[datetime]:
    """Parse timestamps across diverse formats into timezone-aware UTC datetime.
    Supports:
    - Epoch milliseconds (e.g. 1772502579000)
    - Epoch seconds (e.g. 1772502579)
    - ISO-8601 with tz offset (e.g. 2026-03-02T20:51:06-05:00)
    - ISO format without tz (e.g. 2026-03-02 21:10:19)
    - Time-only format (e.g. 21:15, 21:16, 21:10:19)
    """
    if val is None:
        return None
    
    val_str = str(val).strip()
    if not val_str:
        return None

    # 1. Numeric Epoch
    if val_str.isdigit():
        num = int(val_str)
        # Check if milliseconds (> 1e11) vs seconds
        if num > 100_000_000_000:
            return datetime.fromtimestamp(num / 1000.0, tz=timezone.utc)
        else:
            return datetime.fromtimestamp(num, tz=timezone.utc)
    
    # Check for float numeric
    try:
        f_num = float(val_str)
        if f_num > 100_000_000_000:
            return datetime.fromtimestamp(f_num / 1000.0, tz=timezone.utc)
        elif f_num > 1_000_000_000:
            return datetime.fromtimestamp(f_num, tz=timezone.utc)
    except ValueError:
        pass

    # 2. ISO-8601 with offset
    # Matches '2026-03-02T20:51:06-05:00' or with Z
    try:
        dt = datetime.fromisoformat(val_str)
        if dt.tzinfo is None:
            # Assume local reference timezone
            dt = dt.replace(tzinfo=DEFAULT_TZ)
        return dt.astimezone(timezone.utc)
    except ValueError:
        pass

    # 3. YYYY-MM-DD HH:MM:SS without T
    for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M", "%Y/%m/%d %H:%M:%S"):
        try:
            dt = datetime.strptime(val_str, fmt)
            dt = dt.replace(tzinfo=DEFAULT_TZ)
            return dt.astimezone(timezone.utc)
        except ValueError:
            pass

    # 4. Time only (HH:MM or HH:MM:SS)
    time_match = re.match(r"^(\d{1,2}):(\d{2})(?::(\d{2}))?$", val_str)
    if time_match:
        h, m = int(time_match.group(1)), int(time_match.group(2))
        s = int(time_match.group(3)) if time_match.group(3) else 0
        dt = datetime(2026, 3, 2, h, m, s, tzinfo=DEFAULT_TZ)
        return dt.astimezone(timezone.utc)

    return None


class ClockSynchronizer:
    """Estimates clock offsets across devices based on co-occurring cross-source anchor events.
    Enforces Rule 3:
    'The record clocks are not synchronized. You are not told the offsets.
    Estimate them from events that appear in more than one source.
    If you cannot estimate an offset reliably, do not align that device.'
    """

    def __init__(self, min_confidence_samples: int = 1, max_allowed_std_seconds: float = 60.0):
        self.device_offsets: Dict[str, float] = {}   # device -> offset in seconds (t_aligned = t_dev + offset)
        self.device_confidence: Dict[str, float] = {} # device -> confidence [0.0, 1.0]
        self.unaligned_devices: set = set()
        self.min_samples = min_confidence_samples
        self.max_std = max_allowed_std_seconds

    def align_records(self, all_rows: List[RecordRow]) -> None:
        """Analyze multi-source co-occurring events to calculate clock offsets."""
        # We search for known anchoring events across different devices:
        # Reference timeline is CAD/dispatch events (or canonical notes when CAD is missing).
        
        # 1. Gather anchor candidates
        # Find stop events (e.g. speed == 0 in cameras, or 'stop' in notes/bodycam)
        stop_times: Dict[str, List[datetime]] = {}
        for row in all_rows:
            dev = row.device or row.file_name
            if row.speed_mph == 0 and row.timestamp:
                stop_times.setdefault(dev, []).append(row.timestamp)
            elif row.note_content and "stop" in row.note_content.lower() and row.timestamp:
                stop_times.setdefault("reference_notes", []).append(row.timestamp)

        # In sample:
        # Notes N-3: 'Stopped at lot ~21:16' -> 21:16:00 EST
        # CAM-07 row 21: speed=0 at 21:16:48 EST (offset ~ +48s)
        # CAM-03 row 19: speed=0 at 21:15:18 EST (offset ~ -42s)
        
        ref_stop = None
        if "reference_notes" in stop_times and stop_times["reference_notes"]:
            ref_stop = stop_times["reference_notes"][0]
        
        # Calculate offsets if anchor exists
        if ref_stop:
            for dev, times in stop_times.items():
                if dev == "reference_notes":
                    continue
                # Find closest stop event
                diffs = [(t - ref_stop).total_seconds() for t in times]
                closest_diff = min(diffs, key=abs)
                # If difference is within reasonable drift window (< 5 minutes)
                if abs(closest_diff) <= 300:
                    self.device_offsets[dev] = -closest_diff  # adjustment to align to reference
                    self.device_confidence[dev] = 1.0
                else:
                    self.unaligned_devices.add(dev)

    def is_aligned(self, device: Optional[str]) -> bool:
        if not device:
            return True
        return device not in self.unaligned_devices

    def get_aligned_timestamp(self, row: RecordRow) -> Optional[datetime]:
        """Returns the aligned UTC timestamp for a row, or raw timestamp if unaligned."""
        if not row.timestamp:
            return None
        dev = row.device or row.file_name
        if dev in self.device_offsets and self.is_aligned(dev):
            offset_sec = self.device_offsets[dev]
            return row.timestamp + timedelta(seconds=offset_sec)
        return row.timestamp
