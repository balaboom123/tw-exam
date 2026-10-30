"""Tests for the teacher_recruit_newtaipei provider."""

import unittest
from copy import deepcopy
from email.message import Message
from unittest.mock import Mock
from urllib.error import HTTPError

from app.providers.teacher_recruit_newtaipei.client import (
    DETAIL_API_URL,
    LIST_API_URL,
    NewTaipeiTeacherRecruitClient,
    detail_matches_notice,
    parse_candidate_notices,
    parse_detail_downloads,
)

LIST_ROWS = [
    {
        "opn_title": "【初試試題】新北市立國民中學115學年度教師聯合甄選初試試題及答案",
        "opn_tag": "新北市立國民中學115學年度教師聯合甄選",
        "uuid": "notice-uuid",
    },
    {
        "opn_title": "新北市立國民中學115學年度教師聯合甄選初試試題疑義申請",
        "opn_tag": "新北市立國民中學115學年度教師聯合甄選",
        "uuid": "appeal-uuid",
    },
]

DETAIL_ROW = {
    "opn_title": "【初試試題】新北市立國民中學115學年度教師聯合甄選初試試題及答案",
    "opn_tag": "新北市立國民中學115學年度教師聯合甄選",
    "attachment2": [
        {
            "fileName": "新北市國民中學教師聯合甄選試題答案.rar",
            "fileSize": "16255KB",
            "fileUuid": "file-uuid",
        },
        {
            "fileName": "新北市國民中學教師聯合甄選試題疑義申請表.pdf",
            "fileSize": "120KB",
            "fileUuid": "appeal-file",
        },
    ],
}

ANSWER_REVIEW_ROWS = [
    {
        "opn_title": "【初試試題疑義回覆】新北市立國民中學115學年度教師聯合甄選初試試題疑義回覆",
        "opn_tag": LIST_ROWS[0]["opn_tag"],
        "uuid": "562752375fce40cd911ee05190f5f993",
    },
    {
        "opn_title": (
            "【初試試題疑義回覆】新北市立國民中學115學年度教師聯合甄選初試試題疑義回覆－補充公告"
        ),
        "opn_tag": LIST_ROWS[0]["opn_tag"],
        "uuid": "b7b72783546340fcb7bb03736a275f2b",
    },
]
ANSWER_REVIEW_DETAILS = [
    {
        **ANSWER_REVIEW_ROWS[0],
        "attachment2": [
            {
                "fileName": "1-新北市立國民中學115學年度教師聯合甄選筆試試題答案疑義彙整表.pdf",
                "fileUuid": "c6cb4f847c6d41edb012eef4ae59eace",
            },
            {
                "fileName": "2-新北市立國民中學115學年度教師聯合甄選筆試試題疑義回覆.pdf",
                "fileUuid": "f15090bb2ff24d2fadd94642d5d1cbd3",
            },
        ],
    },
    {
        **ANSWER_REVIEW_ROWS[1],
        "attachment2": [
            {
                "fileName": (
                    "1-新北市立國民中學115學年度教師聯合甄選筆試試題答案疑義彙整表-補充公告.pdf"
                ),
                "fileUuid": "5eb164538015414ab578be59b1671043",
            },
            {
                "fileName": "2-新北市立國民中學115學年度教師聯合甄選筆試試題疑義回覆-補充公告.pdf",
                "fileUuid": "938da4d015fa43b49ade335e9e06f5cb",
            },
        ],
    },
]


class NewTaipeiTeacherRecruitParserTests(unittest.TestCase):
    def test_parse_candidate_notices_keeps_paper_notices_only(self) -> None:
        notices = parse_candidate_notices(LIST_ROWS)

        self.assertEqual([notice.uuid for notice in notices], ["notice-uuid"])
        self.assertEqual(notices[0].year_ad, 2026)
        self.assertEqual(notices[0].year_roc, 115)
        self.assertEqual(notices[0].scope_code, "junior")

    def test_detail_matches_notice_requires_title_or_tag_overlap(self) -> None:
        notice = parse_candidate_notices(LIST_ROWS)[0]

        self.assertTrue(detail_matches_notice(notice, DETAIL_ROW))
        self.assertFalse(
            detail_matches_notice(notice, {"opn_title": "其他公告", "opn_tag": "其他甄選"})
        )

    def test_parse_detail_downloads_keeps_question_answer_attachment(self) -> None:
        downloads = parse_detail_downloads(DETAIL_ROW)

        self.assertEqual(len(downloads), 1)
        self.assertEqual(downloads[0].file_type, "question_answer")
        self.assertEqual(downloads[0].file_name, "新北市國民中學教師聯合甄選試題答案.rar")
        self.assertTrue(downloads[0].url.endswith("/download/file-uuid"))

    def test_answer_review_outcomes_are_kept_but_blank_applications_are_not(self) -> None:
        notices = parse_candidate_notices(LIST_ROWS + ANSWER_REVIEW_ROWS)
        self.assertEqual(len(notices), 3)
        self.assertEqual([n.answer_review for n in notices], [False, True, True])
        for detail in ANSWER_REVIEW_DETAILS:
            downloads = parse_detail_downloads(detail)
            self.assertEqual(
                [d.file_type for d in downloads], ["corrected_answer", "answer_clarification"]
            )

    def test_matching_programme_tag_cannot_substitute_another_notice(self) -> None:
        notice = parse_candidate_notices(LIST_ROWS)[0]
        self.assertFalse(detail_matches_notice(notice, ANSWER_REVIEW_DETAILS[0]))
        no_tag = parse_candidate_notices([{**LIST_ROWS[0], "opn_tag": ""}])[0]
        self.assertFalse(detail_matches_notice(no_tag, {"opn_title": "不相關公告"}))

    def test_duplicate_notice_and_file_rows_share_only_identical_contexts(self) -> None:
        self.assertEqual(len(parse_candidate_notices([LIST_ROWS[0], LIST_ROWS[0]])), 1)
        with self.assertRaisesRegex(ValueError, "Conflicting New Taipei notice"):
            parse_candidate_notices(
                [LIST_ROWS[0], {**LIST_ROWS[0], "opn_title": "115年度國中教師答案"}]
            )
        detail = deepcopy(DETAIL_ROW)
        detail["attachment2"].append(detail["attachment2"][0])
        self.assertEqual(len(parse_detail_downloads(detail)), 1)
        detail["attachment2"].append(
            {**detail["attachment2"][0], "fileName": "同一來源的其他試題.pdf"}
        )
        with self.assertRaisesRegex(ValueError, "Conflicting New Taipei attachment"):
            parse_detail_downloads(detail)


class NewTaipeiTeacherRecruitClientTests(unittest.TestCase):
    def test_head_rejection_uses_actual_get_metadata_and_reuses_payload(self) -> None:
        client = NewTaipeiTeacherRecruitClient()
        url = (
            "https://career.ntpc.edu.tw/web-elec-bulletin/open/oauth_data/op_api/download/file-uuid"
        )
        client._fetch_json = lambda url: [{"token": "download-token"}]  # type: ignore[method-assign]
        headers = Message()
        headers["Content-Type"] = "application/pdf"
        headers["Content-Length"] = "10"
        headers["Content-Disposition"] = "inline; filename=native.pdf"
        client.http.head = Mock(side_effect=HTTPError(url, 403, "HEAD denied", {}, None))
        client.http._request = Mock(return_value=(b"%PDF-valid", headers, 200))
        metadata = client.head(url)
        self.assertEqual((metadata.url, metadata.status, metadata.content_length), (url, 200, 10))
        downloaded = client.download_file(url)
        self.assertEqual((downloaded.data, downloaded.file_name), (b"%PDF-valid", "native.pdf"))
        client.http._request.assert_called_once()

    def test_get_failure_and_unrelated_head_errors_are_not_hidden(self) -> None:
        client = NewTaipeiTeacherRecruitClient()
        url = (
            "https://career.ntpc.edu.tw/web-elec-bulletin/open/oauth_data/op_api/download/file-uuid"
        )
        client._fetch_json = lambda url: [{"token": "download-token"}]  # type: ignore[method-assign]
        for head_code, get_code in ((500, 200), (405, 404)):
            client.http.head = Mock(side_effect=HTTPError(url, head_code, "failed", {}, None))
            client.http._request = Mock(side_effect=HTTPError(url, get_code, "failed", {}, None))
            with self.assertRaises(HTTPError):
                client.head(url)
            if head_code == 500:
                client.http._request.assert_not_called()
            self.assertEqual(client._head_downloads, {})

    def review_client(self, *, reverse: bool = False) -> NewTaipeiTeacherRecruitClient:
        rows = [LIST_ROWS[0], *ANSWER_REVIEW_ROWS]
        details = {"notice-uuid": DETAIL_ROW, **{d["uuid"]: d for d in ANSWER_REVIEW_DETAILS}}
        client = NewTaipeiTeacherRecruitClient()
        client._fetch_json = lambda url: (
            (  # type: ignore[method-assign]
                list(reversed(rows)) if reverse else rows
            )
            if url == LIST_API_URL
            else [details[url.rsplit("/", 1)[-1]]]
        )
        return client

    def test_answer_reviews_stay_in_original_event_independent_of_listing_order(self) -> None:
        pages = []
        for reverse in (False, True):
            client = self.review_client(reverse=reverse)
            exams = client.discover_exams(2026)
            self.assertEqual([e.code for e in exams], ["teacher-recruit-newtaipei-115-junior"])
            self.assertEqual(
                client.build_discovery_exam_url(exams[0].code, 2026),
                DETAIL_API_URL.format(uuid="notice-uuid"),
            )
            page = client.fetch_exam_page(exams[0].code, 2026)
            self.assertEqual(len(page.papers), 3)
            self.assertEqual(sum(len(p.files) for p in page.papers), 5)
            self.assertEqual(page.papers[0].subject_code, "junior")
            self.assertEqual(
                page.papers[0].files["question_answer"].rsplit("/", 1)[-1], "file-uuid"
            )
            self.assertEqual(
                {p.subject_code for p in page.papers[1:]},
                {"junior-notice-" + n["uuid"] for n in ANSWER_REVIEW_ROWS},
            )
            pages.append(page)
        self.assertEqual(pages[0], pages[1])

    def test_orphan_answer_reviews_and_competing_originals_require_source_review(self) -> None:
        for rows in (
            ANSWER_REVIEW_ROWS,
            [LIST_ROWS[0], {**LIST_ROWS[0], "uuid": "another-original"}],
        ):
            client = NewTaipeiTeacherRecruitClient()
            client._fetch_json = lambda url, rows=rows: rows  # type: ignore[method-assign]
            with self.assertRaisesRegex(ValueError, "unambiguous original paper notice"):
                client.discover_exams(2026)

    def test_school_scope_alone_cannot_join_different_programme_notices(self) -> None:
        client = NewTaipeiTeacherRecruitClient()
        rows = [LIST_ROWS[0], {**ANSWER_REVIEW_ROWS[0], "opn_tag": "115年度國中代理教師甄選"}]
        client._fetch_json = lambda url: rows  # type: ignore[method-assign]
        with self.assertRaisesRegex(ValueError, "same programme"):
            client.discover_exams(2026)

    def test_fetch_rejects_wrong_year_before_requesting_detail(self) -> None:
        client = self.review_client()
        with self.assertRaisesRegex(ValueError, "year mismatch"):
            client.fetch_exam_page("teacher-recruit-newtaipei-115-junior", 2025)

    def test_wrong_or_empty_detail_fails_without_replacing_retained_event(self) -> None:
        for detail in (ANSWER_REVIEW_DETAILS[0], {**DETAIL_ROW, "attachment2": []}):
            client = NewTaipeiTeacherRecruitClient()
            client._fetch_json = lambda url, detail=detail: (  # type: ignore[method-assign]
                LIST_ROWS if url == LIST_API_URL else [detail]
            )
            with self.assertRaisesRegex(ValueError, "does not match|no recognized files"):
                client.fetch_exam_page("teacher-recruit-newtaipei-115-junior", 2026)

    def test_same_role_files_are_preserved_in_separate_native_slots(self) -> None:
        detail = deepcopy(DETAIL_ROW)
        detail["attachment2"].append(
            {"fileName": "另一原始試題答案.rar", "fileUuid": "another-original-file"}
        )
        client = NewTaipeiTeacherRecruitClient()
        client._fetch_json = lambda url: (  # type: ignore[method-assign]
            LIST_ROWS if url == LIST_API_URL else [detail]
        )
        page = client.fetch_exam_page("teacher-recruit-newtaipei-115-junior", 2026)
        self.assertEqual(len(page.papers), 2)
        self.assertEqual(
            {p.files["question_answer"].rsplit("/", 1)[-1] for p in page.papers},
            {"file-uuid", "another-original-file"},
        )

    def test_discovery_urls_preserve_official_list_and_detail_evidence(self) -> None:
        client = NewTaipeiTeacherRecruitClient()
        client._fetch_json = lambda url: LIST_ROWS  # type: ignore[method-assign]

        exam = client.discover_exams(2026)[0]

        self.assertEqual(client.build_discovery_year_url(2026), LIST_API_URL)
        self.assertEqual(
            client.build_discovery_exam_url(exam.code, 2026),
            DETAIL_API_URL.format(uuid="notice-uuid"),
        )
        with self.assertRaisesRegex(ValueError, "discovery year mismatch"):
            client.build_discovery_exam_url(exam.code, 2025)

    def test_fetch_exam_page_builds_notice_paper(self) -> None:
        client = NewTaipeiTeacherRecruitClient()
        client._fetch_json = lambda url: (
            LIST_ROWS if url.endswith("temopn_newtea_list") else [DETAIL_ROW]
        )  # type: ignore[method-assign]

        exam = client.discover_exams(2026)[0]
        page = client.fetch_exam_page(exam.code, 2026)

        self.assertEqual(page.provider_id, "teacher_recruit_newtaipei")
        self.assertEqual(page.exam_name_raw, "115學年度新北市教師甄試")
        self.assertEqual(len(page.papers), 1)
        self.assertEqual(page.papers[0].category_raw, "新北市教師甄試")
        self.assertEqual(page.papers[0].subject_code, "junior")
        self.assertEqual(page.papers[0].files["question_answer"].split("/")[-1], "file-uuid")

    def test_download_file_exchanges_file_uuid_for_download_token(self) -> None:
        client = NewTaipeiTeacherRecruitClient()
        client._fetch_json = lambda url: [{"token": "download-token"}]  # type: ignore[method-assign]
        client._fetch_bytes = lambda url: (  # type: ignore[method-assign]
            b"Rar!\x1a\x07\x00data",
            {
                "Content-Type": "application/octet-stream",
                "Content-Disposition": "inline; filename = sample.rar",
            },
        )

        downloaded = client.download_file(
            "https://career.ntpc.edu.tw/web-elec-bulletin/open/oauth_data/op_api/download/file-uuid"
        )

        self.assertEqual(downloaded.data, b"Rar!\x1a\x07\x00data")
        self.assertEqual(downloaded.content_type, "application/octet-stream")
        self.assertEqual(downloaded.file_name, "sample.rar")


if __name__ == "__main__":
    unittest.main()
