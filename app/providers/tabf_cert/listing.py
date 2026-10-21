"""Read TABF category rows without discarding their native event context."""

from __future__ import annotations

import re
from dataclasses import dataclass
from html import unescape
from html.parser import HTMLParser
from urllib.parse import parse_qs, urljoin, urlsplit

from app.providers.tabf_cert.identity import normalized_label, programme_for_label

HISTORY_URL = "https://www.tabf.org.tw/LicenseHistoryExam.aspx"
HISTORY_INDEX_URL = HISTORY_URL + "?PHID=424"


@dataclass(frozen=True)
class NativeEventLink:
    phid: str
    programme_code: str
    official_category: str
    label: str
    ordinal: int
    ordinal_kind: str
    explicit_year_ad: int | None
    section: str

    @property
    def url(self) -> str:
        return HISTORY_URL + "?PHID=" + self.phid


@dataclass(frozen=True)
class NativeFileLink:
    label: str
    url: str


class _Rows(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.rows: list[tuple[str, list[str], list[tuple[str, str]]]] = []
        self.section = ""
        self.heading: list[str] | None = None
        self.cells: list[str] | None = None
        self.cell: list[str] | None = None
        self.links: list[tuple[str, str]] = []
        self.href = ""
        self.anchor: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag in {"h1", "h2", "h3"}:
            self.heading = []
        if tag == "tr":
            self.cells, self.links = [], []
        elif tag == "td" and self.cells is not None:
            self.cell = []
        elif tag == "a" and self.cell is not None:
            self.href, self.anchor = dict(attrs).get("href") or "", []

    def handle_data(self, value: str) -> None:
        if self.heading is not None:
            self.heading.append(value)
        if self.cell is not None:
            self.cell.append(value)
        if self.href:
            self.anchor.append(value)

    def handle_endtag(self, tag: str) -> None:
        if tag in {"h1", "h2", "h3"} and self.heading is not None:
            heading = normalized_label("".join(self.heading))
            if heading in {"歷屆試題", "參考試題"}:
                self.section = heading
            self.heading = None
        if tag == "a" and self.href:
            self.links.append((unescape(self.href), " ".join("".join(self.anchor).split())))
            self.href, self.anchor = "", []
        elif tag == "td" and self.cell is not None and self.cells is not None:
            self.cells.append(" ".join("".join(self.cell).split()))
            self.cell = None
        elif tag == "tr" and self.cells is not None:
            self.rows.append((self.section, self.cells, self.links))
            self.cells, self.cell, self.links = None, None, []


def parse_native_events(html: str) -> list[NativeEventLink]:
    parser = _Rows()
    parser.feed(html)
    events: dict[str, NativeEventLink] = {}
    for section, cells, links in parser.rows:
        if section not in {"歷屆試題", "參考試題"} or len(cells) != 2:
            continue
        event_links = [
            (href, label)
            for href, label in links
            if any(key.lower() == "phid" for key in parse_qs(urlsplit(href).query))
        ]
        if links and not event_links:
            if normalized_label(cells[0]) != "金融市場常識與職業道德" or any(
                urljoin(HISTORY_INDEX_URL, href)
                not in {
                    "http://webline.sfi.org.tw/T/ethics/",
                    "https://webline.sfi.org.tw/T/ethics/",
                }
                for href, _label in links
            ):
                raise ValueError(f"Unreviewed TABF external source row: {cells[0]}")
            continue
        for href, label in event_links:
            url = urlsplit(urljoin(HISTORY_INDEX_URL, href))
            query = parse_qs(url.query)
            if not any(key.lower() == "phid" for key in query):
                continue
            phids = query.get("PHID", [])
            if (
                url.scheme != "https"
                or url.netloc != "www.tabf.org.tw"
                or url.path != "/LicenseHistoryExam.aspx"
                or len(phids) != 1
                or sum(key.lower() == "phid" for key in query) != 1
                or not phids[0].isdigit()
                or url.fragment
            ):
                raise ValueError(f"Unreviewed TABF event endpoint: {href}")
            programme = programme_for_label(cells[0])
            event_label = normalized_label(label)
            match = re.fullmatch(r"(?:(\d{3})年)?第(\d+)([期屆次])試題", event_label)
            if match is None:
                raise ValueError(f"Unreviewed TABF native event label: {label}")
            event = NativeEventLink(
                phids[0],
                programme.code,
                cells[0],
                label,
                int(match[2]),
                match[3],
                int(match[1]) + 1911 if match[1] else None,
                section,
            )
            previous = events.get(event.phid)
            if previous is not None:
                fields = (
                    "programme_code",
                    "ordinal",
                    "ordinal_kind",
                    "explicit_year_ad",
                    "section",
                )
                if any(getattr(previous, field) != getattr(event, field) for field in fields):
                    raise ValueError(f"Conflicting TABF category/event rows: PHID {event.phid}")
            else:
                events[event.phid] = event
    if not events:
        raise ValueError("TABF archive contains no native category/event rows")
    return list(events.values())


class _Downloads(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.href = ""
        self.parts: list[str] = []
        self.links: list[tuple[str, str]] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag == "a":
            self.href, self.parts = dict(attrs).get("href") or "", []

    def handle_data(self, value: str) -> None:
        if self.href:
            self.parts.append(value)

    def handle_endtag(self, tag: str) -> None:
        if tag == "a" and self.href:
            self.links.append((unescape(self.href), " ".join("".join(self.parts).split())))
            self.href, self.parts = "", []


def parse_native_files(html: str) -> list[NativeFileLink]:
    parser = _Downloads()
    parser.feed(html)
    files: dict[str, NativeFileLink] = {}
    for href, label in parser.links:
        url = urljoin(HISTORY_INDEX_URL, href)
        parsed = urlsplit(url)
        if "/BEExam/Doc/ExamHistoryEdit/" not in parsed.path:
            continue
        if (
            parsed.scheme != "https"
            or parsed.netloc != "service.tabf.org.tw"
            or re.fullmatch(r"/BEExam/Doc/ExamHistoryEdit/\d+-\d+\.pdf", parsed.path) is None
            or parsed.query
            or parsed.fragment
            or not label
        ):
            raise ValueError(f"Unreviewed TABF native paper endpoint or label: {url}")
        link = NativeFileLink(label, url)
        if url in files and normalized_label(files[url].label) != normalized_label(label):
            raise ValueError(f"Conflicting TABF paper labels: {url}")
        files[url] = link
    return list(files.values())
