# Plearun Verification Engine

## How It Works
`plearun_cli.py` is a deterministic, offline Python CLI (`python3 plearun_cli.py REPORT RECORDS_DIR > findings.json`):
1. **Resilient Ingestion (`record_loader.py`)**: Auto-detects encodings (UTF-16 LE/BE, UTF-8-BOM, UTF-8, CP1252), strips comment markers (`#PLEARUN`), parses CSV/JSON/WebVTT, skips malformed rows safely, and caches raw cues/lines.
2. **Clock Alignment (`clock_sync.py`)**: Normalizes ISO-8601, epoch ms, and local times. Dynamically aligns stop anchors (`speed == 0`). Enforces Rule 3: unanchored devices (CAD) remain unaligned (±90s uncertainty).
3. **Claim Extraction (`claim_extractor.py`)**: Safely splits sentences across titles/abbreviations (Lt., Mr., a.m.) and extracts speeds, posted zones, timestamps, and deltas.
4. **Verification (`verifier.py`)**: Evaluates claims via numerical tolerances and modality routing. Enforces Rule 1 (exact quote substring check) and Rule 2 (avoids false conflicts/matches).

## Three Hardest Decisions
1. **Classifying Claim 3 as `not_settled` (Rules 2 & 3)**: CAD logs the 911 call at 21:10:19. Claim '21:10' matches because 21:10:19 is inside that minute (19s diff <= 30s tolerance). Claim '21:11' is 41s away. With no shared anchors, CAD has ±90s uncertainty; calling conflict on a 41s gap would be a penalized false conflict, making `not_settled` the compliant choice.
2. **Untimed Speed Claims Return `not_settled`**: 'The vehicle was travelling 67 mph' has no time or officer context. Because highway speeds vary over time, matching a transient camera row risks a false match. We require tying to a specific moment (a timestamp or officer observation) to match or conflict; otherwise it returns `not_settled`.
3. **Citation Indexing (WebVTT Cues vs File Lines)**: WebVTT cites 1-indexed cue numbers (Cue 1 for speed) per spec (`'row': <row or cue number>`), while CSV/JSON cite 1-indexed file lines. Quotes are validated against raw cue text.

## What It Gets Wrong
- **Untimed Claims**: Sourceless untimed speeds return `not_settled` even if the vehicle reached that speed at some point on the highway.
- **Compound Claims**: Claim 1 contains speed and time; the tool evaluates the assertion as a whole rather than emitting separate micro-findings.
- **CAD Unaligned**: CAD clock drift cannot be calibrated without shared anchors, keeping timing uncertainty wide (±90s).
- **Regex Extraction**: Unconventional narrative syntax or typos can evade pattern extractors.

## What Would Break First on Real Records
- **Scanned OCR Noise**: Layout shifts, table breaks, and OCR typos in police PDFs break regex extractors.
- **Continuous Clock Drift & Jitter**: Dashcams drift non-linearly over time, requiring dynamic time warping (DTW).
- **Multi-Incident CAD Logs**: CAD exports mix dispatches across multiple simultaneous incidents.

## Tools Used & Verification Methodology
- **AI Assistant**: Google Antigravity was used for architectural scaffolding, regex drafting, and test generation.
- **Human Engineering & Verification**: Directed tool iterations, designed domain verification rules, audited logic, verified crash handling, and validated all 42 offline unit tests (`make test`).
