import unittest

from app.providers.base import SourceProvider
from app.providers.registry import get_provider


class LanguageCertRegistryTests(unittest.TestCase):
    def test_hakka_cert_registered(self) -> None:
        provider = get_provider("hakka_cert")
        self.assertIsInstance(provider, SourceProvider)
        self.assertEqual(provider.provider_id, "hakka_cert")

    def test_taigi_cert_registered(self) -> None:
        provider = get_provider("taigi_cert")
        self.assertIsInstance(provider, SourceProvider)
        self.assertEqual(provider.provider_id, "taigi_cert")


if __name__ == "__main__":
    unittest.main()
