"""Shared attachment rules without mixing the two recruitment programmes."""

import base64
import unittest
from urllib.parse import quote

from app.providers.taipei_downloads import (
    PaperDownload,
    download_name,
    group_paper_files,
    official_pdf_downloads,
    request_url,
)


def named_url(name: str, *, host: str = "www-ws.gov.taipei", scheme: str = "https") -> str:
    encoded = quote(base64.b64encode(name.encode()).decode(), safe="")
    return f"{scheme}://{host}/Download.ashx?u=opaque&n={encoded}&icon=..pdf"


class TaipeiAttachmentTests(unittest.TestCase):
    def test_repeated_links_collapse_without_discarding_distinct_roles_or_subjects(self) -> None:
        question = PaperDownload("國文", "chinese", "question", "native-question")
        answer = PaperDownload("國文", "chinese", "answer", "native-answer")
        other = PaperDownload("英語", "english", "question_answer", "native-combined")
        self.assertEqual(
            group_paper_files([question, answer, question, other, answer]),
            {
                ("chinese", "國文"): {"question": "native-question", "answer": "native-answer"},
                ("english", "英語"): {"question_answer": "native-combined"},
            },
        )

    def test_competing_native_urls_never_select_the_first_or_last_link(self) -> None:
        for role in ["question", "answer", "question_answer", "corrected_answer"]:
            for urls in [("first", "second"), ("second", "first")]:
                with self.subTest(role=role, urls=urls):
                    with self.assertRaisesRegex(ValueError, "Competing Taipei downloads"):
                        group_paper_files(PaperDownload("國文", "chinese", role, u) for u in urls)

    def test_native_name_and_unpadded_names_round_trip(self) -> None:
        for name in ["國文試題.pdf", "1基礎類科知能_含答案.pdf", "家政-雙語教學專長答案.pdf"]:
            url = named_url(name)
            with self.subTest(name=name):
                self.assertEqual(download_name(url), name)
                self.assertEqual(download_name(url.replace("%3D", "")), name)

    def test_legacy_source_path_supplies_a_missing_display_name(self) -> None:
        original = "/001/Upload/342/relfile/10173/native/原始試題.pdf"
        encoded = quote(base64.b64encode(original.encode()).decode(), safe="")
        self.assertEqual(
            download_name(f"https://www-ws.gov.taipei/Download.ashx?u={encoded}"), "原始試題.pdf"
        )

    def test_plain_file_path_is_a_name_fallback(self) -> None:
        self.assertEqual(download_name("https://www-ws.gov.taipei/原始試題.pdf"), "原始試題.pdf")

    def test_only_native_https_attachment_resources_are_collected(self) -> None:
        valid = named_url("國文試題.pdf")
        urls = [
            valid,
            named_url("國文試題.pdf", host="other.example"),
            named_url("國文試題.pdf", host="www-ws.gov.taipei.other.example"),
            named_url("國文試題.pdf", scheme="http"),
            valid.replace("/Download.ashx", "/NotDownload.ashx"),
            named_url("試題答案疑義表.odt"),
        ]
        html = "".join(f'<a href="{url}"><span>下載</span></a>' for url in urls)
        self.assertEqual(
            list(official_pdf_downloads(html, page_url="https://www.doe.gov.taipei/")),
            [("國文試題.pdf", valid)],
        )

    def test_resource_case_variants_preserve_the_original_source_url(self) -> None:
        url = named_url("國文答案.PDF", host="WWW-WS.GOV.TAIPEI").replace(
            "Download.ashx", "download.ASHX"
        )
        self.assertEqual(
            list(
                official_pdf_downloads(
                    f'<a href="{url}">PDF</a>', page_url="https://www.gov.taipei/"
                )
            ),
            [("國文答案.PDF", url)],
        )

    def test_native_request_escaping_does_not_double_encode_the_path_or_query(self) -> None:
        url = "https://www-ws.gov.taipei/原始檔案.pdf?n=5ZyL5paH%3D%3D"
        encoded = request_url(url)
        self.assertNotIn("原始", encoded)
        self.assertIn("?n=5ZyL5paH%3D%3D", encoded)
        self.assertEqual(request_url(encoded), encoded)
