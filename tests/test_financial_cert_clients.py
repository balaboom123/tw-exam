import unittest
from unittest.mock import patch

from app.providers.sfi_cert.client import SfiCertClient, parse_sfi_archive
from app.providers.tabf_cert.client import TabfCertClient, classify_tabf_certificate, parse_tabf_history_links


SFI_ARCHIVE_HTML = """
<html><body>
<p>前一次：115年度第1次筆試測驗試題</p>
<p>前二次：114年度第3次筆試測驗試題</p>
<table>
  <tr><td>證券商業務員</td>
    <td><a href="https://examweb.sfi.org.tw/Download/01/03.pdf">前一次</a></td>
    <td><a href="https://examweb.sfi.org.tw/Download/01/03a.pdf">解答</a></td>
    <td><a href="https://examweb.sfi.org.tw/Download/02/03.pdf">前二次</a></td>
    <td><a href="https://examweb.sfi.org.tw/Download/02/03a.pdf">解答</a></td>
  </tr>
  <tr><td>期貨商業務員</td>
    <td><a href="https://examweb.sfi.org.tw/Download/01/06.pdf">前一次</a></td>
    <td><a href="https://examweb.sfi.org.tw/Download/01/06a.pdf">解答</a></td>
    <td><a href="https://examweb.sfi.org.tw/Download/02/06.pdf">前二次</a></td>
    <td><a href="https://examweb.sfi.org.tw/Download/02/06a.pdf">解答</a></td>
  </tr>
</table>
</body></html>
"""


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


class SfiCertClientTests(unittest.TestCase):
    def test_parse_sfi_archive_groups_question_and_answer_by_round(self) -> None:
        entries = parse_sfi_archive(SFI_ARCHIVE_HTML)

        self.assertEqual(len(entries), 4)
        first = entries[0]
        self.assertEqual(first.slug, "securities-dealer")
        self.assertEqual(first.year_roc, 115)
        self.assertEqual(first.round_no, 1)
        self.assertEqual(first.files["question"], "https://examweb.sfi.org.tw/Download/01/03.pdf")
        self.assertEqual(first.files["answer"], "https://examweb.sfi.org.tw/Download/01/03a.pdf")

    def test_sfi_discovery_and_fetch_page(self) -> None:
        with patch.object(SfiCertClient, "_fetch_text", return_value=SFI_ARCHIVE_HTML):
            client = SfiCertClient()
            self.assertEqual(client.discover_available_years(), [2026, 2025])
            exams = client.discover_exams(2026)
            page = client.fetch_exam_page("sfi-cert-securities-dealer-2026-1", 2026)

        self.assertIn("sfi-cert-securities-dealer-2026-1", [exam.code for exam in exams])
        self.assertEqual(page.provider_id, "sfi_cert")
        self.assertEqual(page.source_exam_id, "sfi-cert-securities-dealer-2026-1")
        self.assertEqual(page.papers[0].files["question"], "https://examweb.sfi.org.tw/Download/01/03.pdf")
        self.assertEqual(page.papers[0].files["answer"], "https://examweb.sfi.org.tw/Download/01/03a.pdf")


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
