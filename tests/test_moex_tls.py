import ssl
import unittest
from unittest.mock import MagicMock, patch

import app.providers.moex.client as moex_client


class FakeTextResponse:
    def __init__(self, body: bytes, content_type: str) -> None:
        self._body = body
        self.headers = {"Content-Type": content_type}

    def read(self) -> bytes:
        return self._body

    def __enter__(self) -> "FakeTextResponse":
        return self

    def __exit__(self, exc_type, exc, tb) -> bool:
        return False


class CrawlerTlsTests(unittest.TestCase):
    def test_build_ssl_context_loads_bundled_twca_chain(self) -> None:
        fake_context = MagicMock()

        with patch.object(moex_client.ssl, "create_default_context", return_value=fake_context) as create_default_context:
            context = moex_client._build_ssl_context()

        self.assertIs(context, fake_context)
        create_default_context.assert_called_once_with()
        fake_context.load_verify_locations.assert_called_once_with(cafile=str(moex_client.TWCA_CA_BUNDLE_PATH))

    def test_fetch_text_uses_client_ssl_context(self) -> None:
        response = MagicMock()
        response.read.return_value = b"demo"
        response.__enter__.return_value = response
        response.__exit__.return_value = False
        ssl_context = ssl.create_default_context()

        with patch.object(moex_client, "urlopen", return_value=response) as urlopen_mock:
            client = moex_client.MoexClient(ssl_context=ssl_context)
            text = client._fetch_text("https://example.test")

        self.assertEqual(text, "demo")
        _, kwargs = urlopen_mock.call_args
        self.assertIs(kwargs["context"], ssl_context)

    def test_fetch_text_uses_http_charset_when_present(self) -> None:
        response = FakeTextResponse(
            body="護理行政".encode("big5"),
            content_type="text/html; charset=big5",
        )

        with patch.object(moex_client, "urlopen", return_value=response):
            client = moex_client.MoexClient(ssl_context=object())
            text = client._fetch_text("https://example.test")

        self.assertEqual(text, "護理行政")


if __name__ == "__main__":
    unittest.main()
