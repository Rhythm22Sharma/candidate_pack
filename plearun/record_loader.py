"""Multi-format resilient record loader for CSV, JSON, and WebVTT files."""
import os
import re
import csv
import json
from typing import Dict, List, Optional, Tuple, Any
from plearun.models import RecordRow, LoadedRecordFile
from plearun.clock_sync import parse_flexible_timestamp

def detect_and_read_file(file_path: str) -> Tuple[str, List[str], str]:
    """Read a file trying encodings: utf-8-sig (handles BOM), utf-8, cp1252, latin-1.
    Returns: (full_content, raw_lines, used_encoding)
    """
    if "cp1252" in file_path.lower() or "windows-1252" in file_path.lower():
        encodings_to_try = ["cp1252", "utf-8-sig", "utf-8", "latin-1"]
    else:
        encodings_to_try = ["utf-8-sig", "utf-8", "cp1252", "latin-1"]
    with open(file_path, "rb") as f:
        raw_bytes = f.read()

    for enc in encodings_to_try:
        try:
            text = raw_bytes.decode(enc)
            # splitlines(keepends=False)
            lines = text.splitlines()
            return text, lines, enc
        except (UnicodeDecodeError, UnicodeError):
            continue

    # Fallback with replacement
    text = raw_bytes.decode("latin-1", errors="replace")
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
                fpath = os.path.join(root, fname)
                self.load_file(fpath, fname)

        return self.all_rows

    def load_file(self, file_path: str, file_name: Optional[str] = None) -> LoadedRecordFile:
        """Parse a single record file based on format/extension/content."""
        if file_name is None:
            file_name = os.path.basename(file_path)

        text, raw_lines, enc = detect_and_read_file(file_path)
        rows: List[RecordRow] = []

        lower_name = file_name.lower()
        if lower_name.endswith(".csv"):
            rows = self._parse_csv(file_name, raw_lines)
            fmt = "csv"
        elif lower_name.endswith(".json"):
            rows = self._parse_json(file_name, text, raw_lines)
            fmt = "json"
        elif lower_name.endswith(".vtt") or text.startswith("WEBVTT"):
            rows = self._parse_vtt(file_name, raw_lines)
            fmt = "vtt"
        else:
            # Fallback text parser
            rows = self._parse_plain_text(file_name, raw_lines)
            fmt = "txt"

        loaded = LoadedRecordFile(
            file_name=file_name,
            file_path=file_path,
            format=fmt,
            encoding=enc,
            raw_lines=raw_lines,
            rows=rows
        )
        self.files[file_name] = loaded
        self.all_rows.extend(rows)
        return loaded

    def _parse_csv(self, file_name: str, raw_lines: List[str]) -> List[RecordRow]:
        """Parse CSV with potential BOM, marker comment lines (#), and arbitrary headers."""
        rows: List[RecordRow] = []
        header: Optional[List[str]] = None
        header_line_idx = -1

        for i, line in enumerate(raw_lines, 1):
            stripped = line.strip()
            if not stripped:
                continue
            # Skip comment / marker lines like '#PLEARUN ...'
            if stripped.startswith("#"):
                continue

            # Parse with csv reader
            parsed_cells = next(csv.reader([line]))
            if header is None:
                header = [c.strip() for c in parsed_cells]
                header_line_idx = i
                continue

            # Data row
            data_map = {}
            for col_idx, col_name in enumerate(header):
                if col_idx < len(parsed_cells):
                    data_map[col_name.lower()] = parsed_cells[col_idx].strip()

            # Extract fields
            # Camera format: timestamp, device, speed_mph, zone_mph
            # Notes format: ID, Author, Note, Time
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

        # Locate events array
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

        # For each event, locate where it appears in raw_lines
        for ev in events:
            if not isinstance(ev, dict):
                continue
            ev_id = ev.get("id", "")
            ev_type = ev.get("type", "")
            logged_val = ev.get("logged") or ev.get("timestamp") or ev.get("time")
            unit_val = ev.get("unit")

            # Find matching line number in raw_lines
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

            parsed_ts = parse_flexible_timestamp(logged_val)

            row_obj = RecordRow(
                file_name=file_name,
                row_idx=line_idx,
                raw_text=line_text,
                timestamp=parsed_ts,
                time_str=str(logged_val) if logged_val is not None else None,
                device=unit_val or ev_id,
                author="Dispatch",
                event_type=ev_type or "cad_event",
                note_content=f"{ev_type} {unit_val or ''}".strip(),
                data=ev
            )
            rows.append(row_obj)

        return rows

    def _parse_vtt(self, file_name: str, raw_lines: List[str]) -> List[RecordRow]:
        """Parse WebVTT files with cues and overlapping timestamps."""
        rows: List[RecordRow] = []
        cue_idx = 0
        i = 0
        n = len(raw_lines)

        while i < n:
            line = raw_lines[i]
            # Match WebVTT timestamp line: '00:00:00.000 --> 00:00:04.000'
            if "-->" in line:
                cue_idx += 1
                timing_line_idx = i + 1
                # Next line is speech payload
                i += 1
                payload_lines = []
                payload_line_idx = i + 1
                while i < n and raw_lines[i].strip():
                    payload_lines.append(raw_lines[i])
                    i += 1
                
                payload_text = " ".join(payload_lines)
                author = None
                speech = payload_text
                # E.g. [SYNTHETIC] Officer Lee: 58 in a 45.
                speaker_match = re.search(r"(?:\[SYNTHETIC\]\s*)?([^:]+):\s*(.*)", payload_text)
                if speaker_match:
                    author = speaker_match.group(1).strip()
                    speech = speaker_match.group(2).strip()

                # In WebVTT, citation row can be line number of text or cue number
                # We cite the line where the quote appears (payload_line_idx)
                row_obj = RecordRow(
                    file_name=file_name,
                    row_idx=payload_line_idx,
                    raw_text=raw_lines[payload_line_idx - 1] if payload_line_idx <= len(raw_lines) else payload_text,
                    timestamp=None,  # VTT times are video offsets (00:00:00)
                    time_str=line.strip(),
                    device="bodycam",
                    author=author,
                    event_type="speech",
                    note_content=speech,
                    data={"cue_index": cue_idx, "timing": line.strip(), "full_cue": payload_text}
                )
                rows.append(row_obj)
            else:
                i += 1

        return rows

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
        """Enforces Rule 1: quote MUST be an exact substring of raw text at row_idx."""
        if file_name not in self.files:
            return False
        loaded = self.files[file_name]
        if not (1 <= row_idx <= len(loaded.raw_lines)):
            return False
        raw_row = loaded.raw_lines[row_idx - 1]
        return quote in raw_row
