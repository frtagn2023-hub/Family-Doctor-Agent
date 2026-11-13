#!/usr/bin/env python3
"""Extract source text with page provenance; never interpret medical results."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import signal
import subprocess
import sys
import tempfile


def pdf_reader(path):
    try:
        from pypdf import PdfReader
    except ImportError:
        try:
            from PyPDF2 import PdfReader
        except ImportError as exc:
            raise RuntimeError("PDF dependency missing; install the project's pypdf requirement") from exc
    return PdfReader(str(path))


def source_info(path, max_mb):
    path = Path(path).expanduser().resolve()
    if not path.is_file():
        raise ValueError("Input must be a local PDF file")
    size = path.stat().st_size
    if not size or size > max_mb * 1024 * 1024:
        raise ValueError("PDF is empty or exceeds --max-mb")
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return {"path": str(path), "sha256": digest.hexdigest(), "size_bytes": size}


def ocr_page(path, page_number, options):
    """Render a single page locally. subprocess argument lists never use a shell."""
    with tempfile.TemporaryDirectory(prefix="family-doctor-pdf-", dir=options.get("_temp_root")) as temp_dir:
        image_base = Path(temp_dir) / "page"
        render = [options["pdftoppm"], "-f", str(page_number), "-l", str(page_number),
                  "-singlefile", "-png", "-scale-to", "3500", str(path), str(image_base)]
        subprocess.run(render, check=True, capture_output=True, timeout=options["ocr_timeout"])
        command = [options["node"], options["ocr_script"], str(image_base) + ".png", "--json",
                   "--lang", options["lang"], "--timeout", str(options["ocr_timeout"])]
        for option in ("lang_path", "cache_path"):
            if options.get(option):
                command.extend(["--" + option.replace("_", "-"), options[option]])
        if options.get("offline"):
            command.append("--offline")
        process = subprocess.run(command, capture_output=True, text=True,
                                 timeout=options["ocr_timeout"] + 2)
        try:
            result = json.loads(process.stdout)
        except (json.JSONDecodeError, TypeError) as exc:
            raise RuntimeError("OCR did not return valid JSON") from exc
        if process.returncode not in (0, 2) or result.get("status") not in ("ok", "empty"):
            raise RuntimeError(result.get("error", "OCR failed"))
        for word in result.get("words", []):
            word["page_number"] = page_number
        return result


def extract_document(path, options, reader_factory=pdf_reader, ocr_function=ocr_page):
    source = source_info(path, options["max_mb"])
    result = {"schema_version": 1, "tool": "family-doctor-pdf-extract", "source": source,
              "status": "error", "pages": [], "warnings": []}
    try:
        reader = reader_factory(source["path"])
        if reader.is_encrypted:
            raise ValueError("Encrypted PDF is not supported; provide a decrypted local copy")
        count = len(reader.pages)
        if count == 0 or count > options["max_pages"]:
            raise ValueError("PDF has no pages or exceeds --max-pages")
        result["page_count"] = count
        total_chars = 0
        for number, page in enumerate(reader.pages, start=1):
            item = {"page_number": number, "text": "", "status": "needs_ocr",
                    "extraction_method": "none", "warnings": []}
            try:
                text = page.extract_text() or ""
                if text.strip():
                    item.update(text=text, status="ok", extraction_method="pdf_text")
                else:
                    item["warnings"].append("No readable text layer; page may be scanned or blank.")
            except Exception as exc:
                item["warnings"].append("PDF text extraction failed: " + str(exc))
            if (item["status"] == "needs_ocr" and options.get("ocr")) or options.get("ocr_all"):
                try:
                    data = ocr_function(source["path"], number, options)
                    item.update(text=data["text"], status=data["status"], extraction_method="ocr",
                                ocr={"confidence": data.get("confidence"), "words": data.get("words", []),
                                     "bbox": (data.get("pages") or [{}])[0].get("bbox"),
                                     "coordinate_system": "rendered_image_pixels", "render_scale_to": 3500})
                    item["warnings"].extend(data.get("warnings", []))
                except (OSError, ValueError, RuntimeError, subprocess.SubprocessError) as exc:
                    item.update(status="error", error="Local OCR failed: " + str(exc))
            total_chars += len(item["text"])
            if total_chars > options["max_chars"]:
                item.update(text="", status="error", error="Extracted text exceeds --max-chars")
                result["pages"].append(item)
                result["warnings"].append("Stopped before processing all pages because of the text limit.")
                break
            result["pages"].append(item)
        states = [page["status"] for page in result["pages"]]
        if len(states) == count and all(status == "ok" for status in states):
            result["status"] = "ok"
        elif any(page["text"].strip() for page in result["pages"]):
            result["status"] = "partial"
        elif "error" in states:
            result["status"] = "error"
        elif "needs_ocr" in states:
            result["status"] = "needs_ocr"
        else:
            result["status"] = "empty"
        result["warnings"].append("Extracted text is unverified source material, not confirmed health data.")
    except Exception as exc:
        result.update(status="error", error=str(exc))
    return result


def positive_number(value):
    import math
    number = float(value)
    if not math.isfinite(number) or number <= 0:
        raise argparse.ArgumentTypeError("must be a positive finite number")
    return number


def positive_int(value):
    number = int(value)
    if number <= 0:
        raise argparse.ArgumentTypeError("must be a positive integer")
    return number


def parser():
    argument_parser = argparse.ArgumentParser(description=__doc__)
    argument_parser.add_argument("input", help="Local PDF path")
    argument_parser.add_argument("-o", "--output", help="Output path (default: stdout)")
    argument_parser.add_argument("--json", action="store_true", help="Page-level results, including partial/error status")
    argument_parser.add_argument("--ocr", action="store_true", help="Opt in to local OCR for pages without text")
    argument_parser.add_argument("--ocr-all", action="store_true", help="Render/OCR every page, including pages with a partial text layer")
    argument_parser.add_argument("--lang", default="chi_sim+eng")
    argument_parser.add_argument("--lang-path", help="Local uncompressed language models")
    argument_parser.add_argument("--cache-path", help="Local OCR language cache")
    argument_parser.add_argument("--offline", action="store_true", help="Do not download language models")
    argument_parser.add_argument("--node", default="node", help="Local Node executable")
    argument_parser.add_argument("--pdftoppm", default="pdftoppm", help="Local Poppler executable")
    argument_parser.add_argument("--ocr-script", default=str(Path(__file__).resolve().parents[1] / "ocr-local/scripts/ocr.js"))
    argument_parser.add_argument("--max-mb", type=positive_number, default=50)
    argument_parser.add_argument("--max-pages", type=positive_int, default=50)
    argument_parser.add_argument("--max-chars", type=positive_int, default=2000000)
    argument_parser.add_argument("--timeout", type=positive_number, default=180, help="Total timeout in seconds")
    argument_parser.add_argument("--ocr-timeout", type=positive_number, default=60, help="Per render/OCR timeout in seconds")
    return argument_parser


def _run_bounded(options):
    """Keep malformed PDF parsing inside a child process with a total deadline."""
    command = [sys.executable, str(Path(__file__).resolve()), "--_worker"]
    process = subprocess.Popen(command, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                               stderr=subprocess.PIPE, text=True, start_new_session=os.name != "nt")
    try:
        stdout, _ = process.communicate(json.dumps(options), timeout=options["timeout"])
    except subprocess.TimeoutExpired:
        if os.name != "nt":
            os.killpg(process.pid, signal.SIGKILL)
        else:
            process.kill()
        process.communicate()
        return {"schema_version": 1, "status": "error", "pages": [], "error": "PDF extraction timed out"}
    try:
        result = json.loads(stdout)
    except json.JSONDecodeError:
        return {"schema_version": 1, "status": "error", "pages": [], "error": "PDF worker failed"}
    return result


def run_bounded(options):
    # The supervisor owns temporary images, so timeout/worker failure also cleans them.
    with tempfile.TemporaryDirectory(prefix="family-doctor-extract-") as temp_root:
        return _run_bounded(dict(options, _temp_root=temp_root))


def main(argv=None):
    args = parser().parse_args(argv)
    try:
        if args.output:
            destination = Path(args.output).expanduser()
            original = Path(args.input).expanduser()
            same_path = destination.resolve() == original.resolve()
            same_file = destination.exists() and original.exists() and os.path.samefile(destination, original)
            if same_path or same_file:
                raise ValueError("Output must not overwrite the input PDF")
        result = run_bounded(vars(args))
        success = result["status"] == "ok"
        if args.json:
            output = json.dumps(result, ensure_ascii=False, indent=2) + "\n"
        elif success:
            output = "\n\n".join("--- Page {} ---\n{}".format(page["page_number"], page["text"])
                                   for page in result["pages"]) + "\n"
        else:
            print("Extraction incomplete ({}). Use --json for page details or --ocr for scanned pages. {}".format(
                result["status"], result.get("error", "")), file=sys.stderr)
            return 2
        if args.output:
            destination = Path(args.output).expanduser()
            fd, temporary = tempfile.mkstemp(prefix=".extracted-", dir=destination.parent)
            try:
                with os.fdopen(fd, "w", encoding="utf-8") as stream:
                    stream.write(output)
                    stream.flush()
                    os.fsync(stream.fileno())
                os.replace(temporary, destination)
            finally:
                if os.path.exists(temporary):
                    os.unlink(temporary)
        else:
            sys.stdout.write(output)
        return 0 if success else 2
    except (OSError, ValueError) as exc:
        if args.json:
            print(json.dumps({"schema_version": 1, "status": "error", "pages": [], "error": str(exc)}))
        else:
            print("Error: " + str(exc), file=sys.stderr)
        return 1


if __name__ == "__main__":
    if sys.argv[1:] == ["--_worker"]:
        try:
            config = json.load(sys.stdin)
            document = extract_document(config["input"], config)
        except Exception as error:
            document = {"schema_version": 1, "status": "error", "pages": [], "error": str(error)}
        print(json.dumps(document, ensure_ascii=False))
    else:
        sys.exit(main())
