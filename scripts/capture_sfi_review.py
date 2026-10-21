"""Capture SFI source bytes for native review without modifying provider state."""

from __future__ import annotations

import argparse
from datetime import UTC, datetime
import hashlib
import json
from pathlib import Path
import shutil
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.providers.http import decode_html
from app.providers.sfi_cert.client import SfiCertClient, parse_sfi_archive
from app.providers.sfi_cert.evidence import ARCHIVE_URL, reviewed_payload


def capture_review(output_dir: Path, client: SfiCertClient | None = None) -> dict:
    if output_dir.exists():
        raise ValueError("Use a new output directory to preserve earlier source captures")
    output_dir.mkdir(parents=True)
    client = client or SfiCertClient()
    response = client.http.download(ARCHIVE_URL, content_disposition_name=False)
    (output_dir / "archive.html").write_bytes(response.data)
    report = {
        "captured_at": datetime.now(UTC).isoformat(),
        "source_url": ARCHIVE_URL,
        "archive_sha256": hashlib.sha256(response.data).hexdigest(),
        "provider_state_changed": False,
        "capture_complete": False,
        "files": [],
    }
    try:
        rows = parse_sfi_archive(decode_html(response.data, response.content_type))
    except ValueError as exc:
        report["listing_review_required"] = str(exc)
        (output_dir / "review.json").write_text(
            json.dumps(report, ensure_ascii=False, indent=2) + "\n"
        )
        return report
    pdf_text = shutil.which("pdftotext")
    for row in rows:
        for role, url in row.files.items():
            response = client.download_file(url)
            checksum = hashlib.sha256(response.data).hexdigest()
            path = output_dir / f"{checksum}.pdf"
            path.write_bytes(response.data)
            record = {
                "source_url": url,
                "listing_label": row.listing_label,
                "listed_role": role,
                "checksum": checksum,
                "bytes": len(response.data),
                "payload": path.name,
            }
            try:
                fact = reviewed_payload(url, response.data, row.programme)
                record["reviewed_native_context"] = fact.id
            except ValueError as exc:
                record["native_review_required"] = str(exc)
            if pdf_text is not None and response.data.startswith(b"%PDF-"):
                extracted = subprocess.run(
                    [pdf_text, "-layout", str(path), "-"],
                    capture_output=True,
                    encoding="utf-8",
                    errors="replace",
                )
                if extracted.returncode == 0:
                    text_path = path.with_suffix(".txt")
                    text_path.write_text(extracted.stdout)
                    record["extracted_text"] = text_path.name
                else:
                    record["text_extraction_error"] = extracted.stderr[:500]
            else:
                record["text_extraction_unavailable"] = True
            report["files"].append(record)
            # Preserve completed downloads even if a later request fails.
            (output_dir / "review.json").write_text(
                json.dumps(report, ensure_ascii=False, indent=2) + "\n"
            )
    report["capture_complete"] = True
    (output_dir / "review.json").write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n")
    return report


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output-dir", type=Path, required=True)
    args = parser.parse_args()
    report = capture_review(args.output_dir)
    pending = (
        not report["capture_complete"]
        or report.get("listing_review_required")
        or any("native_review_required" in row for row in report["files"])
    )
    print(f"Captured {len(report['files'])} files; native review required: {bool(pending)}")
    return 1 if pending else 0


if __name__ == "__main__":
    raise SystemExit(main())
