import tempfile
import threading
import time
import unittest
from pathlib import Path
from urllib.error import HTTPError
from unittest.mock import patch

from app.crawler import DownloadedFile
from app.models import AliasRule, ExamAttachment, ParsedPaper, SourceExamPage
from app.providers.base import SourceProvider
from app.providers.moex.provider import MoexProvider
from app.providers.registry import get_provider
from app.storage import MirrorStore
from app.sync import retry_network, sync_exam_pages


class FakeClient:
    provider_id = "moex"

    def fetch_exam_page(self, exam_code: str, year_ad: int) -> SourceExamPage:
        return SourceExamPage(
            source_exam_id=exam_code,
            year_ad=year_ad,
            year_roc=year_ad - 1911,
            exam_name_raw="115年專技高考護理師",
            attachments=[ExamAttachment(title="全部答案", file_type="all_answers", download_url_source="https://example.test/all.pdf")],
            papers=[
                ParsedPaper(
                    category_raw="護理師",
                    category_code="101",
                    subject_code="0101",
                    subject_name_raw="基礎醫學",
                    files={
                        "question": "https://example.test/question.pdf",
                        "answer": "https://example.test/answer.pdf",
                    },
                )
            ],
        )

    def download_file(self, url: str) -> DownloadedFile:
        if url.endswith("all.pdf") or url.endswith("answer.pdf"):
            raise RuntimeError("boom")
        return DownloadedFile(data=b"%PDF-1.7 demo", content_type="application/pdf", file_name=Path(url).name)


class ReuseExistingMirrorClient:
    provider_id = "moex"

    def __init__(self) -> None:
        self.downloaded_urls: list[str] = []

    def fetch_exam_page(self, exam_code: str, year_ad: int) -> SourceExamPage:
        return SourceExamPage(
            source_exam_id=exam_code,
            year_ad=year_ad,
            year_roc=year_ad - 1911,
            exam_name_raw="demo exam",
            attachments=[ExamAttachment(title="全部答案", file_type="all_answers", download_url_source="https://example.test/all.pdf")],
            papers=[
                ParsedPaper(
                    category_raw="nurse raw",
                    category_code="101",
                    subject_code="0101",
                    subject_name_raw="subject",
                    files={
                        "question": "https://example.test/question.pdf",
                        "answer": "https://example.test/answer.pdf",
                    },
                )
            ],
        )

    def download_file(self, url: str) -> DownloadedFile:
        self.downloaded_urls.append(url)
        return DownloadedFile(data=b"%PDF-1.7 demo", content_type="application/pdf", file_name=Path(url).name)


class QuestionOnlyClient:
    provider_id = "moex"

    def __init__(self) -> None:
        self.downloaded_urls: list[str] = []

    def fetch_exam_page(self, exam_code: str, year_ad: int) -> SourceExamPage:
        return SourceExamPage(
            source_exam_id=exam_code,
            year_ad=year_ad,
            year_roc=year_ad - 1911,
            exam_name_raw="demo exam",
            attachments=[],
            papers=[
                ParsedPaper(
                    category_raw="nurse raw",
                    category_code="101",
                    subject_code="0101",
                    subject_name_raw="subject",
                    files={"question": "https://example.test/question.pdf"},
                )
            ],
        )

    def download_file(self, url: str) -> DownloadedFile:
        self.downloaded_urls.append(url)
        return DownloadedFile(data=b"%PDF-1.7 original payload", content_type="application/pdf", file_name=Path(url).name)


class ConcurrentQuestionClient(QuestionOnlyClient):
    def __init__(self, *, max_concurrency: int) -> None:
        super().__init__()
        self.max_concurrency = max_concurrency
        self.barrier = threading.Barrier(2) if max_concurrency > 1 else None
        self.active = 0
        self.peak = 0
        self.lock = threading.Lock()

    def fetch_exam_page(self, exam_code: str, year_ad: int) -> SourceExamPage:
        page = super().fetch_exam_page(exam_code, year_ad)
        page.papers[0].files["answer"] = "https://example.test/answer.pdf"
        return page

    def download_file(self, url: str) -> DownloadedFile:
        with self.lock:
            self.active += 1
            self.peak = max(self.peak, self.active)
        try:
            if self.barrier is not None:
                self.barrier.wait(timeout=2)
            else:
                time.sleep(0.01)
            return super().download_file(url)
        finally:
            with self.lock:
                self.active -= 1


class MainThreadMirrorStore(MirrorStore):
    def __init__(self, root: Path) -> None:
        super().__init__(root)
        self.owner_thread = threading.get_ident()

    def find_existing(self, storage_key_prefix: str):
        assert threading.get_ident() == self.owner_thread
        return super().find_existing(storage_key_prefix)

    def write_bytes(self, storage_key: str, data: bytes, *, overwrite: bool = False):
        assert threading.get_ident() == self.owner_thread
        return super().write_bytes(storage_key, data, overwrite=overwrite)

    def delete_matching_except(self, storage_key_prefix: str, keep_storage_key: str) -> None:
        assert threading.get_ident() == self.owner_thread
        super().delete_matching_except(storage_key_prefix, keep_storage_key)


class NetworkRetryTests(unittest.TestCase):
    def test_retry_after_controls_transient_http_backoff(self) -> None:
        attempts = 0

        def request() -> str:
            nonlocal attempts
            attempts += 1
            if attempts == 1:
                raise HTTPError("https://example.test/paper.pdf", 429, "rate limited", {"Retry-After": "7"}, None)
            return "ok"

        with patch("app.sync.time.sleep") as sleep, patch("app.sync.random.uniform", return_value=0):
            self.assertEqual(retry_network(request), "ok")
        self.assertEqual(attempts, 2)
        sleep.assert_called_once_with(7.0)

    def test_non_transient_http_error_is_not_retried(self) -> None:
        def request() -> None:
            raise HTTPError("https://example.test/missing.pdf", 404, "missing", {}, None)

        with patch("app.sync.time.sleep") as sleep:
            with self.assertRaises(HTTPError):
                retry_network(request)
        sleep.assert_not_called()

    def test_long_retry_after_defers_to_later_sync(self) -> None:
        def request() -> None:
            raise HTTPError("https://example.test/paper.pdf", 429, "rate limited", {"Retry-After": "600"}, None)

        with patch("app.sync.time.sleep") as sleep:
            with self.assertRaises(HTTPError):
                retry_network(request)
        sleep.assert_not_called()


class RetryOnceClient(QuestionOnlyClient):
    def __init__(self) -> None:
        super().__init__()
        self.attempts = 0

    def download_file(self, url: str) -> DownloadedFile:
        self.attempts += 1
        self.downloaded_urls.append(url)
        if self.attempts == 1:
            raise OSError("temporary network failure")
        return DownloadedFile(data=b"%PDF-1.7 retry payload", content_type="application/pdf", file_name=Path(url).name)


class HtmlPlaceholderClient:
    provider_id = "moex"

    def __init__(self) -> None:
        self.downloaded_urls: list[str] = []

    def fetch_exam_page(self, exam_code: str, year_ad: int) -> SourceExamPage:
        return SourceExamPage(
            source_exam_id=exam_code,
            year_ad=year_ad,
            year_roc=year_ad - 1911,
            exam_name_raw="demo exam",
            attachments=[],
            papers=[
                ParsedPaper(
                    category_raw="nurse raw",
                    category_code="101",
                    subject_code="0101",
                    subject_name_raw="subject",
                    files={"question": "https://example.test/question.ashx"},
                )
            ],
        )

    def download_file(self, url: str) -> DownloadedFile:
        self.downloaded_urls.append(url)
        return DownloadedFile(
            data=b"\xef\xbb\xbf<!DOCTYPE html><html><title>error</title></html>",
            content_type="text/html; charset=utf-8",
            file_name="wHandExamQandA_File.ashx",
        )


class QuestionAltDocxClient:
    provider_id = "ceec_gsat"

    def __init__(self) -> None:
        self.downloaded_urls: list[str] = []

    def fetch_exam_page(self, exam_code: str, year_ad: int) -> SourceExamPage:
        return SourceExamPage(
            source_exam_id=exam_code,
            year_ad=year_ad,
            year_roc=year_ad - 1911,
            exam_name_raw="115學年度學科能力測驗－國綜",
            attachments=[],
            papers=[
                ParsedPaper(
                    category_raw="摮貊測驗",
                    category_code="115",
                    subject_code="guozong-01",
                    subject_name_raw="國綜 試題內容",
                    files={"question": "https://example.test/question.pdf"},
                ),
                ParsedPaper(
                    category_raw="摮貊測驗",
                    category_code="115",
                    subject_code="guozong-02",
                    subject_name_raw="國綜 試題內容",
                    files={"question_alt": "https://example.test/question-alt.docx"},
                ),
            ],
            provider_id=self.provider_id,
        )

    def download_file(self, url: str) -> DownloadedFile:
        self.downloaded_urls.append(url)
        if url.endswith(".docx"):
            return DownloadedFile(
                data=b"PK\x03\x04docx payload",
                content_type="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
                file_name=Path(url).name,
            )
        return DownloadedFile(data=b"%PDF-1.7 demo", content_type="application/pdf", file_name=Path(url).name)


class QuestionDocClient:
    provider_id = "ceec_gsat"

    def __init__(self) -> None:
        self.downloaded_urls: list[str] = []

    def fetch_exam_page(self, exam_code: str, year_ad: int) -> SourceExamPage:
        return SourceExamPage(
            source_exam_id=exam_code,
            year_ad=year_ad,
            year_roc=year_ad - 1911,
            exam_name_raw="102摮詨僑摨血飛蝘?葫撽??芰",
            attachments=[],
            papers=[
                ParsedPaper(
                    category_raw="?株?皜祇?",
                    category_code="102",
                    subject_code="science-01",
                    subject_name_raw="?芰 閰阡??批捆",
                    files={"question": "https://example.test/question.doc"},
                ),
                ParsedPaper(
                    category_raw="?株?皜祇?",
                    category_code="102",
                    subject_code="science-02",
                    subject_name_raw="?芰 閰阡??批捆",
                    files={"question_alt": "https://example.test/question-alt.doc"},
                ),
            ],
            provider_id=self.provider_id,
        )

    def download_file(self, url: str) -> DownloadedFile:
        self.downloaded_urls.append(url)
        return DownloadedFile(
            data=b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1legacy doc payload",
            content_type="application/msword",
            file_name=Path(url).name,
        )


class CorrectedAnswerDocClient:
    provider_id = "ceec_gsat"

    def fetch_exam_page(self, exam_code: str, year_ad: int) -> SourceExamPage:
        return SourceExamPage(
            source_exam_id=exam_code,
            year_ad=year_ad,
            year_roc=year_ad - 1911,
            exam_name_raw="92學年度學科能力測驗－數學",
            attachments=[],
            papers=[
                ParsedPaper(
                    category_raw="學科能力測驗",
                    category_code="92",
                    subject_code="math-02",
                    subject_name_raw="數學 封面",
                    files={"corrected_answer": "https://example.test/math-cover.doc"},
                )
            ],
            provider_id=self.provider_id,
        )

    def download_file(self, url: str) -> DownloadedFile:
        return DownloadedFile(
            data=b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1legacy doc payload",
            content_type="application/msword",
            file_name=Path(url).name,
        )


class QuestionArchiveClient:
    provider_id = "rcpet_cap"

    def __init__(self, *, url: str, data: bytes, content_type: str, file_name: str) -> None:
        self.url = url
        self.data = data
        self.content_type = content_type
        self.file_name = file_name
        self.downloaded_urls: list[str] = []

    def fetch_exam_page(self, exam_code: str, year_ad: int) -> SourceExamPage:
        return SourceExamPage(
            source_exam_id=exam_code,
            year_ad=year_ad,
            year_roc=year_ad - 1911,
            exam_name_raw="115 CAP",
            attachments=[],
            papers=[
                ParsedPaper(
                    category_raw="CAP",
                    category_code="115",
                    subject_code="english-listening",
                    subject_name_raw="English listening",
                    files={"question": self.url},
                )
            ],
            provider_id=self.provider_id,
        )

    def download_file(self, url: str) -> DownloadedFile:
        self.downloaded_urls.append(url)
        return DownloadedFile(data=self.data, content_type=self.content_type, file_name=self.file_name)


class TocflMockAssetClient:
    provider_id = "tocfl_cert"

    def __init__(self) -> None:
        self.downloaded_urls: list[str] = []

    def fetch_exam_page(self, exam_code: str, year_ad: int) -> SourceExamPage:
        return SourceExamPage(
            source_exam_id=exam_code,
            year_ad=year_ad,
            year_roc=year_ad - 1911,
            exam_name_raw="TOCFL mock",
            attachments=[],
            papers=[
                ParsedPaper(
                    category_raw="TOCFL華語文能力測驗官方參考資料",
                    category_code="tocfl-mock",
                    subject_code="novice-listening-one",
                    subject_name_raw="Novice listening part one",
                    files={
                        "question": "https://example.test/question.rar",
                        "listening_audio": "https://example.test/audio.rar",
                        "answer": "https://example.test/answer.xlsx",
                        "question_alt": "https://example.test/script.rar",
                    },
                )
            ],
            provider_id=self.provider_id,
        )

    def download_file(self, url: str) -> DownloadedFile:
        self.downloaded_urls.append(url)
        if url.endswith(".xlsx"):
            return DownloadedFile(data=b"PK\x03\x04xlsx payload", content_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet", file_name=Path(url).name)
        return DownloadedFile(data=b"Rar!\x1a\x07\x00archive payload", content_type="application/octet-stream", file_name=Path(url).name)


class AnswerArchiveClient:
    provider_id = "teacher_recruit_tainan"

    def __init__(self) -> None:
        self.downloaded_urls: list[str] = []

    def fetch_exam_page(self, exam_code: str, year_ad: int) -> SourceExamPage:
        return SourceExamPage(
            source_exam_id=exam_code,
            year_ad=year_ad,
            year_roc=year_ad - 1911,
            exam_name_raw="115學年度臺南市國小教師甄試",
            attachments=[],
            papers=[
                ParsedPaper(
                    category_raw="臺南市國小教師甄試",
                    category_code="115",
                    subject_code="elementary-prek-special-ed",
                    subject_name_raw="國小教師暨學前特教師聯合甄選",
                    files={
                        "answer": "https://example.test/reference-answer.zip",
                        "corrected_answer": "https://example.test/corrected-answer.zip",
                    },
                )
            ],
            provider_id=self.provider_id,
        )

    def download_file(self, url: str) -> DownloadedFile:
        self.downloaded_urls.append(url)
        return DownloadedFile(data=b"PK\x03\x04zip payload", content_type="application/zip", file_name=Path(url).name)


class TcteHistoricalAssetClient:
    provider_id = "tcte_tve"

    def fetch_exam_page(self, exam_code: str, year_ad: int) -> SourceExamPage:
        return SourceExamPage(
            source_exam_id=exam_code,
            year_ad=year_ad,
            year_roc=year_ad - 1911,
            exam_name_raw="90學年度四技二專統一入學測驗",
            attachments=[],
            papers=[
                ParsedPaper(
                    category_raw="四技二專統一入學測驗",
                    category_code="common",
                    subject_code="math",
                    subject_name_raw="共同科目 數學科",
                    files={
                        "question_page_01": "https://example.test/math-p1.jpg",
                        "answer_table": "https://example.test/answer.htm",
                    },
                )
            ],
            provider_id=self.provider_id,
        )

    def download_file(self, url: str) -> DownloadedFile:
        if url.endswith(".jpg"):
            return DownloadedFile(
                data=b"\xff\xd8\xff\xe0jpeg payload",
                content_type="image/jpeg",
                file_name="math-p1.jpg",
            )
        return DownloadedFile(
            data=b"<html><table><tr><td>A</td></tr></table></html>",
            content_type="text/html; charset=utf-8",
            file_name="answer.htm",
        )


class SyncExamPagesTests(unittest.TestCase):
    def test_moex_provider_implements_source_provider_contract(self) -> None:
        self.assertIsInstance(MoexProvider(), SourceProvider)

    def test_session_and_rate_limited_providers_remain_serial(self) -> None:
        for provider_id in (
            "teacher_qual", "wdasec_skill", "hce_cmu", "hce_nsysu", "hce_nthu", "hce_tcu",
        ):
            with self.subTest(provider_id=provider_id):
                self.assertEqual(get_provider(provider_id).max_concurrency, 1)

    def test_downloads_overlap_but_mirror_writes_stay_on_main_thread(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            client = ConcurrentQuestionClient(max_concurrency=4)
            _, catalog, failures = sync_exam_pages(
                client=client, exam_codes=[("115030", 2026)],
                mirror_store=MainThreadMirrorStore(Path(tmp_dir)),
                alias_rules=[], mirror_base_url="",
            )
        self.assertEqual(client.peak, 2)
        self.assertEqual(len(catalog.papers), 2)
        self.assertEqual(failures, [])

    def test_provider_can_limit_downloads_to_one_worker(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            client = ConcurrentQuestionClient(max_concurrency=1)
            _, catalog, failures = sync_exam_pages(
                client=client, exam_codes=[("115030", 2026)],
                mirror_store=MainThreadMirrorStore(Path(tmp_dir)),
                alias_rules=[], mirror_base_url="",
            )
        self.assertEqual(client.peak, 1)
        self.assertEqual(len(catalog.papers), 2)
        self.assertEqual(failures, [])

    def test_sync_exam_pages_keeps_partial_success_and_records_failures(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            raw_pages, normalized, failures = sync_exam_pages(
                client=FakeClient(),
                exam_codes=[("115030", 2026)],
                mirror_store=MirrorStore(Path(tmp_dir)),
                alias_rules=[AliasRule(match_type="exact", raw_pattern="護理師", canonical_id="nurse", canonical_name="護理師")],
                mirror_base_url="",
            )

        self.assertEqual(len(raw_pages), 1)
        self.assertEqual(len(normalized.papers), 1)
        self.assertEqual(normalized.papers[0].file_type, "question")
        self.assertEqual(len(failures), 2)
        self.assertEqual({failure["file_type"] for failure in failures}, {"all_answers", "answer"})

    def test_sync_exam_pages_reuses_existing_mirror_files_before_downloading(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            mirror_root = Path(tmp_dir)
            attachment_path = mirror_root / "providers" / "moex" / "115" / "115030" / "exam" / "all_answers.pdf"
            attachment_path.parent.mkdir(parents=True, exist_ok=True)
            attachment_path.write_bytes(b"%PDF-1.7 cached attachment")
            question_path = mirror_root / "providers" / "moex" / "115" / "115030" / "101" / "0101" / "question.pdf"
            question_path.parent.mkdir(parents=True, exist_ok=True)
            question_path.write_bytes(b"%PDF-1.7 cached question")
            client = ReuseExistingMirrorClient()

            raw_pages, normalized, failures = sync_exam_pages(
                client=client,
                exam_codes=[("115030", 2026)],
                mirror_store=MirrorStore(mirror_root),
                alias_rules=[AliasRule(match_type="exact", raw_pattern="nurse raw", canonical_id="nurse", canonical_name="Nurse")],
                mirror_base_url="",
            )

        self.assertEqual(client.downloaded_urls, ["https://example.test/answer.pdf"])
        self.assertEqual(raw_pages[0].attachments[0].storage_key, "providers/moex/115/115030/exam/all_answers.pdf")
        self.assertEqual(
            raw_pages[0].papers[0].mirror_files["question"]["storage_key"],
            "providers/moex/115/115030/101/0101/question.pdf",
        )
        self.assertEqual(sorted(paper.file_type for paper in normalized.papers), ["answer", "question"])
        self.assertEqual(failures, [])

    def test_sync_exam_pages_reuses_legacy_unscoped_mirror_files_and_promotes_storage_keys(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            mirror_root = Path(tmp_dir)
            legacy_question_path = mirror_root / "115" / "115030" / "101" / "0101" / "question.pdf"
            legacy_question_path.parent.mkdir(parents=True, exist_ok=True)
            legacy_question_path.write_bytes(b"%PDF-1.7 cached legacy question")
            client = ReuseExistingMirrorClient()

            raw_pages, normalized, failures = sync_exam_pages(
                client=client,
                exam_codes=[("115030", 2026)],
                mirror_store=MirrorStore(mirror_root),
                alias_rules=[AliasRule(match_type="exact", raw_pattern="nurse raw", canonical_id="nurse", canonical_name="Nurse")],
                mirror_base_url="",
            )

            promoted_path = mirror_root / "providers" / "moex" / "115" / "115030" / "101" / "0101" / "question.pdf"
            self.assertEqual(sorted(client.downloaded_urls), ["https://example.test/all.pdf", "https://example.test/answer.pdf"])
            self.assertTrue(promoted_path.exists())
            self.assertEqual(promoted_path.read_bytes(), b"%PDF-1.7 cached legacy question")
            self.assertEqual(
                raw_pages[0].papers[0].mirror_files["question"]["storage_key"],
                "providers/moex/115/115030/101/0101/question.pdf",
            )
            question_paper = next(paper for paper in normalized.papers if paper.file_type == "question")
            self.assertEqual(question_paper.storage_key, "providers/moex/115/115030/101/0101/question.pdf")
            self.assertEqual(failures, [])

    def test_sync_exam_pages_can_skip_attachment_downloads(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            client = ReuseExistingMirrorClient()

            raw_pages, normalized, failures = sync_exam_pages(
                client=client,
                exam_codes=[("115030", 2026)],
                mirror_store=MirrorStore(Path(tmp_dir)),
                alias_rules=[AliasRule(match_type="exact", raw_pattern="nurse raw", canonical_id="nurse", canonical_name="Nurse")],
                mirror_base_url="",
                download_attachments=False,
            )

        self.assertEqual(client.downloaded_urls, ["https://example.test/question.pdf", "https://example.test/answer.pdf"])
        self.assertEqual(raw_pages[0].attachments[0].download_url_source, "https://example.test/all.pdf")
        self.assertEqual(raw_pages[0].attachments[0].storage_key, "")
        self.assertEqual(sorted(paper.file_type for paper in normalized.papers), ["answer", "question"])
        self.assertEqual(failures, [])

    def test_sync_exam_pages_rejects_html_placeholder_downloads(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            mirror_root = Path(tmp_dir)
            client = HtmlPlaceholderClient()

            raw_pages, normalized, failures = sync_exam_pages(
                client=client,
                exam_codes=[("115030", 2026)],
                mirror_store=MirrorStore(mirror_root),
                alias_rules=[AliasRule(match_type="exact", raw_pattern="nurse raw", canonical_id="nurse", canonical_name="Nurse")],
                mirror_base_url="",
            )

            self.assertFalse(any(mirror_root.rglob("question.*")))

        self.assertEqual(client.downloaded_urls, ["https://example.test/question.ashx"])
        self.assertEqual(len(raw_pages), 1)
        self.assertEqual(normalized.papers, [])
        self.assertEqual(len(failures), 1)
        self.assertIn("HTML placeholder", failures[0].message)

    def test_sync_exam_pages_accepts_tcte_question_images_and_intentional_answer_html(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            raw_pages, normalized, failures = sync_exam_pages(
                client=TcteHistoricalAssetClient(),
                exam_codes=[("tcte-tve-90", 2001)],
                mirror_store=MirrorStore(Path(tmp_dir)),
                alias_rules=[],
                mirror_base_url="",
            )

        self.assertEqual(
            sorted(paper.file_type for paper in normalized.papers),
            ["answer_table", "question_page_01"],
        )
        self.assertEqual(
            raw_pages[0].papers[0].mirror_files["question_page_01"]["storage_key"],
            "providers/tcte_tve/90/tcte-tve-90/common/math/question_page_01.jpg",
        )
        self.assertEqual(
            raw_pages[0].papers[0].mirror_files["answer_table"]["storage_key"],
            "providers/tcte_tve/90/tcte-tve-90/common/math/answer_table.htm",
        )
        self.assertEqual(failures, [])

    def test_sync_exam_pages_retries_transient_download_failures(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            client = RetryOnceClient()

            raw_pages, normalized, failures = sync_exam_pages(
                client=client,
                exam_codes=[("115030", 2026)],
                mirror_store=MirrorStore(Path(tmp_dir)),
                alias_rules=[AliasRule(match_type="exact", raw_pattern="nurse raw", canonical_id="nurse", canonical_name="Nurse")],
                mirror_base_url="",
            )

        self.assertEqual(client.attempts, 2)
        self.assertEqual(len(raw_pages), 1)
        self.assertEqual([paper.file_type for paper in normalized.papers], ["question"])
        self.assertEqual(failures, [])

    def test_sync_exam_pages_replaces_invalid_existing_ashx_with_valid_pdf(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            mirror_root = Path(tmp_dir)
            file_dir = mirror_root / "providers" / "moex" / "115" / "115030" / "101" / "0101"
            file_dir.mkdir(parents=True, exist_ok=True)
            (file_dir / "question.ashx").write_bytes(b"\xef\xbb\xbf<!DOCTYPE html><html>error</html>")
            client = QuestionOnlyClient()

            raw_pages, normalized, failures = sync_exam_pages(
                client=client,
                exam_codes=[("115030", 2026)],
                mirror_store=MirrorStore(mirror_root),
                alias_rules=[AliasRule(match_type="exact", raw_pattern="nurse raw", canonical_id="nurse", canonical_name="Nurse")],
                mirror_base_url="",
            )
            stored_bytes = (file_dir / "question.pdf").read_bytes()

        self.assertEqual(client.downloaded_urls, ["https://example.test/question.pdf"])
        self.assertEqual(stored_bytes, b"%PDF-1.7 original payload")
        self.assertFalse((file_dir / "question.ashx").exists())
        self.assertEqual(
            raw_pages[0].papers[0].mirror_files["question"]["storage_key"],
            "providers/moex/115/115030/101/0101/question.pdf",
        )
        self.assertEqual([paper.file_type for paper in normalized.papers], ["question"])
        self.assertEqual(failures, [])

    def test_sync_exam_pages_reuses_valid_legacy_file_when_scoped_placeholder_is_invalid(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            mirror_root = Path(tmp_dir)
            scoped_dir = mirror_root / "providers" / "moex" / "115" / "115030" / "101" / "0101"
            scoped_dir.mkdir(parents=True, exist_ok=True)
            (scoped_dir / "question.ashx").write_bytes(b"\xef\xbb\xbf<!DOCTYPE html><html>error</html>")
            legacy_dir = mirror_root / "115" / "115030" / "101" / "0101"
            legacy_dir.mkdir(parents=True, exist_ok=True)
            (legacy_dir / "question.pdf").write_bytes(b"%PDF-1.7 cached legacy payload")
            client = QuestionOnlyClient()

            raw_pages, normalized, failures = sync_exam_pages(
                client=client,
                exam_codes=[("115030", 2026)],
                mirror_store=MirrorStore(mirror_root),
                alias_rules=[AliasRule(match_type="exact", raw_pattern="nurse raw", canonical_id="nurse", canonical_name="Nurse")],
                mirror_base_url="",
            )

            promoted_path = scoped_dir / "question.pdf"
            self.assertEqual(client.downloaded_urls, [])
            self.assertTrue(promoted_path.exists())
            self.assertEqual(promoted_path.read_bytes(), b"%PDF-1.7 cached legacy payload")
            self.assertEqual(
                raw_pages[0].papers[0].mirror_files["question"]["storage_key"],
                "providers/moex/115/115030/101/0101/question.pdf",
            )
            self.assertEqual([paper.file_type for paper in normalized.papers], ["question"])
            self.assertEqual(failures, [])

    def test_sync_exam_pages_accepts_question_alt_docx_payloads(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            mirror_root = Path(tmp_dir)
            client = QuestionAltDocxClient()

            raw_pages, normalized, failures = sync_exam_pages(
                client=client,
                exam_codes=[("gsat-115-guozong", 2026)],
                mirror_store=MirrorStore(mirror_root),
                alias_rules=[],
                mirror_base_url="",
            )

        self.assertEqual(
            client.downloaded_urls,
            ["https://example.test/question.pdf", "https://example.test/question-alt.docx"],
        )
        self.assertEqual(len(raw_pages), 1)
        self.assertEqual(sorted(paper.file_type for paper in normalized.papers), ["question", "question_alt"])
        self.assertEqual(
            raw_pages[0].papers[1].mirror_files["question_alt"]["storage_key"],
            "providers/ceec_gsat/115/gsat-115-guozong/115/guozong-02/question_alt.docx",
        )
        self.assertEqual(failures, [])

    def test_sync_exam_pages_accepts_legacy_question_doc_payloads(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            mirror_root = Path(tmp_dir)
            client = QuestionDocClient()

            raw_pages, normalized, failures = sync_exam_pages(
                client=client,
                exam_codes=[("gsat-102-science", 2013)],
                mirror_store=MirrorStore(mirror_root),
                alias_rules=[],
                mirror_base_url="",
            )

        self.assertEqual(
            client.downloaded_urls,
            ["https://example.test/question.doc", "https://example.test/question-alt.doc"],
        )
        self.assertEqual(len(raw_pages), 1)
        self.assertEqual(sorted(paper.file_type for paper in normalized.papers), ["question", "question_alt"])
        self.assertEqual(
            raw_pages[0].papers[0].mirror_files["question"]["storage_key"],
            "providers/ceec_gsat/102/gsat-102-science/102/science-01/question.doc",
        )
        self.assertEqual(
            raw_pages[0].papers[1].mirror_files["question_alt"]["storage_key"],
            "providers/ceec_gsat/102/gsat-102-science/102/science-02/question_alt.doc",
        )
        self.assertEqual(failures, [])

    def test_sync_exam_pages_accepts_legacy_corrected_answer_doc_payloads(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            raw_pages, normalized, failures = sync_exam_pages(
                client=CorrectedAnswerDocClient(),
                exam_codes=[("gsat-92-math", 2003)],
                mirror_store=MirrorStore(Path(tmp_dir)),
                alias_rules=[],
                mirror_base_url="",
            )

        self.assertEqual(
            raw_pages[0].papers[0].mirror_files["corrected_answer"]["storage_key"],
            "providers/ceec_gsat/92/gsat-92-math/92/math-02/corrected_answer.doc",
        )
        self.assertEqual([paper.file_type for paper in normalized.papers], ["corrected_answer"])
        self.assertEqual(failures, [])

    def test_sync_exam_pages_accepts_question_zip_payloads_without_filename_extension(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            mirror_root = Path(tmp_dir)
            client = QuestionArchiveClient(
                url="https://drive.google.com/uc?id=demo&export=download",
                data=b"PK\x03\x04zip payload",
                content_type="application/octet-stream",
                file_name="uc",
            )

            raw_pages, normalized, failures = sync_exam_pages(
                client=client,
                exam_codes=[("cap-115", 2026)],
                mirror_store=MirrorStore(mirror_root),
                alias_rules=[],
                mirror_base_url="",
            )

        self.assertEqual(client.downloaded_urls, ["https://drive.google.com/uc?id=demo&export=download"])
        self.assertEqual(len(raw_pages), 1)
        self.assertEqual([paper.file_type for paper in normalized.papers], ["question"])
        self.assertEqual(
            raw_pages[0].papers[0].mirror_files["question"]["storage_key"],
            "providers/rcpet_cap/115/cap-115/115/english-listening/question.zip",
        )
        self.assertEqual(failures, [])

    def test_sync_exam_pages_accepts_question_rar_payloads(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            mirror_root = Path(tmp_dir)
            client = QuestionArchiveClient(
                url="https://ws.cpc.com.tw/Download.ashx?u=demo&n=demo",
                data=b"Rar!\x1a\x07\x00archive payload",
                content_type="application/octet-stream",
                file_name="old-question.rar",
            )

            raw_pages, normalized, failures = sync_exam_pages(
                client=client,
                exam_codes=[("cap-115", 2026)],
                mirror_store=MirrorStore(mirror_root),
                alias_rules=[],
                mirror_base_url="",
            )

        self.assertEqual(client.downloaded_urls, ["https://ws.cpc.com.tw/Download.ashx?u=demo&n=demo"])
        self.assertEqual([paper.file_type for paper in normalized.papers], ["question"])
        self.assertEqual(
            raw_pages[0].papers[0].mirror_files["question"]["storage_key"],
            "providers/rcpet_cap/115/cap-115/115/english-listening/question.rar",
        )
        self.assertEqual(failures, [])

    def test_sync_exam_pages_accepts_answer_zip_payloads(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            mirror_root = Path(tmp_dir)
            client = AnswerArchiveClient()

            raw_pages, normalized, failures = sync_exam_pages(
                client=client,
                exam_codes=[("teacher-recruit-tainan-115", 2026)],
                mirror_store=MirrorStore(mirror_root),
                alias_rules=[],
                mirror_base_url="",
            )

        self.assertEqual(
            client.downloaded_urls,
            ["https://example.test/reference-answer.zip", "https://example.test/corrected-answer.zip"],
        )
        self.assertEqual(sorted(paper.file_type for paper in normalized.papers), ["answer", "corrected_answer"])
        self.assertEqual(
            raw_pages[0].papers[0].mirror_files["answer"]["storage_key"],
            "providers/teacher_recruit_tainan/115/teacher-recruit-tainan-115/115/elementary-prek-special-ed/answer.zip",
        )
        self.assertEqual(
            raw_pages[0].papers[0].mirror_files["corrected_answer"]["storage_key"],
            "providers/teacher_recruit_tainan/115/teacher-recruit-tainan-115/115/elementary-prek-special-ed/corrected_answer.zip",
        )
        self.assertEqual(failures, [])

    def test_sync_exam_pages_accepts_tocfl_mock_archive_and_spreadsheet_payloads(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            mirror_root = Path(tmp_dir)
            client = TocflMockAssetClient()

            raw_pages, normalized, failures = sync_exam_pages(
                client=client,
                exam_codes=[("tocfl-cert-2026", 2026)],
                mirror_store=MirrorStore(mirror_root),
                alias_rules=[],
                mirror_base_url="",
            )

        self.assertEqual(
            sorted(paper.file_type for paper in normalized.papers),
            ["answer", "listening_audio", "question", "question_alt"],
        )
        self.assertEqual(
            sorted(raw_pages[0].papers[0].mirror_files),
            ["answer", "listening_audio", "question", "question_alt"],
        )
        self.assertEqual(failures, [])


if __name__ == "__main__":
    unittest.main()
