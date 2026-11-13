"""Only synthetic images/PDFs: current-model transcription, never a model API."""
from concurrent.futures import ThreadPoolExecutor
import copy
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "skills/pdf-text-extract/visual.py"
SPEC = importlib.util.spec_from_file_location("visual_extraction", SCRIPT)
visual = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(visual)
HAS_PILLOW = importlib.util.find_spec("PIL") is not None
HAS_PYPDF = importlib.util.find_spec("pypdf") is not None


@unittest.skipUnless(HAS_PILLOW, "optional Pillow is not installed")
class VisualExtractionTests(unittest.TestCase):
    def setUp(self):
        from PIL import Image
        self.temp = tempfile.TemporaryDirectory(prefix="synthetic-visual-extraction-")
        self.addCleanup(self.temp.cleanup)
        self.home = Path(self.temp.name)
        self.source = self.home / "synthetic.png"
        Image.new("RGB", (80, 40), "white").save(self.source)
        self.previews = self.home / "private-previews"
        self.output = self.home / "extraction.json"
        self.transcription = self.home / "transcription.json"

    def write_json(self, path, value):
        path.write_text(json.dumps(value, ensure_ascii=False), encoding="utf-8")

    def transcript(self, count=1):
        return {"schema_version": 1, "pages": [{"page_number": index, "text": "合成测试转录第 {} 页".format(index),
                "status": "ok", "issues": []} for index in range(1, count + 1)]}

    def prepared(self, source=None):
        return visual.prepare(source or self.source, self.previews)

    def assemble(self, transcript=None, manifest=None, output=None):
        self.write_json(self.transcription, transcript if transcript is not None else self.transcript())
        return visual.assemble(manifest or self.previews / "manifest.json", self.transcription, output or self.output)

    def multipage_tiff(self):
        from PIL import Image
        path = self.home / "synthetic-multiframe.tiff"
        frames = [Image.new("RGB", (80 + index, 40), color) for index, color in enumerate(("white", "blue", "green"))]
        frames[0].save(path, save_all=True, append_images=frames[1:])
        for frame in frames:
            frame.close()
        return path

    def pdf(self, pages=2):
        from pypdf import PdfWriter
        from pypdf.generic import DictionaryObject, NameObject, DecodedStreamObject
        path = self.home / "synthetic-report.pdf"
        writer = PdfWriter()
        for index in range(pages):
            page = writer.add_blank_page(width=120, height=80)
            font = DictionaryObject({NameObject("/Type"): NameObject("/Font"), NameObject("/Subtype"): NameObject("/Type1"),
                                     NameObject("/BaseFont"): NameObject("/Helvetica")})
            page[NameObject("/Resources")] = DictionaryObject({NameObject("/Font"): DictionaryObject({NameObject("/F1"): font})})
            stream = DecodedStreamObject()
            stream.set_data("BT /F1 9 Tf 10 40 Td (SYNTHETIC PAGE {}) Tj ET".format(index + 1).encode())
            page[NameObject("/Contents")] = writer._add_object(stream)
        writer.write(path)
        return path

    def test_prepare_real_hash_private_full_previews_and_incomplete_template(self):
        original = self.source.read_bytes()
        result = self.prepared()
        self.assertEqual(result["page_count"], 1)
        manifest = json.loads(Path(result["manifest"]).read_text())
        self.assertEqual(manifest["source"]["sha256"], hashlib.sha256(original).hexdigest())
        self.assertEqual(manifest["source"]["size_bytes"], len(original))
        self.assertEqual(manifest["kind"], "image")
        self.assertEqual(manifest["pages"][0]["width"], 80)
        self.assertTrue(Path(result["previews"][0]).is_file())
        self.assertEqual(self.source.read_bytes(), original)
        template = json.loads(Path(result["transcription"]).read_text())
        self.assertEqual(template["pages"][0]["status"], "partial")
        assembled = visual.assemble(result["manifest"], result["transcription"], self.output)
        self.assertFalse(assembled["ok"])
        if os.name != "nt":
            self.assertEqual(self.previews.stat().st_mode & 0o777, 0o700)
            for path in [*self.previews.iterdir(), self.output]:
                self.assertEqual(path.stat().st_mode & 0o777, 0o600)

    def test_all_multiframe_images_are_counted_and_previewed(self):
        source = self.multipage_tiff()
        result = self.prepared(source)
        self.assertEqual(result["page_count"], 3)
        self.assertEqual(len(result["previews"]), 3)
        self.assertEqual(len({Path(path).read_bytes() for path in result["previews"]}), 3)
        self.assemble(self.transcript(3))
        result = json.loads(self.output.read_text())
        self.assertEqual(result["page_count"], 3)
        self.assertEqual([page["page_number"] for page in result["pages"]], [1, 2, 3])

    def test_transparent_black_background_is_white_and_black_content_is_visible(self):
        from PIL import Image, ImageDraw
        for mode in ("RGBA", "P"):
            with self.subTest(mode=mode):
                if mode == "RGBA":
                    source = Image.new("RGBA", (80, 40), (0, 0, 0, 0))
                    ImageDraw.Draw(source).rectangle((10, 10, 20, 20), fill=(0, 0, 0, 255))
                else:
                    source = Image.new("P", (80, 40), 0)
                    source.putpalette([0, 0, 0, 0, 0, 0] + [0] * (256 * 3 - 6))
                    source.info["transparency"] = 0
                    ImageDraw.Draw(source).rectangle((10, 10, 20, 20), fill=1)
                source.save(self.source)
                source.close()
                result = visual.prepare(self.source, self.home / ("previews-" + mode))
                with Image.open(result["previews"][0]) as preview:
                    self.assertEqual(preview.mode, "RGB")
                    self.assertEqual(preview.getpixel((0, 0)), (255, 255, 255))
                    self.assertEqual(preview.getpixel((15, 15)), (0, 0, 0))

    def test_exif_orientation_is_applied_to_preview_without_changing_original(self):
        from PIL import Image
        source = Image.new("RGB", (80, 40), "white")
        orientation = Image.Exif()
        orientation[274] = 6
        source.save(self.source, exif=orientation)
        source.close()
        original = self.source.read_bytes()
        result = self.prepared()
        with Image.open(result["previews"][0]) as preview:
            self.assertEqual(preview.size, (40, 80))
        self.assertEqual(self.source.read_bytes(), original)

    @unittest.skipUnless(HAS_PYPDF, "optional pypdf is not installed")
    def test_encrypted_pdf_is_explicitly_rejected_before_preview_creation(self):
        from pypdf import PdfWriter
        source = self.home / "encrypted.pdf"
        writer = PdfWriter()
        writer.add_blank_page(width=100, height=100)
        writer.encrypt("synthetic-test-password")
        writer.write(source)
        with self.assertRaisesRegex(ValueError, "加密"):
            self.prepared(source)
        self.assertFalse(self.previews.exists())

    @unittest.skipUnless(HAS_PYPDF and shutil.which("pdftoppm"), "optional PDF reader/renderer unavailable")
    def test_actual_pdf_every_page_is_rendered_without_ocr(self):
        source = self.pdf()
        result = self.prepared(source)
        self.assertEqual(result["page_count"], 2)
        self.assertEqual(len(result["previews"]), 2)
        self.assertNotEqual(Path(result["previews"][0]).read_bytes(), Path(result["previews"][1]).read_bytes())
        self.assertEqual(visual.inspect_source(source)["kind"], "pdf")
        self.assemble(self.transcript(2))
        self.assertEqual(json.loads(self.output.read_text())["status"], "ok")

    def test_manifest_cannot_hide_real_later_frames_or_reduce_page_count(self):
        self.prepared(self.multipage_tiff())
        manifest_path = self.previews / "manifest.json"
        original = json.loads(manifest_path.read_text())
        for count in (1, True):
            modified = copy.deepcopy(original)
            modified["page_count"] = count
            modified["pages"] = modified["pages"][:1]
            self.write_json(manifest_path, modified)
            with self.assertRaises(ValueError):
                self.assemble(self.transcript(1))
            self.assertFalse(self.output.exists())
        modified = copy.deepcopy(original)
        modified["pages"] = modified["pages"][:1]
        self.write_json(manifest_path, modified)
        with self.assertRaises(ValueError):
            self.assemble(self.transcript(3))

    @unittest.skipUnless(HAS_PYPDF, "optional pypdf is not installed")
    def test_pdf_manifest_cannot_hide_real_later_pages(self):
        source = self.pdf()
        # Synthetic rendering isolates count validation from Poppler availability.
        def render(path, target, number, deadline):
            shutil.copyfile(self.source, target)
            os.chmod(target, 0o600)
        with patch.object(visual, "_render_pdf", side_effect=render):
            self.prepared(source)
        manifest_path = self.previews / "manifest.json"
        manifest = json.loads(manifest_path.read_text())
        manifest["page_count"] = 1
        manifest["pages"] = manifest["pages"][:1]
        self.write_json(manifest_path, manifest)
        with self.assertRaises(ValueError):
            self.assemble(self.transcript())
        self.assertFalse(self.output.exists())

    def test_transcription_missing_duplicate_or_out_of_range_page_fails(self):
        self.prepared(self.multipage_tiff())
        variants = [self.transcript(2), self.transcript(4)]
        duplicate = self.transcript(3)
        duplicate["pages"][2]["page_number"] = 2
        variants.append(duplicate)
        boolean = self.transcript(3)
        boolean["pages"][0]["page_number"] = True
        variants.append(boolean)
        for value in variants:
            with self.assertRaises(ValueError):
                self.assemble(value)
            self.assertFalse(self.output.exists())

    def test_model_output_cannot_inject_source_confidence_or_custom_fields(self):
        self.prepared()
        variants = []
        value = self.transcript()
        value["source"] = {"sha256": "f" * 64}
        variants.append(value)
        value = self.transcript()
        value["schema_version"] = True
        variants.append(value)
        for key, field in (("confidence", 99), ("extraction_method", "ocr"), ("summary", "invented")):
            value = self.transcript()
            value["pages"][0][key] = field
            variants.append(value)
        for value in variants:
            with self.assertRaises(ValueError):
                self.assemble(value)
            self.assertFalse(self.output.exists())

    def test_assembly_preserves_issues_and_partial_can_never_become_ok(self):
        self.prepared(self.multipage_tiff())
        value = self.transcript(3)
        value["pages"][1].update(status="partial", issues=["右侧表格无法辨认"])
        result = self.assemble(value)
        self.assertFalse(result["ok"])
        extracted = json.loads(self.output.read_text())
        self.assertEqual(extracted["status"], "partial")
        self.assertEqual(extracted["coverage_basis"], "local_manifest")
        self.assertEqual(extracted["extraction_method"], "model_vision")
        self.assertEqual(extracted["pages"][1]["issues"], ["右侧表格无法辨认"])
        self.assertEqual(extracted["pages"][1]["warnings"], ["右侧表格无法辨认"])
        self.assertTrue(all(page["confidence"] is None for page in extracted["pages"]))
        self.assertTrue(all(page["extraction_method"] == "model_vision" for page in extracted["pages"]))

    def test_empty_error_and_partial_page_statuses_remain_incomplete(self):
        self.prepared()
        for status in ("empty", "error", "partial"):
            transcript = self.transcript()
            transcript["pages"][0].update(status=status, text="", issues=["合成未识别页面"])
            result = self.assemble(transcript, output=self.home / (status + ".json"))
            self.assertFalse(result["ok"])
            self.assertEqual(result["status"], status)

    def test_ok_requires_nonempty_text_and_zero_issues_and_empty_means_no_text(self):
        self.prepared()
        for changes in ({"text": "  "}, {"issues": ["尚未看清"]}, {"status": "empty"}, {"status": "UNKNOWN"},
                        {"issues": "not-a-list"}, {"issues": [False]}):
            transcript = self.transcript()
            transcript["pages"][0].update(changes)
            with self.assertRaises(ValueError):
                self.assemble(transcript)
            self.assertFalse(self.output.exists())

    def test_source_or_preview_changed_after_prepare_is_rejected(self):
        result = self.prepared()
        original = self.source.read_bytes()
        self.source.write_bytes(original + b"changed")
        with self.assertRaises(ValueError):
            self.assemble()
        self.source.write_bytes(original)
        preview = Path(result["previews"][0])
        preview.write_bytes(preview.read_bytes() + b"changed")
        with self.assertRaises(ValueError):
            self.assemble()
        self.assertFalse(self.output.exists())

    def test_preview_missing_symlink_or_manifest_traversal_is_rejected(self):
        result = self.prepared()
        preview = Path(result["previews"][0])
        original = preview.read_bytes()
        preview.unlink()
        with self.assertRaises(ValueError):
            self.assemble()
        preview.symlink_to(self.source)
        with self.assertRaises(ValueError):
            self.assemble()
        preview.unlink()
        preview.write_bytes(original)
        manifest_path = self.previews / "manifest.json"
        manifest = json.loads(manifest_path.read_text())
        manifest["pages"][0]["preview_file"] = "../synthetic.png"
        self.write_json(manifest_path, manifest)
        with self.assertRaises(ValueError):
            self.assemble()

    def test_existing_outputs_original_and_directories_are_never_overwritten(self):
        self.prepared()
        before = self.source.read_bytes()
        with self.assertRaises(ValueError):
            visual.prepare(self.source, self.previews)
        with self.assertRaises(ValueError):
            self.assemble(output=self.source)
        self.assertEqual(self.source.read_bytes(), before)
        self.output.write_text("original output")
        with self.assertRaises(ValueError):
            self.assemble()
        self.assertEqual(self.output.read_text(), "original output")

    @unittest.skipUnless(HAS_PYPDF, "optional pypdf is not installed")
    def test_failed_second_render_cleans_only_new_private_directory(self):
        source = self.pdf()
        before = source.read_bytes()
        unrelated = self.home / "untouched.txt"
        unrelated.write_text("keep")
        def render(path, target, number, deadline):
            if number == 2:
                raise ValueError("synthetic timeout")
            shutil.copyfile(self.source, target)
        with patch.object(visual, "_render_pdf", side_effect=render), self.assertRaises(ValueError):
            self.prepared(source)
        self.assertFalse(self.previews.exists())
        self.assertEqual(source.read_bytes(), before)
        self.assertEqual(unrelated.read_text(), "keep")

    @unittest.skipUnless(HAS_PYPDF, "optional pypdf is not installed")
    def test_subprocess_timeout_fails_cleanly_without_partial_previews(self):
        source = self.pdf()
        with patch.object(visual.shutil, "which", return_value="pdftoppm"), \
             patch.object(visual.subprocess, "run", side_effect=subprocess.TimeoutExpired("synthetic", 1)), \
             self.assertRaises(ValueError):
            self.prepared(source)
        self.assertFalse(self.previews.exists())

    def test_source_limits_unknown_dicom_and_content_identification(self):
        with self.assertRaises(ValueError):
            visual.inspect_source(self.source, max_bytes=1)
        with self.assertRaises(ValueError):
            visual.inspect_source(self.multipage_tiff(), max_pages=2)
        for name, data in (("unknown.bin", b"not an image"), ("scan.dcm", b"\0" * 128 + b"DICM" + b"unsupported")):
            path = self.home / name
            path.write_bytes(data)
            with self.assertRaisesRegex(ValueError, "DICOM"):
                visual.inspect_source(path)
        renamed = self.home / "same-image.txt"
        renamed.write_bytes(self.source.read_bytes())
        self.assertEqual(visual.inspect_source(renamed)["kind"], "image")

    def test_character_limit_duplicate_json_keys_and_nonfinite_values(self):
        self.prepared()
        transcript = self.transcript()
        transcript["pages"][0]["text"] = "X" * 21
        with patch.object(visual, "MAX_CHARS", 20), self.assertRaises(ValueError):
            self.assemble(transcript)
        for text in ('{"schema_version":1,"schema_version":1,"pages":[]}', '{"schema_version":NaN,"pages":[]}'):
            self.transcription.write_text(text)
            with self.assertRaises(ValueError):
                visual.assemble(self.previews / "manifest.json", self.transcription, self.output)
        self.assertFalse(self.output.exists())

    def test_cli_partial_writes_result_with_exit_two_and_success_uses_zero(self):
        prepared = subprocess.run([sys.executable, str(SCRIPT), "prepare", str(self.source), "--output-dir", str(self.previews)],
                                  capture_output=True, text=True, timeout=30)
        self.assertEqual(prepared.returncode, 0, prepared.stdout + prepared.stderr)
        paths = json.loads(prepared.stdout)
        partial = subprocess.run([sys.executable, str(SCRIPT), "assemble", "--manifest", paths["manifest"],
                                  "--transcription", paths["transcription"], "--output", str(self.output)],
                                 capture_output=True, text=True, timeout=30)
        self.assertEqual(partial.returncode, 2, partial.stdout + partial.stderr)
        self.assertEqual(json.loads(partial.stdout)["status"], "partial")
        self.write_json(self.transcription, self.transcript())
        complete = subprocess.run([sys.executable, str(SCRIPT), "assemble", "--manifest", paths["manifest"],
                                   "--transcription", str(self.transcription), "--output", str(self.home / "complete.json")],
                                  capture_output=True, text=True, timeout=30)
        self.assertEqual(complete.returncode, 0, complete.stdout + complete.stderr)

    def test_concurrent_assembly_cannot_overwrite_the_other_output(self):
        self.prepared()
        self.write_json(self.transcription, self.transcript())
        def attempt(_):
            try:
                return visual.assemble(self.previews / "manifest.json", self.transcription, self.output)
            except (ValueError, OSError):
                return None
        with ThreadPoolExecutor(max_workers=2) as pool:
            outcomes = list(pool.map(attempt, range(2)))
        self.assertEqual(sum(value is not None for value in outcomes), 1)
        self.assertEqual(json.loads(self.output.read_text())["status"], "ok")
        self.assertEqual(list(self.home.glob(".visual-*")), [])


if __name__ == "__main__":
    unittest.main()
