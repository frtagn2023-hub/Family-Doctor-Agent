"""Optional real renderer smoke test, using only an isolated synthetic profile."""
import importlib.util
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "skills/family-doctor/scripts/generate_health_report.py"


class RealPDFTests(unittest.TestCase):
    def test_real_pdf_export_contains_synthetic_report(self):
        if importlib.util.find_spec("pypdf") is None:
            self.skipTest("optional pypdf not installed")
        probe = subprocess.run([sys.executable, "-c", "import weasyprint"],
                               capture_output=True, text=True, timeout=30)
        if probe.returncode:
            self.skipTest("optional WeasyPrint or system rendering libraries unavailable")
        from pypdf import PdfReader
        with tempfile.TemporaryDirectory() as directory:
            home = Path(directory)
            health = home / "health-records"
            health.mkdir()
            profile = {"version": "3.0", "owner": {"name": "SYNTHETIC QA", "allergies": [],
                       "factStatus": {"allergies": "unknown"}}, "consultations": [], "records": {}}
            (health / "user-profile.json").write_text(json.dumps(profile), encoding="utf-8")
            output = home / "report.pdf"
            environment = dict(os.environ, FAMILY_DOCTOR_HOME=directory, FAMILY_DOCTOR_TIMEZONE="Asia/Shanghai")
            result = subprocess.run([sys.executable, str(SCRIPT), "--role", "self", "--pdf-only",
                                     "--output", str(output)], env=environment,
                                    capture_output=True, text=True, timeout=60)
            self.assertEqual(result.returncode, 0, result.stderr)
            reader = PdfReader(output)
            self.assertGreaterEqual(len(reader.pages), 1)
            text = "\n".join(page.extract_text() or "" for page in reader.pages)
            self.assertIn("SYNTHETIC QA", text)
            self.assertFalse((home / "report.md").exists())


if __name__ == "__main__":
    unittest.main()
