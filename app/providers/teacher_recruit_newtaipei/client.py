from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass
from email.message import Message
from pathlib import Path
from urllib.parse import quote, unquote, unquote_plus, urlparse, urlsplit, urlunsplit
from urllib.request import Request, urlopen

from app.models import ExamOption, ParsedPaper, SourceExamPage
from app.providers.base import DownloadedFile, ResponseMetadata

LIST_API_URL = (
    "https://career.ntpc.edu.tw/web-elec-bulletin/open/oauth_data/op_api/temopn_newtea_list"
)
DETAIL_API_URL = (
    "https://career.ntpc.edu.tw/web-elec-bulletin/open/oauth_data/op_api/temopn_edu/uuid/{uuid}"
)
DOWNLOAD_TOKEN_URL = (
    "https://career.ntpc.edu.tw/web-elec-bulletin/open/oauth_data/op_api/download/{file_uuid}"
)
TOKEN_DOWNLOAD_URL = "https://career.ntpc.edu.tw/web-elec-bulletin/open/oauth_data/op_api/d/{token}"
USER_AGENT = "Mozilla/5.0 (compatible; teacher-recruit-newtaipei-mirror/1.0)"
CANONICAL_CATEGORY = "新北市教師甄試"
KEEP_TITLE_TOKENS = ("試題", "題目", "答案")
SKIP_TITLE_TOKENS = ("疑義", "成績", "錄取", "試場", "分配", "報名", "查詢", "演示", "提醒")
SKIP_FILE_TOKENS = (
    "疑義",
    "申請表",
    "釋復",
    "成績",
    "錄取",
    "試場",
    "名單",
    "分配",
    "提醒",
    "演示",
)


@dataclass(frozen=True)
class NewTaipeiNotice:
    uuid: str
    title: str
    tag: str
    year_ad: int
    year_roc: int
    scope_code: str
    scope_name: str


@dataclass(frozen=True)
class NewTaipeiDownload:
    file_name: str
    file_type: str
    url: str


def _text(value: object) -> str:
    return str(value or "").strip()


def _request_url(url: str) -> str:
    parts = urlsplit(url)
    return urlunsplit(
        (
            parts.scheme,
            parts.netloc,
            quote(parts.path, safe="/%"),
            quote(parts.query, safe="=&%"),
            parts.fragment,
        )
    )


def _year_from_text(text: str) -> tuple[int, int] | None:
    match = re.search(r"(\d{3})(?:學)?年度", text)
    if not match:
        return None
    year_roc = int(match.group(1))
    return year_roc + 1911, year_roc


def _scope_code(text: str) -> str:
    if "高級中等" in text or "高中" in text:
        return "senior"
    if "國民中學" in text or "國中" in text:
        return "junior"
    if "國民小學" in text or "國小" in text:
        return "elementary-kindergarten" if "幼兒園" in text else "elementary"
    if "幼兒園" in text or "教保員" in text:
        return "preschool"
    digest = hashlib.sha1(text.encode("utf-8")).hexdigest()[:8]
    return f"notice-{digest}"


def _paper_file_type(file_name: str) -> str | None:
    if not file_name.lower().endswith((".pdf", ".zip", ".rar")):
        return None
    if any(token in file_name for token in SKIP_FILE_TOKENS):
        return None
    has_question = "試題" in file_name or "題目" in file_name
    has_answer = "答案" in file_name
    if has_question and has_answer:
        return "question_answer"
    if has_question:
        return "question"
    if has_answer:
        return "answer"
    return None


def parse_candidate_notices(rows: list[dict[str, object]]) -> list[NewTaipeiNotice]:
    notices: list[NewTaipeiNotice] = []
    for row in rows:
        title = _text(row.get("opn_title") or row.get("opn_title_trim"))
        tag = _text(row.get("opn_tag"))
        joined = f"{title} {tag}"
        uuid = _text(row.get("uuid"))
        years = _year_from_text(joined)
        if not uuid or years is None:
            continue
        if "教師" not in joined and "教保員" not in joined:
            continue
        if not any(token in title for token in KEEP_TITLE_TOKENS):
            continue
        if any(token in title for token in SKIP_TITLE_TOKENS):
            continue
        year_ad, year_roc = years
        scope_name = tag or title
        notices.append(
            NewTaipeiNotice(
                uuid=uuid,
                title=title,
                tag=tag,
                year_ad=year_ad,
                year_roc=year_roc,
                scope_code=_scope_code(scope_name),
                scope_name=scope_name,
            )
        )
    return notices


def detail_matches_notice(notice: NewTaipeiNotice, detail: dict[str, object]) -> bool:
    detail_title = _text(detail.get("opn_title"))
    detail_tag = _text(detail.get("opn_tag"))
    return (
        detail_title == notice.title
        or detail_tag == notice.tag
        or notice.title in detail_title
        or notice.tag in detail_tag
    )


def parse_detail_downloads(detail: dict[str, object]) -> list[NewTaipeiDownload]:
    downloads: list[NewTaipeiDownload] = []
    attachments = detail.get("attachment2") or []
    if not isinstance(attachments, list):
        return downloads
    for attachment in attachments:
        if not isinstance(attachment, dict):
            continue
        file_name = _text(attachment.get("fileName"))
        file_uuid = _text(attachment.get("fileUuid"))
        file_type = _paper_file_type(file_name)
        if not file_uuid or file_type is None:
            continue
        downloads.append(
            NewTaipeiDownload(
                file_name=file_name,
                file_type=file_type,
                url=DOWNLOAD_TOKEN_URL.format(file_uuid=file_uuid),
            )
        )
    return downloads


def _detail_row(payload: object) -> dict[str, object]:
    if isinstance(payload, list):
        first = payload[0] if payload else {}
        return first if isinstance(first, dict) else {}
    return payload if isinstance(payload, dict) else {}


def _filename_from_content_disposition(value: str) -> str:
    match = re.search(r"filename\*?\s*=\s*(?:UTF-8'')?\"?([^\";]+)", value, flags=re.IGNORECASE)
    if not match:
        return ""
    return unquote_plus(match.group(1).strip())


class NewTaipeiTeacherRecruitClient:
    provider_id = "teacher_recruit_newtaipei"

    def __init__(self) -> None:
        self._notice_cache: list[NewTaipeiNotice] | None = None
        self._exam_notice_cache: dict[str, NewTaipeiNotice] | None = None

    def _fetch_json(self, url: str) -> object:
        request = Request(_request_url(url), headers={"User-Agent": USER_AGENT})
        with urlopen(request, timeout=60) as response:
            return json.loads(response.read().decode("utf-8-sig"))

    def _fetch_bytes(self, url: str) -> tuple[bytes, Message]:
        request = Request(_request_url(url), headers={"User-Agent": USER_AGENT})
        with urlopen(request, timeout=120) as response:
            return response.read(), response.headers

    def _notices(self) -> list[NewTaipeiNotice]:
        if self._notice_cache is None:
            payload = self._fetch_json(LIST_API_URL)
            rows = payload if isinstance(payload, list) else []
            self._notice_cache = parse_candidate_notices(rows)
        return self._notice_cache

    def _exam_notice_map(self) -> dict[str, NewTaipeiNotice]:
        if self._exam_notice_cache is None:
            result: dict[str, NewTaipeiNotice] = {}
            for notice in self._notices():
                base_code = f"teacher-recruit-newtaipei-{notice.year_roc}-{notice.scope_code}"
                code = base_code if base_code not in result else f"{base_code}-{notice.uuid[:8]}"
                result[code] = notice
            self._exam_notice_cache = result
        return self._exam_notice_cache

    def discover_available_years(self) -> list[int]:
        return sorted({notice.year_ad for notice in self._notices()}, reverse=True)

    def discover_exams(self, year_ad: int) -> list[ExamOption]:
        return [
            ExamOption(
                code=code,
                year_ad=notice.year_ad,
                year_roc=notice.year_roc,
                label=f"{notice.year_roc}學年度{CANONICAL_CATEGORY}_{notice.scope_name}",
            )
            for code, notice in sorted(self._exam_notice_map().items())
            if notice.year_ad == year_ad
        ]

    def build_discovery_year_url(self, year_ad: int) -> str:
        return LIST_API_URL

    def build_discovery_exam_url(self, exam_code: str, year_ad: int) -> str:
        notice = self._exam_notice_map()[exam_code]
        if notice.year_ad != year_ad:
            raise ValueError(
                f"New Taipei discovery year mismatch for {exam_code}: "
                f"expected {notice.year_ad}, got {year_ad}"
            )
        return DETAIL_API_URL.format(uuid=notice.uuid)

    def fetch_exam_page(self, exam_code: str, year_ad: int) -> SourceExamPage:
        notice = self._exam_notice_map()[exam_code]
        detail_payload = self._fetch_json(DETAIL_API_URL.format(uuid=notice.uuid))
        detail = _detail_row(detail_payload)
        downloads = parse_detail_downloads(detail) if detail_matches_notice(notice, detail) else []
        files: dict[str, str] = {}
        for download in downloads:
            files.setdefault(download.file_type, download.url)
        papers = (
            [
                ParsedPaper(
                    category_raw=CANONICAL_CATEGORY,
                    category_code=str(notice.year_roc),
                    subject_code=notice.scope_code,
                    subject_name_raw=notice.scope_name,
                    files=files,
                )
            ]
            if files
            else []
        )
        return SourceExamPage(
            source_exam_id=exam_code,
            year_ad=year_ad,
            year_roc=notice.year_roc,
            exam_name_raw=f"{notice.year_roc}學年度{CANONICAL_CATEGORY}",
            attachments=[],
            papers=papers,
            provider_id=self.provider_id,
        )

    def _token_download_url(self, url: str) -> str:
        payload = self._fetch_json(url)
        if not isinstance(payload, list) or not payload or not isinstance(payload[0], dict):
            raise RuntimeError("New Taipei download token response is empty")
        token = _text(payload[0].get("token"))
        if not token:
            raise RuntimeError("New Taipei download token response is missing token")
        return TOKEN_DOWNLOAD_URL.format(token=token)

    def head(self, url: str) -> ResponseMetadata:
        download_url = self._token_download_url(url)
        request = Request(
            _request_url(download_url), headers={"User-Agent": USER_AGENT}, method="HEAD"
        )
        with urlopen(request, timeout=60) as response:
            content_length = response.headers.get("Content-Length")
            return ResponseMetadata(
                url=url,
                status=response.status,
                content_length=int(content_length) if content_length else None,
                content_type=response.headers.get("Content-Type", ""),
                content_disposition=response.headers.get("Content-Disposition", ""),
                cache_control=response.headers.get("Cache-Control", ""),
            )

    def download_file(self, url: str) -> DownloadedFile:
        download_url = self._token_download_url(url)
        data, headers = self._fetch_bytes(download_url)
        content_disposition = headers.get("Content-Disposition", "")
        file_name = (
            _filename_from_content_disposition(content_disposition)
            or Path(unquote(urlparse(download_url).path)).name
        )
        return DownloadedFile(
            data=data,
            content_type=headers.get("Content-Type", "application/octet-stream"),
            file_name=file_name,
        )
