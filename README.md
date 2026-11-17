# Plearun Verification Engine

## How It Works
`plearun_cli.py` is a deterministic, offline Python CLI (`python3 plearun_cli.py REPORT RECORDS_DIR > findings.json`):
1. **Resilient Ingestion (`record_loader.py`)**: Auto-detects encodings across reports and records (UTF-16 LE/BE, UTF-8 BOM, CP1252, UTF-8), skips comment markers (`#PLEARUN`), parses CSV/JSON/WebVTT, neutralizes prompt injections (`security.py`), and caches raw cues/lines.
2. **Clock Alignment (`clock_sync.py`)**: Normalizes ISO-8601, epoch ms, and local times. Dynamically aligns stop anchors (`speed == 0`). Enforces Rule 3: unanchored devices (CAD) remain unaligned (±90s uncertainty).
3. **Claim Extraction (`claim_extractor.py`)**: Extracts speeds, zones, timestamps, relative deltas, and claimed sources (camera vs bodycam).
4. **Verification (`verifier.py`)**: Evaluates claims via numerical tolerances and modality routing. Enforces Rule 1 (exact quote substring check) and Rule 2 (avoids false conflicts/matches).

## Three Hardest Decisions
1. **Classifying Claim 3 as `not_settled` (Rules 2 & 3)**: CAD logs the 911 call at 21:10:19. Claim '21:10' matches because 21:10:19 falls within that minute (19s diff <= 30s tolerance). Claim '21:11' is 41s away. With no shared anchors, CAD has ±90s uncertainty; calling conflict on a 41s gap would be a false conflict, making `not_settled` the only compliant choice.
2. **Citation Indexing (WebVTT Cues vs File Lines)**: WebVTT cites 1-indexed cue numbers (Cue 1 for speed) per spec (`'row': <row or cue number>`), while CSV/JSON cite 1-indexed file lines. Quotes are validated against raw cue text.
3. **Modality & Bounded Tolerances**: Camera claims and generic speeds route to camera logs (e.g. '67 mph' matches camera row 4), while officer speech checks bodycam transcripts. Speeds require exact parity; zone limits must match (a 55-zone claim conflicts with a 45-zone record). Times use bounded windows (30s for minutes, ±150s for 'about/around'); gaps >10 minutes trigger `conflict`.

## What It Gets Wrong
- **Partial Claim Verification (Claim 1)**: Speed and zone match Cue 1 verbatim. We defend `match` because the core violation is confirmed and the time is uncontradicted, but strictly the bodycam lacks an absolute clock to verify 21:14.
- **Untimed Speed Claims**: A sourceless speed claim ('The vehicle was travelling 67 mph') matches candidate camera reading row 4; this is a known limitation, as the matching row may record a reading from a different moment along the route without temporal confirmation.

## What Would Break First on Real Records
- **Scanned OCR Noise**: Layout shifts, table breaks, and OCR typos in police PDFs break regex extractors.
- **Continuous Clock Drift & Jitter**: Dashcams drift non-linearly over time, requiring dynamic time warping (DTW).
- **Multi-Incident CAD Logs**: CAD exports mix dispatches across multiple simultaneous incidents.

## Tools Used & Verification Methodology
- **AI Coding Assistant**: Google Antigravity AI was used for scaffolding, regex drafting, and test generation.
- **Direction & Verification**: Directed tool iterations, audited verifier logic against 18 changed test probes (false speeds, zones, drift bounds), and validated 31 unit tests (`make test`).
