import hashlib
import io
import ssl
import tempfile
import unittest
import zipfile
from datetime import date
from email.message import Message
from pathlib import Path
from unittest.mock import patch
from urllib.parse import quote

from app.providers.base import DownloadedFile
from app.providers.tii_cert.client import (
    DOWNLOAD_CENTER_URL,
    PAPER_SOURCES,
    TiiCertClient,
    _build_ssl_context,
    parse_tii_message_page,
    parse_tii_paper_link,
)
from app.providers.tii_cert.provider import TiiCertProvider
from app.source_revisions import revision_blob_key
from app.storage import MirrorStore
from app.sync import sync_exam_pages

FIXTURES = Path(__file__).parent / "fixtures" / "tii"


def official_listing(url: str) -> str:
    name = (
        "download-center.html"
        if url == DOWNLOAD_CENTER_URL
        else f"message-{url.rsplit('/', 1)[1]}.html"
    )
    return (FIXTURES / name).read_text(encoding="utf-8")


def history_zip(names: list[str]) -> bytes:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        for name in names:
            archive.writestr(name, b"%PDF-1.7\n" + name.encode("utf-8"))
    return buffer.getvalue()


# Native filenames from the official package, retrieved with verified TLS.
HISTORY_NAMES = [
    f"保發中心-防制洗錢測驗歷屆試題/{day}-{number}.{session}{role}"
    " ─ 防制洗錢與打擊資恐法令及實務.pdf"
    for day, sessions in [
        ("107年11月3日", [""]),
        ("108年9月7日", [""]),
        ("109年9月6日", [""]),
        ("110年8月22日", ["上午", "下午"]),
        ("111年 9月4日", [""]),
        ("112年3月11日", [""]),
    ]
    for session in sessions
    for number, role in [(1, "試題"), (2, "解答")]
]


class TiiListingTests(unittest.TestCase):
    def test_default_sync_replaces_brochure_and_retains_original_history_archive(self) -> None:
        client = TiiCertClient()
        raw_archive = history_zip(HISTORY_NAMES)
        native_download = client.download_file

        def download(url: str) -> DownloadedFile:
            if "zip-entry=" in url or "downloadtype=" in url:
                return native_download(url)
            return DownloadedFile(b"%PDF-1.7 paper " + url.encode(), "application/pdf", "paper.pdf")

        with (
            tempfile.TemporaryDirectory() as directory,
            patch.object(client, "_fetch_text", side_effect=official_listing),
            patch.object(client.http, "_request", return_value=(raw_archive, Message(), 200)),
            patch.object(client, "download_file", side_effect=download),
        ):
            store = MirrorStore(Path(directory))
            key = (
                "providers/tii_cert/115/tii-cert-investment-insurance-2026-1/"
                "investment-insurance/main/question.pdf"
            )
            brochure = b"%PDF-1.7 examination brochure"
            store.write_bytes(key, brochure)
            pages, catalog, failures = sync_exam_pages(
                TiiCertProvider(client),
                [("tii-cert-investment-insurance-2026-1", 2026), ("tii-cert-aml-2021-08-22", 2021)],
                store,
                [],
                "",
                download_attachments=False,
            )
            self.assertEqual(failures, [])
            self.assertEqual(len(catalog.papers), 8)
            self.assertTrue((store.root / key).read_bytes().endswith(b"/668"))
            blob = revision_blob_key("tii_cert", hashlib.sha256(brochure).hexdigest(), ".pdf")
            self.assertEqual((store.root / blob).read_bytes(), brochure)
            history = next(page for page in pages if page.year_ad == 2021)
            self.assertEqual(
                (store.root / history.attachments[0].storage_key).read_bytes(), raw_archive
            )

    def test_native_listing_preserves_each_date_section_and_corrected_role(self) -> None:
        entries = [
            entry
            for source in PAPER_SOURCES
            for entry in parse_tii_message_page(official_listing(source.url), source)
        ]
        self.assertEqual(len(entries), 10)
        self.assertEqual(sum(len(entry.links) for entry in entries), 24)
        self.assertEqual({entry.exam_date.year for entry in entries}, {2024, 2025, 2026})
        self.assertNotIn(
            "https://edu.tii.org.tw/exam/users/message_download/665",
            {link.url for entry in entries for link in entry.links},
        )
        investment = next(
            entry
            for entry in entries
            if entry.source_exam_id == "tii-cert-investment-insurance-2026-1"
        )
        self.assertEqual(investment.exam_date, date(2026, 1, 11))
        self.assertEqual(
            {
                (link.subject_code, link.file_type, link.url.rsplit("/", 1)[1])
                for link in investment.links
            },
            {
                ("main", "question", "668"),
                ("main", "answer", "669"),
                ("section-2", "question", "670"),
                ("section-2", "answer", "671"),
            },
        )
        aml = next(entry for entry in entries if entry.source_exam_id == "tii-cert-aml-2025-2")
        self.assertEqual({link.file_type for link in aml.links}, {"question", "corrected_answer"})
        self.assertEqual(
            sum(link.file_type == "corrected_answer" for entry in entries for link in entry.links),
            4,
        )
        policyholder = PAPER_SOURCES[1]
        self.assertEqual(
            parse_tii_message_page(official_listing(policyholder.url), policyholder), []
        )

    def test_dates_come_from_each_native_link_not_surrounding_year_or_revision(self) -> None:
        link = parse_tii_paper_link(
            "114年6月7日-2.解答(115/1/1修正)", "https://edu.tii.org.tw/paper"
        )
        self.assertIsNotNone(link)
        self.assertEqual(link.exam_date, date(2025, 6, 7))
        self.assertEqual(link.file_type, "corrected_answer")
        for label in ["115年度應試簡章", "115年1月11日試題申請表", "115年試題"]:
            if label == "115年試題":
                with self.assertRaisesRegex(ValueError, "date"):
                    parse_tii_paper_link(label, "url")
            else:
                self.assertIsNone(parse_tii_paper_link(label, "url"))

    def test_duplicate_reference_is_collapsed_but_conflicting_sources_are_rejected(self) -> None:
        row = "<tr><td>歷屆試題</td><td>{links}</td></tr>"
        anchor = '<a href="/exam/users/message_download/{id}">114年6月7日-試題</a>'
        first = anchor.format(id=640)
        source = PAPER_SOURCES[2]
        entries = parse_tii_message_page(row.format(links=first + first), source)
        self.assertEqual(len(entries[0].links), 1)
        with self.assertRaisesRegex(ValueError, "conflicting"):
            parse_tii_message_page(row.format(links=first + anchor.format(id=999)), source)

    def test_complete_discovery_keeps_history_and_uses_stable_retained_identifiers(self) -> None:
        client = TiiCertClient()
        raw = history_zip(HISTORY_NAMES)
        with (
            patch.object(client, "_fetch_text", side_effect=official_listing),
            patch.object(client.http, "_request", return_value=(raw, Message(), 200)) as request,
        ):
            self.assertEqual(client.discover_available_years(), list(range(2026, 2017, -1)))
            entries = client._entries()
            self.assertEqual(len(entries), 16)
            self.assertEqual(sum(len(entry.links) for entry in entries), 38)
            page = client.fetch_exam_page("tii-cert-aml-2021-08-22", 2021)
            self.assertEqual(
                {paper.subject_code for paper in page.papers}, {"morning", "afternoon"}
            )
            self.assertEqual(len(page.papers), 4)
            self.assertEqual(len(page.attachments), 1)
            for paper in page.papers:
                self.assertEqual(paper.source_material.kind, "administered")
                self.assertEqual(paper.source_material.date.year_ad, 2021)
                downloaded = client.download_file(next(iter(paper.files.values())))
                self.assertTrue(downloaded.data.startswith(b"%PDF-"))
            self.assertEqual(
                client.download_file(page.attachments[0].download_url_source).data, raw
            )
            self.assertEqual(request.call_count, 1)
            self.assertTrue(
                request.call_args.args[0].startswith("https://edu.tii.org.tw/home/download.php?")
            )
            self.assertNotIn("/../", request.call_args.args[0])
            self.assertEqual(
                client.build_discovery_exam_url(page.source_exam_id, 2021), DOWNLOAD_CENTER_URL
            )
            self.assertEqual(
                client.build_discovery_exam_url("tii-cert-aml-2025-2", 2025), PAPER_SOURCES[2].url
            )
            with self.assertRaises(ValueError):
                client.build_discovery_exam_url("unknown", 2026)
            current = client.fetch_exam_page("tii-cert-investment-insurance-2026-1", 2026)
            self.assertEqual(len(current.papers), 4)
            self.assertTrue(all(p.source_material.date.year_ad == 2026 for p in current.papers))

    def test_intermediate_does_not_replace_roots_or_disable_full_chain_and_hostname_verification(
        self,
    ) -> None:
        context = _build_ssl_context()
        self.assertTrue(context.check_hostname)
        self.assertEqual(context.verify_mode, ssl.CERT_REQUIRED)
        self.assertFalse(context.verify_flags & ssl.VERIFY_X509_PARTIAL_CHAIN)
        self.assertGreater(context.cert_store_stats()["x509_ca"], 1)

    def test_unsafe_duplicate_and_corrupt_archives_fail_before_cache(self) -> None:
        for names in [
            ["../paper.pdf"],
            ["/paper.pdf"],
            ["C:paper.pdf"],
            ["a\\paper.pdf"],
            ["paper.pdf", "PAPER.pdf"],
        ]:
            client = TiiCertClient()
            with (
                self.subTest(names=names),
                patch.object(
                    client.http, "_request", return_value=(history_zip(names), Message(), 200)
                ),
            ):
                with self.assertRaises(ValueError):
                    client._archive("https://edu.tii.org.tw/history.zip")
                self.assertEqual(client._archive_payloads, {})
        client = TiiCertClient()
        with patch.object(client.http, "_request", return_value=(b"not a ZIP", Message(), 200)):
            with self.assertRaises(zipfile.BadZipFile):
                client._archive("https://edu.tii.org.tw/history.zip")
            self.assertEqual(client._archives, {})

    def test_member_locator_names_are_exact_and_preserve_pdf_filename(self) -> None:
        name = HISTORY_NAMES[0]
        client = TiiCertClient()
        with patch.object(
            client.http, "_request", return_value=(history_zip([name]), Message(), 200)
        ):
            url = "https://edu.tii.org.tw/history.zip#zip-entry=" + quote(name, safe="")
            file = client.download_file(url)
            self.assertEqual(file.file_name, name.rsplit("/", 1)[1])
            self.assertEqual(client.head(url).content_length, len(file.data))
            with self.assertRaises(ValueError):
                client.download_file("https://edu.tii.org.tw/history.zip#other=paper.pdf")

    def test_raw_utf8_http_filename_is_recovered_without_changing_payload(self) -> None:
        headers = Message()
        name = "115年1月11日第一節試題.pdf"
        headers["Content-Disposition"] = (
            'attachment; filename="' + name.encode("utf-8").decode("latin-1") + '"'
        )
        headers["Content-Type"] = "application/pdf"
        client = TiiCertClient()
        with patch.object(client.http, "_request", return_value=(b"%PDF-1.7", headers, 200)):
            file = client.download_file("https://edu.tii.org.tw/exam/users/message_download/668")
        self.assertEqual(file.file_name, name)
        self.assertEqual(file.data, b"%PDF-1.7")
