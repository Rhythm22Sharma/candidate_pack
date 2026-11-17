"""Multi-format resilient record loader for CSV, JSON, and WebVTT files."""
import os
import re
import csv
import json
from typing import Dict, List, Optional, Tuple, Any
from plearun.models import RecordRow, LoadedRecordFile
from plearun.clock_sync import parse_flexible_timestamp
from plearun.security import sanitize_as_literal_data, contains_adversarial_injection, neutralize_injections

def read_text(path: str) -> str:
    """Read a text file resiliently, detecting UTF-16 (BOM/no BOM),
    UTF-8-SIG (BOM), plain UTF-8, and CP1252/Latin-1.
    """
    with open(path, "rb") as f:
        raw = f.read()

    # 1. UTF-16 with BOM
    if raw.startswith((b"\xff\xfe", b"\xfe\xff")):
        return raw.decode("utf-16")

    # 2. UTF-8 with BOM
    if raw.startswith(b"\xef\xbb\xbf"):
        return raw.decode("utf-8-sig")

    # 3. UTF-16 without BOM (detect null bytes in the first 200 bytes)
    if b"\x00" in raw[:200]:
        even_nulls = sum(1 for i in range(0, min(len(raw), 200), 2) if raw[i] == 0)
        odd_nulls = sum(1 for i in range(1, min(len(raw), 200), 2) if raw[i] == 0)
        preferred = "utf-16-be" if even_nulls > odd_nulls else "utf-16-le"
        alternate = "utf-16-le" if preferred == "utf-16-be" else "utf-16-be"
        try:
            return raw.decode(preferred)
        except UnicodeDecodeError:
            pass
        try:
            return raw.decode(alternate)
        except UnicodeDecodeError:
            pass

    # 4. Check if filename hints at CP1252
    if "cp1252" in path.lower() or "windows-1252" in path.lower():
        try:
            return raw.decode("cp1252")
        except UnicodeDecodeError:
            pass

    # 5. Plain UTF-8
    try:
        return raw.decode("utf-8")
    except UnicodeDecodeError:
        pass

    # 6. CP1252 / Latin-1 fallback
    try:
        return raw.decode("cp1252")
    except UnicodeDecodeError:
        return raw.decode("latin-1", errors="replace")


def detect_and_read_file(file_path: str) -> Tuple[str, List[str], str]:
    """Read a file detecting encodings (UTF-16, UTF-8-SIG, UTF-8, CP1252, Latin-1).
    Returns: (full_content, raw_lines, used_encoding)
    """
    with open(file_path, "rb") as f:
        raw = f.read()

    if raw.startswith((b"\xff\xfe", b"\xfe\xff")):
        text = raw.decode("utf-16")
        return text, text.splitlines(), "utf-16"

    if raw.startswith(b"\xef\xbb\xbf"):
        text = raw.decode("utf-8-sig")
        return text, text.splitlines(), "utf-8-sig"

    if b"\x00" in raw[:200]:
        even_nulls = sum(1 for i in range(0, min(len(raw), 200), 2) if raw[i] == 0)
        odd_nulls = sum(1 for i in range(1, min(len(raw), 200), 2) if raw[i] == 0)
        preferred = "utf-16-be" if even_nulls > odd_nulls else "utf-16-le"
        alternate = "utf-16-le" if preferred == "utf-16-be" else "utf-16-be"
        try:
            text = raw.decode(preferred)
            return text, text.splitlines(), preferred
        except UnicodeDecodeError:
            pass
        try:
            text = raw.decode(alternate)
            return text, text.splitlines(), alternate
        except UnicodeDecodeError:
            pass
            pass

    if "cp1252" in file_path.lower() or "windows-1252" in file_path.lower():
        try:
            text = raw.decode("cp1252")
            return text, text.splitlines(), "cp1252"
        except UnicodeDecodeError:
            pass

    try:
        text = raw.decode("utf-8")
        return text, text.splitlines(), "utf-8"
    except UnicodeDecodeError:
        pass

    try:
        text = raw.decode("cp1252")
        return text, text.splitlines(), "cp1252"
    except UnicodeDecodeError:
        text = raw.decode("latin-1", errors="replace")
        return text, text.splitlines(), "latin-1"


class RecordLoader:
    """Loads and caches records from RECORDS_DIR across all supported machine formats."""

    def __init__(self):
        self.files: Dict[str, LoadedRecordFile] = {}
        self.all_rows: List[RecordRow] = []

    def load_directory(self, records_dir: str) -> List[RecordRow]:
        """Scan and load all files in records_dir."""
        if not os.path.exists(records_dir):
            raise FileNotFoundError(f"Records directory not found: {records_dir}")

        for root, _, filenames in os.walk(records_dir):
            for fname in sorted(filenames):
                # Ignore hidden files or cache directories
                if fname.startswith(".") or fname.endswith(".pyc"):
                    continue
                fpath = os.path.join(root, fname)
                self.load_file(fpath, fname)

        return self.all_rows

    def load_file(self, file_path: str, file_name: Optional[str] = None) -> LoadedRecordFile:
        """Parse a single record file based on format/extension/content."""
        if file_name is None:
            file_name = os.path.basename(file_path)

        text, raw_lines, enc = detect_and_read_file(file_path)
        rows: List[RecordRow] = []
        cues_map: Dict[int, str] = {}

        lower_name = file_name.lower()
        if lower_name.endswith(".csv"):
            rows = self._parse_csv(file_name, raw_lines)
            fmt = "csv"
        elif lower_name.endswith(".json"):
            rows = self._parse_json(file_name, text, raw_lines)
            fmt = "json"
        elif lower_name.endswith(".vtt") or text.startswith("WEBVTT"):
            rows, cues_map = self._parse_vtt(file_name, raw_lines)
            fmt = "vtt"
        else:
            rows = self._parse_plain_text(file_name, raw_lines)
            fmt = "txt"

        loaded = LoadedRecordFile(
            file_name=file_name,
            file_path=file_path,
            format=fmt,
            encoding=enc,
            raw_lines=raw_lines,
            cues=cues_map,
            rows=rows
        )
        self.files[file_name] = loaded
        self.all_rows.extend(rows)
        return loaded

    def _parse_csv(self, file_name: str, raw_lines: List[str]) -> List[RecordRow]:
        """Parse CSV with BOM, marker comment lines (#), and arbitrary headers."""
        rows: List[RecordRow] = []
        header: Optional[List[str]] = None

        for i, line in enumerate(raw_lines, 1):
            stripped = line.strip()
            if not stripped:
                continue
            # Treat comment / marker line as data row if requested, but skip as CSV header
            if stripped.startswith("#"):
                continue

            if '"' in line:
                try:
                    parsed_cells = next(csv.reader([line]))
                except Exception:
                    continue
            else:
                parsed_cells = line.split(",")

            if header is None:
                header = [c.strip().lower() for c in parsed_cells]
                continue

            data_map = {}
            for col_idx, col_name in enumerate(header):
                if col_idx < len(parsed_cells):
                    data_map[col_name] = sanitize_as_literal_data(parsed_cells[col_idx].strip())

            ts_val = data_map.get("timestamp") or data_map.get("time")
            dev_val = data_map.get("device") or data_map.get("author") or data_map.get("id")
            
            speed = None
            if "speed_mph" in data_map and data_map["speed_mph"].isdigit():
                speed = int(data_map["speed_mph"])
            
            zone = None
            if "zone_mph" in data_map and data_map["zone_mph"].isdigit():
                zone = int(data_map["zone_mph"])

            note_text = data_map.get("note") or data_map.get("message")
            author = data_map.get("author")
            is_adv = contains_adversarial_injection(line)
            if note_text:
                note_text = neutralize_injections(note_text)

            parsed_ts = parse_flexible_timestamp(ts_val) if ts_val else None

            row_obj = RecordRow(
                file_name=file_name,
                row_idx=i,  # 1-indexed original line number
                raw_text=line,
                timestamp=parsed_ts,
                time_str=ts_val,
                device=dev_val,
                author=author,
                event_type="speed_reading" if speed is not None else "note",
                speed_mph=speed,
                zone_mph=zone,
                note_content=note_text,
                is_adversarial=is_adv,
                data=data_map
            )
            rows.append(row_obj)

        return rows

    def _parse_json(self, file_name: str, raw_text: str, raw_lines: List[str]) -> List[RecordRow]:
        """Parse JSON (handling CAD export events) and map events to line numbers."""
        rows: List[RecordRow] = []
        try:
            parsed = json.loads(raw_text)
        except json.JSONDecodeError:
            return rows

        events = []
        if isinstance(parsed, dict):
            if "export" in parsed and "events" in parsed["export"]:
                events = parsed["export"]["events"]
            elif "events" in parsed:
                events = parsed["events"]
            else:
                events = [parsed]
        elif isinstance(parsed, list):
            events = parsed

        for ev in events:
            if not isinstance(ev, dict):
                continue
            ev_id = sanitize_as_literal_data(ev.get("id", ""))
            ev_type = sanitize_as_literal_data(ev.get("type", ""))
            logged_val = ev.get("logged") or ev.get("timestamp") or ev.get("time")
            unit_val = sanitize_as_literal_data(ev.get("unit", ""))

            # Map to line number in raw_lines
            line_idx = 1
            line_text = ""
            for idx, l in enumerate(raw_lines, 1):
                if logged_val is not None and str(logged_val) in l:
                    line_idx = idx
                    line_text = l
                    break
                elif ev_id and ev_id in l:
                    line_idx = idx
                    line_text = l
                elif ev_type and ev_type in l and not line_text:
                    line_idx = idx
                    line_text = l

            if not line_text and 1 <= line_idx <= len(raw_lines):
                line_text = raw_lines[line_idx - 1]

            is_adv = contains_adversarial_injection(line_text)
            parsed_ts = parse_flexible_timestamp(logged_val)

            row_obj = RecordRow(
                file_name=file_name,
                row_idx=line_idx,
                raw_text=line_text,
                timestamp=parsed_ts,
                time_str=str(logged_val) if logged_val is not None else None,
                device=unit_val or ev_id or "CAD",
                author="Dispatch",
                event_type=ev_type or "cad_event",
                note_content=f"{ev_type} {unit_val}".strip(),
                is_adversarial=is_adv,
                data=ev
            )
            rows.append(row_obj)

        return rows

    def _parse_vtt(self, file_name: str, raw_lines: List[str]) -> Tuple[List[RecordRow], Dict[int, str]]:
        """Parse WebVTT cues, assigning 1-indexed cue numbers according to the specification."""
        rows: List[RecordRow] = []
        cues_map: Dict[int, str] = {}
        cue_idx = 0
        i = 0
        n = len(raw_lines)

        while i < n:
            line = raw_lines[i]
            if "-->" in line:
                cue_idx += 1
                timing_str = line.strip()
                i += 1
                payload_lines = []
                while i < n and raw_lines[i].strip():
                    payload_lines.append(raw_lines[i])
                    i += 1

                cue_text = "\n".join(payload_lines)
                cues_map[cue_idx] = cue_text

                author = None
                speech = cue_text
                speaker_match = re.search(r"(?:\[SYNTHETIC\]\s*)?([^:]+):\s*(.*)", cue_text, re.DOTALL)
                if speaker_match:
                    author = speaker_match.group(1).strip()
                    speech = speaker_match.group(2).strip()

                is_adv = contains_adversarial_injection(cue_text)
                if speech:
                    speech = neutralize_injections(speech)

                # For WebVTT, row_idx is the cue number (1, 2, 3...) per spec: "row number or cue number"
                row_obj = RecordRow(
                    file_name=file_name,
                    row_idx=cue_idx,     # Cue number as row index!
                    cue_idx=cue_idx,
                    raw_text=cue_text,   # Raw text of that cue
                    timestamp=None,      # WebVTT timestamps are relative media offsets
                    time_str=timing_str,
                    device="bodycam",
                    author=author,
                    event_type="speech",
                    note_content=speech,
                    is_adversarial=is_adv,
                    data={"cue_index": cue_idx, "timing": timing_str, "full_cue": cue_text}
                )
                rows.append(row_obj)
            else:
                i += 1

        return rows, cues_map

    def _parse_plain_text(self, file_name: str, raw_lines: List[str]) -> List[RecordRow]:
        rows: List[RecordRow] = []
        for i, line in enumerate(raw_lines, 1):
            if not line.strip():
                continue
            rows.append(RecordRow(
                file_name=file_name,
                row_idx=i,
                raw_text=line,
                note_content=line.strip(),
                data={"text": line.strip()}
            ))
        return rows

    def verify_substring(self, file_name: str, row_idx: int, quote: str) -> bool:
        """Enforces Rule 1: quote MUST be an exact substring of raw text of that row/cue."""
        if not file_name or file_name not in self.files or not quote:
            return False
        loaded = self.files[file_name]

        # If it's a VTT file, check cue text (or raw line as fallback)
        if loaded.format == "vtt":
            if row_idx in loaded.cues:
                if quote in loaded.cues[row_idx]:
                    return True
            # Fallback to line index
            if 1 <= row_idx <= len(loaded.raw_lines):
                if quote in loaded.raw_lines[row_idx - 1]:
                    return True
            return False

        # For line-based files (CSV, JSON, TXT)
        if not (1 <= row_idx <= len(loaded.raw_lines)):
            return False
        raw_row = loaded.raw_lines[row_idx - 1]
        return quote in raw_row
