"""Tests for multi-format record loaders and encoding resilience."""
import os
import unittest
from plearun.record_loader import RecordLoader, detect_and_read_file

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

if __name__ == "__main__":
    unittest.main()
