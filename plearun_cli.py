#!/usr/bin/env python3
"""Plearun Verification Engine CLI.
Usage:
    python3 plearun_cli.py REPORT RECORDS_DIR > findings.json
"""
import sys
import os
import json

# Ensure plearun package is on sys.path
SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
if SCRIPT_DIR not in sys.path:
    sys.path.insert(0, SCRIPT_DIR)

from plearun.record_loader import RecordLoader
from plearun.clock_sync import ClockSynchronizer
from plearun.claim_extractor import ClaimExtractor
from plearun.verifier import ReportVerifier

def main():
    if len(sys.argv) < 3:
        print("Usage: python3 plearun_cli.py REPORT RECORDS_DIR", file=sys.stderr)
        sys.exit(2)

    report_path = sys.argv[1]
    records_dir = sys.argv[2]

    if not os.path.exists(report_path):
        print(f"Error: report file not found: {report_path}", file=sys.stderr)
        sys.exit(1)

    if not os.path.exists(records_dir):
        print(f"Error: records directory not found: {records_dir}", file=sys.stderr)
        sys.exit(1)

    # 1. Ingest machine records
    loader = RecordLoader()
    loader.load_directory(records_dir)

    # 2. Clock synchronization & device alignment
    clock_sync = ClockSynchronizer()
    clock_sync.align_records(loader.all_rows)

    # 3. Extract claims from report
    extractor = ClaimExtractor()
    claims = extractor.extract_from_file(report_path)

    # 4. Verify claims against records
    verifier = ReportVerifier(loader, clock_sync)
    findings = verifier.verify_all(claims)

    # 5. Format output JSON strictly according to specification
    output_obj = {
        "findings": [f.to_dict() for f in findings]
    }

    # Print to stdout
    print(json.dumps(output_obj, indent=2))

if __name__ == "__main__":
    main()
