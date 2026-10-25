import unittest
from unittest.mock import patch

from app.models import NormalizedCatalog
from app.normalizer import normalize_papers
from app.providers.base import SourceProvider
from app.providers.registry import get_provider
from app.providers.special_admission.client import (
    SpecialAdmissionClient,
    parse_available_years,
    parse_question_page,
)

QUESTION_HTML = """
<html><body>
<select id="year" name="year">
  <option value="">請選擇</option>
  <option value="115">115</option>
  <option value="114" selected>114</option>
</select>
<h2>114學年度身心障礙學生升學大專校院甄試 (大學組)</h2>
<table>
  <tr><th>年度</th><th>學制</th><th>類組</th><th>考科名稱</th><th>附件</th></tr>
  <tr>
    <td>114</td><td>大學組</td><td>共同</td><td>國文</td>
    <td>
      <a href="/EnableSys/file/A2301國文試題.pdf">A2301國文試題.pdf</a>
      <a href="/EnableSys/file/A2301國文參考答案.pdf">A2301國文參考答案.pdf</a>
    </td>
  </tr>
  <tr>
    <td>114</td><td>四技二專組</td><td>共同</td><td>國文</td>
    <td><a href="/EnableSys/file/C2301試題-共同-國文.pdf">C2301試題-共同-國文.pdf</a></td>
  </tr>
</table>
</body></html>
"""


class SpecialAdmissionParserTests(unittest.TestCase):
    def test_historical_mathematics_keeps_native_names_and_both_file_roles(self) -> None:
        rows = []
        for subject in ("數學甲", "數學乙"):
            rows.append(
                f"<tr><td>110</td><td>大學組</td><td>共同</td><td>{subject}</td>"
                f'<td><a href="/{subject}-question.pdf">{subject}試題.pdf</a>'
                f'<a href="/{subject}-answer.pdf">{subject}參考答案.pdf</a></td></tr>'
            )
        html = "<table>" + "".join(rows) + "</table>"
        papers = parse_question_page(html, "https://cis.ncu.edu.tw/EnableSys/")

        self.assertEqual(
            [(p.subject_name_raw, p.subject_code) for p in papers],
            [("數學甲", "math-jia"), ("數學乙", "math-yi")],
        )
        self.assertTrue(all(set(p.files) == {"question", "answer"} for p in papers))

    def test_historical_mathematics_does_not_merge_with_new_curriculum_or_other_tracks(
        self,
    ) -> None:
        rows = []
        for year, school, subject in (
            (110, "大學組", "數學甲"),
            (110, "大學組", "數學乙"),
            (111, "大學組", "數學A"),
            (111, "大學組", "數學B"),
            (110, "四技二專組", "數學(A)"),
            (110, "二技組", "數學甲"),
        ):
            rows.append(
                f"<tr><td>{year}</td><td>{school}</td><td>共同</td><td>{subject}</td>"
                f'<td><a href="/{year}-{school}-{subject}.pdf">{subject}試題.pdf</a></td></tr>'
            )
        papers = parse_question_page(
            "<table>" + "".join(rows) + "</table>", "https://cis.ncu.edu.tw/EnableSys/"
        )
        self.assertEqual(
            [(p.subject_name_raw, p.subject_code) for p in papers],
            [
                ("數學甲", "math-jia"),
                ("數學乙", "math-yi"),
                ("數學A", "math-a"),
                ("數學B", "math-b"),
            ],
        )

    def test_parse_available_years_reads_select_options(self) -> None:
        self.assertEqual(parse_available_years(QUESTION_HTML), [2026, 2025])

    def test_parse_question_page_keeps_university_common_subject_assets(self) -> None:
        papers = parse_question_page(
            QUESTION_HTML,
            "https://cis.ncu.edu.tw/EnableSys/admissionInfo/examInfo/question?year=114",
        )

        self.assertEqual(len(papers), 1)
        self.assertEqual(papers[0].category_raw, "身心障礙學生升學大專校院甄試")
        self.assertEqual(papers[0].category_code, "university-common")
        self.assertEqual(papers[0].subject_code, "chinese")
        self.assertEqual(
            papers[0].files["question"], "https://cis.ncu.edu.tw/EnableSys/file/A2301國文試題.pdf"
        )
        self.assertEqual(
            papers[0].files["answer"], "https://cis.ncu.edu.tw/EnableSys/file/A2301國文參考答案.pdf"
        )

    def test_fetch_exam_page_turns_year_into_source_page(self) -> None:
        with patch.object(SpecialAdmissionClient, "_fetch_text", return_value=QUESTION_HTML):
            client = SpecialAdmissionClient()
            page = client.fetch_exam_page("special-admission-114", 2025)

        self.assertEqual(page.provider_id, "special_admission")
        self.assertEqual(page.source_exam_id, "special-admission-114")
        self.assertEqual(page.exam_name_raw, "114學年度身心障礙學生升學大專校院甄試")
        self.assertEqual(len(page.papers), 1)

    def test_discovery_builders_reuse_one_listing_fetch(self) -> None:
        with patch.object(
            SpecialAdmissionClient, "_fetch_text", return_value=QUESTION_HTML
        ) as fetch:
            client = SpecialAdmissionClient()

            self.assertEqual(client.discover_available_years(), [2026, 2025])
            self.assertEqual(
                client.build_discovery_year_url(2025),
                "https://cis.ncu.edu.tw/EnableSys/admissionInfo/examInfo/question?year=114",
            )
            self.assertEqual(
                client.build_discovery_exam_url("special-admission-114", 2025),
                "https://cis.ncu.edu.tw/EnableSys/admissionInfo/examInfo/question?year=114",
            )
            self.assertEqual(client.discover_exams(2025)[0].code, "special-admission-114")

        self.assertEqual(fetch.call_count, 1)

    def test_discovery_builders_reject_unknown_year_and_exam(self) -> None:
        with patch.object(SpecialAdmissionClient, "_fetch_text", return_value=QUESTION_HTML):
            client = SpecialAdmissionClient()
            with self.assertRaisesRegex(ValueError, "Unknown special-admission year"):
                client.build_discovery_year_url(2024)
            with self.assertRaisesRegex(ValueError, "Unknown special-admission exam"):
                client.build_discovery_exam_url("special-admission-113", 2025)

    def test_registry_returns_special_admission_provider(self) -> None:
        provider = get_provider("special_admission")

        self.assertIsInstance(provider, SourceProvider)
        self.assertEqual(provider.provider_id, "special_admission")

    def test_special_admission_normalization_uses_stable_canonical_bundle_identity(self) -> None:
        with patch.object(SpecialAdmissionClient, "_fetch_text", return_value=QUESTION_HTML):
            client = SpecialAdmissionClient()
            page = client.fetch_exam_page("special-admission-114", 2025)

        mirror_metadata = {}
        for paper in page.papers:
            for file_type in paper.files:
                mirror_metadata[(paper.category_code, paper.subject_code, file_type)] = {
                    "checksum": f"{paper.subject_code}-{file_type}",
                    "storage_key": (
                        f"providers/special_admission/114/{page.source_exam_id}/"
                        f"{paper.category_code}/{paper.subject_code}/{file_type}.pdf"
                    ),
                }

        normalized = normalize_papers(
            source_exam_id=page.source_exam_id,
            year_ad=page.year_ad,
            exam_name_raw=page.exam_name_raw,
            papers=page.papers,
            alias_rules=[],
            mirror_base_url="",
            mirror_metadata=mirror_metadata,
        )

        self.assertIsInstance(normalized, NormalizedCatalog)
        self.assertEqual({paper.canonical_id for paper in normalized.papers}, {"special-admission"})
        self.assertEqual(
            {paper.canonical_name for paper in normalized.papers}, {"身心障礙學生升學大專校院甄試"}
        )
        self.assertEqual(normalized.review_queue, [])


if __name__ == "__main__":
    unittest.main()
