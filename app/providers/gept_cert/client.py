from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass
from html import unescape
from html.parser import HTMLParser
from pathlib import Path
from urllib.parse import quote, unquote, urljoin, urlparse, urlsplit, urlunsplit

from app.models import ExamAttachment, ExamOption, ParsedPaper, SourceExamPage
from app.providers.base import DownloadedFile, ResponseMetadata
from app.providers.gept_cert.identity import LEVELS, STORAGE_YEAR_AD, event_code
from app.providers.http import Http, decode_html
from app.source_material import SourceDate, SourceMaterial

USER_AGENT = "Mozilla/5.0 (compatible; gept-cert-mirror/1.0)"
CANONICAL_CATEGORY = "GEPT全民英檢官方練習資料"


@dataclass(frozen=True)
class GeptDownload:
    level_code: str
    label: str
    file_type: str
    url: str
    material_kind: str
    evidence_url: str
    attachment: bool = False
    practice_set: int | None = None

    @property
    def material(self) -> SourceMaterial:
        return SourceMaterial(
            self.material_kind, SourceDate("undated", None), self.evidence_url, self.label
        )


class _AnchorParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.links: list[tuple[str, str]] = []
        self._in_anchor = False
        self._href = ""
        self._parts: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        values = dict(attrs)
        if tag == "a":
            self._in_anchor = True
            self._href = values.get("href") or ""
            self._parts = []
        elif self._in_anchor and tag == "img":
            self._parts.append(values.get("alt") or "")

    def handle_data(self, data: str) -> None:
        if self._in_anchor:
            self._parts.append(data)

    def handle_endtag(self, tag: str) -> None:
        if tag == "a" and self._in_anchor:
            self.links.append((" ".join(unescape("".join(self._parts)).split()), self._href))
            self._in_anchor = False


class _Dependencies(HTMLParser):
    def __init__(self, base_url: str) -> None:
        super().__init__()
        self.base_url = base_url
        self.urls: list[str] = []
        self.scripts: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        values = dict(attrs)
        path = ""
        if tag in {"img", "script"}:
            path = values.get("src") or ""
        elif tag == "link" and values.get("rel") == "stylesheet":
            path = values.get("href") or ""
        if not path:
            return
        url = _quote_url_for_request(urljoin(self.base_url, path))
        if urlsplit(url).netloc != "www.gept.org.tw" or urlsplit(url).scheme != "https":
            return
        self.urls.append(url)
        if tag == "script":
            self.scripts.append(url)


def _quote_url_for_request(url: str) -> str:
    parts = urlsplit(url)
    return urlunsplit(
        (
            parts.scheme,
            parts.netloc,
            quote(unquote(parts.path), safe="/:%"),
            quote(unquote(parts.query), safe="=&:%"),
            quote(unquote(parts.fragment), safe=""),
        )
    )


def parse_intro_downloads(
    html: str, *, base_url: str, level_code: str
) -> tuple[list[GeptDownload], list[str]]:
    parser = _AnchorParser()
    parser.feed(html)
    downloads = []
    practice_pages = []
    for label, href in parser.links:
        if not href:
            continue
        url = _quote_url_for_request(urljoin(base_url, href))
        path = urlsplit(url).path
        filename = Path(unquote(path)).name
        if path.lower().endswith((".pdf", ".zip")):
            attachment = False
            if "各級分範例" in "".join(label.split()):
                role, kind = "scoring_examples", "reference"
                label = f"評分範例｜{Path(filename).stem}"
            elif filename.lower() == "geptcbtpracticetest.zip":
                role, kind, attachment = "source_archive", "reference", True
                label = "電腦化測驗模擬練習安裝程式"
            elif label == "安裝說明":
                role, kind, attachment = "instructions", "reference", True
                label = "電腦化測驗模擬練習安裝說明"
            elif level_code == "superior" and "試題與作答說明" in label:
                role, kind = "question", "sample"
            else:
                raise ValueError(f"Unreviewed GEPT download role: {label} ({url})")
            downloads.append(GeptDownload(level_code, label, role, url, kind, base_url, attachment))
        elif "geptpractice" in path.lower():
            practice_pages.append(url)
    return downloads, list(dict.fromkeys(practice_pages))


class _PracticeAudioParser(HTMLParser):
    def __init__(self, base_url: str, level_code: str) -> None:
        super().__init__()
        self.base_url = base_url
        self.level_code = level_code
        self.section = ""
        self.downloads: dict[str, GeptDownload] = {}

    def handle_data(self, data: str) -> None:
        value = "".join(unescape(data).split())
        if re.fullmatch(r"一[、.]聽力測驗[:：]?", value):
            self.section = "listening"
        elif re.fullmatch(r"四[、.]口說能力測驗[:：]?", value):
            self.section = "speaking"
        elif re.fullmatch(r"[二三][、.](?:閱讀|寫作)能力測驗[:：]?", value):
            self.section = "other"

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag != "a":
            return
        match = re.fullmatch(
            r"javascript:playAudio\(['\"]([^'\"]+\.mp3)['\"]\);?",
            dict(attrs).get("href") or "",
            re.IGNORECASE,
        )
        if match is None:
            return
        if self.section not in {"listening", "speaking"}:
            raise ValueError("GEPT audio lacks a reviewed listening/speaking section")
        url = _quote_url_for_request(urljoin(self.base_url, match.group(1)))
        role = f"{self.section}_audio"
        skill = "聽力" if self.section == "listening" else "口說"
        label = f"{skill}練習｜{Path(urlparse(url).path).name}"
        download = GeptDownload(
            self.level_code, label, role, url, "practice_collection", self.base_url
        )
        if url in self.downloads and self.downloads[url].file_type != role:
            raise ValueError("GEPT audio has conflicting source sections")
        self.downloads[url] = download


def parse_practice_audio(html: str, *, base_url: str, level_code: str) -> list[GeptDownload]:
    parser = _PracticeAudioParser(base_url, level_code)
    parser.feed(html)
    return list(parser.downloads.values())


class GeptCertClient:
    provider_id = "gept_cert"
    LEVELS = LEVELS

    def __init__(self) -> None:
        self.http = Http(self.provider_id, max_attempts=1, min_interval=0.1, user_agent=USER_AGENT)
        self._responses: dict[str, DownloadedFile] = {}
        self._resources: dict[str, list[GeptDownload]] = {}

    def _fetch_text(self, url: str) -> str:
        response = self.download_file(url)
        return decode_html(response.data, response.content_type)

    def _advanced_resources(self, pages: dict[str, str]) -> list[GeptDownload]:
        form_pages = {}
        scripts = set()
        for url, html in pages.items():
            match = re.search(r"/Form/([23])/Listening$", urlsplit(url).path)
            if match is None:
                raise ValueError("Unreviewed GEPT advanced practice route")
            form_pages[int(match.group(1))] = url
            parser = _Dependencies(url)
            parser.feed(html)
            scripts.update(parser.scripts)
        if set(form_pages) != {2, 3} or len(scripts) != 1:
            raise ValueError("GEPT advanced practice lacks its two forms and native application")
        script_url = scripts.pop()
        javascript = self._fetch_text(script_url)
        if set(re.findall(r'formNo:(\d+),type:"listening"', javascript)) != {"2", "3"}:
            raise ValueError("Unreviewed GEPT advanced application form data")
        home_names = set(re.findall(r"HomeView-[A-Za-z0-9_-]+\.js", javascript))
        if len(home_names) != 1:
            raise ValueError("GEPT advanced practice lacks its visible form labels")
        home_url = urljoin(script_url, home_names.pop())
        home = self._fetch_text(home_url)
        visible = set(re.findall(r"formCode:(\d+)}.{0,200}?練習題\s*(\d+)", home))
        if visible != {("2", "1"), ("3", "2")}:
            raise ValueError("GEPT internal form codes no longer match visible practice-set labels")
        prefix = re.search(r"window\.open\(`([^`$]+)\$\{\w+}`", javascript)
        if prefix is None or not re.search(r"Form_\$\{[^}]+}_L_Transcript\.pdf", javascript):
            raise ValueError("Unreviewed GEPT advanced transcript endpoint")
        resources = []
        for url in sorted(
            set(
                re.findall(
                    r'https://portal\.lttc\.org\.tw/audio/GEPTAdvanced/[^"\s]+\.mp3', javascript
                )
            )
        ):
            group = urlsplit(url).path.split("/")[-2]
            if group not in {"01", "02"}:
                raise ValueError("Unreviewed GEPT advanced audio form")
            practice_set = int(group)
            resources.append(
                GeptDownload(
                    "advanced",
                    f"練習題{practice_set}｜聽力｜{Path(urlsplit(url).path).name}",
                    "listening_audio",
                    url,
                    "practice_collection",
                    form_pages[practice_set + 1],
                    practice_set=practice_set,
                )
            )
        if not resources:
            raise ValueError("GEPT advanced practice contains no source audio")
        for form, page in form_pages.items():
            resources.append(
                GeptDownload(
                    "advanced",
                    f"練習題{form - 1}｜聽力逐字稿",
                    "listening_transcript",
                    urljoin(page, prefix.group(1) + f"Form_{form}_L_Transcript.pdf"),
                    "practice_collection",
                    page,
                    practice_set=form - 1,
                )
            )
        for path in re.findall(r'["\']([^"\'\s]+\.(?:png|jpg|css|js))["\']', javascript):
            if path.startswith("/2022/geptpracticead/"):
                asset_url = urljoin(script_url, path)
            elif path.startswith("assets/"):
                asset_url = urljoin(script_url, "../" + path)
            elif path.startswith("./"):
                asset_url = urljoin(script_url, path)
            else:
                continue
            resources.append(
                GeptDownload(
                    "advanced",
                    Path(path).name,
                    "source_asset",
                    asset_url,
                    "practice_collection",
                    form_pages[2],
                    attachment=True,
                )
            )
        resources.append(
            GeptDownload(
                "advanced",
                "高級練習題選單來源",
                "source_asset",
                home_url,
                "practice_collection",
                form_pages[2],
                attachment=True,
            )
        )
        return resources

    def _level_resources(self, level: str, intro_url: str) -> list[GeptDownload]:
        if level in self._resources:
            return self._resources[level]
        intro = self._fetch_text(intro_url)
        resources, practice_urls = parse_intro_downloads(
            intro, base_url=intro_url, level_code=level
        )
        resources.append(
            GeptDownload(
                level,
                "級別介紹與評分說明來源",
                "source_page",
                intro_url,
                "reference",
                intro_url,
                attachment=True,
            )
        )
        advanced_pages = {}
        for url in practice_urls:
            html = self._fetch_text(url)
            resources.append(
                GeptDownload(
                    level,
                    "原始互動練習網頁（含試題、正答與說明）",
                    "source_page",
                    url,
                    "practice_collection",
                    url,
                    attachment=True,
                )
            )
            dependencies = _Dependencies(url)
            dependencies.feed(html)
            for asset in dependencies.urls:
                resources.append(
                    GeptDownload(
                        level,
                        Path(urlsplit(asset).path).name,
                        "source_asset",
                        asset,
                        "practice_collection",
                        url,
                        attachment=True,
                    )
                )
            if level == "advanced":
                advanced_pages[url] = html
            else:
                resources.extend(parse_practice_audio(html, base_url=url, level_code=level))
        if advanced_pages:
            resources.extend(self._advanced_resources(advanced_pages))
        unique: dict[tuple[str, bool], GeptDownload] = {}
        for resource in resources:
            key = (resource.url, resource.attachment)
            if key in unique and unique[key].file_type != resource.file_type:
                raise ValueError("GEPT source resource has conflicting file roles")
            unique.setdefault(key, resource)
        if not any(not resource.attachment for resource in unique.values()):
            raise ValueError("GEPT level contains no recognized source material")
        self._resources[level] = list(unique.values())
        return self._resources[level]

    def discover_available_years(self) -> list[int]:
        return [STORAGE_YEAR_AD]

    def discover_exams(self, year_ad: int) -> list[ExamOption]:
        if year_ad != STORAGE_YEAR_AD:
            return []
        return [
            ExamOption(event_code(code), year_ad, year_ad - 1911, f"GEPT全民英檢 {label}")
            for code, label, _ in self.LEVELS
        ]

    def fetch_exam_page(self, exam_code: str, year_ad: int) -> SourceExamPage:
        selected = next(
            (
                (code, label, url)
                for code, label, url in self.LEVELS
                if exam_code == event_code(code)
            ),
            None,
        )
        if selected is None or year_ad != STORAGE_YEAR_AD:
            raise ValueError("Unsupported GEPT event/storage partition")
        level, label, intro_url = selected
        papers = []
        attachments = []
        for download in self._level_resources(level, intro_url):
            if download.attachment:
                attachments.append(
                    ExamAttachment(
                        download.label,
                        download.file_type,
                        download.url,
                        source_material=download.material,
                    )
                )
            else:
                category = level + (
                    f"-practice-{download.practice_set}" if download.practice_set else ""
                )
                papers.append(
                    ParsedPaper(
                        category_raw=f"{CANONICAL_CATEGORY}_{label}",
                        category_code=category,
                        subject_code="resource-"
                        + hashlib.sha256(download.url.encode()).hexdigest()[:20],
                        subject_name_raw=download.label,
                        files={download.file_type: download.url},
                        source_material=download.material,
                    )
                )
        return SourceExamPage(
            exam_code,
            year_ad,
            year_ad - 1911,
            f"GEPT全民英檢 {label}",
            attachments,
            papers,
            provider_id=self.provider_id,
        )

    def head(self, url: str) -> ResponseMetadata:
        return self.http.head(_quote_url_for_request(url))

    def download_file(self, url: str) -> DownloadedFile:
        url = _quote_url_for_request(url)
        parsed = urlsplit(url)
        if parsed.scheme != "https" or parsed.netloc not in {
            "www.gept.org.tw",
            "portal.lttc.org.tw",
        }:
            raise ValueError("GEPT resource must use an official source host")
        if parsed.netloc == "portal.lttc.org.tw" and not parsed.path.startswith(
            "/audio/GEPTAdvanced/"
        ):
            raise ValueError("Unreviewed GEPT portal resource path")
        if url not in self._responses:
            self._responses[url] = self.http.download(url, content_disposition_name=False)
        return self._responses[url]
