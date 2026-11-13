"""Model transcription receipts use real synthetic image/PDF bytes, never family data."""
import copy
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
MANAGE = ROOT / "skills" / "family-doctor" / "scripts" / "manage_records.py"
AVAILABLE = importlib.util.find_spec("PIL") and importlib.util.find_spec("pypdf")


@unittest.skipUnless(AVAILABLE, "model vision file dependencies are optional")
class ModelVisionRecordTests(unittest.TestCase):
    def setUp(self):
        from PIL import Image, ImageDraw
        self.temp = tempfile.TemporaryDirectory(prefix="synthetic-vision-record-")
        self.addCleanup(self.temp.cleanup)
        self.base = Path(self.temp.name)
        self.workspace = self.base / "personal"
        self.health = self.workspace / "health-records"
        self.env = {key: value for key, value in os.environ.items()
                    if not key.startswith(("FAMILY_DOCTOR_", "HERMES_SESSION_"))}
        self.env.update(FAMILY_DOCTOR_HOME=str(self.workspace), PYTHONDONTWRITEBYTECODE="1")
        self.text = "Synthetic value 1.2 mg/L"
        first = Image.new("RGB", (400, 240), "white")
        ImageDraw.Draw(first).text((20, 50), self.text, fill="black")
        second = Image.new("RGB", (400, 240), "white")
        ImageDraw.Draw(second).text((20, 50), "Synthetic second page", fill="black")
        self.png = self.base / "synthetic.png"
        self.pdf = self.base / "synthetic-scanned.pdf"
        first.save(self.png)
        first.save(self.pdf, "PDF", save_all=True, append_images=[second])
        self.extraction_path = self.base / "extraction.json"
        self.fields_path = self.base / "fields.json"
        self.write(self.fields_path, [{"name": "合成指标", "value": 1.2, "unit": "mg/L",
            "observed_at": "2026-01-02", "source_page": 1, "original_text": self.text, "confidence": None}])

    @staticmethod
    def write(path, value):
        path.write_text(json.dumps(value, ensure_ascii=False), encoding="utf-8")

    def extraction(self, source=None, page_count=1):
        source = source or self.png
        return {"schema_version": 1, "tool": "family-doctor-model-vision",
            "source": {"path": str(source), "sha256": hashlib.sha256(source.read_bytes()).hexdigest(),
                       "size_bytes": source.stat().st_size},
            "page_count": page_count, "status": "ok", "extraction_method": "model_vision",
            "coverage_basis": "local_manifest", "warnings": ["合成转录，仍需对照原件核对"],
            "pages": [{"page_number": n, "text": self.text if n == 1 else "Synthetic second page",
                       "status": "ok", "extraction_method": "model_vision", "confidence": None,
                       "issues": [], "warnings": []} for n in range(1, page_count + 1)]}

    def run_cli(self, *args, ok=True, command=None):
        result = subprocess.run(command or [sys.executable, str(MANAGE), *map(str, args)],
                                env=self.env, text=True, capture_output=True, timeout=30)
        self.assertEqual(result.returncode == 0, ok, result.stdout + result.stderr)
        self.assertNotIn("Traceback", result.stderr)
        return json.loads(result.stdout) if ok else result

    def stage(self, extraction=None, source=None, ok=True, fields=True):
        self.write(self.extraction_path, extraction or self.extraction(source))
        args = ["stage", "--role", "self", "--file", source or self.png, "--extraction-json", self.extraction_path]
        if fields:
            args += ["--fields-json", self.fields_path]
        return self.run_cli(*args, ok=ok)

    def confirm(self, record, ok=True):
        return self.run_cli("confirm", record["id"], "--role", "self", "--reviewed", ok=ok)

    def test_real_png_and_scanned_pdf_stage_confirm_and_preserve_model_metadata(self):
        for source, count in ((self.png, 1), (self.pdf, 2)):
            with self.subTest(kind=source.suffix):
                original = source.read_bytes()
                pending = self.stage(self.extraction(source, count), source)
                self.assertEqual(pending["page_count"], count)
                self.assertEqual(pending["extraction_method"], "model_vision")
                self.assertEqual(pending["extraction_schema_version"], 1)
                self.assertEqual(pending["extraction_warnings"], ["合成转录，仍需对照原件核对"])
                confirmed = self.confirm(pending)
                self.assertEqual(confirmed["status"], "confirmed")
                self.assertEqual(source.read_bytes(), original)
                self.assertEqual((self.health / confirmed["raw_file"]).read_bytes(), original)
                self.assertTrue(all(page["confidence"] is None and page["issues"] == [] for page in confirmed["pages"]))
        records = json.loads((self.health / "user-profile.json").read_text())["records"]["检验结果"]
        self.assertEqual(len(records), 2)
        self.assertTrue(all(entry["extraction_method"] == "model_vision" for entry in records))
        self.assertTrue(all(entry["upload_sources"][0]["extraction_tool"] == "family-doctor-model-vision" for entry in records))

    def test_visual_adapter_cli_to_stage_review_confirm_uses_the_actual_shared_protocol(self):
        visual = ROOT / "skills" / "pdf-text-extract" / "visual.py"
        prepared = self.run_cli(command=[sys.executable, str(visual), "prepare", str(self.png),
                                         "--output-dir", str(self.base / "previews")])
        self.assertEqual(prepared["page_count"], 1)
        self.assertTrue(Path(prepared["previews"][0]).is_file())
        transcription = json.loads(Path(prepared["transcription"]).read_text())
        self.assertEqual(transcription["pages"][0]["status"], "partial")
        # Supply a synthetic result in place of a model; this test never calls one.
        transcription["pages"][0].update(text=self.text, status="ok", issues=[])
        filled = self.base / "synthetic-transcription.json"
        self.write(filled, transcription)
        output = self.base / "assembled.json"
        assembled = self.run_cli(command=[sys.executable, str(visual), "assemble", "--manifest", prepared["manifest"],
            "--transcription", str(filled), "--output", str(output)])
        self.assertEqual(assembled["status"], "ok")
        receipt = self.run_cli("stage", "--role", "self", "--file", self.png, "--extraction-json", output)
        self.assertEqual(receipt["fields"], [])
        self.assertEqual(receipt["pages"], json.loads(output.read_text())["pages"])
        reviewed = self.run_cli("review", receipt["id"], "--role", "self", "--fields-json", self.fields_path)
        self.assertIn("reviewed_at", reviewed)
        confirmed = self.confirm(reviewed)
        self.assertEqual(confirmed["status"], "confirmed")
        self.assertEqual(confirmed["extraction_method"], "model_vision")
        self.assertEqual((self.health / confirmed["raw_file"]).read_bytes(), self.png.read_bytes())

    def test_stage_rejects_missing_actual_pages_even_when_declared_complete(self):
        cases = []
        short = self.extraction(self.pdf, 1); cases.append(short)
        missing = self.extraction(self.pdf, 2); missing["pages"].pop(); cases.append(missing)
        skipped = self.extraction(self.pdf, 2); skipped["pages"][1]["page_number"] = 3; cases.append(skipped)
        duplicate = self.extraction(self.pdf, 2); duplicate["pages"][1]["page_number"] = 1; cases.append(duplicate)
        for extraction in cases:
            with self.subTest(pages=[page["page_number"] for page in extraction["pages"]]):
                self.stage(extraction, self.pdf, ok=False)
        self.assertFalse((self.health / "raw").exists())
        self.assertFalse((self.health / "imports").exists())

    def test_confirm_rechecks_actual_page_count_after_receipt_loses_last_page(self):
        pending = self.stage(self.extraction(self.pdf, 2), self.pdf)
        path = self.health / "imports" / (pending["id"] + ".json")
        edited = copy.deepcopy(pending)
        edited["pages"].pop(); edited["page_count"] = 1
        self.write(path, edited)
        before = path.read_bytes()
        failed = self.confirm(edited, ok=False)
        self.assertIn("原文件一致", failed.stderr)
        self.assertEqual(path.read_bytes(), before)
        self.assertFalse((self.health / "user-profile.json").exists())
        self.assertEqual(hashlib.sha256((self.health / pending["raw_file"]).read_bytes()).hexdigest(), pending["sha256"])

    def test_partial_empty_and_error_keep_issues_pending_and_cannot_confirm(self):
        for status in ("partial", "empty", "error"):
            with self.subTest(status=status):
                extraction = self.extraction()
                extraction["status"] = status
                extraction["pages"][0].update(status=status, text=self.text if status == "partial" else "",
                                               issues=["合成无法辨认区域"], warnings=["合成无法辨认区域"])
                pending = self.stage(extraction, fields=status == "partial")
                self.assertEqual(pending["pages"][0]["status"], status)
                self.assertEqual(pending["pages"][0]["issues"], ["合成无法辨认区域"])
                self.confirm(pending, ok=False)
                stored = json.loads((self.health / "imports" / (pending["id"] + ".json")).read_text())
                self.assertEqual(stored["status"], "pending")
        self.assertFalse((self.health / "user-profile.json").exists())

    def test_model_schema_and_page_quality_are_never_invented_or_upgraded(self):
        cases = []
        for key in ("schema_version", "tool", "extraction_method", "coverage_basis", "warnings", "page_count"):
            value = self.extraction(); value.pop(key); cases.append(("missing " + key, value))
        for key in ("status", "confidence", "issues", "warnings", "extraction_method"):
            value = self.extraction(); value["pages"][0].pop(key); cases.append(("page missing " + key, value))
        for key, wrong in (("confidence", 99), ("confidence", 0), ("issues", "not an array"),
                           ("issues", [3]), ("issues", ["仍有未解决问题"]), ("status", "unknown"), ("text", "  ")):
            value = self.extraction(); value["pages"][0][key] = wrong; cases.append((key, value))
        value = self.extraction(); value["pages"][0]["status"] = "partial"; cases.append(("false complete", value))
        value = self.extraction(); value["source"]["size_bytes"] += 1; cases.append(("source size", value))
        for name, extraction in cases:
            with self.subTest(name=name):
                self.stage(extraction, ok=False)
        self.assertFalse((self.health / "raw").exists())

    def test_old_pdf_text_and_ocr_remain_compatible_and_raw_dedup_ignores_extension(self):
        renamed = self.base / "earlier-name.txt"; renamed.write_bytes(self.png.read_bytes())
        legacy = {"source": {"sha256": hashlib.sha256(renamed.read_bytes()).hexdigest()}, "status": "ok", "page_count": 1,
                  "pages": [{"page_number": 1, "status": "ok", "text": self.text, "extraction_method": "pdf_text"}]}
        first = self.stage(legacy, renamed); self.confirm(first)
        ocr = copy.deepcopy(legacy); ocr["extraction_method"] = "ocr"
        ocr["pages"][0].update(extraction_method="ocr", confidence=87)
        self.confirm(self.stage(ocr))
        model = self.stage()
        self.assertEqual(model["raw_file"], first["raw_file"])
        self.assertTrue(model["raw_file"].endswith(".txt"))
        linked = self.confirm(model)
        self.assertEqual(linked["canonical_document_id"], first["id"])
        records = json.loads((self.health / "user-profile.json").read_text())["records"]["检验结果"]
        self.assertEqual(len(records), 1)
        self.assertEqual(len(records[0]["upload_sources"]), 3)
        self.assertNotIn("extraction_method", records[0], "a new model source must not relabel the legacy canonical document")
        self.assertNotIn("extraction_schema_version", records[0])
        self.assertEqual(records[0]["source_pages"][0]["extraction_method"], "pdf_text")
        self.assertEqual(records[0]["upload_sources"][-1]["extraction_method"], "model_vision")
        self.assertEqual(len(list((self.health / "raw").glob("*"))), 1)

    def test_model_field_confidence_cannot_masquerade_as_an_ocr_score(self):
        fields = json.loads(self.fields_path.read_text())
        fields[0]["confidence"] = 99
        self.write(self.fields_path, fields)
        result = self.stage(ok=False)
        self.assertIn("不能编造 OCR 置信度", result.stderr)
        self.assertFalse((self.health / "raw").exists())
        fields[0]["confidence"] = None
        self.write(self.fields_path, fields)
        self.assertEqual(self.confirm(self.stage())["status"], "confirmed")

    def test_multiframe_gif_is_accepted_only_for_verified_model_vision(self):
        from PIL import Image
        source = self.base / "synthetic-animation.gif"
        with Image.open(self.png) as first:
            second = first.copy(); second.putpixel((0, 0), (255, 0, 0))
            first.save(source, save_all=True, append_images=[second], duration=100, loop=0)
        extraction = self.extraction(source, 2)
        receipt = self.stage(extraction, source)
        self.assertEqual(self.confirm(receipt)["page_count"], 2)
        legacy = {"source": {"sha256": extraction["source"]["sha256"]}, "status": "ok", "page_count": 1,
                  "pages": [{"page_number": 1, "status": "ok", "text": self.text, "extraction_method": "ocr"}]}
        self.stage(legacy, source, ok=False)

    def test_family_proxy_vision_upload_stays_with_the_named_subject(self):
        household = self.base / "household"
        prefix = [sys.executable, str(ROOT / "family.py"), "--home", str(household)]
        self.run_cli(command=prefix + ["init", "--name", "合成视觉转录家庭"])
        father = self.run_cli(command=prefix + ["add-member", "--name", "合成父亲", "--profile", "father"])
        child = self.run_cli(command=prefix + ["add-member", "--name", "合成家属", "--profile", "child"])
        self.write(self.extraction_path, self.extraction(self.pdf, 2))
        route = prefix + ["--profile", "child", "--member", father["id"], "run", "manage_records", "--"]
        receipt = self.run_cli(command=route + ["stage", "--file", str(self.pdf), "--extraction-json", str(self.extraction_path), "--fields-json", str(self.fields_path)])
        confirmed = self.run_cli(command=route + ["confirm", receipt["id"], "--reviewed"])
        self.assertEqual(confirmed["subject_member_id"], father["id"])
        self.assertEqual(confirmed["reporter_member_id"], child["id"])
        self.assertEqual(confirmed["report_mode"], "proxy")
        target = household / "members" / father["id"] / "health-records"
        self.assertTrue((target / confirmed["raw_file"]).is_file())
        other = household / "members" / child["id"] / "health-records"
        self.assertFalse((other / "imports").exists())
        self.assertFalse((other / "raw").exists())


if __name__ == "__main__":
    unittest.main()
