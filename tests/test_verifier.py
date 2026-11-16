"""Tests for verification engine, Rule 1 exact quote validation, and schema conformance."""
import os
import unittest
from plearun.record_loader import RecordLoader
from plearun.claim_extractor import ClaimExtractor
from plearun.verifier import ReportVerifier
from plearun.models import Finding, ExtractedClaim

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
RECORDS_DIR = os.path.join(ROOT, "records")
REPORT_PATH = os.path.join(ROOT, "reports/report_fictional_02.txt")

class TestVerifier(unittest.TestCase):

    def setUp(self):
        self.loader = RecordLoader()
        self.loader.load_directory(RECORDS_DIR)
        self.verifier = ReportVerifier(self.loader)

    def test_sample_claims_verified_accurately(self):
        extractor = ClaimExtractor()
        claims = extractor.extract_from_file(REPORT_PATH)
        self.assertEqual(len(claims), 5)

        findings = self.verifier.verify_all(claims)
        self.assertEqual(len(findings), 5)

        # Claim 1: Observation/speed -> match
        self.assertEqual(findings[0].finding, "match")
        self.assertIsNotNone(findings[0].cite)
        self.assertIsNotNone(findings[0].quote)
        self.assertTrue(self.loader.verify_substring(
            findings[0].cite["file"], findings[0].cite["row"], findings[0].quote
        ))

        # Claim 2: Stopped at lot -> match
        self.assertEqual(findings[1].finding, "match")
        self.assertIsNotNone(findings[1].cite)
        self.assertTrue(self.loader.verify_substring(
            findings[1].cite["file"], findings[1].cite["row"], findings[1].quote
        ))

        # Claim 3: 911 call logged -> conflict (CAD officially logged 21:10:19, not 21:11)
        self.assertEqual(findings[2].finding, "conflict")
        self.assertIsNotNone(findings[2].cite)
        self.assertEqual(findings[2].cite["file"], "dispatch.json")
        self.assertTrue(self.loader.verify_substring(
            findings[2].cite["file"], findings[2].cite["row"], findings[2].quote
        ))

        # Claim 4: Unit on scene roughly 2 min after stop -> match
        self.assertEqual(findings[3].finding, "match")
        self.assertIsNotNone(findings[3].cite)
        self.assertTrue(self.loader.verify_substring(
            findings[3].cite["file"], findings[3].cite["row"], findings[3].quote
        ))

        # Claim 5: Only car in lane -> not_checkable
        self.assertEqual(findings[4].finding, "not_checkable")
        self.assertIsNone(findings[4].cite)
        self.assertIsNone(findings[4].quote)

    def test_rule_1_downgrades_to_not_settled_on_quote_mismatch(self):
        # Create an artificial finding with an invalid quote
        fake_finding = Finding(
            claim="Officer Lee observed the vehicle.",
            finding="match",
            cite={"file": "bodycam_transcript.vtt", "row": 4},
            quote="THIS_STRING_DOES_NOT_EXIST_IN_ROW_4"
        )
        finalized = self.verifier._finalize_and_enforce_rules(fake_finding)
        # MUST downgrade to not_settled
        self.assertEqual(finalized.finding, "not_settled")
        # MUST omit cite and quote
        self.assertIsNone(finalized.cite)
        self.assertIsNone(finalized.quote)
        d = finalized.to_dict()
        self.assertNotIn("cite", d)
        self.assertNotIn("quote", d)

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
