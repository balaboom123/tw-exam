import unittest
from unittest.mock import patch

from app.providers.tabf_cert.client import TabfCertClient, classify_tabf_certificate, parse_tabf_history_links


TABF_INDEX_HTML = """
<html><body>
<a href="LicenseHistoryExam.aspx?PHID=424">第 45 屆試題</a>
<a href="LicenseHistoryExam.aspx?PHID=450">114年第2次測驗試題</a>
</body></html>
"""

TABF_TRUST_HTML = """
<html><body>
<h1>歷屆試題</h1>
<a href="LicenseHistoryExam.aspx?PHID=424">第 45 屆試題</a>
<div>
  <a href="https://service.tabf.org.tw/BEExam/Doc/ExamHistoryEdit/624445-1.pdf">信託法規</a>
  <a href="https://service.tabf.org.tw/BEExam/Doc/ExamHistoryEdit/624445-2.pdf">信託實務</a>
  <a href="https://service.tabf.org.tw/BEExam/Doc/ExamHistoryEdit/624445-3.pdf">答案</a>
</div>
</body></html>
"""


class TabfCertClientTests(unittest.TestCase):
    def test_parse_tabf_history_links_extracts_phids(self) -> None:
        links = parse_tabf_history_links(TABF_INDEX_HTML, 2026)

        self.assertEqual([link.phid for link in links], ["424", "450"])
        self.assertEqual(links[0].year_ad, 2026)
        self.assertEqual(links[1].year_ad, 2025)

    def test_classify_tabf_certificate_from_subjects(self) -> None:
        slug, name = classify_tabf_certificate(["信託法規", "信託實務", "洗錢防制"])

        self.assertEqual(slug, "trust-business")
        self.assertEqual(name, "信託業業務人員")

    def test_tabf_discovery_and_fetch_page(self) -> None:
        def fake_fetch(url: str) -> str:
            if "PHID=424" in url:
                return TABF_TRUST_HTML
            return TABF_INDEX_HTML

        with patch.object(TabfCertClient, "_fetch_text", side_effect=fake_fetch):
            client = TabfCertClient()
            exams = client.discover_exams(2026)
            page = client.fetch_exam_page("tabf-cert-phid-424", 2026)

        self.assertEqual(exams[0].code, "tabf-cert-phid-424")
        self.assertEqual(page.provider_id, "tabf_cert")
        self.assertTrue(page.source_exam_id.startswith("tabf-cert-trust-business-"))
        self.assertEqual(len(page.papers), 3)
        self.assertEqual(page.papers[0].files["question"], "https://service.tabf.org.tw/BEExam/Doc/ExamHistoryEdit/624445-1.pdf")
        self.assertEqual(page.papers[2].files["answer"], "https://service.tabf.org.tw/BEExam/Doc/ExamHistoryEdit/624445-3.pdf")


if __name__ == "__main__":
    unittest.main()
