import unittest
from dataclasses import replace
from urllib.parse import quote

from app.models import BundleAsset, NormalizedCatalog, NormalizedPaper, ParsedPaper, ReviewItem, SourceExamPage
from app.state import merge_incremental_state, merge_targeted_state


class IncrementalStateTests(unittest.TestCase):
    def test_merge_incremental_state_preserves_older_years_and_marks_affected_canonicals(self) -> None:
        existing_raw_pages = [
            SourceExamPage(
                source_exam_id="113180",
                year_ad=2024,
                year_roc=113,
                exam_name_raw="113年第三次專門職業及技術人員高等考試護理師考試",
                attachments=[],
                papers=[],
            ),
            SourceExamPage(
                source_exam_id="115030",
                year_ad=2026,
                year_roc=115,
                exam_name_raw="115年第一次專門職業及技術人員高等考試營養師、護理師、社會工作師考試",
                attachments=[],
                papers=[],
            ),
        ]
        existing_catalog = NormalizedCatalog(
            papers=[
                NormalizedPaper(
                    canonical_id="nurse",
                    canonical_name="護理師",
                    year_roc=113,
                    exam_name_raw="113年第三次專門職業及技術人員高等考試護理師考試",
                    category_raw="專門職業及技術人員高等考試護理師考試",
                    category_code="101",
                    source_exam_id="113180",
                    subject_code="0101",
                    subject_name_raw="基礎醫學",
                    paper_code="101-0101-question",
                    file_type="question",
                    download_url_source="https://source.example/113-question.pdf",
                    storage_key="113/113180/101/0101/question.pdf",
                    checksum="old113",
                ),
                NormalizedPaper(
                    canonical_id="teacher",
                    canonical_name="教育行政",
                    year_roc=115,
                    exam_name_raw="115年公務人員初等考試",
                    category_raw="教育行政",
                    category_code="401",
                    source_exam_id="115010",
                    subject_code="0401",
                    subject_name_raw="教育學大意",
                    paper_code="401-0401-question",
                    file_type="question",
                    download_url_source="https://source.example/teacher-question.pdf",
                    storage_key="115/115010/401/0401/question.pdf",
                    checksum="teacher115",
                ),
            ],
            review_queue=[ReviewItem(raw_category="專門職業及技術人員高等考試護理師考試", normalized_candidate="護理師", source_exam_id="113180", year_roc=113)],
        )
        existing_bundles = [
            BundleAsset(
                canonical_id="nurse",
                canonical_name="護理師",
                years=[115, 113],
                file_count=2,
                storage_key="bundles/護理師__nurse.zip",
                asset_name="護理師__nurse.zip",
                download_url="https://bundles.example/護理師__nurse.zip",
                legacy_asset_names=["nurse.zip"],
            ),
            BundleAsset(
                canonical_id="teacher",
                canonical_name="教育行政",
                years=[115],
                file_count=1,
                storage_key="bundles/教育行政__teacher.zip",
                asset_name="教育行政__teacher.zip",
                download_url="https://bundles.example/教育行政__teacher.zip",
                legacy_asset_names=["teacher.zip"],
            ),
        ]

        refreshed_raw_pages = [
            SourceExamPage(
                source_exam_id="115030",
                year_ad=2026,
                year_roc=115,
                exam_name_raw="115年第一次專門職業及技術人員高等考試營養師、護理師、社會工作師考試",
                attachments=[],
                papers=[],
            )
        ]
        refreshed_catalog = NormalizedCatalog(
            papers=[
                NormalizedPaper(
                    canonical_id="nurse",
                    canonical_name="護理師",
                    year_roc=115,
                    exam_name_raw="115年第一次專門職業及技術人員高等考試營養師、護理師、社會工作師考試",
                    category_raw="高等考試_護理師",
                    category_code="101",
                    source_exam_id="115030",
                    subject_code="0101",
                    subject_name_raw="基礎醫學",
                    paper_code="101-0101-question",
                    file_type="question",
                    download_url_source="https://source.example/115-question.pdf",
                    storage_key="115/115030/101/0101/question.pdf",
                    checksum="new115",
                )
            ],
            review_queue=[],
        )

        merged_raw_pages, merged_catalog, preserved_bundles, affected_canonical_ids, canonical_aliases = merge_incremental_state(
            existing_raw_pages=existing_raw_pages,
            existing_catalog=existing_catalog,
            existing_bundles=existing_bundles,
            refreshed_raw_pages=refreshed_raw_pages,
            refreshed_catalog=refreshed_catalog,
        )

        self.assertEqual({page.year_roc for page in merged_raw_pages}, {113, 115})
        self.assertEqual(
            {(paper.canonical_id, paper.year_roc, paper.checksum) for paper in merged_catalog.papers},
            {
                ("nurse", 113, "old113"),
                ("nurse", 115, "new115"),
                ("teacher", 115, "teacher115"),
            },
        )
        self.assertEqual({bundle.canonical_id for bundle in preserved_bundles}, {"teacher"})
        self.assertEqual(preserved_bundles[0].asset_name, "教育行政__teacher.zip")
        self.assertEqual(preserved_bundles[0].legacy_asset_names, ["teacher.zip"])
        self.assertEqual(affected_canonical_ids, {"nurse"})
        self.assertEqual(canonical_aliases, {})

    def test_merge_incremental_state_uses_v2_bundle_ids_for_targeted_publication(self) -> None:
        def paper(source_exam_id: str, year_roc: int, bundle_id: str, checksum: str) -> NormalizedPaper:
            return NormalizedPaper(
                canonical_id="canonical-shared",
                canonical_name="共享科目",
                year_roc=year_roc,
                exam_name_raw="exam",
                category_raw="共享科目",
                subject_name_raw="subject",
                paper_code="101-0101-question",
                file_type="question",
                download_url_source="https://source.example/paper.pdf",
                category_code="101",
                source_exam_id=source_exam_id,
                subject_code="0101",
                storage_key=f"{year_roc}/{source_exam_id}/101/0101/question.pdf",
                checksum=checksum,
                bundle_id=bundle_id,
                schema_version=2,
            )

        refreshed_page = SourceExamPage(
            source_exam_id="115030",
            year_ad=2026,
            year_roc=115,
            exam_name_raw="exam",
            attachments=[],
            papers=[],
        )
        existing_catalog = NormalizedCatalog(
            papers=[
                paper("115030", 115, "bundle-old", "old"),
                paper("114030", 114, "bundle-historical", "historical"),
            ],
            review_queue=[],
        )
        refreshed_catalog = NormalizedCatalog(
            papers=[paper("115030", 115, "bundle-new", "new")],
            review_queue=[],
        )
        existing_bundles = [
            BundleAsset(
                canonical_id="canonical-shared",
                canonical_name="共享科目",
                years=[115],
                file_count=1,
                storage_key="bundles/bundle-old.zip",
                asset_name="bundle-old.zip",
                bundle_id="bundle-old",
                schema_version=2,
            ),
            BundleAsset(
                canonical_id="canonical-shared",
                canonical_name="共享科目",
                years=[114],
                file_count=1,
                storage_key="bundles/bundle-historical.zip",
                asset_name="bundle-historical.zip",
                bundle_id="bundle-historical",
                schema_version=2,
            ),
        ]

        _, _, preserved_bundles, affected_canonical_ids, _ = merge_incremental_state(
            existing_raw_pages=[refreshed_page],
            existing_catalog=existing_catalog,
            existing_bundles=existing_bundles,
            refreshed_raw_pages=[refreshed_page],
            refreshed_catalog=refreshed_catalog,
        )

        self.assertEqual(affected_canonical_ids, {"bundle-old", "bundle-new"})
        self.assertEqual({bundle.bundle_id for bundle in preserved_bundles}, {"bundle-historical"})

    def test_merge_incremental_state_migrates_previous_canonical_family_to_refreshed_id(self) -> None:
        existing_raw_pages = [
            SourceExamPage(source_exam_id="114030", year_ad=2025, year_roc=114, exam_name_raw="old 114", attachments=[], papers=[]),
            SourceExamPage(source_exam_id="115030", year_ad=2026, year_roc=115, exam_name_raw="old 115", attachments=[], papers=[]),
        ]
        existing_catalog = NormalizedCatalog(
            papers=[
                NormalizedPaper(
                    canonical_id="canonical-badold",
                    canonical_name="舊亂碼名稱",
                    year_roc=114,
                    exam_name_raw="old 114",
                    category_raw="舊亂碼名稱",
                    category_code="101",
                    source_exam_id="114030",
                    subject_code="0101",
                    subject_name_raw="基礎醫學",
                    paper_code="101-0101-question",
                    file_type="question",
                    download_url_source="https://source.example/114-question.pdf",
                    storage_key="114/114030/101/0101/question.pdf",
                    checksum="old114",
                ),
                NormalizedPaper(
                    canonical_id="canonical-badold",
                    canonical_name="舊亂碼名稱",
                    year_roc=115,
                    exam_name_raw="old 115",
                    category_raw="舊亂碼名稱",
                    category_code="101",
                    source_exam_id="115030",
                    subject_code="0101",
                    subject_name_raw="基礎醫學",
                    paper_code="101-0101-question",
                    file_type="question",
                    download_url_source="https://source.example/115-old-question.pdf",
                    storage_key="115/115030/101/0101/question.pdf",
                    checksum="old115",
                ),
            ],
            review_queue=[],
        )
        existing_bundles = [
            BundleAsset(
                canonical_id="canonical-badold",
                canonical_name="舊亂碼名稱",
                years=[115, 114],
                file_count=2,
                storage_key="bundles/canonical-badold.zip",
                asset_name="canonical-badold.zip",
                download_url=f"https://bundles.example/{quote('canonical-badold.zip')}",
            )
        ]
        refreshed_raw_pages = [
            SourceExamPage(source_exam_id="115030", year_ad=2026, year_roc=115, exam_name_raw="new 115", attachments=[], papers=[])
        ]
        refreshed_catalog = NormalizedCatalog(
            papers=[
                NormalizedPaper(
                    canonical_id="nurse",
                    canonical_name="護理師",
                    year_roc=115,
                    exam_name_raw="new 115",
                    category_raw="高等考試_護理師",
                    category_code="101",
                    source_exam_id="115030",
                    subject_code="0101",
                    subject_name_raw="基礎醫學",
                    paper_code="101-0101-question",
                    file_type="question",
                    download_url_source="https://source.example/115-question.pdf",
                    storage_key="115/115030/101/0101/question.pdf",
                    checksum="new115",
                )
            ],
            review_queue=[],
        )

        _, merged_catalog, preserved_bundles, affected_canonical_ids, canonical_aliases = merge_incremental_state(
            existing_raw_pages=existing_raw_pages,
            existing_catalog=existing_catalog,
            existing_bundles=existing_bundles,
            refreshed_raw_pages=refreshed_raw_pages,
            refreshed_catalog=refreshed_catalog,
        )

        self.assertEqual(
            {(paper.canonical_id, paper.canonical_name, paper.year_roc) for paper in merged_catalog.papers},
            {("nurse", "護理師", 114), ("nurse", "護理師", 115)},
        )
        self.assertEqual(preserved_bundles, [])
        self.assertEqual(affected_canonical_ids, {"canonical-badold", "nurse"})
        self.assertEqual(canonical_aliases, {"nurse": ["canonical-badold"]})

    def test_merge_targeted_state_removes_deleted_exam_and_marks_previous_canonical(self) -> None:
        existing_raw_pages = [
            SourceExamPage(source_exam_id="115040", year_ad=2026, year_roc=115, exam_name_raw="keep", attachments=[], papers=[]),
            SourceExamPage(source_exam_id="115030", year_ad=2026, year_roc=115, exam_name_raw="remove", attachments=[], papers=[]),
        ]
        existing_catalog = NormalizedCatalog(
            papers=[
                NormalizedPaper(
                    canonical_id="nurse",
                    canonical_name="Nurse",
                    year_roc=115,
                    exam_name_raw="keep",
                    category_raw="Nurse",
                    subject_name_raw="Subject",
                    paper_code="101-0101-question",
                    file_type="question",
                    download_url_source="https://source.example/keep.pdf",
                    source_exam_id="115040",
                ),
                NormalizedPaper(
                    canonical_id="doctor",
                    canonical_name="Doctor",
                    year_roc=115,
                    exam_name_raw="remove",
                    category_raw="Doctor",
                    subject_name_raw="Subject",
                    paper_code="101-0101-question",
                    file_type="question",
                    download_url_source="https://source.example/remove.pdf",
                    source_exam_id="115030",
                ),
            ],
            review_queue=[],
        )
        existing_bundles = [
            BundleAsset(canonical_id="nurse", canonical_name="Nurse", years=[115], file_count=1, storage_key="bundles/nurse.zip", asset_name="nurse.zip"),
            BundleAsset(canonical_id="doctor", canonical_name="Doctor", years=[115], file_count=1, storage_key="bundles/doctor.zip", asset_name="doctor.zip"),
        ]

        merged_raw_pages, merged_catalog, preserved_bundles, affected_canonical_ids, canonical_aliases = merge_targeted_state(
            existing_raw_pages=existing_raw_pages,
            existing_catalog=existing_catalog,
            existing_bundles=existing_bundles,
            refreshed_raw_pages=[],
            refreshed_catalog=NormalizedCatalog(papers=[], review_queue=[]),
            removed_exam_ids={"115030"},
        )

        self.assertEqual([page.source_exam_id for page in merged_raw_pages], ["115040"])
        self.assertEqual([paper.source_exam_id for paper in merged_catalog.papers], ["115040"])
        self.assertEqual({bundle.canonical_id for bundle in preserved_bundles}, {"nurse"})
        self.assertEqual(affected_canonical_ids, {"doctor"})
        self.assertEqual(canonical_aliases, {})


class DelistedPaperRetentionTests(unittest.TestCase):
    """CPC re-scoped its archive pages on 2026-08-08 and delisted three papers
    whose files still returned HTTP 200. The event itself survived, so the
    event-level retention from daddce4 did not apply and a plain replace dropped
    them; only the source floor caught it.
    """

    def _page(self, papers):
        return SourceExamPage(
            source_exam_id="cpc-recruit-98",
            year_ad=2009,
            year_roc=98,
            exam_name_raw="98年中油公司新進人員甄試",
            attachments=[],
            papers=papers,
        )

    def _parsed(self, subject_code, name):
        return ParsedPaper(
            category_raw="中油新進人員甄試",
            category_code="98",
            subject_code=subject_code,
            subject_name_raw=name,
            files={"question": f"https://example/{subject_code}.pdf"},
            mirror_files={"question": {"storage_key": f"providers/cpc_recruit/{subject_code}/question.pdf"}},
        )

    def _normalized(self, subject_code, name):
        return NormalizedPaper(
            canonical_id="cpc-recruit",
            canonical_name="中油新進人員甄試",
            year_roc=98,
            exam_name_raw="98年中油公司新進人員甄試",
            category_raw="中油新進人員甄試",
            category_code="98",
            source_exam_id="cpc-recruit-98",
            subject_code=subject_code,
            subject_name_raw=name,
            paper_code=f"98-{subject_code}-question",
            file_type="question",
            download_url_source=f"https://example/{subject_code}.pdf",
            storage_key=f"providers/cpc_recruit/{subject_code}/question.pdf",
        )

    def test_a_paper_the_event_stopped_listing_is_retained(self) -> None:
        existing_pages = [self._page([self._parsed("phd-01", "博士甄試試題"), self._parsed("hire-02", "雇用人員")])]
        existing_catalog = NormalizedCatalog(
            papers=[self._normalized("phd-01", "博士甄試試題"), self._normalized("hire-02", "雇用人員")],
            review_queue=[],
        )
        refreshed_pages = [self._page([self._parsed("phd-01", "博士甄試試題")])]
        refreshed_catalog = NormalizedCatalog(papers=[self._normalized("phd-01", "博士甄試試題")], review_queue=[])

        merged_raw_pages, merged_catalog, _bundles, _affected, _aliases = merge_incremental_state(
            existing_raw_pages=existing_pages,
            existing_catalog=existing_catalog,
            existing_bundles=[],
            refreshed_raw_pages=refreshed_pages,
            refreshed_catalog=refreshed_catalog,
        )

        self.assertEqual(
            sorted(paper.subject_code for paper in merged_raw_pages[0].papers),
            ["hire-02", "phd-01"],
        )
        self.assertEqual(
            sorted(paper.subject_code for paper in merged_catalog.papers),
            ["hire-02", "phd-01"],
        )
        # The retained raw entry must keep its mirror reference, or
        # --prune-orphaned-mirror deletes the file the record still points at.
        retained = next(p for p in merged_raw_pages[0].papers if p.subject_code == "hire-02")
        self.assertEqual(retained.mirror_files["question"]["storage_key"], "providers/cpc_recruit/hire-02/question.pdf")

    def test_a_refreshed_paper_is_replaced_not_duplicated(self) -> None:
        existing_pages = [self._page([self._parsed("phd-01", "old name")])]
        existing_catalog = NormalizedCatalog(papers=[self._normalized("phd-01", "old name")], review_queue=[])
        refreshed_pages = [self._page([self._parsed("phd-01", "new name")])]
        refreshed_catalog = NormalizedCatalog(papers=[self._normalized("phd-01", "new name")], review_queue=[])

        merged_raw_pages, merged_catalog, _bundles, _affected, _aliases = merge_incremental_state(
            existing_raw_pages=existing_pages,
            existing_catalog=existing_catalog,
            existing_bundles=[],
            refreshed_raw_pages=refreshed_pages,
            refreshed_catalog=refreshed_catalog,
        )

        self.assertEqual([p.subject_name_raw for p in merged_raw_pages[0].papers], ["new name"])
        self.assertEqual([p.subject_name_raw for p in merged_catalog.papers], ["new name"])

    def test_a_refreshed_file_role_replaces_the_obsolete_normalized_role(self) -> None:
        source_url = "https://example/paper.zip"
        existing_raw = replace(
            self._parsed("worker-01", "試題及解答"),
            files={"accessible_bundle": source_url},
            mirror_files={"accessible_bundle": {"storage_key": "providers/test/paper.zip"}},
        )
        refreshed_raw = replace(
            existing_raw,
            files={"question_answer": source_url},
            mirror_files={"question_answer": {"storage_key": "providers/test/paper.zip"}},
        )
        existing_normalized = replace(
            self._normalized("worker-01", "試題及解答"),
            paper_code="98-worker-01-accessible_bundle",
            file_type="accessible_bundle",
            download_url_source=source_url,
            storage_key="providers/test/paper.zip",
        )
        refreshed_normalized = replace(
            existing_normalized,
            paper_code="98-worker-01-question_answer",
            file_type="question_answer",
        )

        merged_raw_pages, merged_catalog, _bundles, _affected, _aliases = merge_incremental_state(
            existing_raw_pages=[self._page([existing_raw])],
            existing_catalog=NormalizedCatalog(papers=[existing_normalized], review_queue=[]),
            existing_bundles=[],
            refreshed_raw_pages=[self._page([refreshed_raw])],
            refreshed_catalog=NormalizedCatalog(papers=[refreshed_normalized], review_queue=[]),
        )

        self.assertEqual(list(merged_raw_pages[0].papers[0].files), ["question_answer"])
        self.assertEqual([paper.file_type for paper in merged_catalog.papers], ["question_answer"])

    def test_targeted_removal_still_deletes_the_papers_it_was_told_to(self) -> None:
        # Retention must not resurrect a deliberate removal: it only applies to
        # events this run actually refreshed.
        existing_pages = [self._page([self._parsed("phd-01", "博士甄試試題")])]
        existing_catalog = NormalizedCatalog(papers=[self._normalized("phd-01", "博士甄試試題")], review_queue=[])

        merged_raw_pages, merged_catalog, _bundles, _affected, _aliases = merge_targeted_state(
            existing_raw_pages=existing_pages,
            existing_catalog=existing_catalog,
            existing_bundles=[],
            refreshed_raw_pages=[],
            refreshed_catalog=NormalizedCatalog(papers=[], review_queue=[]),
            removed_exam_ids={"cpc-recruit-98"},
        )

        self.assertEqual(merged_raw_pages, [])
        self.assertEqual(merged_catalog.papers, [])

    def test_retained_failed_paper_keeps_its_review_after_event_refresh(self) -> None:
        paper = replace(
            self._normalized("failed", "待確認試題"),
            classification_confidence="review",
            bundle_id="unresolved-identity",
        )
        review = ReviewItem(
            paper.category_raw, paper.canonical_name, paper.source_exam_id, paper.year_roc,
            bundle_id=paper.bundle_id, reason="Native content still requires review",
        )
        _, catalog, _, _, _ = merge_incremental_state(
            [self._page([self._parsed("failed", "待確認試題")])],
            NormalizedCatalog([paper], [review]), [],
            [self._page([])], NormalizedCatalog([], []),
        )
        self.assertEqual(catalog.papers, [paper])
        self.assertEqual(catalog.review_queue, [review])

    def test_fresh_review_replaces_same_retained_identity_review(self) -> None:
        paper = replace(self._normalized("old", "舊試題"), classification_confidence="review")
        old = ReviewItem(paper.category_raw, "candidate", paper.source_exam_id, paper.year_roc)
        fresh = replace(old, reason="Updated evidence")
        _, catalog, _, _, _ = merge_incremental_state(
            [self._page([self._parsed("old", "舊試題")])],
            NormalizedCatalog([paper], [old]), [],
            [self._page([])], NormalizedCatalog([], [fresh]),
        )
        self.assertEqual(catalog.review_queue, [fresh])

    def test_refreshed_identity_does_not_hide_retained_sibling_review(self) -> None:
        retained = replace(
            self._normalized("old", "待確認"), classification_confidence="review",
            bundle_id="unresolved-identity",
        )
        current = replace(self._normalized("new", "已確認"), bundle_id="approved-identity")
        review = ReviewItem(
            retained.category_raw, "candidate", retained.source_exam_id, retained.year_roc,
            bundle_id=retained.bundle_id,
        )
        _, catalog, _, _, _ = merge_incremental_state(
            [self._page([self._parsed("old", "待確認")])],
            NormalizedCatalog([retained], [review]), [],
            [self._page([self._parsed("new", "已確認")])], NormalizedCatalog([current], []),
        )
        self.assertEqual(catalog.review_queue, [review])

    def test_resolved_or_explicitly_removed_paper_drops_obsolete_review(self) -> None:
        paper = replace(self._normalized("paper", "試題"), classification_confidence="review")
        review = ReviewItem(paper.category_raw, "candidate", paper.source_exam_id, paper.year_roc)
        pages = [self._page([self._parsed("paper", "試題")])]
        _, resolved, _, _, _ = merge_incremental_state(
            pages, NormalizedCatalog([paper], [review]), [], pages,
            NormalizedCatalog([replace(paper, classification_confidence="high")], []),
        )
        self.assertEqual(resolved.review_queue, [])
        _, removed, _, _, _ = merge_targeted_state(
            pages, NormalizedCatalog([paper], [review]), [], [], NormalizedCatalog([], []),
            {paper.source_exam_id},
        )
        self.assertEqual(removed.review_queue, [])


if __name__ == "__main__":
    unittest.main()
