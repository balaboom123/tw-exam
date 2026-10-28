"""Self-contained native PDF members and their real fixture ZIP containers."""

import hashlib
import io
import zipfile
from dataclasses import replace
from urllib.parse import urlsplit

from app.models import ExamAttachment, SourceExamPage
from app.providers.taisugar_recruit.archives import member_locator, parse_member_locator


def bind_parent_fixtures(root, papers):
    groups = {}
    for paper in papers:
        if not urlsplit(paper.download_url_source).fragment:
            continue
        parent, checksum, name = parse_member_locator(paper.download_url_source)
        group = groups.setdefault((paper.source_exam_id, paper.year_roc, parent, checksum), {})
        payload = (root / "mirror" / paper.storage_key).read_bytes()
        if name in group:
            assert group[name] == payload
        group[name] = payload
    urls, pages, paths = {}, [], []
    for (event, year, parent, old_checksum), members in groups.items():
        stream = io.BytesIO()
        with zipfile.ZipFile(stream, "w") as archive:
            for name, payload in members.items():
                archive.writestr(name, payload)
        data = stream.getvalue()
        checksum = hashlib.sha256(data).hexdigest()
        key = f"providers/taisugar_recruit/{year}/fixture-parents/{checksum}.zip"
        path = root / "mirror" / key
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)
        paths.append(path)
        attachment = ExamAttachment(
            "Native container", "source_archive", parent, key, checksum=checksum
        )
        pages.append(SourceExamPage(event, year + 1911, year, "Fixture", [attachment], []))
        for name in members:
            urls[member_locator(parent, old_checksum, name)] = member_locator(
                parent, checksum, name
            )
    return (
        [
            replace(p, download_url_source=urls.get(p.download_url_source, p.download_url_source))
            for p in papers
        ],
        pages,
        paths,
    )
