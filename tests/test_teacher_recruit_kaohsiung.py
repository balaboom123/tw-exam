"""Current official-bureau acquisition and drift guards for Kaohsiung."""

import html
import unittest
from dataclasses import replace
from unittest.mock import Mock

from app.providers.base import DownloadedFile
from app.providers.teacher_recruit_kaohsiung.client import (
    KaohsiungTeacherRecruitClient,
    search_url,
    validate_notice_html,
)
from app.providers.teacher_recruit_kaohsiung.evidence import annual_exams
from app.providers.teacher_recruit_kaohsiung.provider import KaohsiungTeacherRecruitProvider


def notice_html(notice):
    published = notice.publication_date.split("-")
    year, month, day = map(int, published)
    anchors = "".join(
        f'<a href="{html.escape(a.url, quote=True)}">{html.escape(a.label)}</a>'
        for a in notice.attachments
    )
    return (
        f'<div id="BHead">公告編號：{notice.notice_id}<br>'
        f"發布日期：{year - 1911} 年 {month} 月 {day} 日</div>"
        f'<div id="BSubject">{html.escape(notice.title)}</div>'
        f'<div id="BAttachment">{anchors}</div>'
    )


def source_pages():
    pages = {}
    for exam in annual_exams():
        rows = "".join(f'<a href="{n.url}">{html.escape(n.title)}</a>' for n in exam.notices)
        rows += '<a href="https://bulletin.kh.edu.tw/view.php?sn=B115001111">轉知外縣市115學年度教師甄選簡章</a>'
        pages[search_url(exam)] = rows + f"<p>共 {len(exam.notices) + 1} 筆資料</p>"
        pages.update((n.url, notice_html(n)) for n in exam.notices)
    return pages


class KaohsiungBureauClientTests(unittest.TestCase):
    def setUp(self):
        self.pages = source_pages()
        self.client = KaohsiungTeacherRecruitClient(self.pages)
        self.client.http = Mock(side_effect=AssertionError("Unexpected source request"))

    def test_discovery_and_native_pages_use_both_reviewed_official_programmes(self):
        self.assertEqual(self.client.discover_available_years(), [2026])
        options = self.client.discover_exams(2026)
        self.assertEqual({o.code for o in options}, {e.source_exam_id for e in annual_exams()})
        pages = [self.client.fetch_exam_page(o.code, 2026) for o in options]
        self.assertEqual([len(p.papers) for p in pages], [76, 15])
        self.assertEqual(self.client.discover_exams(2025), [])

    def test_unknown_or_mismatched_event_year_is_rejected_before_fetch(self):
        for code, year in [("unknown", 2026), ("teacher-recruit-kaohsiung-115-special", 2025)]:
            with self.subTest(code=code, year=year):
                with self.assertRaisesRegex(ValueError, "event or source year"):
                    self.client.fetch_exam_page(code, year)
        self.client.http.get_text.assert_not_called()

    def test_new_own_notice_rejects_the_whole_annual_projection(self):
        exam = annual_exams()[0]
        pages = dict(self.pages)
        pages[search_url(exam)] = (
            pages[search_url(exam)].replace(
                f"共 {len(exam.notices) + 1} 筆資料", f"共 {len(exam.notices) + 2} 筆資料"
            )
            + '<a href="https://bulletin.kh.edu.tw/view.php?sn=B115009999">公告本市115年度市立國民小學教師聯合甄選答案更正</a>'
        )
        client = KaohsiungTeacherRecruitClient(pages)
        with self.assertRaisesRegex(ValueError, "notice population changed"):
            client.fetch_exam_page(exam.source_exam_id, 2026)

    def test_cjk_radical_administrative_title_still_belongs_to_the_native_programme(self):
        exam = annual_exams()[0]
        odd = next(n for n in exam.notices if n.notice_id == "B115003444")
        self.assertIn("國⺠⼩", odd.title)
        self.client.fetch_exam_page(exam.source_exam_id, 2026)

    def test_competing_or_missing_attachment_is_rejected_even_with_an_unchanged_title(self):
        exam = annual_exams()[0]
        notice = next(n for n in exam.notices if n.notice_id == "B115002760")
        text = notice_html(notice)
        for changed in [
            text.replace("126646", "999999"),
            text
            + '<a href="https://bulletin.kh.edu.tw/attachment.php?b_sn=B115002760&amp;a_sn=999999">new.pdf</a>',
            text.replace(notice.attachments[-1].label, "unexpected label"),
        ]:
            with self.subTest(changed=changed[-100:]):
                with self.assertRaisesRegex(ValueError, "population changed"):
                    validate_notice_html(notice, changed)

    def test_identical_html_links_are_deduplicated_but_conflicting_labels_are_rejected(self):
        notice = next(n for n in annual_exams()[1].notices if n.notice_id == "B115002913")
        first = notice.attachments[0]
        link = f'<a href="{html.escape(first.url, quote=True)}">{first.label}</a>'
        validate_notice_html(notice, notice_html(notice) + link)
        with self.assertRaisesRegex(ValueError, "competing labels"):
            validate_notice_html(
                notice, notice_html(notice) + link.replace(first.label, "other.pdf")
            )

    def test_stale_native_year_date_and_title_are_not_reused(self):
        notice = annual_exams()[0].notices[0]
        for changed in [
            replace(notice, publication_date="2025-01-01"),
            replace(notice, notice_id="B114000001"),
            replace(notice, title="different annual programme"),
        ]:
            with self.subTest(changed=changed):
                with self.assertRaisesRegex(ValueError, "identity, title or date changed"):
                    validate_notice_html(changed, notice_html(notice))

    def test_incomplete_search_population_is_not_a_successful_discovery(self):
        exam = annual_exams()[0]
        pages = dict(self.pages)
        pages[search_url(exam)] = "<p>source error</p>"
        with self.assertRaisesRegex(ValueError, "complete population count"):
            KaohsiungTeacherRecruitClient(pages).discover_available_years()


class KaohsiungProviderRevisionTests(unittest.TestCase):
    def test_changed_pdf_and_parent_zip_fail_before_a_source_page_is_returned(self):
        for exam in annual_exams():
            with self.subTest(scope=exam.scope):
                client = Mock(spec=KaohsiungTeacherRecruitClient)
                client.known_exam.return_value = exam
                client.fetch_exam_page.return_value = exam.source_page()
                client.download_file.return_value = DownloadedFile(
                    b"<html>source error</html>", "text/html", "error"
                )
                provider = KaohsiungTeacherRecruitProvider(client)
                with self.assertRaisesRegex(ValueError, "changed"):
                    provider.fetch_exam_page(exam.source_exam_id, 2026)
                self.assertEqual(provider._downloads, {})

    def test_unreviewed_downloads_cannot_fetch_administrative_results_or_expired_urls(self):
        client = Mock(spec=KaohsiungTeacherRecruitClient)
        provider = KaohsiungTeacherRecruitProvider(client)
        with self.assertRaisesRegex(ValueError, "Unreviewed"):
            provider.download_file("https://exam.kh.edu.tw/special/upload/unknown.pdf")
        client.download_file.assert_not_called()
