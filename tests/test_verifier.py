"""Tests for verification engine, Rule 1 exact quote validation, and schema conformance."""
import os
import unittest
from plearun.record_loader import RecordLoader
from plearun.claim_extractor import ClaimExtractor
from plearun.verifier import ReportVerifier
from plearun.clock_sync import ClockSynchronizer
from plearun.models import Finding, ExtractedClaim

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
RECORDS_DIR = os.path.join(ROOT, "records")
REPORT_PATH = os.path.join(ROOT, "reports/report_fictional_02.txt")

class TestVerifier(unittest.TestCase):

    def setUp(self):
        self.loader = RecordLoader()
        self.loader.load_directory(RECORDS_DIR)
        self.sync = ClockSynchronizer()
        self.sync.align_records(self.loader.all_rows)
        self.verifier = ReportVerifier(self.loader, self.sync)

    def test_sample_claims_verified_accurately(self):
        extractor = ClaimExtractor()
        claims = extractor.extract_from_file(REPORT_PATH)
        self.assertEqual(len(claims), 5)

        findings = self.verifier.verify_all(claims)
        self.assertEqual(len(findings), 5)

        # Claim 1: Observation/speed -> match (from bodycam cue 1)
        self.assertEqual(findings[0].finding, "match")
        self.assertIsNotNone(findings[0].cite)
        self.assertEqual(findings[0].cite["file"], "bodycam_transcript.vtt")
        self.assertEqual(findings[0].cite["row"], 1)  # Cue 1
        self.assertTrue(self.loader.verify_substring(
            findings[0].cite["file"], findings[0].cite["row"], findings[0].quote
        ))

        # Claim 2: Stopped at lot around 21:16 -> match (from notes N-3)
        self.assertEqual(findings[1].finding, "match")
        self.assertIsNotNone(findings[1].cite)
        self.assertEqual(findings[1].cite["file"], "notes_cp1252.csv")
        self.assertEqual(findings[1].cite["row"], 4)
        self.assertTrue(self.loader.verify_substring(
            findings[1].cite["file"], findings[1].cite["row"], findings[1].quote
        ))

        # Claim 3: 911 call logged at 21:11 -> not_settled (41s gap against unaligned CAD clock)
        self.assertEqual(findings[2].finding, "not_settled")
        self.assertIsNone(findings[2].cite)
        self.assertIsNone(findings[2].quote)

        # Claim 4: Unit on scene roughly 2 min after stop -> not_settled (79s gap against unaligned CAD clock)
        self.assertEqual(findings[3].finding, "not_settled")
        self.assertIsNone(findings[3].cite)
        self.assertIsNone(findings[3].quote)

        # Claim 5: Only car in lane -> not_checkable
        self.assertEqual(findings[4].finding, "not_checkable")
        self.assertIsNone(findings[4].cite)
        self.assertIsNone(findings[4].quote)

    def test_false_claims_never_match(self):
        """Hidden cases test: Obviously false claims must return conflict or not_settled, never match."""
        extractor = ClaimExtractor()

        # False speed: 71 in a 45
        c1 = extractor.extract_from_text("Officer Lee observed the blue sedan travelling 71 mph in a 45.")[0]
        f1 = self.verifier.verify_claim(c1)
        self.assertNotEqual(f1.finding, "match", "71 mph must not match 58 mph records!")
        self.assertEqual(f1.finding, "conflict")

        # False stop time: stopped around 21:40
        c2 = extractor.extract_from_text("The driver pulled into the Harbor Gas lot and stopped around 21:40.")[0]
        f2 = self.verifier.verify_claim(c2)
        self.assertNotEqual(f2.finding, "match", "21:40 must not match 21:16 stop records!")
        self.assertEqual(f2.finding, "conflict")

        # False 911 call time: 21:40
        c3 = extractor.extract_from_text("A 911 call reporting the incident was logged at 21:40.")[0]
        f3 = self.verifier.verify_claim(c3)
        self.assertNotEqual(f3.finding, "match", "21:40 call must not match 21:10 call records!")
        self.assertEqual(f3.finding, "conflict")

        # False delta: roughly 10 minutes after stop
        c4 = extractor.extract_from_text("Dispatch records show a unit on scene roughly 10 minutes after the stop.")[0]
        f4 = self.verifier.verify_claim(c4)
        self.assertNotEqual(f4.finding, "match", "10 minutes must not match ~1.3 minute actual delta!")
        self.assertEqual(f4.finding, "conflict")

    def test_rule_1_downgrades_to_not_settled_on_quote_mismatch(self):
        fake_finding = Finding(
            claim="Officer Lee observed the vehicle.",
            finding="match",
            cite={"file": "bodycam_transcript.vtt", "row": 1},
            quote="THIS_STRING_DOES_NOT_EXIST_IN_CUE_1"
        )
        finalized = self.verifier._finalize_and_enforce_rules(fake_finding)
        self.assertEqual(finalized.finding, "not_settled")
        self.assertIsNone(finalized.cite)
        self.assertIsNone(finalized.quote)

    def test_rule_1_downgrades_on_invalid_row_or_file(self):
        fake_finding = Finding(
            claim="Officer Lee observed the vehicle.",
            finding="conflict",
            cite={"file": "nonexistent.csv", "row": 10},
            quote="something"
        )
        finalized = self.verifier._finalize_and_enforce_rules(fake_finding)
        self.assertEqual(finalized.finding, "not_settled")
        self.assertIsNone(finalized.cite)
        self.assertIsNone(finalized.quote)

if __name__ == "__main__":
    unittest.main()
