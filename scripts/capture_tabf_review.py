"""Capture TABF HTML and local payload evidence without examination-PDF requests."""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
from dataclasses import asdict
from datetime import UTC, datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.paths import provider_paths  # noqa: E402
from app.providers.tabf_cert.client import TabfCertClient  # noqa: E402
from app.providers.tabf_cert.evidence import native_evidence  # noqa: E402
from app.providers.tabf_cert.listing import (  # noqa: E402
    HISTORY_INDEX_URL,
    HISTORY_URL,
    parse_native_events,
    parse_native_files,
)
from app.state import load_provider_state  # noqa: E402


def capture_review(
    output_dir: Path, *, repo_root: Path = ROOT, client: TabfCertClient | None = None
) -> dict:
    if output_dir.exists():
        raise ValueError("Use a new output directory to preserve earlier captures")
    output_dir.mkdir(parents=True)
    client = client or TabfCertClient()
    native = native_evidence()
    report: dict = {
        "captured_at": datetime.now(UTC).isoformat(),
        "source_url": HISTORY_INDEX_URL,
        "HTML_capture_complete": False,
        "provider_state_changed": False,
        "examination_PDF_requests": 0,
        "surfaces": [],
        "events": [],
        "local_payloads": [],
    }

    def save() -> None:
        (output_dir / "review.json").write_text(
            json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
        )

    def surface(name: str, url: str) -> str | None:
        row = {"source_url": url, "captured_at": datetime.now(UTC).isoformat()}
        try:
            html = client._fetch_text(url)
            path = output_dir / f"{name}.html"
            path.write_text(html, encoding="utf-8")
            row.update(payload=path.name, checksum=hashlib.sha256(path.read_bytes()).hexdigest())
            return html
        except Exception as exc:
            row["error"] = str(exc)
            return None
        finally:
            report["surfaces"].append(row)
            save()

    home = surface("index", HISTORY_INDEX_URL)
    if home is None:
        return report
    try:
        links = parse_native_events(home)
    except ValueError as exc:
        report["listing_review_required"] = str(exc)
        save()
        return report
    current = {link.phid: link for link in links}
    approved = {event.phid: event for event in native.events}
    for phid in sorted(current.keys() | approved.keys(), key=int):
        link, event = current.get(phid), approved.get(phid)
        row = {
            "phid": phid,
            "currently_listed": link is not None,
            "current_listing": asdict(link) if link else None,
            "reviewed_event": event.source_exam_id if event else None,
            "native_review_required": event is None
            or (link is not None and not event.matches_listing(link)),
        }
        html = surface(f"phid-{phid}", HISTORY_URL + "?PHID=" + phid)
        if html is not None:
            try:
                files = parse_native_files(html)
                row["files"] = [asdict(file) for file in files]
                known = {p.source_url for p in native.papers if p.phid == phid}
                row["unreviewed_links"] = [file.url for file in files if file.url not in known]
                if not files or row["unreviewed_links"]:
                    row["native_review_required"] = True
            except ValueError as exc:
                row["parsing_error"] = str(exc)
        report["events"].append(row)
        save()
    provider = provider_paths(repo_root, "tabf_cert")
    try:
        _pages, catalog, _failures = load_provider_state(provider)
        for paper in catalog.papers:
            path = repo_root / "mirror" / paper.storage_key
            row = {
                "source_exam_id": paper.source_exam_id,
                "source_url": paper.download_url_source,
                "expected_checksum": paper.checksum,
                "storage_key": paper.storage_key,
            }
            try:
                if path.is_symlink() or not path.resolve().is_relative_to(
                    provider.mirror_dir.resolve()
                ):
                    raise ValueError("Local payload escapes its provider owner")
                with path.open("rb") as stream:
                    checksum = hashlib.file_digest(stream, "sha256").hexdigest()
                row["actual_checksum"] = checksum
                row["checksum_verified"] = checksum == paper.checksum
                if checksum != paper.checksum:
                    raise ValueError("Local payload checksum mismatch")
            except (OSError, ValueError) as exc:
                row["error"] = str(exc)
            report["local_payloads"].append(row)
    except (OSError, ValueError) as exc:
        report["local_state_error"] = str(exc)
    report["HTML_capture_complete"] = not any(
        "error" in row or "parsing_error" in row for row in [*report["surfaces"], *report["events"]]
    )
    save()
    return report


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--repo-root", type=Path, default=ROOT)
    args = parser.parse_args()
    report = capture_review(args.output_dir, repo_root=args.repo_root)
    pending = (
        not report["HTML_capture_complete"]
        or bool(report.get("listing_review_required"))
        or bool(report.get("local_state_error"))
        or any(row["native_review_required"] for row in report["events"])
        or any("error" in row for row in report["local_payloads"])
    )
    print(f"Captured {len(report['events'])} TABF event contexts; review required: {pending}")
    return 1 if pending else 0


if __name__ == "__main__":
    raise SystemExit(main())
