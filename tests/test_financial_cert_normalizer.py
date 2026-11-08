import unittest

from app.normalizer import _derive_canonical


class SfiCertCanonicalTests(unittest.TestCase):
    def _derive(self, source_exam_id: str) -> tuple[str, str, str, bool]:
        return _derive_canonical(source_exam_id, "", "", 2025, [])

    def test_securities_dealer(self) -> None:
        cid, cname, _, review = self._derive("sfi-cert-securities-dealer-2025-03")
        self.assertEqual(cid, "sfi-securities-dealer")
        self.assertEqual(cname, "證券商業務員")
        self.assertFalse(review)

    def test_senior_securities_dealer(self) -> None:
        cid, cname, _, review = self._derive("sfi-cert-senior-securities-dealer-2025-01")
        self.assertEqual(cid, "sfi-senior-securities-dealer")
        self.assertEqual(cname, "證券商高級業務員")
        self.assertFalse(review)

    def test_futures_dealer(self) -> None:
        cid, cname, _, review = self._derive("sfi-cert-futures-dealer-2025-06")
        self.assertEqual(cid, "sfi-futures-dealer")
        self.assertEqual(cname, "期貨商業務員")
        self.assertFalse(review)

    def test_securities_analyst(self) -> None:
        cid, cname, _, review = self._derive("sfi-cert-securities-analyst-2025-01")
        self.assertEqual(cid, "sfi-securities-analyst")
        self.assertEqual(cname, "證券投資分析人員")
        self.assertFalse(review)

    def test_sitca(self) -> None:
        cid, cname, _, review = self._derive("sfi-cert-sitca-2025-03")
        self.assertEqual(cid, "sfi-sitca")
        self.assertEqual(cname, "投信投顧業務員")
        self.assertFalse(review)

    def test_corporate_internal_control(self) -> None:
        cid, cname, _, review = self._derive("sfi-cert-corporate-internal-control-2025-01")
        self.assertEqual(cid, "sfi-corporate-internal-control")
        self.assertEqual(cname, "企業內部控制基本能力測驗")
        self.assertFalse(review)

    def test_bills_dealer(self) -> None:
        cid, cname, _, review = self._derive("sfi-cert-bills-dealer-2025-06")
        self.assertEqual(cid, "sfi-bills-dealer")
        self.assertEqual(cname, "票券商業務人員")
        self.assertFalse(review)

    def test_stock_affairs(self) -> None:
        cid, cname, _, review = self._derive("sfi-cert-stock-affairs-2025-03")
        self.assertEqual(cid, "sfi-stock-affairs")
        self.assertEqual(cname, "股務人員")
        self.assertFalse(review)

    def test_asset_securitization(self) -> None:
        cid, cname, _, review = self._derive("sfi-cert-asset-securitization-2025-01")
        self.assertEqual(cid, "sfi-asset-securitization")
        self.assertEqual(cname, "資產證券化基本能力測驗")
        self.assertFalse(review)

    def test_business_ethics(self) -> None:
        cid, cname, _, review = self._derive("sfi-cert-business-ethics-2025-06")
        self.assertEqual(cid, "sfi-business-ethics")
        self.assertEqual(cname, "工商倫理測驗")
        self.assertFalse(review)


if __name__ == "__main__":
    unittest.main()
