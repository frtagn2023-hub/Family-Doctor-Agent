"""Extraction contract tests use only synthetic text/files, never health records."""
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
TOOL = ROOT / "skills/pdf-text-extract/tool.py"
SPEC = importlib.util.spec_from_file_location("pdf_extract_tool", TOOL)
extract = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(extract)


class Page:
    def __init__(self, text):
        self.text = text

    def extract_text(self):
        if isinstance(self.text, Exception):
            raise self.text
        return self.text


class Reader:
    is_encrypted = False

    def __init__(self, texts):
        self.pages = [Page(text) for text in texts]


class ExtractionTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.source = Path(self.temp.name) / "synthetic.pdf"
        self.source.write_bytes(b"synthetic test bytes")
        self.options = vars(extract.parser().parse_args([str(self.source)]))

    def run_extract(self, texts, **options):
        return extract.extract_document(self.source, dict(self.options, **options),
                                        reader_factory=lambda _: Reader(texts))

    def cli(self, *args):
        return subprocess.run([sys.executable, str(TOOL), str(self.source), *args],
                              capture_output=True, text=True, timeout=15)

    def test_text_preserves_page_numbers_original_text_and_fingerprint(self):
        result = self.run_extract(["original 12.5 mg\n", "page two"])
        self.assertEqual(result["status"], "ok")
        self.assertEqual(result["pages"][0]["text"], "original 12.5 mg\n")
        self.assertEqual(result["pages"][1]["page_number"], 2)
        self.assertEqual(result["source"]["sha256"], hashlib.sha256(self.source.read_bytes()).hexdigest())
        self.assertEqual(result["source"]["path"], str(self.source.resolve()))

    def test_scan_and_mixed_pages_are_not_silently_successful(self):
        self.assertEqual(self.run_extract([""])["status"], "needs_ocr")
        result = self.run_extract(["first page", None])
        self.assertEqual(result["status"], "partial")
        self.assertEqual(result["pages"][1]["status"], "needs_ocr")

    def test_local_ocr_only_fills_missing_pages_and_keeps_provenance(self):
        calls = []

        def ocr(path, number, options):
            calls.append(number)
            return {"status": "ok", "text": "scanned text", "confidence": 70,
                    "words": [{"page_number": number, "text": "scanned"}], "pages": []}

        result = extract.extract_document(self.source, dict(self.options, ocr=True),
                                          reader_factory=lambda _: Reader(["digital", ""]), ocr_function=ocr)
        self.assertEqual(calls, [2])
        self.assertEqual(result["status"], "ok")
        self.assertEqual(result["pages"][1]["extraction_method"], "ocr")
        self.assertEqual(result["pages"][1]["ocr"]["words"][0]["page_number"], 2)

    def test_failed_and_empty_ocr_are_explicit(self):
        def fail(*_):
            raise RuntimeError("missing local model")

        result = extract.extract_document(self.source, dict(self.options, ocr=True),
                                          reader_factory=lambda _: Reader(["digital", ""]), ocr_function=fail)
        self.assertEqual(result["status"], "partial")
        self.assertIn("missing local model", result["pages"][1]["error"])
        result = extract.extract_document(self.source, dict(self.options, ocr=True),
                                          reader_factory=lambda _: Reader([""]),
                                          ocr_function=lambda *_: {"status": "empty", "text": ""})
        self.assertEqual(result["status"], "empty")

    def test_limits_fail_instead_of_truncating_successfully(self):
        self.assertEqual(self.run_extract(["one", "two"], max_pages=1)["status"], "error")
        result = self.run_extract(["oversized text"], max_chars=2)
        self.assertEqual(result["status"], "error")
        self.assertEqual(result["pages"][0]["text"], "")
        with self.assertRaisesRegex(ValueError, "exceeds"):
            self.run_extract(["small"], max_mb=0.000001)

    def test_bad_pdf_and_timeout_return_machine_readable_errors(self):
        process = self.cli("--json")
        self.assertNotEqual(process.returncode, 0)
        self.assertEqual(json.loads(process.stdout)["status"], "error")
        process = self.cli("--json", "--timeout", "0.000001")
        self.assertNotEqual(process.returncode, 0)
        self.assertIn("timed out", json.loads(process.stdout)["error"])

    def test_plain_error_never_creates_empty_output_or_overwrites_source(self):
        output = Path(self.temp.name) / "output.txt"
        process = self.cli("-o", str(output))
        self.assertNotEqual(process.returncode, 0)
        self.assertFalse(output.exists())
        before = self.source.read_bytes()
        process = self.cli("--json", "-o", str(self.source))
        self.assertNotEqual(process.returncode, 0)
        self.assertEqual(before, self.source.read_bytes())
        alias = Path(self.temp.name) / "same-input.pdf"
        os.link(self.source, alias)
        process = self.cli("--json", "-o", str(alias))
        self.assertNotEqual(process.returncode, 0)
        self.assertEqual(before, self.source.read_bytes())

    @unittest.skipUnless(importlib.util.find_spec("pypdf"), "pypdf optional dependency is not installed")
    def test_actual_pdf_text_blank_and_mixed_pages(self):
        from pypdf import PdfWriter
        from pypdf.generic import DictionaryObject, NameObject, DecodedStreamObject
        writer = PdfWriter()
        page = writer.add_blank_page(width=300, height=200)
        font = DictionaryObject({NameObject("/Type"): NameObject("/Font"),
                                 NameObject("/Subtype"): NameObject("/Type1"),
                                 NameObject("/BaseFont"): NameObject("/Helvetica")})
        page[NameObject("/Resources")] = DictionaryObject({NameObject("/Font"): DictionaryObject({NameObject("/F1"): font})})
        stream = DecodedStreamObject()
        stream.set_data(b"BT /F1 18 Tf 20 100 Td (SYNTHETIC 12.5) Tj ET")
        page[NameObject("/Contents")] = stream
        writer.write(self.source)
        process = self.cli("--json")
        self.assertEqual(process.returncode, 0, process.stderr)
        self.assertIn("SYNTHETIC 12.5", json.loads(process.stdout)["pages"][0]["text"])
        writer.add_blank_page(width=300, height=200)
        writer.write(self.source)
        process = self.cli("--json")
        self.assertNotEqual(process.returncode, 0)
        result = json.loads(process.stdout)
        self.assertEqual(result["status"], "partial")
        self.assertEqual(result["pages"][1]["page_number"], 2)
        self.assertEqual(result["pages"][1]["status"], "needs_ocr")


if __name__ == "__main__":
    unittest.main()
