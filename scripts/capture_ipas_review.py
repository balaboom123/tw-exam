"""Capture native iPAS documents for review without changing provider state."""

from __future__ import annotations

import argparse
import hashlib
import json
import shutil
import subprocess
import sys
import xml.etree.ElementTree as ET
import zipfile
from datetime import UTC, datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.providers.http import decode_html  # noqa: E402 - repository script bootstrap
from app.providers.ipas_cert.client import (  # noqa: E402
    CURRENT_BASE_URL,
    IPAS_SECTIONS,
    IpasCertClient,
    parse_document_downloads,
    parse_programme_listing,
)
from app.providers.ipas_cert.evidence import (  # noqa: E402
    reviewed_payload,
    validated_payload_format,
)
from app.providers.ipas_cert.identity import PROGRAMMES  # noqa: E402


def capture_review(
    output_dir: Path, client: IpasCertClient | None = None, *, extract_text: bool = False
) -> dict:
    if output_dir.exists():
        raise ValueError("Use a new output directory to preserve earlier captures")
    output_dir.mkdir(parents=True)
    client = client or IpasCertClient()
    report = {
        "captured_at": datetime.now(UTC).isoformat(),
        "source_url": CURRENT_BASE_URL,
        "capture_complete": False,
        "provider_state_changed": False,
        "surfaces": [],
        "files": [],
    }

    def save() -> None:
        (output_dir / "review.json").write_text(
            json.dumps(report, ensure_ascii=False, indent=2) + "\n"
        )

    def surface(name: str, url: str) -> str | None:
        row = {"source_url": url, "captured_at": datetime.now(UTC).isoformat()}
        try:
            response = client.http.download(url, content_disposition_name=False)
            path = output_dir / f"{name}.html"
            path.write_bytes(response.data)
            row.update(payload=path.name, checksum=hashlib.sha256(response.data).hexdigest())
            return decode_html(response.data, response.content_type)
        except Exception as exc:
            row["error"] = str(exc)
            return None
        finally:
            report["surfaces"].append(row)
            save()

    home = surface("home", CURRENT_BASE_URL)
    if home is None:
        return report
    try:
        listing = parse_programme_listing(home)
    except ValueError as exc:
        report["listing_review_required"] = str(exc)
        save()
        return report
    report["programme_listing"] = listing
    expected = {p.code: "".join(p.label.split()) for p in PROGRAMMES}
    if listing != expected:
        report["listing_review_required"] = "Native programme membership or labels changed"
    requests = {}
    for code in listing:
        for section in IPAS_SECTIONS:
            url = f"{CURRENT_BASE_URL}certification/{code}/{section}"
            html = surface(f"{code}-{section}", url)
            if html is None:
                continue
            try:
                downloads = parse_document_downloads(html, cert_code=code)
                for download in downloads:
                    requests[(code, download.url)] = download
            except ValueError as exc:
                report["surfaces"][-1]["parsing_error"] = str(exc)
                save()
    pdf_text = shutil.which("pdftotext") if extract_text else None
    for (code, url), download in requests.items():
        row = {
            "programme_code": code,
            "source_url": url,
            "listing_label": download.label,
            "captured_at": datetime.now(UTC).isoformat(),
        }
        try:
            response = client.http.download(url, content_disposition_name=False)
            checksum = hashlib.sha256(response.data).hexdigest()
            try:
                payload_format = validated_payload_format(url, response.data)
            except ValueError:
                path = output_dir / (checksum + ".bin")
                path.write_bytes(response.data)
                row.update(checksum=checksum, bytes=len(response.data), payload=path.name)
                raise
            path = output_dir / (checksum + "." + payload_format)
            if path.exists():
                if path.read_bytes() != response.data:
                    raise ValueError("Capture payload path changed")
            else:
                path.write_bytes(response.data)
            row.update(
                checksum=checksum,
                bytes=len(response.data),
                payload=path.name,
                payload_format=payload_format,
            )
            try:
                row["reviewed_native_context"] = reviewed_payload(code, url, checksum).id
            except ValueError as exc:
                row["native_review_required"] = str(exc)
            if extract_text and payload_format in {"odt", "ods"}:
                with zipfile.ZipFile(path) as archive:
                    content = ET.fromstring(archive.read("content.xml"))
                    text_path = path.with_suffix(".txt")
                    text_path.write_text(" ".join(content.itertext()))
                    row["extracted_text"] = text_path.name
            elif pdf_text is not None:
                extracted = subprocess.run(
                    [pdf_text, "-layout", str(path), "-"],
                    capture_output=True,
                    encoding="utf-8",
                    errors="replace",
                )
                if extracted.returncode:
                    row["text_extraction_error"] = extracted.stderr[:500]
                else:
                    text_path = path.with_suffix(".txt")
                    text_path.write_text(extracted.stdout)
                    row["extracted_text"] = text_path.name
        except Exception as exc:
            row["error"] = str(exc)
        report["files"].append(row)
        save()
    report["capture_complete"] = bool(requests) and not any(
        "error" in row or "parsing_error" in row for row in [*report["surfaces"], *report["files"]]
    )
    save()
    return report


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--extract-text", action="store_true")
    args = parser.parse_args()
    report = capture_review(args.output_dir, extract_text=args.extract_text)
    pending = (
        not report["capture_complete"]
        or bool(report.get("listing_review_required"))
        or any("native_review_required" in row for row in report["files"])
    )
    print(f"Captured {len(report['files'])} source documents; native review required: {pending}")
    return 1 if pending else 0


if __name__ == "__main__":
    raise SystemExit(main())
