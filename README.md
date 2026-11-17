# Plearun Verification Engine

## How It Works
`plearun_cli.py` is a deterministic, offline Python CLI tool (`python3 plearun_cli.py REPORT RECORDS_DIR > findings.json`) that checks incident report claims against multi-source records:
1. **Resilient Ingestion (`record_loader.py`)**: Auto-detects encodings (`utf-8-sig` BOM, `cp1252`, `utf-8`), skips comment markers (`#PLEARUN`), parses CSV/JSON/WebVTT, and neutralizes prompt injections (`security.py`). Caches raw lines and cues for verification.
2. **Clock Alignment (`clock_sync.py`)**: Normalizes timestamps across ISO-8601, epoch ms, and local formats. Discovers co-occurring anchor events (e.g. stop events with `speed == 0`). Enforces Rule 3: devices without multi-source anchors (like CAD) remain unaligned, widening uncertainty windows to 90s+.
3. **Claim Extraction (`claim_extractor.py`)**: Segments sentences and extracts semantic entities: speeds, posted zones, timestamps, relative deltas, and claimed sources (camera vs bodycam).
4. **Verification & Rule Enforcement (`verifier.py`)**: Compares claims against records using numerical and modality-specific tolerances. Enforces Rule 1 (downgrades to `not_settled` if `quote` is not an exact substring of the cited row/cue) and Rule 2 (avoids false conflicts/matches).

## Three Hardest Decisions
1. **Classifying Claim 3 as `not_settled` (Rule 2 & 3)**: CAD logs the 911 call at `21:10:19`, while the report states `21:11` (a 41-second gap). Because CAD shares no co-occurring events with cameras or notes, its clock offset is unknown. Since 41 seconds is within unaligned clock uncertainty, declaring a conflict would be a false conflict. Downgrading to `not_settled` strictly follows Rule 2 and Rule 3.
2. **Citation Indexing: WebVTT Cues vs. File Lines**: Per the specification (`"row": <row number or cue number>`), we cite WebVTT by 1-indexed cue number (e.g. Cue 1 for the 58 in a 45 observation) and verify quotes against the raw cue text. For CSV and JSON, `row` cites the 1-indexed file line.
3. **Modality & Tolerance-Based Comparisons**: Claims are matched against corresponding record modalities (camera claims against camera logs, officer statements against bodycam). Speeds require exact parity (`58 == 58`; `71` triggers a conflict). Zone limits must match (a 55 zone claim conflicts with a 45 zone record). Times use tolerance windows ($\pm 60$s for exact, $\pm 150$s for "around/about"); gaps $>10$ minutes trigger `conflict`, avoiding false matches on unseen test cases.

## What It Gets Wrong
- **Partial Claim Verification**: In Claim 1 (*"observed ... at about 21:14 ... travelling 58 mph"*), the speed matches bodycam audio (Cue 1), but the unaligned transcript lacks an absolute clock timestamp to verify `21:14`.
- **Compound Predicates**: Evaluates sentences as single assertions; does not output composite micro-findings if one clause matches and another is uncheckable.

## What Would Break First on Real Records
- **Scanned OCR Noise**: Police and medical PDFs contain broken layouts, OCR typos, and fragmented tables that disrupt regex parsing.
- **Continuous Clock Drift & Packet Jitter**: IoT dashcams and bodycams experience continuous drift and network buffering rather than static offsets, requiring dynamic time warping (DTW).
- **Overlapping Incidents**: CAD exports often contain multiple dispatches and units at similar timestamps across unrelated incidents.

## Tools Used & Verification Methodology
- **AI Coding Assistant**: Google Antigravity AI was used for architectural scaffolding, regex drafting, and test generation.
- **Direction & Verification**: Directed tool iterations, designed multi-probe edge case suites (testing false speeds, zone mismatches, and unaligned clock bounds), verified quote substring matches against cited source files, and validated 27 unit tests (`make test`).
