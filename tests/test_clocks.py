"""Tests for universal timestamp parsing and Rule 3 clock alignment."""
import unittest
from datetime import datetime, timezone, timedelta
from plearun.clock_sync import parse_flexible_timestamp, ClockSynchronizer
from plearun.models import RecordRow

class TestClockSync(unittest.TestCase):

    def test_epoch_milliseconds_parsing(self):
        # 1772502579000 ms
        dt = parse_flexible_timestamp(1772502579000)
        self.assertIsNotNone(dt)
        self.assertEqual(dt.tzinfo, timezone.utc)
        self.assertEqual(dt.year, 2026)

    def test_iso_with_timezone_parsing(self):
        dt = parse_flexible_timestamp("2026-03-02T20:51:06-05:00")
        self.assertIsNotNone(dt)
        self.assertEqual(dt.tzinfo, timezone.utc)

    def test_iso_without_timezone_parsing(self):
        dt = parse_flexible_timestamp("2026-03-02 21:10:19")
        self.assertIsNotNone(dt)
        # EST -5 offset to UTC
        self.assertEqual(dt.hour, 2)  # 21 + 5 = 26 -> 02:10:19 UTC next day

    def test_time_only_parsing(self):
        dt = parse_flexible_timestamp("21:15")
        self.assertIsNotNone(dt)
        self.assertEqual(dt.minute, 15)

    def test_rule_3_unaligned_device_handling(self):
        sync = ClockSynchronizer()
        # Device without co-occurring anchor events
        row_orphan = RecordRow(
            file_name="mystery_cam.csv",
            row_idx=1,
            raw_text="...",
            timestamp=datetime(2026, 3, 2, 21, 0, 0, tzinfo=timezone.utc),
            device="CAM-UNKNOWN",
            speed_mph=50
        )
        sync.align_records([row_orphan])
        # Device has no anchor stop event, so must remain UNALIGNED (is_aligned returns False)
        self.assertFalse(sync.is_aligned("CAM-UNKNOWN"))
        self.assertNotIn("CAM-UNKNOWN", sync.device_offsets)
        aligned_ts = sync.get_aligned_timestamp(row_orphan)
        self.assertEqual(aligned_ts, row_orphan.timestamp)

    def test_dynamic_clock_drift_estimation_with_synthetic_shifts(self):
        """Test that ClockSynchronizer dynamically computes offsets from co-occurring events."""
        sync = ClockSynchronizer(max_allowed_drift_sec=300.0, min_anchors_required=2)

        ref_notes = RecordRow(
            file_name="notes.csv",
            row_idx=1,
            raw_text="Stopped at lot",
            timestamp=datetime(2026, 3, 2, 21, 16, 0, tzinfo=timezone.utc),
            note_content="Stopped at lot ~21:16"
        )
        # Synthetic camera with clock running 120s slow (records stop at 21:14:00)
        cam_row1 = RecordRow(
            file_name="cam_synth.csv",
            row_idx=10,
            raw_text="2026-03-02T21:14:00Z,CAM-SHIFT,0,45",
            timestamp=datetime(2026, 3, 2, 21, 14, 0, tzinfo=timezone.utc),
            device="CAM-SHIFT",
            speed_mph=0
        )
        cam_row2 = RecordRow(
            file_name="cam_synth.csv",
            row_idx=11,
            raw_text="2026-03-02T21:14:00Z,CAM-SHIFT,0,45",
            timestamp=datetime(2026, 3, 2, 21, 14, 0, tzinfo=timezone.utc),
            device="CAM-SHIFT",
            speed_mph=0
        )

        sync.align_records([ref_notes, cam_row1, cam_row2])
        self.assertTrue(sync.is_aligned("CAM-SHIFT"))
        self.assertAlmostEqual(sync.device_offsets["CAM-SHIFT"], 120.0, delta=0.1)

        aligned = sync.get_aligned_timestamp(cam_row1)
        self.assertEqual(aligned, datetime(2026, 3, 2, 21, 16, 0, tzinfo=timezone.utc))

    def test_clock_drift_negative_75s_shift(self):
        """Test shifted camera times running 75s fast (offset -75s)."""
        sync = ClockSynchronizer(max_allowed_drift_sec=300.0, min_anchors_required=2)

        ref_notes = RecordRow(
            file_name="notes.csv",
            row_idx=1,
            raw_text="Stopped at lot",
            timestamp=datetime(2026, 3, 2, 21, 16, 0, tzinfo=timezone.utc),
            note_content="Stopped at lot ~21:16"
        )
        # Camera running 75s fast records stop at 21:17:15
        cam_row1 = RecordRow(
            file_name="cam_fast.csv",
            row_idx=1,
            raw_text="2026-03-02T21:17:15Z,CAM-FAST,0,45",
            timestamp=datetime(2026, 3, 2, 21, 17, 15, tzinfo=timezone.utc),
            device="CAM-FAST",
            speed_mph=0
        )
        cam_row2 = RecordRow(
            file_name="cam_fast.csv",
            row_idx=2,
            raw_text="2026-03-02T21:17:15Z,CAM-FAST,0,45",
            timestamp=datetime(2026, 3, 2, 21, 17, 15, tzinfo=timezone.utc),
            device="CAM-FAST",
            speed_mph=0
        )

        sync.align_records([ref_notes, cam_row1, cam_row2])
        self.assertTrue(sync.is_aligned("CAM-FAST"))
        self.assertAlmostEqual(sync.device_offsets["CAM-FAST"], -75.0, delta=0.1)
        aligned = sync.get_aligned_timestamp(cam_row1)
        self.assertEqual(aligned, datetime(2026, 3, 2, 21, 16, 0, tzinfo=timezone.utc))

    def test_disagreeing_anchors_stay_unaligned(self):
        """Two anchors that disagree by more than a few seconds must keep the device unaligned."""
        sync = ClockSynchronizer(max_allowed_drift_sec=300.0, min_anchors_required=2)

        ref_notes = RecordRow(
            file_name="notes.csv",
            row_idx=1,
            raw_text="Stopped at lot",
            timestamp=datetime(2026, 3, 2, 21, 16, 0, tzinfo=timezone.utc),
            note_content="Stopped at lot ~21:16"
        )
        # One stop at 21:14:00 (+120s offset), second stop at 21:15:30 (+30s offset). Spread is 90s!
        cam_err1 = RecordRow(
            file_name="cam_err.csv",
            row_idx=1,
            raw_text="2026-03-02T21:14:00Z,CAM-ERR,0,45",
            timestamp=datetime(2026, 3, 2, 21, 14, 0, tzinfo=timezone.utc),
            device="CAM-ERR",
            speed_mph=0
        )
        cam_err2 = RecordRow(
            file_name="cam_err.csv",
            row_idx=2,
            raw_text="2026-03-02T21:15:30Z,CAM-ERR,0,45",
            timestamp=datetime(2026, 3, 2, 21, 15, 30, tzinfo=timezone.utc),
            device="CAM-ERR",
            speed_mph=0
        )

        sync.align_records([ref_notes, cam_err1, cam_err2])
        self.assertFalse(sync.is_aligned("CAM-ERR"))
        self.assertIn("CAM-ERR", sync.unaligned_devices)

    def test_single_anchor_device_stays_unaligned(self):
        """A device with only a single anchor cannot be verified and must stay unaligned."""
        sync = ClockSynchronizer(max_allowed_drift_sec=300.0, min_anchors_required=2)

        ref_notes = RecordRow(
            file_name="notes.csv",
            row_idx=1,
            raw_text="Stopped at lot",
            timestamp=datetime(2026, 3, 2, 21, 16, 0, tzinfo=timezone.utc),
            note_content="Stopped at lot ~21:16"
        )
        cam_single = RecordRow(
            file_name="cam_single.csv",
            row_idx=1,
            raw_text="2026-03-02T21:15:00Z,CAM-SINGLE,0,45",
            timestamp=datetime(2026, 3, 2, 21, 15, 0, tzinfo=timezone.utc),
            device="CAM-SINGLE",
            speed_mph=0
        )

        sync.align_records([ref_notes, cam_single])
        self.assertFalse(sync.is_aligned("CAM-SINGLE"))
        self.assertIn("CAM-SINGLE", sync.unaligned_devices)

if __name__ == "__main__":
    unittest.main()
