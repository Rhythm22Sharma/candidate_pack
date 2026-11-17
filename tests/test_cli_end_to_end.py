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

if __name__ == "__main__":
    unittest.main()
