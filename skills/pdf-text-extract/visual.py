#!/usr/bin/env python3
"""Prepare local previews and validate the current model's visual transcription.

No model API or OCR is called. File/page provenance is checked locally; neither
the manifest nor status=ok proves that a model viewed or understood every region.
Rendering has per-page/total budgets; in-process parsers have no hard timeout or
security isolation.
"""
import argparse
import hashlib
import io
import json
import os
from pathlib import Path
import shutil
import struct
import subprocess
import sys
import time
import warnings

MAX_BYTES = 50 * 1024 * 1024
MAX_PAGES = 50
MAX_CHARS = 2_000_000
MAX_JSON_BYTES = 16 * 1024 * 1024
MAX_FRAME_PIXELS = 50_000_000
MAX_PREVIEW_BYTES = 50 * 1024 * 1024
PREVIEW_EDGE = 3500
RENDER_TIMEOUT = 60
TOTAL_TIMEOUT = 180
IMAGE_FORMATS = {"PNG", "JPEG", "TIFF", "WEBP", "BMP", "GIF"}
NOTICE = "模型转录尚需对照原件核对；本地清单只核对文件和页数，不证明视觉覆盖完整或医学准确。"


def _keys(value, expected):
    if not isinstance(value, dict) or set(value) != set(expected):
        raise ValueError("JSON 字段缺失或包含不支持的字段")


def _positive_int(value, label):
    if type(value) is not int or value <= 0:
        raise ValueError(label + "必须为正整数")
    return value


def _sha(data):
    return hashlib.sha256(data).hexdigest()


def _read_limited(path, maximum):
    path = Path(path).expanduser()
    if not path.is_file() or path.is_symlink():
        raise ValueError("输入必须为存在的普通本地文件，不能是符号链接")
    with path.open("rb") as handle:
        data = handle.read(maximum + 1)
    if not data or len(data) > maximum:
        raise ValueError("输入为空或超过大小限制")
    return data


def _pillow():
    try:
        from PIL import Image, ImageOps
    except ImportError as exc:
        raise ValueError("图片预览需要可选依赖 Pillow；请先安装文件处理依赖") from exc
    return Image, ImageOps


def _pdf_reader(data):
    try:
        from pypdf import PdfReader
    except ImportError:
        try:
            from PyPDF2 import PdfReader
        except ImportError as exc:
            raise ValueError("PDF 页数校验需要可选依赖 pypdf") from exc
    try:
        reader = PdfReader(io.BytesIO(data))
        if reader.is_encrypted:
            raise ValueError("不支持加密 PDF，请先提供可读取的本地副本")
        return reader
    except Exception as exc:
        if isinstance(exc, ValueError) and "加密" in str(exc):
            raise
        raise ValueError("PDF 结构不可读取，不能猜测页数或生成完整标记") from exc


def _check_dimensions(width, height):
    if type(width) is not int or type(height) is not int or min(width, height) <= 0 or width * height > MAX_FRAME_PIXELS:
        raise ValueError("图片帧尺寸无效或超过像素限制")


def inspect_source(path, max_bytes=MAX_BYTES, max_pages=MAX_PAGES):
    """Read actual bytes and actual PDF pages/image frames; never run a model.

    Returned source has only path, sha256 and size_bytes. Callers can compare
    page_count against an extraction rather than trusting a supplied manifest.
    """
    _positive_int(max_bytes, "文件上限")
    _positive_int(max_pages, "页数上限")
    requested = Path(path).expanduser()
    data = _read_limited(requested, max_bytes)
    path = requested.resolve(strict=True)
    # The archival store deduplicates bytes across renamed uploads, so a saved
    # blob may retain an earlier extension. Identify the actual encoding.
    if b"%PDF-" in data[:1024]:
        reader = _pdf_reader(data)
        try:
            count = len(reader.pages)
        except Exception as exc:
            raise ValueError("PDF 页数不可读取") from exc
        kind = "pdf"
    elif (data.startswith((b"\x89PNG\r\n\x1a\n", b"\xff\xd8\xff", b"II*\x00", b"MM\x00*", b"II+\x00", b"MM\x00+",
                            b"GIF87a", b"GIF89a", b"BM")) or (data[:4] == b"RIFF" and data[8:12] == b"WEBP")):
        Image, _ = _pillow()
        try:
            with warnings.catch_warnings():
                warnings.simplefilter("error", Image.DecompressionBombWarning)
                with Image.open(io.BytesIO(data)) as image:
                    if image.format not in IMAGE_FORMATS:
                        raise ValueError("不支持该图片编码格式")
                    count = getattr(image, "n_frames", 1)
                    if type(count) is not int or not 1 <= count <= max_pages:
                        raise ValueError("图片帧数为空或超过页数限制")
                    for index in range(count):
                        image.seek(index)
                        _check_dimensions(*image.size)
        except Exception as exc:
            raise ValueError("图片或多帧结构不可读取、格式不支持或超过限制") from exc
        kind = "image"
    else:
        raise ValueError("仅支持 PDF 和常见图片；DICOM、未知格式不支持此视觉转录流程")
    if type(count) is not int or not 1 <= count <= max_pages:
        raise ValueError("原文件页数为空或超过页数限制")
    return {"source": {"path": str(path), "sha256": _sha(data), "size_bytes": len(data)},
            "page_count": count, "kind": kind}


def _read_json(path, maximum=MAX_JSON_BYTES):
    def pairs(items):
        result = {}
        for key, value in items:
            if key in result:
                raise ValueError("JSON 不允许重复字段")
            result[key] = value
        return result

    def constant(_):
        raise ValueError("JSON 不允许非有限数值")

    try:
        return json.loads(_read_limited(path, maximum).decode("utf-8"), object_pairs_hook=pairs, parse_constant=constant)
    except (UnicodeError, json.JSONDecodeError) as exc:
        raise ValueError("JSON 编码或结构无效；原始内容未输出") from exc


def _new_json(path, value):
    """Publish a private file atomically, without replacing any existing name."""
    import tempfile
    path = Path(path).expanduser()
    if path.exists() or path.is_symlink() or not path.parent.is_dir():
        raise ValueError("输出必须为现有目录中的新文件，不能覆盖原件或已有结果")
    text = (json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False) + "\n").encode("utf-8")
    descriptor, temporary = tempfile.mkstemp(prefix=".visual-", dir=path.parent)
    try:
        with os.fdopen(descriptor, "wb") as handle:
            handle.write(text)
            handle.flush()
            os.fsync(handle.fileno())
        os.chmod(temporary, 0o600)
        # Unlike replace/rename, link fails if another process creates output.
        os.link(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def _remaining(deadline):
    remaining = deadline - time.monotonic()
    if remaining <= 0:
        raise ValueError("逐页预览超过总处理时间限制")
    return min(RENDER_TIMEOUT, remaining)


def _png_info(path):
    data = _read_limited(path, MAX_PREVIEW_BYTES)
    if len(data) < 33 or data[:8] != b"\x89PNG\r\n\x1a\n" or data[12:16] != b"IHDR":
        raise ValueError("预览不是有效 PNG")
    width, height = struct.unpack(">II", data[16:24])
    _check_dimensions(width, height)
    if max(width, height) > PREVIEW_EDGE:
        raise ValueError("预览尺寸超过限制")
    return {"sha256": _sha(data), "width": width, "height": height}


def _render_pdf(path, output, page_number, deadline):
    executable = shutil.which("pdftoppm")
    if not executable:
        raise ValueError("PDF 预览需要 Poppler 的 pdftoppm；渲染预览不会调用 OCR")
    base = output.with_suffix("")
    try:
        subprocess.run([executable, "-f", str(page_number), "-l", str(page_number), "-singlefile", "-png",
                        "-scale-to", str(PREVIEW_EDGE), str(path), str(base)],
                       check=True, capture_output=True, timeout=_remaining(deadline))
    except (subprocess.SubprocessError, OSError) as exc:
        raise ValueError("PDF 页面渲染失败或超时，未发布不完整预览") from exc
    os.chmod(output, 0o600)


def prepare(input_path, output_dir):
    """Create all previews, manifest and a deliberately non-confirmable template."""
    deadline = time.monotonic() + TOTAL_TIMEOUT
    source = inspect_source(input_path)
    output = Path(output_dir).expanduser()
    if output.exists() or output.is_symlink() or not output.parent.is_dir():
        raise ValueError("预览目录必须尚不存在，且父目录必须存在")
    output = output.parent.resolve() / output.name
    created = False
    try:
        output.mkdir(mode=0o700)
        created = True
        os.chmod(output, 0o700)
        previews = []
        if source["kind"] == "pdf":
            for number in range(1, source["page_count"] + 1):
                target = output / ("page-{:04d}.png".format(number))
                _render_pdf(source["source"]["path"], target, number, deadline)
                previews.append({"page_number": number, "preview_file": target.name, **_png_info(target)})
        else:
            Image, ImageOps = _pillow()
            data = _read_limited(source["source"]["path"], MAX_BYTES)
            with Image.open(io.BytesIO(data)) as image:
                for number in range(1, source["page_count"] + 1):
                    _remaining(deadline)
                    image.seek(number - 1)
                    _check_dimensions(*image.size)
                    oriented = ImageOps.exif_transpose(image.copy())
                    try:
                        if "A" in oriented.getbands() or "transparency" in oriented.info:
                            rgba = oriented.convert("RGBA")
                            alpha = rgba.getchannel("A")
                            try:
                                frame = Image.new("RGB", rgba.size, "white")
                                frame.paste(rgba, mask=alpha)
                            finally:
                                alpha.close()
                                rgba.close()
                        else:
                            frame = oriented.convert("RGB")
                    finally:
                        oriented.close()
                    try:
                        frame.thumbnail((PREVIEW_EDGE, PREVIEW_EDGE), Image.Resampling.LANCZOS)
                        target = output / ("page-{:04d}.png".format(number))
                        frame.save(target, format="PNG")
                        os.chmod(target, 0o600)
                        previews.append({"page_number": number, "preview_file": target.name, **_png_info(target)})
                    finally:
                        frame.close()
        _remaining(deadline)
        if inspect_source(source["source"]["path"]) != source:
            raise ValueError("原文件在准备过程中发生变化，请重新准备")
        manifest = {"schema_version": 1, "tool": "family-doctor-visual-prepare", **source, "pages": previews}
        template = {"schema_version": 1, "pages": [{"page_number": number, "text": "", "status": "partial",
                     "issues": ["尚未由当前模型逐页核对"]} for number in range(1, source["page_count"] + 1)]}
        _new_json(output / "manifest.json", manifest)
        _new_json(output / "transcription-template.json", template)
        return {"ok": True, "manifest": str(output / "manifest.json"), "transcription": str(output / "transcription-template.json"),
                "page_count": source["page_count"], "previews": [str(output / page["preview_file"]) for page in previews]}
    except BaseException:
        if created and output.is_dir() and not output.is_symlink():
            shutil.rmtree(output)
        raise


def _validated_manifest(path):
    path = Path(path).expanduser()
    manifest = _read_json(path, 512 * 1024)
    _keys(manifest, ("schema_version", "tool", "source", "page_count", "kind", "pages"))
    if type(manifest["schema_version"]) is not int or manifest["schema_version"] != 1 or manifest["tool"] != "family-doctor-visual-prepare":
        raise ValueError("本地清单版本或来源无效")
    _keys(manifest["source"], ("path", "sha256", "size_bytes"))
    if not isinstance(manifest["source"]["path"], str) or not Path(manifest["source"]["path"]).is_absolute():
        raise ValueError("本地清单必须包含真实原文件路径")
    actual = inspect_source(manifest["source"]["path"])
    if type(manifest["page_count"]) is not int or type(manifest["source"]["size_bytes"]) is not int or any(manifest[key] != actual[key] for key in ("source", "page_count", "kind")):
        raise ValueError("清单与实际原文件哈希、大小、格式或页数不一致，请重新准备")
    pages = manifest["pages"]
    if not isinstance(pages, list) or len(pages) != actual["page_count"]:
        raise ValueError("清单漏页，不能通过减少声明页数忽略原文件后续页")
    for number, page in enumerate(pages, 1):
        _keys(page, ("page_number", "preview_file", "sha256", "width", "height"))
        if type(page["page_number"]) is not int or page["page_number"] != number or page["preview_file"] != "page-{:04d}.png".format(number):
            raise ValueError("预览清单页码或文件名无效")
        if type(page["width"]) is not int or type(page["height"]) is not int:
            raise ValueError("预览尺寸格式无效")
        info = _png_info(path.parent / page["preview_file"])
        if any(page[key] != info[key] for key in info):
            raise ValueError("逐页预览内容已变化，请重新准备")
    return actual


def assemble(manifest_path, transcription_path, output_path):
    output = Path(output_path).expanduser()
    if output.exists() or output.is_symlink():
        raise ValueError("输出必须为新文件，不能覆盖原件、清单、转录或已有结果")
    actual = _validated_manifest(manifest_path)
    transcription = _read_json(transcription_path)
    _keys(transcription, ("schema_version", "pages"))
    if type(transcription["schema_version"]) is not int or transcription["schema_version"] != 1:
        raise ValueError("转录协议版本无效")
    supplied = transcription["pages"]
    if not isinstance(supplied, list) or len(supplied) != actual["page_count"]:
        raise ValueError("转录必须包含实际原文件的每一页；未识别页也应保留非 ok 状态")
    pages, chars, seen = [], 0, set()
    for page in supplied:
        _keys(page, ("page_number", "text", "status", "issues"))
        number = page["page_number"]
        if type(number) is not int or not 1 <= number <= actual["page_count"] or number in seen:
            raise ValueError("转录页码重复、越界或无效")
        seen.add(number)
        text, status, issues = page["text"], page["status"], page["issues"]
        if not isinstance(text, str) or not isinstance(status, str) or status not in ("ok", "partial", "empty", "error"):
            raise ValueError("页面文本或状态无效")
        chars += len(text)
        if chars > MAX_CHARS:
            raise ValueError("转录超过 2,000,000 字符限制")
        if not isinstance(issues, list) or len(issues) > 20 or any(not isinstance(issue, str) or not issue.strip() or len(issue) > 500 for issue in issues):
            raise ValueError("issues 必须是有限长度的非空问题文本数组")
        if status == "ok" and (not text.strip() or issues):
            raise ValueError("ok 页面必须有非空原文且没有尚未解决的问题")
        if status == "empty" and text.strip():
            raise ValueError("empty 页面不能同时包含非空转录")
        pages.append({"page_number": number, "text": text, "status": status, "extraction_method": "model_vision",
                      "confidence": None, "issues": list(issues), "warnings": list(issues)})
    pages.sort(key=lambda page: page["page_number"])
    if all(page["status"] == "ok" for page in pages):
        status = "ok"
    elif any(page["text"].strip() or page["status"] == "partial" for page in pages):
        status = "partial"
    elif any(page["status"] == "error" for page in pages):
        status = "error"
    else:
        status = "empty"
    # Recheck after parsing as well: a source/preview change while assembling
    # cannot be silently published under the earlier provenance.
    if _validated_manifest(manifest_path) != actual:
        raise ValueError("原文件在组装过程中发生变化")
    result = {"schema_version": 1, "tool": "family-doctor-model-vision", "source": actual["source"],
              "page_count": actual["page_count"], "status": status, "extraction_method": "model_vision",
              "coverage_basis": "local_manifest", "pages": pages, "warnings": [NOTICE]}
    _new_json(output, result)
    return {"ok": status == "ok", "output": str(output.resolve()), "status": status, "page_count": actual["page_count"]}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    first = commands.add_parser("prepare", help="准备真实页数、完整逐页预览及未完成转录模板，不调用 OCR")
    first.add_argument("input")
    first.add_argument("--output-dir", required=True)
    second = commands.add_parser("assemble", help="验证当前模型的逐页转录并生成既有提取协议")
    second.add_argument("--manifest", required=True)
    second.add_argument("--transcription", required=True)
    second.add_argument("--output", required=True)
    args = parser.parse_args(argv)
    try:
        result = prepare(args.input, args.output_dir) if args.command == "prepare" else assemble(args.manifest, args.transcription, args.output)
        print(json.dumps(result, ensure_ascii=False))
        return 0 if result["ok"] else 2
    except (ValueError, OSError, RuntimeError) as exc:
        print(json.dumps({"ok": False, "error": str(exc)}, ensure_ascii=False))
        return 1


if __name__ == "__main__":
    sys.exit(main())
