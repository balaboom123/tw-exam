import unittest

from app.providers.base import SourceProvider
from app.providers.registry import get_provider


class FinancialCertRegistryTests(unittest.TestCase):
    def test_sfi_cert_registered(self) -> None:
        provider = get_provider("sfi_cert")
        self.assertIsInstance(provider, SourceProvider)
        self.assertEqual(provider.provider_id, "sfi_cert")


if __name__ == "__main__":
    unittest.main()
