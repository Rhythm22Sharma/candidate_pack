"""End-to-end integration test for plearun_cli.py executable."""
import os
import sys
import json
import subprocess
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CLI_PATH = os.path.join(ROOT, "plearun_cli.py")
REPORT_PATH = os.path.join(ROOT, "reports/report_fictional_02.txt")
RECORDS_DIR = os.path.join(ROOT, "records")

class TestCLIEndToEnd(unittest.TestCase):

    def test_cli_execution_and_schema_validation(self):
        proc = subprocess.run(
            [sys.executable, CLI_PATH, REPORT_PATH, RECORDS_DIR],
            capture_output=True,
            text=True,
            cwd=ROOT
        )
        self.assertEqual(proc.returncode, 0, f"CLI failed with stderr: {proc.stderr}")

        data = json.loads(proc.stdout)
        self.assertIn("findings", data)
        findings = data["findings"]
        self.assertEqual(len(findings), 5)

        valid_findings = {"match", "conflict", "not_settled", "not_checkable"}
        for f in findings:
            self.assertIn("claim", f)
            self.assertIn("finding", f)
            self.assertIn(f["finding"], valid_findings)

            if f["finding"] in ("match", "conflict"):
                self.assertIn("cite", f)
                self.assertIn("file", f["cite"])
                self.assertIn("row", f["cite"])
                self.assertIn("quote", f)

                # Check exact quote substring against cited file/cue
                target_file = os.path.join(RECORDS_DIR, f["cite"]["file"])
                self.assertTrue(os.path.exists(target_file))
                with open(target_file, "r", encoding="utf-8-sig", errors="replace") as rf:
                    content = rf.read()

                # Quote must exist in file
                self.assertIn(f["quote"], content)
            else:
                # Rule: cite and quote MUST be omitted for not_settled and not_checkable
                self.assertNotIn("cite", f)
                self.assertNotIn("quote", f)

    def test_cli_missing_args_fails(self):
        proc = subprocess.run(
            [sys.executable, CLI_PATH],
            capture_output=True,
            text=True,
            cwd=ROOT
        )
        self.assertNotEqual(proc.returncode, 0)

    def test_cli_empty_report_exits_zero_with_valid_json(self):
        import tempfile
        with tempfile.NamedTemporaryFile("w", delete=False) as tf:
            tf.write("")
            tf_path = tf.name
        try:
            proc = subprocess.run(
                [sys.executable, CLI_PATH, tf_path, RECORDS_DIR],
                capture_output=True,
                text=True,
                cwd=ROOT
            )
            self.assertEqual(proc.returncode, 0)
            data = json.loads(proc.stdout)
            self.assertEqual(data, {"findings": []})
        finally:
            os.remove(tf_path)

    def test_cli_empty_records_dir_exits_zero(self):
        import tempfile
        with tempfile.TemporaryDirectory() as empty_dir:
            proc = subprocess.run(
                [sys.executable, CLI_PATH, REPORT_PATH, empty_dir],
                capture_output=True,
                text=True,
                cwd=ROOT
            )
            self.assertEqual(proc.returncode, 0)
            data = json.loads(proc.stdout)
            self.assertIn("findings", data)

    def test_cli_malformed_json_and_bad_csv_does_not_crash(self):
        import tempfile
        with tempfile.TemporaryDirectory() as tmp_records:
            # Create a broken JSON and a corrupt CSV line
            with open(os.path.join(tmp_records, "broken.json"), "w") as f:
                f.write("{ invalid json content !!!")
            with open(os.path.join(tmp_records, "bad.csv"), "w") as f:
                f.write("a,b\n\"unclosed quote\n1,2,3,4,5\n")

            proc = subprocess.run(
                [sys.executable, CLI_PATH, REPORT_PATH, tmp_records],
                capture_output=True,
                text=True,
                cwd=ROOT
            )
            self.assertEqual(proc.returncode, 0)
            data = json.loads(proc.stdout)
            self.assertIn("findings", data)

    def test_cli_missing_report_file_exits_nonzero(self):
        proc = subprocess.run(
            [sys.executable, CLI_PATH, "non_existent_report_file_xyz.txt", RECORDS_DIR],
            capture_output=True,
            text=True,
            cwd=ROOT
        )
        self.assertNotEqual(proc.returncode, 0)

if __name__ == "__main__":
    unittest.main()
