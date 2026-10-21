from __future__ import annotations

import hashlib

from app.models import ExamOption, ParsedPaper, SourceExamPage
from app.providers.base import DownloadedFile, ResponseMetadata
from app.providers.http import Http
from app.providers.tabf_cert.evidence import NativeEvent, native_evidence, validate_paper_url
from app.providers.tabf_cert.identity import PROGRAMMES, normalized_label
from app.providers.tabf_cert.listing import (
    HISTORY_INDEX_URL,
    HISTORY_URL,
    NativeEventLink,
    parse_native_events,
    parse_native_files,
)

USER_AGENT = "Mozilla/5.0 (compatible; tabf-cert-mirror/1.0)"


class TabfCertClient:
    provider_id = "tabf_cert"

    def __init__(self) -> None:
        self.http = Http(self.provider_id, max_attempts=2, user_agent=USER_AGENT)
        self._html: dict[str, str] = {}
        self._links: tuple[NativeEventLink, ...] | None = None
        self._payloads: dict[str, DownloadedFile] = {}
        self._pages: dict[str, SourceExamPage] = {}

    def _fetch_text(self, url: str) -> str:
        return self.http.get_text(url, encoding="utf-8")

    def _text(self, url: str) -> str:
        if url not in self._html:
            self._html[url] = self._fetch_text(url)
        return self._html[url]

    def _native_links(self) -> tuple[NativeEventLink, ...]:
        if self._links is None:
            links = parse_native_events(self._text(HISTORY_INDEX_URL))
            actual = {link.programme_code for link in links}
            expected = {programme.code for programme in PROGRAMMES}
            if actual != expected:
                raise ValueError(
                    "TABF native programme membership changed: "
                    f"missing={sorted(expected - actual)}, unreviewed={sorted(actual - expected)}"
                )
            self._links = tuple(links)
        return self._links

    def _current_events(self) -> list[NativeEvent]:
        evidence = native_evidence()
        by_phid = {event.phid: event for event in evidence.events}
        events = []
        unreviewed = []
        for link in self._native_links():
            event = by_phid.get(link.phid)
            if event is None or not event.matches_listing(link):
                unreviewed.append(link.phid)
            else:
                events.append(event)
        if unreviewed:
            raise ValueError(
                "TABF native events require programme/date/payload review: PHID "
                + ", ".join(unreviewed)
            )
        return events

    def discover_available_years(self) -> list[int]:
        return sorted({event.year_ad for event in self._current_events()})

    def discover_exams(self, year_ad: int) -> list[ExamOption]:
        return [
            ExamOption(
                event.source_exam_id,
                year_ad,
                year_ad - 1911,
                f"{event.programme.label}｜{event.listing_label}",
            )
            for event in self._current_events()
            if event.year_ad == year_ad
        ]

    def fetch_exam_page(self, exam_code: str, year_ad: int) -> SourceExamPage:
        evidence = native_evidence()
        event = next(
            (
                event
                for event in evidence.events
                if exam_code
                in {
                    event.source_exam_id,
                    f"tabf-cert-phid-{event.phid}",
                }
            ),
            None,
        )
        if event is None or event.year_ad != year_ad:
            raise ValueError("TABF requested event/year lacks matching native evidence")
        if event.source_exam_id in self._pages:
            return self._pages[event.source_exam_id]
        current = next((link for link in self._native_links() if link.phid == event.phid), None)
        if current is not None and not event.matches_listing(current):
            raise ValueError(f"TABF native category/event context changed: PHID {event.phid}")
        source_url = HISTORY_URL + "?PHID=" + event.phid
        files = parse_native_files(self._text(source_url))
        if not files:
            raise ValueError(f"TABF native paper listing is empty: PHID {event.phid}")
        approved_urls = {p.source_url for p in evidence.papers if p.phid == event.phid}
        if any(link.url not in approved_urls for link in files):
            raise ValueError(f"TABF native paper links require review: PHID {event.phid}")
        papers = []
        for link in files:
            payload = self.download_file(link.url)
            checksum = hashlib.sha256(payload.data).hexdigest()
            context = evidence.context(event.phid, link.url, checksum)
            if context is None or normalized_label(context.paper.source_label) != normalized_label(
                link.label
            ):
                raise ValueError(f"TABF paper role/context requires native review: {link.url}")
            fact = context.paper
            papers.append(
                ParsedPaper(
                    category_raw=event.programme.label,
                    category_code=event.programme_code,
                    subject_code=fact.subject_code,
                    subject_name_raw=fact.subject_label,
                    files={fact.file_type: fact.source_url},
                    source_material=context.material,
                )
            )
        page = SourceExamPage(
            source_exam_id=event.source_exam_id,
            year_ad=year_ad,
            year_roc=year_ad - 1911,
            exam_name_raw=(
                f"{year_ad - 1911}年{event.programme.label}｜"
                f"第{event.ordinal}{event.native_ordinal_kind}"
            ),
            attachments=[],
            papers=papers,
            provider_id=self.provider_id,
        )
        self._pages[event.source_exam_id] = page
        return page

    def head(self, url: str) -> ResponseMetadata:
        return self.http.head(url)

    def download_file(self, url: str) -> DownloadedFile:
        validate_paper_url(url)
        approved = {p.checksum for p in native_evidence().papers if p.source_url == url}
        if not approved:
            raise ValueError(f"TABF unreviewed native URL cannot be acquired automatically: {url}")
        if url not in self._payloads:
            payload = self.http.download(
                url,
                filename_fallback="download.pdf",
                content_disposition_name=False,
            )
            if not payload.data.startswith(b"%PDF-"):
                raise ValueError(f"TABF source is not a native PDF: {url}")
            checksum = hashlib.sha256(payload.data).hexdigest()
            if checksum not in approved:
                raise ValueError(
                    f"TABF replacement bytes require native review: {url} SHA256={checksum}"
                )
            self._payloads[url] = payload
        return self._payloads[url]
