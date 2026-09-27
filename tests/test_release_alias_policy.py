import json
import tempfile
import unittest
from pathlib import Path

from app.audit import build_release_plan
from app.classification import classify_normalized_paper, identity_fields
from app.models import BundleAsset, NormalizedCatalog, NormalizedPaper
from app.paths import provider_paths, site_paths
from app.publisher import publish_site, write_provider_state, write_site_state
from app.release_tags import assign_release_tags, physical_asset_names, project_release_assets
from app.site_registry import get_site_config
from app.state import load_site_bundles


class ReleaseAliasPolicyTests(unittest.TestCase):
    def test_retirement_opens_capacity_without_moving_existing_primary(self) -> None:
        existing = BundleAsset(
            canonical_id="a",
            canonical_name="A",
            years=[115],
            file_count=1,
            storage_key="a.zip",
            asset_name="a.zip",
            release_tag="default-bundles-v2-001",
            legacy_asset_names=["old-a.zip"],
        )
        new = BundleAsset(
            canonical_id="b",
            canonical_name="B",
            years=[115],
            file_count=1,
            storage_key="b.zip",
            asset_name="b.zip",
            legacy_asset_names=["old-b.zip"],
        )
        projected, _conflicts = project_release_assets(
            [existing, new], retain_legacy_asset_names=False
        )
        assigned = assign_release_tags(
            release_tag_prefix="default-bundles-v2",
            existing_bundles=[existing],
            bundles=projected,
            max_assets_per_release=2,
        )
        self.assertEqual(
            [bundle.release_tag for bundle in assigned],
            ["default-bundles-v2-001", "default-bundles-v2-001"],
        )
        self.assertEqual(existing.legacy_asset_names, ["old-a.zip"])
        retained, _conflicts = project_release_assets([existing], retain_legacy_asset_names=True)
        self.assertEqual(physical_asset_names(retained[0]), {"a.zip", "old-a.zip"})

    def test_partial_publication_retires_preserved_and_rebuilt_aliases(self) -> None:
        def paper(provider: str, year: int) -> NormalizedPaper:
            record = NormalizedPaper(
                provider_id=provider,
                canonical_id=f"{provider}-old",
                canonical_name="數學",
                year_roc=year,
                exam_name_raw=f"{year}年學科能力測驗"
                if provider == "ceec_gsat"
                else f"{year}年分科測驗",
                category_raw="數學",
                subject_name_raw="數學",
                paper_code="math-question",
                file_type="question",
                download_url_source="https://example.test/math.pdf",
                category_code="math",
                source_exam_id=f"{provider}-{year}",
                subject_code="math",
                storage_key=f"{year}/math.pdf",
            )
            for key, value in identity_fields(classify_normalized_paper(record)).items():
                setattr(record, key, value)
            return record

        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            for provider in ("moex", "ceec_gsat", "ceec_ast"):
                papers = (
                    [] if provider == "moex" else [paper(provider, year) for year in (114, 115)]
                )
                paths = provider_paths(root, provider)
                for record in papers:
                    payload = paths.mirror_dir / record.storage_key
                    payload.parent.mkdir(parents=True, exist_ok=True)
                    payload.write_bytes(b"%PDF-1.7 sample")
                write_provider_state(
                    paths, [], NormalizedCatalog(papers=papers, review_queue=[]), [], [], None
                )
            _normalized, initial = publish_site(root, site_id="default", repository="example/repo")
            self.assertEqual(len(initial), 2)
            # Model a previous publication containing declared aliases, including
            # the bundle a partial publish will preserve rather than rebuild.
            for index, bundle in enumerate(initial):
                bundle.legacy_asset_names = [f"old-{index}.zip"]
            site = site_paths(root, "default")
            frontend = json.loads(site.frontend_bundles_path.read_text())["bundles"]
            write_site_state(site, initial, frontend)
            old_projection = {
                bundle.asset_name: (bundle.checksum, bundle.release_tag, bundle.storage_key)
                for bundle in initial
            }
            target = paper("ceec_gsat", 115).bundle_id
            _normalized, published = publish_site(
                root, site_id="default", repository="example/repo", affected_canonical_ids={target}
            )
            self.assertEqual(
                {
                    bundle.asset_name: (bundle.checksum, bundle.release_tag, bundle.storage_key)
                    for bundle in published
                },
                old_projection,
            )
            self.assertTrue(all(not bundle.legacy_asset_names for bundle in published))
            for path, collection in (
                (site.bundles_path, "bundles"),
                (site.release_assets_path, "assets"),
            ):
                self.assertTrue(
                    all(
                        "legacy_asset_names" not in row
                        for row in json.loads(path.read_text())[collection]
                    )
                )
            self.assertEqual(len(load_site_bundles(site)), 2)
            plan = build_release_plan(root)
            self.assertEqual(sum(shard["asset_count"] for shard in plan["shards"]), 2)
            self.assertFalse(get_site_config("default").retain_legacy_asset_names)


if __name__ == "__main__":
    unittest.main()
