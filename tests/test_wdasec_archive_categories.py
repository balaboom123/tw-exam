import unittest
from unittest.mock import patch
from urllib.parse import parse_qs

from app.providers.wdasec_skill.client import (
    ARCHIVE_CATEGORIES,
    PAGE_URL,
    ListingRow,
    WdasecSkillClient,
)
from tests.test_wdasec_skill import CATEGORY_LISTING_HTML, DETAIL_PAGE_HTML, INITIAL_PAGE_HTML


def _archive_listing(event_id, title, *, year=115, state="listing", pages=1):
    pager = ""
    if pages > 1:
        pager = '<tr class="pgr"><td>' + str(pages) + "</td></tr>"
    return (
        f'<input type="hidden" name="__VIEWSTATE" value="{state}">'
        f'<table id="gvData"><tr><td>{year}</td><td>{title}</td>'
        f'<td><input name="gvData$ctl02$hdfPLAID" value="{event_id}"></td></tr>'
        + pager
        + "</table>"
    )


class WdasecSkillArchiveCategoryTests(unittest.TestCase):
    def test_category_posts_its_native_value_with_fresh_session(self):
        for category, label in [
            ("btnSelectA", "全國技能檢定各梯次試題及答案"),
            ("btnSelectB", "乙級專案檢定試題及答案"),
            ("btnSelectC", "在校生丙級專案檢定"),
        ]:
            with self.subTest(category=category):
                client = WdasecSkillClient()
                client._hidden_fields = {"__VIEWSTATE": "stale-category-state"}
                responses = [
                    (INITIAL_PAGE_HTML.encode(), {}, 200),
                    (CATEGORY_LISTING_HTML.encode(), {}, 200),
                ]
                with patch.object(client.http, "_request", side_effect=responses) as request:
                    client.fetch_category_listing(category=category)
                self.assertEqual(request.call_args_list[0].args, (PAGE_URL,))
                self.assertEqual(request.call_args_list[0].kwargs, {})
                post = request.call_args_list[1].kwargs
                self.assertEqual(post["method"], "POST")
                fields = parse_qs(post["data"].decode())
                self.assertEqual(fields[category], [label])
                self.assertEqual(fields["__VIEWSTATE"], ["FAKE_VS_1"])
                self.assertFalse(set(ARCHIVE_CATEGORIES) - {category} & fields.keys())

    def test_native_submit_label_can_change_without_changing_category(self):
        client = WdasecSkillClient()
        html = INITIAL_PAGE_HTML.replace("乙級專案檢定試題及答案", "乙級專案試題（官方修訂標題）")
        with patch.object(client, "_get", return_value=html), patch.object(client, "_post") as post:
            client.fetch_category_listing(category="btnSelectB")
        post.assert_called_once_with({"btnSelectB": "乙級專案試題（官方修訂標題）"})

    def test_missing_empty_or_repeated_native_button_fails_before_post(self):
        for html in [
            '<input type="submit" name="btnSelectA" value="A">',
            '<input type="submit" name="btnSelectB" value>',
            '<input type="button" name="btnSelectB" value="B">',
            '<input type="submit" name="btnSelectB" value="B">' * 2,
        ]:
            with self.subTest(html=html):
                client = WdasecSkillClient()
                with (
                    patch.object(client, "_get", return_value=html),
                    patch.object(client, "_post") as post,
                ):
                    with self.assertRaises(ValueError):
                        client.fetch_category_listing(category="btnSelectB")
                post.assert_not_called()

    def test_new_source_category_requires_review_instead_of_being_silently_omitted(self):
        html = INITIAL_PAGE_HTML + '<input type="submit" name="btnSelectD" value="新專案">'
        client = WdasecSkillClient()
        with patch.object(client, "_get", return_value=html), patch.object(client, "_post") as post:
            with self.assertRaisesRegex(ValueError, "Unreviewed WDA archive category"):
                client.fetch_category_listing()
        post.assert_not_called()

    def test_unknown_category_fails_before_network_access(self):
        client = WdasecSkillClient()
        with patch.object(client.http, "_request") as request:
            with self.assertRaisesRegex(ValueError, "Unknown WDA archive category"):
                client.fetch_category_listing(category="unreviewed-button")
        request.assert_not_called()

    def test_each_category_paginates_before_switching_and_all_rows_are_cached(self):
        client = WdasecSkillClient()
        responses = [
            INITIAL_PAGE_HTML,
            _archive_listing("A-1", "全國檢定", state="A-page1", pages=2),
            _archive_listing("A-2", "全國檢定第二頁", state="A-page2"),
            INITIAL_PAGE_HTML,
            _archive_listing("B-1", "乙級專案", state="B-page1"),
            INITIAL_PAGE_HTML,
            _archive_listing("C-1", "在校生工業類", state="C-page1", pages=2),
            _archive_listing("C-2", "在校生商業類", state="C-page2"),
        ]
        with patch.object(
            client.http, "_request", side_effect=[(html.encode(), {}, 200) for html in responses]
        ) as request:
            rows = client.discover_all_listing_rows()
            self.assertEqual([row.plaid for row in rows], ["A-1", "A-2", "B-1", "C-1", "C-2"])
            self.assertEqual(client.discover_all_listing_rows(), rows)
            self.assertEqual(len(client.discover_exams(2026)), 5)
            self.assertEqual(client.discover_available_years(), [2026])
        self.assertEqual(request.call_count, 8)
        for index, state in [(2, "A-page1"), (7, "C-page1")]:
            fields = parse_qs(request.call_args_list[index].kwargs["data"].decode())
            self.assertEqual(fields["__VIEWSTATE"], [state])
            self.assertEqual(fields["__EVENTARGUMENT"], ["Page$2"])
        for index, category in [(1, "btnSelectA"), (4, "btnSelectB"), (6, "btnSelectC")]:
            fields = parse_qs(request.call_args_list[index].kwargs["data"].decode())
            self.assertIn(category, fields)
            self.assertEqual(fields["__VIEWSTATE"], ["FAKE_VS_1"])

    def test_identical_titles_with_distinct_event_ids_are_preserved(self):
        client = WdasecSkillClient()
        responses = [
            _archive_listing("A", "全國檢定"),
            _archive_listing("B-1", "100年度乙級專案檢定試題及答案", pages=2),
            _archive_listing("B-2", "100年度乙級專案檢定試題及答案"),
            _archive_listing("C", "在校生"),
        ]
        with patch.object(
            client.http, "_request", side_effect=AssertionError("unexpected network")
        ):
            with patch.object(
                client,
                "fetch_category_listing",
                side_effect=[responses[0], responses[1], responses[3]],
            ):
                with patch.object(client, "fetch_listing_page", return_value=responses[2]):
                    rows = client.discover_all_listing_rows()
        self.assertEqual([row.plaid for row in rows], ["A", "B-1", "B-2", "C"])

    def test_shared_id_is_deduplicated_only_with_identical_native_context(self):
        client = WdasecSkillClient()
        with (
            patch.object(client, "fetch_category_listing", return_value="ignored"),
            patch("app.providers.wdasec_skill.client.parse_page_count", return_value=1),
            patch(
                "app.providers.wdasec_skill.client.parse_listing_rows",
                side_effect=[
                    [ListingRow(115, "全國檢定", "same", 0)],
                    [ListingRow(115, "全國檢定", "same", 9)],
                    [ListingRow(115, "在校生", "other", 0)],
                ],
            ),
        ):
            self.assertEqual(len(client.discover_all_listing_rows()), 2)

    def test_conflicting_shared_id_and_failed_category_never_cache_partial_scope(self):
        failures = [
            _archive_listing("same", "different title"),
            _archive_listing("same", "same title", year=114),
            "<html>unexpected empty response</html>",
            RuntimeError("source unavailable"),
        ]
        for failed_response in failures:
            with self.subTest(response=failed_response):
                client = WdasecSkillClient()
                first = _archive_listing("same", "same title")
                with patch.object(
                    client, "fetch_category_listing", side_effect=[first, failed_response]
                ):
                    with self.assertRaises((ValueError, RuntimeError)):
                        client.discover_all_listing_rows()
                self.assertIsNone(client._listing_rows_cache)
                with patch.object(client, "fetch_category_listing", return_value=first) as fetch:
                    self.assertEqual(len(client.discover_all_listing_rows()), 1)
                self.assertEqual(fetch.call_count, 3)

    def test_project_menu_does_not_replace_paper_grade_with_menu_grade(self):
        for title, expected_code, expected_name in [
            ("98年度乙級專案檢定試題及答案", "00100-class_a-question", "甲級"),
            ("115年度在校生（工業類）丙級專案檢定", "00100-single-question", "單一級"),
        ]:
            with self.subTest(title=title):
                client = WdasecSkillClient()
                client._listing_rows_cache = (ListingRow(115, title, "native-event", 0),)
                html = DETAIL_PAGE_HTML
                if expected_name == "單一級":
                    html = html.replace("甲級", "單一級")
                with patch.object(client, "_get", return_value=html):
                    page = client.fetch_exam_page("native-event", 2026)
                self.assertEqual(page.exam_name_raw, title)
                self.assertEqual(page.papers[0].subject_code, expected_code)
                self.assertIn(expected_name, page.papers[0].subject_name_raw)
