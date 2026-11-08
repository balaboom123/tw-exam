from __future__ import annotations

import hashlib
import json
import re
import unicodedata
from dataclasses import dataclass
from email.message import Message
from pathlib import Path
from urllib.error import HTTPError
from urllib.parse import quote, unquote, unquote_plus, urlparse, urlsplit, urlunsplit

from app.models import ExamOption, ParsedPaper, SourceExamPage
from app.providers.base import DownloadedFile, ResponseMetadata
from app.providers.http import Http

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
SKIP_TITLE_TOKENS = ("成績", "錄取", "試場", "分配", "報名", "查詢", "演示", "提醒")
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
    answer_review: bool = False


@dataclass(frozen=True)
class NewTaipeiEvent:
    original: NewTaipeiNotice
    notices: tuple[NewTaipeiNotice, ...]


@dataclass(frozen=True)
class NewTaipeiDownload:
    file_name: str
    file_type: str
    url: str


def _text(value: object) -> str:
    return str(value or "").strip()


def _same_text(left: str, right: str) -> bool:
    def normalized(value: str) -> str:
        return "".join(unicodedata.normalize("NFKC", value).split())

    return bool(left and right) and normalized(left) == normalized(right)


def _is_answer_review(title: str) -> bool:
    return ("疑義" in title and any(t in title for t in ("回覆", "結果", "彙整", "釋復"))) or (
        "答案" in title and "更正" in title
    )


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


def _paper_file_type(file_name: str, *, answer_review: bool = False) -> str | None:
    if not file_name.lower().endswith((".pdf", ".zip", ".rar")):
        return None
    if any(token in file_name for token in ("申請表", "申請書")):
        return None
    if answer_review:
        if "答案" in file_name and ("彙整" in file_name or "更正" in file_name):
            return "corrected_answer"
        if "疑義" in file_name and any(t in file_name for t in ("回覆", "釋復", "結果")):
            return "answer_clarification"
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
    seen: dict[str, NewTaipeiNotice] = {}
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
        answer_review = _is_answer_review(title)
        if "疑義" in title and not answer_review:
            continue
        year_ad, year_roc = years
        scope_name = tag or title
        notice = NewTaipeiNotice(
            uuid=uuid,
            title=title,
            tag=tag,
            year_ad=year_ad,
            year_roc=year_roc,
            scope_code=_scope_code(scope_name),
            scope_name=scope_name,
            answer_review=answer_review,
        )
        if uuid in seen:
            if notice != seen[uuid]:
                raise ValueError("Conflicting New Taipei notice rows for one source UUID")
            continue
        seen[uuid] = notice
        notices.append(notice)
    return notices


def detail_matches_notice(notice: NewTaipeiNotice, detail: dict[str, object]) -> bool:
    detail_title = _text(detail.get("opn_title"))
    detail_tag = _text(detail.get("opn_tag"))
    # A shared programme tag is not evidence that this is the requested notice.
    return _same_text(detail_title, notice.title) and (
        not notice.tag or _same_text(detail_tag, notice.tag)
    )


def parse_detail_downloads(detail: dict[str, object]) -> list[NewTaipeiDownload]:
    downloads: list[NewTaipeiDownload] = []
    seen: dict[str, NewTaipeiDownload] = {}
    answer_review = _is_answer_review(_text(detail.get("opn_title")))
    attachments = detail.get("attachment2") or []
    if not isinstance(attachments, list):
        return downloads
    for attachment in attachments:
        if not isinstance(attachment, dict):
            continue
        file_name = _text(attachment.get("fileName"))
        file_uuid = _text(attachment.get("fileUuid"))
        file_type = _paper_file_type(file_name, answer_review=answer_review)
        if not file_uuid or file_type is None:
            continue
        download = NewTaipeiDownload(
            file_name=file_name,
            file_type=file_type,
            url=DOWNLOAD_TOKEN_URL.format(file_uuid=file_uuid),
        )
        if file_uuid in seen:
            if download != seen[file_uuid]:
                raise ValueError("Conflicting New Taipei attachment roles or labels for one UUID")
            continue
        seen[file_uuid] = download
        downloads.append(download)
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
        self._exam_notice_cache: dict[str, NewTaipeiEvent] | None = None
        self.http = Http(self.provider_id, max_attempts=1, user_agent=USER_AGENT)
        self._head_downloads: dict[str, DownloadedFile] = {}

    def _fetch_json(self, url: str) -> object:
        raw, _headers, _status = self.http._request(_request_url(url))
        return json.loads(raw.decode("utf-8-sig"))

    def _fetch_bytes(self, url: str) -> tuple[bytes, Message]:
        data, headers, _status = self.http._request(_request_url(url), timeout=120)
        return data, headers

    def _notices(self) -> list[NewTaipeiNotice]:
        if self._notice_cache is None:
            payload = self._fetch_json(LIST_API_URL)
            rows = payload if isinstance(payload, list) else []
            self._notice_cache = parse_candidate_notices(rows)
        return self._notice_cache

    def _exam_notice_map(self) -> dict[str, NewTaipeiEvent]:
        if self._exam_notice_cache is None:
            grouped: dict[str, list[NewTaipeiNotice]] = {}
            for notice in self._notices():
                code = f"teacher-recruit-newtaipei-{notice.year_roc}-{notice.scope_code}"
                grouped.setdefault(code, []).append(notice)
            result: dict[str, NewTaipeiEvent] = {}
            for code, notices in grouped.items():
                originals = [n for n in notices if not n.answer_review]
                if len(originals) != 1:
                    raise ValueError(
                        "New Taipei event needs one unambiguous original paper notice; "
                        "retain previous state and review its source history"
                    )
                original = originals[0]
                if any(not _same_text(n.scope_name, original.scope_name) for n in notices):
                    raise ValueError("New Taipei notices do not establish the same programme")
                result[code] = NewTaipeiEvent(
                    original,
                    (
                        original,
                        *sorted((n for n in notices if n.answer_review), key=lambda n: n.uuid),
                    ),
                )
            self._exam_notice_cache = result
        return self._exam_notice_cache

    def discover_available_years(self) -> list[int]:
        return sorted({notice.year_ad for notice in self._notices()}, reverse=True)

    def discover_exams(self, year_ad: int) -> list[ExamOption]:
        return [
            ExamOption(
                code=code,
                year_ad=event.original.year_ad,
                year_roc=event.original.year_roc,
                label=(
                    f"{event.original.year_roc}學年度{CANONICAL_CATEGORY}_"
                    f"{event.original.scope_name}"
                ),
            )
            for code, event in sorted(self._exam_notice_map().items())
            if event.original.year_ad == year_ad
        ]

    def build_discovery_year_url(self, year_ad: int) -> str:
        return LIST_API_URL

    def build_discovery_exam_url(self, exam_code: str, year_ad: int) -> str:
        notice = self._exam_notice_map()[exam_code].original
        if notice.year_ad != year_ad:
            raise ValueError(
                f"New Taipei discovery year mismatch for {exam_code}: "
                f"expected {notice.year_ad}, got {year_ad}"
            )
        return DETAIL_API_URL.format(uuid=notice.uuid)

    def fetch_exam_page(self, exam_code: str, year_ad: int) -> SourceExamPage:
        self.build_discovery_exam_url(exam_code, year_ad)
        event = self._exam_notice_map()[exam_code]
        return self._fetch_event_page(exam_code, year_ad, event)

    def fetch_reviewed_exam_page(
        self, exam_code: str, year_ad: int, reviewed: NewTaipeiEvent
    ) -> SourceExamPage:
        """Revisit an exact retained event even after its rolling notice expires."""
        original = reviewed.original
        if (
            year_ad != original.year_ad
            or exam_code != f"teacher-recruit-newtaipei-{original.year_roc}-{original.scope_code}"
        ):
            raise ValueError("New Taipei reviewed event year or source context differs")
        current = self._exam_notice_map().get(exam_code)
        if current is not None and current != reviewed:
            raise ValueError(
                "New Taipei active notice history changed; retain event pending review"
            )
        return self._fetch_event_page(exam_code, year_ad, reviewed)

    def _fetch_event_page(
        self, exam_code: str, year_ad: int, event: NewTaipeiEvent
    ) -> SourceExamPage:
        notice = event.original
        papers = []
        for source_notice in event.notices:
            detail = _detail_row(self._fetch_json(DETAIL_API_URL.format(uuid=source_notice.uuid)))
            if not detail_matches_notice(source_notice, detail):
                raise ValueError("New Taipei detail does not match the requested source notice")
            downloads = parse_detail_downloads(detail)
            if not downloads:
                raise ValueError("New Taipei paper or answer-review notice has no recognized files")
            subject_code = notice.scope_code
            subject_name = notice.scope_name
            if source_notice.answer_review:
                subject_code += f"-notice-{source_notice.uuid}"
                subject_name = source_notice.title
            roles = [download.file_type for download in downloads]
            groups = [downloads] if len(set(roles)) == len(roles) else [[d] for d in downloads]
            for group in groups:
                split = len(groups) > 1
                file_uuid = group[0].url.rsplit("/", 1)[-1]
                papers.append(
                    ParsedPaper(
                        category_raw=CANONICAL_CATEGORY,
                        category_code=str(notice.year_roc),
                        subject_code=f"{subject_code}-file-{file_uuid}" if split else subject_code,
                        subject_name_raw=group[0].file_name if split else subject_name,
                        files={d.file_type: d.url for d in group},
                    )
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
        try:
            metadata = self.http.head(_request_url(download_url))
        except HTTPError as exc:
            if exc.code not in {403, 405}:
                raise
            # The official token service permits GET but can reject HEAD.
            # Observe an actual GET response, keeping its bytes for the next
            # download. Never fabricate successful metadata from the review plan.
            download_url = self._token_download_url(url)
            data, headers, status = self.http._request(_request_url(download_url), timeout=120)
            self._head_downloads[url] = self._downloaded_file(download_url, data, headers)
            length = headers.get("Content-Length")
            metadata = ResponseMetadata(
                url=url,
                status=status,
                content_length=int(length) if length else None,
                content_type=headers.get("Content-Type", ""),
                content_disposition=headers.get("Content-Disposition", ""),
                cache_control=headers.get("Cache-Control", ""),
            )
        return ResponseMetadata(
            url=url,
            status=metadata.status,
            content_length=metadata.content_length,
            content_type=metadata.content_type,
            content_disposition=metadata.content_disposition,
            cache_control=metadata.cache_control,
        )

    def download_file(self, url: str) -> DownloadedFile:
        if url in self._head_downloads:
            return self._head_downloads.pop(url)
        download_url = self._token_download_url(url)
        data, headers = self._fetch_bytes(download_url)
        return self._downloaded_file(download_url, data, headers)

    @staticmethod
    def _downloaded_file(download_url: str, data: bytes, headers: Message) -> DownloadedFile:
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
