import ssl
import re
import unittest
from email.message import Message
from pathlib import Path
from urllib.error import HTTPError
from unittest.mock import Mock, patch

from app.providers.ceec_gsat.client import CeecGsatClient
from app.providers.http import Http, ad, links, roc
from app.providers.post_recruit.client import PostRecruitClient
from app.providers.sfi_cert.client import SfiCertClient
from app.providers.special_admission.client import SpecialAdmissionClient
from app.providers.tabf_cert.client import TabfCertClient
from app.providers.tcte_tve.client import TcteTveClient
from app.providers.tocfl_cert.client import TocflCertClient
from app.providers.twc_recruit.client import TwcRecruitClient


class Response:
    def __init__(self, data: bytes, *, status: int = 200, headers: dict[str, str] | None = None):
        self.data = data
        self.status = status
        self.headers = Message()
        for name, value in (headers or {}).items():
            self.headers[name] = value

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False

    def read(self):
        return self.data


class ProviderHttpTests(unittest.TestCase):
    def test_default_headers_are_sent_with_every_request(self) -> None:
        client = Http(
            "sample", user_agent="Browser/1.0", headers={"Referer": "https://example.test/"},
            max_attempts=1,
        )
        with patch("app.providers.http.urlopen", return_value=Response(b"ok")) as open_url:
            client.get_text("https://example.test/a")
            client.post_form("https://example.test/b", {"k": "v"})
        for call in open_url.call_args_list:
            request = call.args[0]
            self.assertEqual(request.get_header("Referer"), "https://example.test/")
            self.assertEqual(request.get_header("User-agent"), "Browser/1.0")
        self.assertEqual(
            open_url.call_args_list[1].args[0].get_header("Content-type"),
            "application/x-www-form-urlencoded",
        )

    def test_migrated_clients_preserve_request_methods_headers_and_filenames(self) -> None:
        url = "https://example.test/files/question%20paper.pdf"
        for client_type in (
            SfiCertClient, CeecGsatClient, TcteTveClient, TocflCertClient,
            PostRecruitClient, TabfCertClient, SpecialAdmissionClient,
        ):
            with self.subTest(client=client_type.__name__):
                client = client_type()
                with patch("app.providers.http.urlopen", return_value=Response(b"hello")) as open_url:
                    self.assertEqual(client._fetch_text(url), "hello")
                    request = open_url.call_args.args[0]
                    self.assertEqual(request.get_method(), "GET")
                    self.assertIn("mirror/1.0", request.get_header("User-agent"))
                    self.assertEqual(open_url.call_args.kwargs["timeout"], 60)

                head_response = Response(b"", headers={"Content-Length": "12"})
                with patch("app.providers.http.urlopen", return_value=head_response) as open_url:
                    self.assertEqual(client.head(url).content_length, 12)
                    self.assertEqual(open_url.call_args.args[0].get_method(), "HEAD")

                download_response = Response(b"pdf", headers={
                    "Content-Type": "application/pdf",
                    "Content-Disposition": 'attachment; filename="other.pdf"',
                })
                with patch("app.providers.http.urlopen", return_value=download_response) as open_url:
                    downloaded = client.download_file(url)
                    self.assertEqual(downloaded.file_name, "question paper.pdf")
                    self.assertEqual(downloaded.data, b"pdf")
                    self.assertEqual(open_url.call_args.kwargs["timeout"], 120)

    def test_twc_transport_keeps_download_url_guard(self) -> None:
        client = TwcRecruitClient()
        url = "https://www.water.gov.tw/ch/ServerFile/Get/12345678-1234-1234-1234-123456789abc?nodeId=715"
        with patch("app.providers.http.urlopen", return_value=Response(b"page")) as open_url:
            self.assertEqual(client._fetch_text("https://www.water.gov.tw/ch/Subject/Detail/59619"), "page")
            self.assertEqual(open_url.call_args.args[0].get_method(), "GET")

        with patch("app.providers.http.urlopen", return_value=Response(b"", headers={"Content-Length": "4"})) as open_url:
            self.assertEqual(client.head(url).content_length, 4)
            self.assertEqual(open_url.call_args.args[0].get_method(), "HEAD")

        with patch("app.providers.http.urlopen", return_value=Response(b"data")) as open_url:
            self.assertEqual(client.download_file(url).file_name, "12345678-1234-1234-1234-123456789abc")
            self.assertEqual(open_url.call_args.kwargs["timeout"], 120)

        with self.assertRaises(ValueError):
            client.download_file("https://example.test/paper.zip")

    def test_retry_after_and_transient_errors(self) -> None:
        headers = Message()
        headers["Retry-After"] = "2"
        error = HTTPError("https://example.test", 503, "busy", headers, None)
        client = Http("sample", max_attempts=2)
        with patch("app.providers.http.urlopen", side_effect=[error, Response(b"ok")]) as open_url, \
                patch("app.providers.http.time.sleep") as sleep:
            self.assertEqual(client.get_text("https://example.test"), "ok")
        self.assertEqual(open_url.call_count, 2)
        sleep.assert_called_once_with(2.0)

    def test_charset_form_filename_and_links(self) -> None:
        client = Http("sample", max_attempts=1)
        raw = '<meta charset="big5">測試'.encode("big5")
        with patch("app.providers.http.urlopen", return_value=Response(raw)):
            self.assertIn("測試", client.get_text("https://example.test"))

        with patch("app.providers.http.urlopen", return_value=Response(b"done")) as open_url:
            self.assertEqual(client.post_form("https://example.test", {"key": "a b"}), "done")
            request = open_url.call_args.args[0]
            self.assertEqual(request.get_method(), "POST")
            self.assertEqual(request.data, b"key=a+b")

        archive_response = Response(b"zip", headers={
            "Content-Disposition": 'attachment; filename="../paper.zip"',
        })
        with patch("app.providers.http.urlopen", return_value=archive_response):
            self.assertEqual(client.download("https://example.test/file").file_name, "paper.zip")
        self.assertEqual(links('<a href="/papers"> A &amp; B </a>', "https://example.test/archive"),
                         [("A & B", "https://example.test/papers")])
        self.assertEqual(ad(roc(2026)), 2026)

    def test_custom_ssl_context_is_used(self) -> None:
        context = ssl.create_default_context()
        client = Http("sample", ssl_context=context, max_attempts=1)
        with patch("app.providers.http.urlopen", return_value=Response(b"ok")) as open_url:
            self.assertEqual(client.get_text("https://example.test"), "ok")
        self.assertIs(open_url.call_args.kwargs["context"], context)

    def test_cookie_session_and_request_pacing(self) -> None:
        opener = Mock()
        opener.open.return_value = Response(b"ok")
        with patch("app.providers.http.build_opener", return_value=opener):
            client = Http("sample", cookies=True, min_interval=0.5, max_attempts=1)
        with patch("app.providers.http.time.monotonic", return_value=100.0), \
                patch("app.providers.http.time.sleep") as sleep:
            self.assertEqual(client.get_text("https://example.test"), "ok")
            self.assertEqual(client.get_text("https://example.test"), "ok")
        self.assertEqual(opener.open.call_count, 2)
        sleep.assert_called_once_with(0.5)


# Modules that keep their own network code, with the reason. Everything else
# under app/providers must go through app.providers.http.Http.
TRANSPORT_EXCEPTIONS = {
    "hce_archive.py": "own filename fallback and per-config crawl delay",
    "moex/client.py": "pinned TWCA context with bespoke charset and filename parsing",
    "rcpet_cap/client.py": "Google Drive confirmation flow reads the redirected URL",
    "teacher_qual/client.py": "a fresh cookie session per listing request",
    "wdasec_skill/client.py": "ASP.NET view-state session with strict decoding",
}
# Not migrated yet; remove one entry per migration commit.
PENDING_TRANSPORT_MIGRATIONS = {
    "cpc_recruit/client.py",
    "hakka_cert/client.py",
    "moea_recruit/client.py",
    "taigi_cert/client.py",
    "taipower_recruit/client.py",
    "taisugar_recruit/client.py",
    "teacher_recruit_central_alliance/client.py",
    "teacher_recruit_kaohsiung/client.py",
    "teacher_recruit_newtaipei/client.py",
    "teacher_recruit_tainan/client.py",
    "teacher_recruit_taipei_elementary/client.py",
    "teacher_recruit_taipei_junior/client.py",
    "teacher_recruit_taoyuan_elementary/client.py",
    "tii_cert/client.py",
    "tqc_cert/client.py",
}


def test_only_listed_modules_open_network_connections_themselves():
    providers = Path(__file__).resolve().parents[1] / "app" / "providers"
    direct = sorted(
        path.relative_to(providers).as_posix()
        for path in providers.rglob("*.py")
        if path.name != "http.py"
        and re.search(r"\burlopen\b|\bbuild_opener\b", path.read_text(encoding="utf-8"))
    )
    assert direct == sorted({*TRANSPORT_EXCEPTIONS, *PENDING_TRANSPORT_MIGRATIONS})
