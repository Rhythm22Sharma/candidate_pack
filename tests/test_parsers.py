"""Tests for multi-format record loaders and encoding resilience."""
import os
import unittest
import tempfile
import shutil
from plearun.record_loader import RecordLoader, detect_and_read_file
from plearun.claim_extractor import ClaimExtractor

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
RECORDS_DIR = os.path.join(ROOT, "records")

class TestParsers(unittest.TestCase):

    def setUp(self):
        self.loader = RecordLoader()

    def test_load_all_records(self):
        rows = self.loader.load_directory(RECORDS_DIR)
        self.assertGreater(len(rows), 0)
        self.assertIn("camera_log.csv", self.loader.files)
        self.assertIn("notes_cp1252.csv", self.loader.files)
        self.assertIn("dispatch.json", self.loader.files)
        self.assertIn("bodycam_transcript.vtt", self.loader.files)

    def test_camera_log_skips_comment_marker_and_keeps_line_numbers(self):
        loaded = self.loader.load_file(os.path.join(RECORDS_DIR, "camera_log.csv"))
        self.assertTrue(loaded.raw_lines[0].startswith("#PLEARUN"))
        self.assertEqual(loaded.rows[0].row_idx, 3)
        self.assertEqual(loaded.rows[0].raw_text, loaded.raw_lines[2])

    def test_notes_cp1252_decodes_cleanly(self):
        loaded = self.loader.load_file(os.path.join(RECORDS_DIR, "notes_cp1252.csv"))
        self.assertEqual(loaded.encoding, "cp1252")
        self.assertEqual(len(loaded.rows), 3)
        self.assertEqual(loaded.rows[2].row_idx, 4)
        self.assertIn("Stopped at lot", loaded.rows[2].note_content)

    def test_dispatch_json_maps_to_exact_file_lines(self):
        loaded = self.loader.load_file(os.path.join(RECORDS_DIR, "dispatch.json"))
        row_911 = next(r for r in loaded.rows if r.event_type == "911_call")
        self.assertEqual(row_911.row_idx, 9)
        self.assertIn("2026-03-02 21:10:19", loaded.raw_lines[row_911.row_idx - 1])

    def test_vtt_cites_by_cue_number_per_specification(self):
        loaded = self.loader.load_file(os.path.join(RECORDS_DIR, "bodycam_transcript.vtt"))
        self.assertEqual(len(loaded.rows), 3)
        # Cue 1 has row_idx == 1
        self.assertEqual(loaded.rows[0].row_idx, 1)
        self.assertEqual(loaded.rows[0].cue_idx, 1)
        self.assertIn("58 in a 45", loaded.rows[0].raw_text)

    def test_verify_substring_enforces_exactness(self):
        self.loader.load_directory(RECORDS_DIR)
        # Exact quote in VTT Cue 1
        self.assertTrue(self.loader.verify_substring("bodycam_transcript.vtt", 1, "58 in a 45"))
        # Non-matching quote fails
        self.assertFalse(self.loader.verify_substring("bodycam_transcript.vtt", 1, "99 in a 45"))
        # Exact quote in CSV line 4
        self.assertTrue(self.loader.verify_substring("notes_cp1252.csv", 4, "Stopped at lot ~21:16"))
        # Out-of-bounds row fails
        self.assertFalse(self.loader.verify_substring("bodycam_transcript.vtt", 9999, "58 in a 45"))
        # Non-existent file fails
        self.assertFalse(self.loader.verify_substring("ghost.csv", 1, "anything"))

    def test_multiformat_report_encoding_parity(self):
        """Test that reports in UTF-8, UTF-8-BOM, UTF-16-LE, UTF-16-BE produce identical claims."""
        extractor = ClaimExtractor()
        sample_text = (
            "Officer Lee observed the vehicle at about 21:14 travelling 58 mph in a posted 45 zone.\n"
            "The driver pulled into the Harbor Gas lot and stopped around 21:16."
        )

        tmp_dir = tempfile.mkdtemp()
        try:
            encodings = {
                "utf8_plain.txt": sample_text.encode("utf-8"),
                "utf8_bom.txt": sample_text.encode("utf-8-sig"),
                "utf16_le_bom.txt": sample_text.encode("utf-16"),
                "utf16_be_bom.txt": sample_text.encode("utf-16-be"),
                "utf16_le_nobom.txt": sample_text.encode("utf-16-le"),
            }

            results = {}
            for fname, byte_data in encodings.items():
                fpath = os.path.join(tmp_dir, fname)
                with open(fpath, "wb") as f:
                    f.write(byte_data)
                claims = extractor.extract_from_file(fpath)
                results[fname] = claims

            baseline = results["utf8_plain.txt"]
            self.assertEqual(len(baseline), 2)

            for fname, claims in results.items():
                self.assertEqual(len(claims), len(baseline), f"Claim count mismatch for {fname}")
                for i in range(len(baseline)):
                    self.assertEqual(claims[i].raw_claim, baseline[i].raw_claim, f"Claim text mismatch for {fname}")
                    self.assertEqual(claims[i].stated_speed, baseline[i].stated_speed, f"Speed mismatch for {fname}")
                    self.assertEqual(claims[i].stated_zone, baseline[i].stated_zone, f"Zone mismatch for {fname}")
                    self.assertEqual(claims[i].stated_time, baseline[i].stated_time, f"Time mismatch for {fname}")
        finally:
            shutil.rmtree(tmp_dir)

    def test_utf16_record_and_crlf_line_endings(self):
        """Test loading a record file encoded in UTF-16 with Windows CRLF endings."""
        tmp_dir = tempfile.mkdtemp()
        try:
            csv_path = os.path.join(tmp_dir, "records_utf16_crlf.csv")
            content = "timestamp,device,speed_mph,zone_mph\r\n1772504000000,CAM-01,58,45\r\n1772504010000,CAM-01,60,45\r\n"
            with open(csv_path, "wb") as f:
                f.write(content.encode("utf-16"))

            loader = RecordLoader()
            loaded = loader.load_file(csv_path)
            self.assertEqual(len(loaded.rows), 2)
            self.assertEqual(loaded.rows[0].speed_mph, 58)
            self.assertEqual(loaded.rows[0].row_idx, 2)
            # Verify exact quote matching works on UTF-16 CRLF record
            self.assertTrue(loader.verify_substring("records_utf16_crlf.csv", 2, "58,45"))
        finally:
            shutil.rmtree(tmp_dir)

    def test_rule_1_exact_raw_preservation_accents_and_case(self):
        """Rule 1: quote must match exact raw row preserving case, spacing, and accents."""
        tmp_dir = tempfile.mkdtemp()
        try:
            csv_path = os.path.join(tmp_dir, "accents_cp1252.csv")
            # Write CP1252 text with French/Spanish accents
            raw_text = "ID,Author,Note,Time\nN-99,Ofc. Café,\"Arrêté près du café\",21:16\n"
            with open(csv_path, "wb") as f:
                f.write(raw_text.encode("cp1252"))

            loader = RecordLoader()
            loader.load_file(csv_path)

            # Exact quote matches
            self.assertTrue(loader.verify_substring("accents_cp1252.csv", 2, "Arrêté près du café"))
            # Normalized or lowercase fails
            self.assertFalse(loader.verify_substring("accents_cp1252.csv", 2, "arrete pres du cafe"))
            self.assertFalse(loader.verify_substring("accents_cp1252.csv", 2, "ARRÊTÉ"))
        finally:
            shutil.rmtree(tmp_dir)

    def test_vtt_overlapping_cues_cites_correct_cue_number(self):
        """Test VTT cue indexing with overlapping time intervals."""
        tmp_dir = tempfile.mkdtemp()
        try:
            vtt_path = os.path.join(tmp_dir, "overlap.vtt")
            vtt_text = (
                "WEBVTT\n\n"
                "00:00:01.000 --> 00:00:05.000\n"
                "Officer: First statement\n\n"
                "00:00:03.000 --> 00:00:07.000\n"
                "Dispatch: Overlapping response\n\n"
                "00:00:06.000 --> 00:00:10.000\n"
                "Officer: Third observation\n\n"
            )
            with open(vtt_path, "w", encoding="utf-8") as f:
                f.write(vtt_text)

            loader = RecordLoader()
            loaded = loader.load_file(vtt_path)
            self.assertEqual(len(loaded.rows), 3)

            # Cue 1
            self.assertEqual(loaded.rows[0].row_idx, 1)
            self.assertTrue(loader.verify_substring("overlap.vtt", 1, "First statement"))
            # Cue 2
            self.assertEqual(loaded.rows[1].row_idx, 2)
            self.assertTrue(loader.verify_substring("overlap.vtt", 2, "Overlapping response"))
            # Cue 3
            self.assertEqual(loaded.rows[2].row_idx, 3)
            self.assertTrue(loader.verify_substring("overlap.vtt", 3, "Third observation"))
        finally:
            shutil.rmtree(tmp_dir)

if __name__ == "__main__":
    unittest.main()
