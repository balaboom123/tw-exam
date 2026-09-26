import hashlib
import importlib.util
import json
import re
import shlex
import tempfile
import tomllib
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest import mock

import yaml

from app.cli import build_parser
from app.providers.registry import _PROVIDER_FACTORIES
from app.publication_quarantine import quarantined_provider_ids

REPO_ROOT = Path(__file__).resolve().parents[1]
RELEASE_SCRIPT_PATH = REPO_ROOT / ".github" / "scripts" / "release_assets.py"
_EMPTY_ZIP = b"PK\x05\x06"
_EMPTY_ZIP_DIGEST = hashlib.sha256(_EMPTY_ZIP).hexdigest()


def _load_release_script():
    spec = importlib.util.spec_from_file_location("release_assets", RELEASE_SCRIPT_PATH)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _workflow_implementation(path: Path) -> str:
    """Include same-repository reusable jobs when checking a caller's gates."""
    text = path.read_text(encoding="utf-8")
    for name in re.findall(r"uses: \./\.github/workflows/([\w-]+\.yml)", text):
        text += "\n" + (path.parent / name).read_text(encoding="utf-8")
    return text


class WorkflowLoader(yaml.SafeLoader):
    # YAML 1.1 treats `on` as a boolean. Actions uses it as a mapping key.
    yaml_implicit_resolvers = {
        key: [(tag, pattern) for tag, pattern in resolvers
              if tag != "tag:yaml.org,2002:bool"]
        for key, resolvers in yaml.SafeLoader.yaml_implicit_resolvers.items()
    }


WorkflowLoader.add_implicit_resolver(
    "tag:yaml.org,2002:bool", re.compile(r"^(?:true|false)$", re.IGNORECASE), list("tTfF"),
)


def _workflow(text: str) -> dict:
    return yaml.load(text, Loader=WorkflowLoader)


def _workflow_run_workflows(workflow: str) -> list[str]:
    return _workflow(workflow)["on"].get("workflow_run", {}).get("workflows", [])


def _data_writing_workflow_names() -> list[str]:
    names: list[str] = []
    for path in sorted((REPO_ROOT / ".github" / "workflows").glob("*.yml")):
        if path.name.startswith("_"):
            continue
        text = _workflow_implementation(path)
        if "commit-and-push.sh" not in text:
            continue
        for line in path.read_text(encoding="utf-8").splitlines():
            if line.startswith("name:"):
                names.append(line.removeprefix("name:").strip())
                break
    return sorted(names)


def _workflow_push_paths(workflow: str) -> list[str]:
    return _workflow(workflow)["on"].get("push", {}).get("paths", [])


def _app_steps(workflow: dict) -> list[tuple[dict, object]]:
    commands = []
    for job in workflow["jobs"].values():
        for step in job.get("steps", []):
            for line in step.get("run", "").splitlines():
                tokens = shlex.split(line)
                if tokens[:3] == ["python", "-m", "app"]:
                    commands.append((step, build_parser().parse_args(tokens[3:])))
    return commands


def _app_step(workflow: dict, command: str) -> tuple[dict, object]:
    return next((step, args) for step, args in _app_steps(workflow) if args.command == command)


class WorkflowTests(unittest.TestCase):
    def test_incremental_workflow_fails_fast_when_release_is_incomplete_on_hosted_ci(self) -> None:
        workflow = _workflow((REPO_ROOT / ".github/workflows/sync-incremental.yml").read_text())
        steps = workflow["jobs"]["sync"]["steps"]
        coverage = next(step for step in steps if "release_assets.py coverage" in step.get("run", ""))
        guard = next(step for step in steps if "bootstrap_required" in step.get("if", ""))
        targeted, _ = _app_step(workflow, "sync-targeted")
        self.assertLess(steps.index(coverage), steps.index(guard))
        self.assertLess(steps.index(guard), steps.index(targeted))
        self.assertIn("exit 1", guard["run"])
        self.assertNotIn("sync-full", [args.command for _, args in _app_steps(workflow)])

    def test_incremental_workflow_probes_before_syncing(self) -> None:
        workflow = _workflow((REPO_ROOT / ".github/workflows/sync-incremental.yml").read_text())
        steps = workflow["jobs"]["sync"]["steps"]
        probe, probe_args = _app_step(workflow, "probe-latest")
        sync, sync_args = _app_step(workflow, "sync-targeted")
        publish, publish_args = _app_step(workflow, "publish-site")
        self.assertEqual(probe_args.provider, "moex")
        self.assertEqual(probe_args.years, 2)
        self.assertEqual(sync_args.provider, probe_args.provider)
        self.assertEqual(sync_args.manifest, probe_args.manifest)
        self.assertEqual(sync_args.probe, probe_args.output)
        self.assertEqual(sync_args.publish_plan_output, publish_args.publish_plan)
        self.assertEqual(publish_args.site_id, "default")
        self.assertLess(steps.index(probe), steps.index(sync))
        self.assertLess(steps.index(sync), steps.index(publish))

    def test_incremental_workflow_can_exit_before_heavy_steps_when_unchanged(self) -> None:
        workflow = _workflow((REPO_ROOT / ".github/workflows/sync-incremental.yml").read_text())
        for command in ("sync-targeted", "publish-site"):
            step, _ = _app_step(workflow, command)
            self.assertEqual(step["if"], "steps.probe.outputs.should_sync == 'true'")
        manifest_commit = next(step for step in workflow["jobs"]["sync"]["steps"]
                               if step.get("name") == "Commit source manifest")
        self.assertEqual(manifest_commit["if"], "steps.probe.outputs.should_sync != 'true'")
        _, args = _app_step(workflow, "probe-latest")
        self.assertIn(str(args.manifest), shlex.split(manifest_commit["run"]))

    def test_incremental_workflow_downloads_only_affected_release_bundles_via_targeted_sync(self) -> None:
        workflow = _workflow((REPO_ROOT / ".github/workflows/sync-incremental.yml").read_text())
        _, args = _app_step(workflow, "sync-targeted")
        self.assertTrue(args.download_affected_bundles)
        self.assertIsNotNone(args.publish_plan_output)
        self.assertFalse(any("gh release download" in step.get("run", "")
                             for step in workflow["jobs"]["sync"]["steps"]))

    def test_monthly_audit_workflow_exists(self) -> None:
        workflow = _workflow((REPO_ROOT / ".github/workflows/audit-recent.yml").read_text())
        self.assertEqual(workflow["on"]["schedule"], [{"cron": "45 3 1 * *"}])
        _, args = _app_step(workflow, "sync-incremental")
        _, publication = _app_step(workflow, "publish-site")
        self.assertEqual(args.provider, "moex")
        self.assertEqual(args.year_window, 2)
        self.assertTrue(args.write_manifest)
        self.assertTrue(args.download_affected_bundles)
        self.assertEqual(args.publish_plan_output, publication.publish_plan)
        self.assertNotIn("sync-full", [args.command for _, args in _app_steps(workflow)])

    def test_workflows_prune_stale_assets_via_shared_script(self) -> None:
        workflows_dir = REPO_ROOT / ".github" / "workflows"
        for workflow_name in ("sync-full.yml", "sync-incremental.yml", "audit-recent.yml"):
            workflow = (workflows_dir / workflow_name).read_text(encoding="utf-8")
            self.assertIn("release_assets.py upload", workflow)
            self.assertIn("release_assets.py prune", workflow)


    def test_sync_workflows_do_not_stage_legacy_site_output(self) -> None:
        workflows_dir = REPO_ROOT / ".github" / "workflows"
        for workflow_name in ("sync-full.yml", "sync-incremental.yml", "audit-recent.yml"):
            workflow = (workflows_dir / workflow_name).read_text(encoding="utf-8")
            self.assertNotIn("git add -f site", workflow)

    def test_pages_deploy_rebuilds_when_site_scoped_bundle_inputs_change(self) -> None:
        workflow = (REPO_ROOT / ".github" / "workflows" / "deploy-pages.yml").read_text(encoding="utf-8")
        push_paths = _workflow_push_paths(workflow)

        for expected_path in (
            "data/providers/**",
            "data/sites/default/**",
            "app/**",
            "scripts/validate_publication.py",
        ):
            self.assertIn(expected_path, push_paths)

    def test_pages_deploy_runs_frontend_gates_before_upload(self) -> None:
        workflow = (REPO_ROOT / ".github" / "workflows" / "deploy-pages.yml").read_text(encoding="utf-8")

        self.assertNotIn("actions/setup-python@", workflow)
        self.assertNotIn("Run Python and catalog gates", workflow)
        for required in (
            "npm test",
            "npm run lint",
            "npm run build",
        ):
            self.assertIn(required, workflow)
        self.assertLess(workflow.index("npm ci"), workflow.index("npm test"))
        self.assertLess(workflow.index("npm run lint"), workflow.index("npm run build"))
        self.assertLess(workflow.index("npm run build"), workflow.index("uses: actions/upload-pages-artifact@"))


    def test_ci_history_audit_skips_missing_mirror_dimension(self) -> None:
        # CI checks out without the gitignored mirror tree. Without the opt-out
        # every retained paper reports a download gap, so the gate cannot pass.
        workflow = (REPO_ROOT / ".github" / "workflows" / "ci.yml").read_text(encoding="utf-8")
        invocations = [line for line in workflow.splitlines() if "app history-audit" in line]
        self.assertTrue(invocations)
        for line in invocations:
            self.assertIn("--skip-mirror-check", line)

    def test_release_script_only_deletes_stale_zip_assets(self) -> None:
        module = _load_release_script()
        release_digests = {"keep.zip": "keep-digest", "stale.zip": "stale-digest"}
        with mock.patch.object(module, "_local_assets", return_value=[{"asset_name": "keep.zip", "release_tag": "default-bundles-001"}]), \
                mock.patch.object(module, "_release_zip_digests", return_value=release_digests), \
                mock.patch.object(module.subprocess, "run") as run_mock:
            exit_code = module.prune()

        self.assertEqual(exit_code, 0)
        self.assertEqual(
            [call.args[0] for call in run_mock.call_args_list],
            [["gh", "release", "delete-asset", "default-bundles-001", "stale.zip", "--yes"]],
        )

    def test_release_script_never_prunes_legacy_alias_assets(self) -> None:
        module = _load_release_script()
        local_assets = [
            {
                "asset_name": "nurse-id.zip",
                "legacy_asset_names": ["nurse-display.zip", "nurse.zip"],
                "release_tag": "default-bundles-001",
            }
        ]
        release_digests = {
            "nurse-id.zip": "a", "nurse-display.zip": "b", "nurse.zip": "c", "stale.zip": "d",
        }
        with mock.patch.object(module, "_local_assets", return_value=local_assets), \
                mock.patch.object(module, "_release_zip_digests", return_value=release_digests), \
                mock.patch.object(module.subprocess, "run") as run_mock:
            self.assertEqual(module.prune(), 0)

        self.assertEqual(
            [call.args[0] for call in run_mock.call_args_list],
            [["gh", "release", "delete-asset", "default-bundles-001", "stale.zip", "--yes"]],
        )

    def test_release_script_reads_wrapped_site_release_assets_schema(self) -> None:
        module = _load_release_script()
        with tempfile.TemporaryDirectory() as tmp:
            release_assets_path = Path(tmp) / "release-assets.json"
            release_assets_path.write_text(
                json.dumps(
                    {
                        "schema_version": 1,
                        "site_id": "default",
                        "assets": [{"asset_name": "nurse.zip", "release_tag": "default-bundles-001"}],
                    }
                ),
                encoding="utf-8",
            )
            with mock.patch.object(module, "RELEASE_ASSETS_PATH", release_assets_path):
                self.assertEqual(module._local_assets(), [{"asset_name": "nurse.zip", "release_tag": "default-bundles-001"}])

    def test_release_script_defaults_to_scoped_site_release_assets_path(self) -> None:
        module = _load_release_script()

        self.assertEqual(module.RELEASE_ASSETS_PATH, Path("data") / "sites" / "default" / "release-assets.json")

    def test_release_script_fails_closed_when_release_tag_metadata_is_missing(self) -> None:
        module = _load_release_script()

        with mock.patch.object(module, "RELEASE_TAG", ""):
            with self.assertRaisesRegex(ValueError, "missing release_tag"):
                module._group_assets_by_release_tag([{"asset_name": "nurse.zip"}])

    def test_release_script_coverage_compares_expected_and_current_zip_names(self) -> None:
        module = _load_release_script()
        local_assets = [
            {
                "asset_name": "a.zip",
                "legacy_asset_names": ["a-alias.zip"],
                "release_tag": "default-bundles-001",
                "checksum": "current-digest",
            }
        ]
        cases = [
            ({}, "bootstrap_required=true"),
            ({"a-alias.zip": "current-digest"}, "bootstrap_required=true"),
            ({"a.zip": "current-digest"}, "bootstrap_required=false"),
            ({"a-alias.zip": "current-digest", "a.zip": "current-digest"}, "bootstrap_required=false"),
        ]
        for release_digests, expected_line in cases:
            with self.subTest(release_digests=release_digests):
                with tempfile.TemporaryDirectory() as tmp:
                    output_path = Path(tmp) / "github-output"
                    with mock.patch.object(module, "_local_assets", return_value=local_assets), \
                            mock.patch.object(module, "_release_zip_digests", return_value=release_digests), \
                            mock.patch.dict(module.os.environ, {"GITHUB_OUTPUT": str(output_path)}):
                        self.assertEqual(module.coverage(), 0)
                    self.assertIn(expected_line, output_path.read_text(encoding="utf-8"))

    def test_release_script_coverage_reports_published_bytes_that_lost_their_checksum(self) -> None:
        # The asset name is derived from bundle identity and stays put across a
        # rebuild, so a name-only check reads a pre-recovery download as covered.
        module = _load_release_script()
        local_assets = [
            {"asset_name": "a.zip", "release_tag": "default-bundles-001", "checksum": "rebuilt-digest"}
        ]
        with tempfile.TemporaryDirectory() as tmp:
            output_path = Path(tmp) / "github-output"
            with mock.patch.object(module, "_local_assets", return_value=local_assets), \
                    mock.patch.object(module, "_release_zip_digests", return_value={"a.zip": "pre-recovery-digest"}), \
                    mock.patch.dict(module.os.environ, {"GITHUB_OUTPUT": str(output_path)}):
                self.assertEqual(module.coverage(), 0)
            recorded = output_path.read_text(encoding="utf-8")

        self.assertIn("stale_required=true", recorded)
        # Stale bytes are repaired by the upload step, so they must not be
        # reported as a bootstrap the hosted runner has to refuse.
        self.assertIn("bootstrap_required=false", recorded)

    def test_release_script_reads_release_metadata_as_utf8(self) -> None:
        module = _load_release_script()
        payloads = ["4242\n", "學科能力測驗__ceec-gsat.zip\tsha256:abc123\nnotes.txt\t\n"]

        with mock.patch.object(module.subprocess, "check_output", side_effect=payloads) as check_output_mock, \
                mock.patch.dict(module.os.environ, {"GITHUB_REPOSITORY": "owner/repo"}):
            self.assertEqual(
                module._release_zip_digests("default-bundles-002"),
                {"學科能力測驗__ceec-gsat.zip": "abc123"},
            )

        self.assertEqual(
            [call.args[0] for call in check_output_mock.call_args_list],
            [
                ["gh", "api", "repos/owner/repo/releases/tags/default-bundles-002", "--jq", ".id"],
                [
                    "gh", "api", "--paginate",
                    "repos/owner/repo/releases/4242/assets?per_page=100",
                    "--jq", '.[] | [.name, (.digest // "")] | @tsv',
                ],
            ],
        )
        for call in check_output_mock.call_args_list:
            self.assertEqual(call.kwargs["encoding"], "utf-8")

    def test_release_script_reports_undigested_release_asset_as_unverifiable(self) -> None:
        # GitHub returns no digest for some older assets. Treating that as a
        # match would make the byte-level check silently vacuous.
        module = _load_release_script()
        payloads = ["7\n", "nurse.zip\t\n"]

        with mock.patch.object(module.subprocess, "check_output", side_effect=payloads):
            self.assertEqual(module._release_zip_digests("default-bundles-001"), {"nurse.zip": ""})

    def test_release_script_uploads_primary_asset_name_only(self) -> None:
        module = _load_release_script()
        with tempfile.TemporaryDirectory() as tmp:
            bundle_path = Path(tmp) / "bundle.zip"
            bundle_path.write_bytes(b"PK\x05\x06")
            local_assets = [
                {
                    "storage_key": str(bundle_path),
                    "asset_name": "nurse-id.zip",
                    "legacy_asset_names": ["nurse.zip"],
                    "release_tag": "default-bundles-001",
                }
            ]
            with mock.patch.object(module, "_local_assets", return_value=local_assets), \
                    mock.patch.object(module, "_release_zip_digests", return_value={}), \
                    mock.patch.object(module.subprocess, "run") as run_mock:
                self.assertEqual(module.upload(), 0)

        self.assertEqual(
            [call.args[0] for call in run_mock.call_args_list],
            [
                [
                    "gh",
                    "release",
                    "upload",
                    "default-bundles-001",
                    f"{bundle_path}#nurse-id.zip",
                    "--clobber",
                ],
            ],
        )

    def test_release_script_rejects_assets_at_github_byte_limit(self) -> None:
        module = _load_release_script()
        with tempfile.TemporaryDirectory() as tmp:
            bundle_path = Path(tmp) / "bundle.zip"
            bundle_path.write_bytes(b"PK\x05\x06")
            local_assets = [{
                "storage_key": str(bundle_path),
                "asset_name": "bundle.zip",
                "release_tag": "default-bundles-001",
            }]
            with mock.patch.object(module, "GITHUB_RELEASE_ASSET_BYTE_LIMIT", 1), \
                    mock.patch.object(module, "_local_assets", return_value=local_assets), \
                    mock.patch.object(module, "_release_zip_digests", return_value={}), \
                    mock.patch.object(module.subprocess, "run") as run_mock:
                self.assertEqual(module.upload(), 1)
        run_mock.assert_not_called()

    def test_release_script_upload_skips_remote_zip_names_that_already_exist(self) -> None:
        module = _load_release_script()
        with tempfile.TemporaryDirectory() as tmp:
            bundle_path = Path(tmp) / "bundle.zip"
            bundle_path.write_bytes(b"PK\x05\x06")
            local_assets = [
                {
                    "storage_key": str(bundle_path),
                    "asset_name": "nurse-id.zip",
                    "legacy_asset_names": ["nurse.zip"],
                    "release_tag": "default-bundles-001",
                }
            ]
            with mock.patch.object(module, "_local_assets", return_value=local_assets), \
                    mock.patch.object(module, "_release_zip_digests", return_value={"nurse-id.zip": _EMPTY_ZIP_DIGEST}), \
                    mock.patch.object(module.subprocess, "run") as run_mock:
                self.assertEqual(module.upload(), 0)

        run_mock.assert_not_called()

    def test_release_script_upload_groups_assets_by_release_tag(self) -> None:
        module = _load_release_script()
        with tempfile.TemporaryDirectory() as tmp:
            first_path = Path(tmp) / "first.zip"
            second_path = Path(tmp) / "second.zip"
            first_path.write_bytes(b"PK\x05\x06")
            second_path.write_bytes(b"PK\x05\x06")
            local_assets = [
                {"storage_key": str(first_path), "asset_name": "first.zip", "release_tag": "default-bundles-001"},
                {"storage_key": str(second_path), "asset_name": "second.zip", "release_tag": "default-bundles-002"},
            ]
            with mock.patch.object(module, "_local_assets", return_value=local_assets), \
                    mock.patch.object(module, "_release_zip_digests", return_value={}), \
                    mock.patch.object(module.subprocess, "run") as run_mock:
                self.assertEqual(module.upload(), 0)

        self.assertEqual(
            [call.args[0] for call in run_mock.call_args_list],
            [
                ["gh", "release", "upload", "default-bundles-001", f"{first_path}#first.zip", "--clobber"],
                ["gh", "release", "upload", "default-bundles-002", f"{second_path}#second.zip", "--clobber"],
            ],
        )

    def test_release_script_upload_fails_when_local_bundle_files_missing(self) -> None:
        module = _load_release_script()
        with tempfile.TemporaryDirectory() as tmp:
            absent = str(Path(tmp) / "absent.zip")
            local_assets = [{"storage_key": absent, "asset_name": "absent.zip", "release_tag": "default-bundles-001"}]
            with mock.patch.object(module, "_local_assets", return_value=local_assets), \
                    mock.patch.object(module, "_release_zip_digests", return_value={}), \
                    mock.patch.object(module.subprocess, "run") as run_mock:
                self.assertEqual(module.upload(), 1)
        run_mock.assert_not_called()

    def test_release_script_upload_skips_missing_local_bundle_when_remote_asset_already_exists(self) -> None:
        module = _load_release_script()
        with tempfile.TemporaryDirectory() as tmp:
            absent = str(Path(tmp) / "nurse.zip")
            local_assets = [{"storage_key": absent, "asset_name": "nurse.zip", "release_tag": "default-bundles-001"}]
            with mock.patch.object(module, "_local_assets", return_value=local_assets), \
                    mock.patch.object(module, "_release_zip_digests", return_value={"nurse.zip": _EMPTY_ZIP_DIGEST}), \
                    mock.patch.object(module.subprocess, "run") as run_mock:
                self.assertEqual(module.upload(), 0)

        run_mock.assert_not_called()

    def test_release_script_reuploads_when_published_bytes_are_stale(self) -> None:
        module = _load_release_script()
        with tempfile.TemporaryDirectory() as tmp:
            bundle_path = Path(tmp) / "bundle.zip"
            bundle_path.write_bytes(_EMPTY_ZIP)
            local_assets = [
                {
                    "storage_key": str(bundle_path),
                    "asset_name": "nurse-id.zip",
                    "release_tag": "default-bundles-001",
                    "checksum": _EMPTY_ZIP_DIGEST,
                }
            ]
            with mock.patch.object(module, "_local_assets", return_value=local_assets), \
                    mock.patch.object(
                        module, "_release_zip_digests", return_value={"nurse-id.zip": "pre-recovery-digest"}
                    ), \
                    mock.patch.object(module.subprocess, "run") as run_mock:
                self.assertEqual(module.upload(), 0)

        self.assertEqual(
            [call.args[0] for call in run_mock.call_args_list],
            [
                [
                    "gh", "release", "upload", "default-bundles-001",
                    f"{bundle_path}#nurse-id.zip", "--clobber",
                ],
            ],
        )

    def test_release_script_upload_refuses_bundles_that_contradict_the_published_checksum(self) -> None:
        # Uploading here would serve bytes that fail the checksum the site
        # publishes for verification; the catalog has to be regenerated first.
        module = _load_release_script()
        with tempfile.TemporaryDirectory() as tmp:
            bundle_path = Path(tmp) / "bundle.zip"
            bundle_path.write_bytes(_EMPTY_ZIP)
            local_assets = [
                {
                    "storage_key": str(bundle_path),
                    "asset_name": "nurse-id.zip",
                    "release_tag": "default-bundles-001",
                    "checksum": "checksum-from-an-older-build",
                }
            ]
            with mock.patch.object(module, "_local_assets", return_value=local_assets), \
                    mock.patch.object(module, "_release_zip_digests", return_value={}), \
                    mock.patch.object(module.subprocess, "run") as run_mock:
                self.assertEqual(module.upload(), 1)

        run_mock.assert_not_called()

    def test_release_script_upload_leaves_assets_whose_published_bytes_already_match(self) -> None:
        module = _load_release_script()
        with tempfile.TemporaryDirectory() as tmp:
            bundle_path = Path(tmp) / "bundle.zip"
            bundle_path.write_bytes(_EMPTY_ZIP)
            local_assets = [
                {
                    "storage_key": str(bundle_path),
                    "asset_name": "nurse-id.zip",
                    "release_tag": "default-bundles-001",
                    "checksum": _EMPTY_ZIP_DIGEST,
                }
            ]
            with mock.patch.object(module, "_local_assets", return_value=local_assets), \
                    mock.patch.object(
                        module, "_release_zip_digests", return_value={"nurse-id.zip": _EMPTY_ZIP_DIGEST}
                    ), \
                    mock.patch.object(module.subprocess, "run") as run_mock:
                self.assertEqual(module.upload(), 0)

        run_mock.assert_not_called()

    def test_incremental_sync_repairs_stale_release_bytes_without_a_source_change(self) -> None:
        workflow = (REPO_ROOT / ".github" / "workflows" / "sync-incremental.yml").read_text(encoding="utf-8")
        upload_condition = next(
            line for line in workflow.splitlines()
            if "if:" in line and "stale_required" in line
        )
        self.assertIn("steps.release_state.outputs.stale_required == 'true'", upload_condition)
        self.assertIn("steps.probe.outputs.should_sync == 'true'", upload_condition)

    def test_workflows_no_longer_install_or_use_ghostscript(self) -> None:
        workflows_dir = REPO_ROOT / ".github" / "workflows"
        for workflow_name in ("sync-full.yml", "sync-incremental.yml", "audit-recent.yml", "_sync-provider.yml"):
            workflow = (workflows_dir / workflow_name).read_text(encoding="utf-8")
            self.assertNotIn("ghostscript", workflow.lower())
            self.assertNotIn("--optimize-pdfs", workflow)
            self.assertNotIn("--pdf-quality", workflow)
            self.assertNotIn("--rewrite-existing-pdfs", workflow)

    def test_moex_workflows_save_fresh_mirror_cache_after_sync_attempts(self) -> None:
        workflows_dir = REPO_ROOT / ".github" / "workflows"
        for workflow_name in ("sync-full.yml", "sync-incremental.yml", "audit-recent.yml"):
            workflow = (workflows_dir / workflow_name).read_text(encoding="utf-8")
            self.assertIn("moex-mirror-${{ github.run_id }}-${{ github.run_attempt }}", workflow)
            self.assertIn("actions/cache/restore@", workflow)
            self.assertIn("actions/cache/save@", workflow)
            self.assertLess(workflow.index("Restore mirror directory"), workflow.index("Save mirror directory"))
            self.assertLess(workflow.index("Run "), workflow.index("Save mirror directory"))
            self.assertIn("!cancelled()", workflow)
            self.assertNotIn("PDF_CACHE_VERSION", workflow)
            self.assertNotIn("PDF_QUALITY_PROFILE", workflow)


    def test_workflows_define_timeout_and_concurrency_controls(self) -> None:
        workflows_dir = REPO_ROOT / ".github" / "workflows"
        for workflow_name in ("sync-full.yml", "sync-incremental.yml", "audit-recent.yml"):
            workflow = (workflows_dir / workflow_name).read_text(encoding="utf-8")
            self.assertIn("concurrency:", workflow)
            self.assertIn("timeout-minutes:", workflow)


    def test_manual_discovery_is_read_only_bounded_and_uploads_its_result(self) -> None:
        workflow = (REPO_ROOT / ".github" / "workflows" / "discover.yml").read_text(encoding="utf-8")

        self.assertIn("workflow_dispatch:", workflow)
        self.assertNotIn("schedule:", workflow)
        self.assertIn("contents: read", workflow)
        self.assertIn("timeout-minutes: 60", workflow)
        self.assertIn("python -m app discover > discover.json", workflow)
        self.assertIn("uses: actions/upload-artifact@", workflow)
        self.assertNotIn("commit-and-push.sh", workflow)

    def test_cold_cache_workflows_have_full_hosted_timeout_budget(self) -> None:
        workflows_dir = REPO_ROOT / ".github" / "workflows"
        for workflow_name in ("sync-full.yml", "sync-incremental.yml", "audit-recent.yml"):
            workflow = (workflows_dir / workflow_name).read_text(encoding="utf-8")
            self.assertIn("timeout-minutes: 360", workflow, workflow_name)


    def test_data_writing_workflows_use_conflict_safe_publisher(self) -> None:
        workflows_dir = REPO_ROOT / ".github" / "workflows"
        workflow_paths = sorted(workflows_dir.glob("sync-*.yml")) + [workflows_dir / "audit-recent.yml"]

        for workflow_path in workflow_paths:
            workflow = _workflow_implementation(workflow_path)
            if "contents: write" not in workflow:
                continue
            self.assertIn(".github/scripts/commit-and-push.sh", workflow, workflow_path.name)
            self.assertNotIn("git add data\n", workflow, workflow_path.name)
            self.assertNotIn("\n          git push\n", workflow, workflow_path.name)

    def test_pages_deploy_reacts_to_every_workflow_that_writes_catalog_data(self) -> None:
        # A GITHUB_TOKEN push starts no workflow run, so deploy-pages' push
        # trigger never fires for a scheduled sync. Reacting to the sync run
        # is the only thing that republishes the site, and a sync missing
        # from this list publishes data the site will never serve.
        workflow = (REPO_ROOT / ".github" / "workflows" / "deploy-pages.yml").read_text(encoding="utf-8")

        self.assertEqual(
            sorted(_workflow_run_workflows(workflow)),
            _data_writing_workflow_names(),
        )

    def test_pages_deploy_keeps_a_scheduled_backstop(self) -> None:
        workflow = (REPO_ROOT / ".github" / "workflows" / "deploy-pages.yml").read_text(encoding="utf-8")

        self.assertIn("schedule:", workflow)
        self.assertIn("cron:", workflow)

    def test_pages_deploy_ignores_failed_upstream_syncs(self) -> None:
        workflow = (REPO_ROOT / ".github" / "workflows" / "deploy-pages.yml").read_text(encoding="utf-8")

        self.assertIn(
            "if: ${{ github.event_name != 'workflow_run' || github.event.workflow_run.conclusion == 'success' }}",
            workflow,
        )

    def test_shared_publisher_gates_generated_data_before_commit(self) -> None:
        # Bot-authored pushes do not start push CI, so every generated-data
        # writer must enforce both the reviewed source floor and complete
        # publication deployability before it can commit to main.
        script = (REPO_ROOT / ".github" / "scripts" / "commit-and-push.sh").read_text(encoding="utf-8")

        self.assertIn("scripts/check_sync_floor.py", script)
        self.assertLess(
            script.index("scripts/check_sync_floor.py"),
            script.index('git commit -m "$commit_message"'),
        )

        self.assertIn("scripts/validate_publication.py", script)
        self.assertLess(
            script.index("scripts/validate_publication.py"),
            script.index('git commit -m "$commit_message"'),
        )

    def test_shared_publisher_revalidates_after_rebase(self) -> None:
        script = (REPO_ROOT / ".github" / "scripts" / "commit-and-push.sh").read_text(encoding="utf-8")
        rebase_index = script.index("git rebase origin/main")

        for gate in ("scripts/check_sync_floor.py", "scripts/validate_publication.py"):
            with self.subTest(gate=gate):
                self.assertGreater(script.rindex(gate), rebase_index)

    def test_every_data_writing_workflow_routes_through_the_gated_publisher(self) -> None:
        workflows_dir = REPO_ROOT / ".github" / "workflows"
        writers = [
            path
            for path in sorted(workflows_dir.glob("*.yml"))
            if "contents: write" in path.read_text(encoding="utf-8")
        ]

        self.assertTrue(writers)
        for workflow_path in writers:
            workflow = _workflow_implementation(workflow_path)
            with self.subTest(workflow=workflow_path.name):
                self.assertIn(".github/scripts/commit-and-push.sh", workflow)
                for forbidden in ("git commit -m", "git push origin"):
                    self.assertNotIn(forbidden, workflow)

    def test_workflows_describe_downloadable_bundle_release(self) -> None:
        workflows_dir = REPO_ROOT / ".github" / "workflows"
        for workflow_name in ("sync-full.yml", "sync-incremental.yml", "audit-recent.yml"):
            workflow = (workflows_dir / workflow_name).read_text(encoding="utf-8")
            self.assertEqual(_app_step(_workflow(workflow), "publish-site")[1].site_id, "default")
            self.assertIn("release_assets.py ensure", workflow)

        module = _load_release_script()
        local_assets = [{"asset_name": "nurse.zip", "release_tag": "default-bundles-001"}]
        with mock.patch.object(module, "_local_assets", return_value=local_assets), \
                mock.patch.object(module.subprocess, "run") as run_mock:
            run_mock.side_effect = [mock.Mock(returncode=1), mock.Mock(returncode=0)]
            self.assertEqual(module.ensure(), 0)
        create_command = run_mock.call_args_list[1].args[0]
        self.assertIn("default-bundles-001", create_command)
        self.assertTrue(any("Downloadable exam bundles" in part for part in create_command))
        self.assertTrue(any("Human-friendly exam bundles with compatibility aliases" in part for part in create_command))

    def test_sync_full_workflow_requires_explicit_override_before_running_unsupported_hosted_bootstrap(self) -> None:
        workflow = _workflow((REPO_ROOT / ".github/workflows/sync-full.yml").read_text())
        self.assertEqual(workflow["on"]["workflow_dispatch"]["inputs"]["allow_unsupported_hosted_bootstrap"]["default"], "false")
        guard = next(step for step in workflow["jobs"]["sync"]["steps"]
                     if "allow_unsupported_hosted_bootstrap" in step.get("if", ""))
        self.assertIn("exit 1", guard["run"])
        _, args = _app_step(workflow, "sync-full")
        self.assertEqual(args.provider, "moex")
        self.assertTrue(args.write_manifest)


class LaunchCITest(unittest.TestCase):
    def test_ci_enforces_locked_application_lint_and_strict_types(self) -> None:
        workflow = _workflow((REPO_ROOT / ".github/workflows/ci.yml").read_text())
        steps = workflow["jobs"]["fast-python"]["steps"]
        install = next(step for step in steps if step.get("run") == "uv sync --frozen")
        checks = next(step for step in steps if step.get("name") == "Lint and type-check application")
        self.assertLess(steps.index(install), steps.index(checks))
        self.assertEqual(checks["run"].splitlines(), [
            "uv run --frozen ruff check app",
            "uv run --frozen ruff format app --check",
            "uv run --frozen mypy app",
        ])
        config = tomllib.loads((REPO_ROOT / "pyproject.toml").read_text())
        self.assertTrue(config["tool"]["mypy"]["strict"])
        self.assertEqual(config["tool"]["mypy"]["files"], ["app"])
        self.assertEqual(set(config["tool"]["ruff"]["lint"]["select"]), {"E", "F", "I", "UP", "B"})

    def test_ci_workflow_covers_release_and_frontend_gates(self) -> None:
        workflow = (REPO_ROOT / ".github" / "workflows" / "ci.yml").read_text(encoding="utf-8")

        for required in (
            "pull_request:",
            'python -m pytest -q -m "not repo_data"',
            "python -m pytest -q -m repo_data",
            "python -m app audit-catalog",
            "python -m app history-audit",
            "python scripts/validate_publication.py",
            "python scripts/validate_schemas.py",
            "python -m app plan-release",
            "npm ci",
            "npm test",
            "npm run lint",
            "npm run build",
        ):
            self.assertIn(required, workflow)
        self.assertEqual(workflow.count("uv sync --frozen"), 2)
        self.assertTrue((REPO_ROOT / "uv.lock").is_file())
        self.assertIn('node-version: "22"', workflow)

    def test_ci_catalog_gate_is_conditional_but_docs_run_on_every_change(self) -> None:
        workflow = (REPO_ROOT / ".github" / "workflows" / "ci.yml").read_text(encoding="utf-8")
        fast_job = workflow.split("  fast-python:\n", 1)[1].split("  python:\n", 1)[0]
        catalog_job = workflow.split("  python:\n", 1)[1].split("  frontend:\n", 1)[0]

        self.assertIn("fetch-depth: 0", workflow)
        self.assertIn("ci_scope.py", workflow)
        self.assertIn("needs: changes", catalog_job)
        self.assertIn("if: needs.changes.outputs.catalog == 'true'", catalog_job)
        self.assertIn("scripts/validate_docs.py --check", fast_job)
        self.assertIn("scripts/validate_publication.py", catalog_job)


def _load_health_script():
    spec = importlib.util.spec_from_file_location(
        "workflow_health", REPO_ROOT / ".github" / "scripts" / "workflow_health.py"
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class WorkflowHealthTest(unittest.TestCase):
    # sync-incremental failed on 2026-07-13, 07-20, 07-27 and 08-03 without
    # anything reacting, while 86% of the published catalog went stale.
    def _scheduled_workflow_names(self) -> list[str]:
        names = []
        for path in sorted((REPO_ROOT / ".github" / "workflows").glob("*.yml")):
            text = path.read_text(encoding="utf-8")
            if "\n  schedule:\n" not in text or path.name == "workflow-health.yml":
                continue
            for line in text.splitlines():
                if line.startswith("name:"):
                    names.append(line.removeprefix("name:").strip())
                    break
        return sorted(names)

    def test_health_workflow_uses_one_daily_audit(self) -> None:
        workflow = (REPO_ROOT / ".github" / "workflows" / "workflow-health.yml").read_text(encoding="utf-8")

        self.assertNotIn("workflow_run:", workflow)
        self.assertIn("workflow_health.py daily --max-age-days 14", workflow)
        self.assertGreater(len(self._scheduled_workflow_names()), 1)

    def test_health_workflow_does_not_react_to_itself(self) -> None:
        workflow = (REPO_ROOT / ".github" / "workflows" / "workflow-health.yml").read_text(encoding="utf-8")

        self.assertNotIn("workflow_run:", workflow)

    def test_health_workflow_keeps_a_schedule_for_workflows_that_stop_running(self) -> None:
        workflow = (REPO_ROOT / ".github" / "workflows" / "workflow-health.yml").read_text(encoding="utf-8")

        self.assertIn("schedule:", workflow)
        self.assertIn("issues: write", workflow)

    def test_health_workflow_does_not_drop_bursty_completion_events(self) -> None:
        # GitHub keeps at most one pending run in a concurrency group, even when
        # cancel-in-progress is false. Provider jobs often finish together, so
        # one global group silently discarded the older pending health events.
        workflow = (REPO_ROOT / ".github" / "workflows" / "workflow-health.yml").read_text(encoding="utf-8")

        self.assertNotIn("\nconcurrency:\n", workflow)

    def test_report_opens_one_issue_for_a_failing_workflow(self) -> None:
        module = _load_health_script()
        with mock.patch.dict(module.os.environ, {"GITHUB_REPOSITORY": "o/r"}), \
                mock.patch.object(module, "_gh_api", return_value=[]), \
                mock.patch.object(module.subprocess, "run") as run_mock:
            module.report("sync-incremental", "failure", "https://example/run/1")

        created = [call for call in run_mock.call_args_list if "issues" in call.args[0][2]]
        self.assertTrue(created)
        payload = " ".join(created[-1].args[0])
        self.assertIn("Workflow health: sync-incremental", payload)
        self.assertIn("https://example/run/1", payload)

    def test_report_does_not_repeat_notifications_for_an_open_issue(self) -> None:
        module = _load_health_script()
        existing = [{"title": "Workflow health: sync-incremental", "number": 42}]
        with mock.patch.dict(module.os.environ, {"GITHUB_REPOSITORY": "o/r"}), \
                mock.patch.object(module, "_gh_api", return_value=existing), \
                mock.patch.object(module.subprocess, "run") as run_mock:
            module.report("sync-incremental", "failure", "https://example/run/2")

        paths = [call.args[0][2] for call in run_mock.call_args_list]
        self.assertNotIn("repos/o/r/issues/42/comments", paths)
        self.assertNotIn("repos/o/r/issues", paths)

    def test_report_closes_the_issue_when_the_workflow_recovers(self) -> None:
        module = _load_health_script()
        existing = [{"title": "Workflow health: sync-incremental", "number": 42}]
        with mock.patch.dict(module.os.environ, {"GITHUB_REPOSITORY": "o/r"}), \
                mock.patch.object(module, "_gh_api", return_value=existing), \
                mock.patch.object(module.subprocess, "run") as run_mock:
            module.report("sync-incremental", "success", "https://example/run/3")

        calls = [" ".join(call.args[0]) for call in run_mock.call_args_list]
        self.assertTrue(any("state=closed" in call for call in calls))

    def test_report_stays_quiet_when_a_healthy_workflow_succeeds(self) -> None:
        module = _load_health_script()
        with mock.patch.dict(module.os.environ, {"GITHUB_REPOSITORY": "o/r"}), \
                mock.patch.object(module, "_gh_api", return_value=[]), \
                mock.patch.object(module.subprocess, "run") as run_mock:
            module.report("sync-incremental", "success", "https://example/run/4")

        run_mock.assert_not_called()

    def test_health_uses_the_largest_matrix_timeout_budget(self) -> None:
        module = _load_health_script()
        with tempfile.TemporaryDirectory() as directory:
            workflow = Path(directory) / "caller.yml"
            workflow.write_text(
                "jobs:\n  sync:\n    strategy:\n      matrix:\n        include:\n"
                "          - timeout_minutes: 30\n          - timeout_minutes: 360\n"
                "    with:\n      timeout_minutes: ${{ matrix.timeout_minutes }}\n",
                encoding="utf-8",
            )
            self.assertEqual(module._workflow_timeout_minutes(workflow), 360)

    def test_daily_audit_reports_latest_failed_run_once(self) -> None:
        module = _load_health_script()
        workflow = {"id": 1, "name": "sync-incremental", "timeout_minutes": 360}
        run = {"status": "completed", "conclusion": "failure", "html_url": "https://example/run/5"}
        with mock.patch.dict(module.os.environ, {"GITHUB_REPOSITORY": "o/r"}), \
                mock.patch.object(module, "_scheduled_workflows", return_value=[workflow]), \
                mock.patch.object(module, "_latest_run", return_value=run), \
                mock.patch.object(module, "_open_health_issue", return_value=None), \
                mock.patch.object(module, "_create_issue") as create_mock:
            module.audit_latest()

        create_mock.assert_called_once()
        self.assertIn("https://example/run/5", create_mock.call_args.args[2])

    def test_daily_audit_ignores_quick_cancellation_but_reports_timeout(self) -> None:
        module = _load_health_script()
        workflow = {"id": 1, "name": "deploy-pages", "timeout_minutes": 30}
        start = datetime.now(timezone.utc) - timedelta(minutes=35)
        cancelled = {
            "status": "completed", "conclusion": "cancelled",
            "run_started_at": start.isoformat(),
            "updated_at": (start + timedelta(minutes=2)).isoformat(),
        }
        with mock.patch.dict(module.os.environ, {"GITHUB_REPOSITORY": "o/r"}), \
                mock.patch.object(module, "_scheduled_workflows", return_value=[workflow]), \
                mock.patch.object(module, "_latest_run", return_value=cancelled), \
                mock.patch.object(module, "_open_health_issue", return_value=None), \
                mock.patch.object(module, "_create_issue") as create_mock:
            module.audit_latest()
            create_mock.assert_not_called()
            cancelled["updated_at"] = (start + timedelta(minutes=31)).isoformat()
            module.audit_latest()

        create_mock.assert_called_once()
        self.assertIn("**cancelled**", create_mock.call_args.args[2])

    def test_daily_audit_closes_a_failure_issue_after_success(self) -> None:
        module = _load_health_script()
        workflow = {"id": 1, "name": "sync-incremental", "timeout_minutes": 360}
        existing = {"number": 42, "body": "`sync-incremental` concluded **failure**."}
        run = {"status": "completed", "conclusion": "success", "html_url": "https://example/run/6"}
        with mock.patch.dict(module.os.environ, {"GITHUB_REPOSITORY": "o/r"}), \
                mock.patch.object(module, "_scheduled_workflows", return_value=[workflow]), \
                mock.patch.object(module, "_latest_run", return_value=run), \
                mock.patch.object(module, "_open_health_issue", return_value=existing), \
                mock.patch.object(module, "_close") as close_mock:
            module.audit_latest()

        close_mock.assert_called_once()

    def test_slow_run_uses_three_or_more_prior_successful_durations(self) -> None:
        module = _load_health_script()

        def run(run_id: int, minutes: int) -> dict:
            start = datetime(2026, 9, 25, tzinfo=timezone.utc)
            return {
                "id": run_id,
                "run_started_at": start.isoformat(),
                "updated_at": (start + timedelta(minutes=minutes)).isoformat(),
            }

        current = run(5, 31)
        history = {"workflow_runs": [current, run(4, 10), run(3, 11), run(2, 9), run(1, 10)]}
        with mock.patch.object(module, "_gh_api", return_value=history) as api:
            self.assertEqual(module._slow_run("o/r", 1, current), (31, 10.0))
            api.assert_called_once_with(
                "repos/o/r/actions/workflows/1/runs",
                "-X", "GET", "-f", "status=success", "-f", "per_page=11",
            )
            self.assertIsNone(module._slow_run("o/r", 1, run(5, 30)))
            history["workflow_runs"] = [current, run(4, 10), run(3, 11)]
            self.assertIsNone(module._slow_run("o/r", 1, current))

    def test_daily_audit_opens_and_resolves_slow_run_issue(self) -> None:
        module = _load_health_script()
        workflow = {"id": 1, "name": "sync-incremental", "timeout_minutes": 360}
        run = {"status": "completed", "conclusion": "success", "html_url": "https://example/run/7"}
        with mock.patch.dict(module.os.environ, {"GITHUB_REPOSITORY": "o/r"}), \
                mock.patch.object(module, "_scheduled_workflows", return_value=[workflow]), \
                mock.patch.object(module, "_latest_run", return_value=run), \
                mock.patch.object(module, "_slow_run", return_value=(40.0, 10.0)) as slow_run, \
                mock.patch.object(module, "_open_health_issue", return_value=None), \
                mock.patch.object(module, "_create_issue") as create_mock:
            module.audit_latest()

        slow_run.assert_called_once_with("o/r", 1, run)
        create_mock.assert_called_once()
        self.assertIn("exceeded 3 times its recent median", create_mock.call_args.args[2])
        self.assertIn("https://example/run/7", create_mock.call_args.args[2])

        existing = {"number": 42, "body": create_mock.call_args.args[2]}
        with mock.patch.dict(module.os.environ, {"GITHUB_REPOSITORY": "o/r"}), \
                mock.patch.object(module, "_scheduled_workflows", return_value=[workflow]), \
                mock.patch.object(module, "_latest_run", return_value=run), \
                mock.patch.object(module, "_slow_run", return_value=(40.0, 10.0)), \
                mock.patch.object(module, "_open_health_issue", return_value=existing), \
                mock.patch.object(module, "_replace_issue_body") as replace_mock:
            module.audit_latest()
            replace_mock.assert_not_called()
            run["html_url"] = "https://example/run/8"
            module.audit_latest()

        replace_mock.assert_called_once()
        self.assertIn("https://example/run/8", replace_mock.call_args.args[2])

        with mock.patch.dict(module.os.environ, {"GITHUB_REPOSITORY": "o/r"}), \
                mock.patch.object(module, "_scheduled_workflows", return_value=[workflow]), \
                mock.patch.object(module, "_latest_run", return_value=run), \
                mock.patch.object(module, "_slow_run", return_value=None), \
                mock.patch.object(module, "_open_health_issue", return_value=existing), \
                mock.patch.object(module, "_close") as close_mock:
            module.audit_latest()

        close_mock.assert_called_once()
        self.assertEqual(close_mock.call_args.args[:2], ("o/r", 42))

    def test_read_requests_force_the_get_method(self) -> None:
        # gh turns a bare -f into a request body and posts it, so a read that
        # carries query parameters without -X GET reaches the API as a POST and
        # fails. Every other test here mocks _gh_api, so nothing else can catch
        # this.
        module = _load_health_script()
        with mock.patch.dict(module.os.environ, {"GITHUB_REPOSITORY": "o/r"}), \
                mock.patch.object(module.subprocess, "run") as run_mock:
            run_mock.return_value = mock.Mock(stdout='{"workflow_runs": []}')
            module._last_success("o/r", 1)
            module._latest_run("o/r", 1)
            module._scheduled_workflows("o/r")

        for call in run_mock.call_args_list:
            argv = call.args[0]
            with self.subTest(argv=" ".join(argv)):
                if any(arg == "-f" for arg in argv):
                    self.assertIn("-X", argv)
                    self.assertEqual(argv[argv.index("-X") + 1], "GET")
        latest_argv = run_mock.call_args_list[1].args[0]
        self.assertIn("status=completed", latest_argv)

    def test_manual_recovery_counts_as_a_recent_success(self) -> None:
        module = _load_health_script()
        recovered_at = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
        with mock.patch.object(module.subprocess, "run") as run_mock:
            run_mock.return_value = mock.Mock(
                stdout=json.dumps({"workflow_runs": [{"created_at": recovered_at}]})
            )
            self.assertIsNotNone(module._last_success("o/r", 1))

        argv = run_mock.call_args.args[0]
        self.assertIn("status=success", argv)
        self.assertNotIn("event=schedule", argv)

    def test_staleness_closes_an_obsolete_issue_after_manual_recovery(self) -> None:
        module = _load_health_script()
        workflows = [{"id": 1, "name": "audit-recent", "interval_days": 31}]
        existing = {
            "number": 42,
            "body": "`audit-recent` has no successful scheduled run within the last 62 days.",
        }
        with mock.patch.dict(module.os.environ, {"GITHUB_REPOSITORY": "o/r"}), \
                mock.patch.object(module, "_scheduled_workflows", return_value=workflows), \
                mock.patch.object(module, "_last_success", return_value=datetime.now(timezone.utc)), \
                mock.patch.object(module, "_open_health_issue", return_value=existing), \
                mock.patch.object(module, "_close") as close_mock:
            module.stale(14)

        close_mock.assert_called_once()
        self.assertEqual(close_mock.call_args.args[:2], ("o/r", 42))

    def test_staleness_does_not_close_a_newer_failure_issue(self) -> None:
        module = _load_health_script()
        workflows = [{"id": 1, "name": "sync-incremental", "interval_days": 7}]
        existing = {
            "number": 42,
            "body": "`sync-incremental` concluded **failure**.",
        }
        with mock.patch.dict(module.os.environ, {"GITHUB_REPOSITORY": "o/r"}), \
                mock.patch.object(module, "_scheduled_workflows", return_value=workflows), \
                mock.patch.object(module, "_last_success", return_value=datetime.now(timezone.utc)), \
                mock.patch.object(module, "_open_health_issue", return_value=existing), \
                mock.patch.object(module, "_close") as close_mock:
            module.stale(14)

        close_mock.assert_not_called()

    def test_staleness_only_considers_workflows_that_actually_have_a_schedule(self) -> None:
        module = _load_health_script()
        scheduled = module._scheduled_workflow_paths()

        self.assertIn(".github/workflows/sync-incremental.yml", scheduled)
        self.assertIn(".github/workflows/audit-recent.yml", scheduled)
        self.assertNotIn(".github/workflows/sync-full.yml", scheduled)
        self.assertNotIn(".github/workflows/ci.yml", scheduled)

    def test_staleness_window_follows_each_workflow_cadence(self) -> None:
        module = _load_health_script()

        self.assertEqual(module._interval_days('    - cron: "15 3 * * 1"'), 7)
        self.assertEqual(module._interval_days('    - cron: "45 3 1 * *"'), 31)
        self.assertEqual(module._interval_days('    - cron: "20 3 * * *"'), 1)
        # The most frequent cron is the one silence should be measured against.
        self.assertEqual(
            module._interval_days('- cron: "45 3 1 * *"\n- cron: "20 3 * * *"'), 1
        )

    def test_a_monthly_workflow_is_not_stale_three_weeks_after_it_ran(self) -> None:
        # audit-recent runs on the 1st. A flat 14-day window reported it stale
        # from the 15th of every month however healthy it was, which is noise
        # that trains the operator to ignore the label.
        module = _load_health_script()
        workflows = [{"id": 1, "name": "audit-recent", "interval_days": 31}]
        last = datetime.now(timezone.utc) - timedelta(days=21)
        with mock.patch.dict(module.os.environ, {"GITHUB_REPOSITORY": "o/r"}), \
                mock.patch.object(module, "_scheduled_workflows", return_value=workflows), \
                mock.patch.object(module, "_last_success", return_value=last), \
                mock.patch.object(module, "_open_health_issue", return_value=None), \
                mock.patch.object(module.subprocess, "run") as run_mock:
            module.stale(14)

        run_mock.assert_not_called()

    def test_a_weekly_workflow_is_still_stale_after_two_missed_runs(self) -> None:
        module = _load_health_script()
        workflows = [{"id": 1, "name": "sync-incremental", "interval_days": 7}]
        last = datetime.now(timezone.utc) - timedelta(days=21)
        with mock.patch.dict(module.os.environ, {"GITHUB_REPOSITORY": "o/r"}), \
                mock.patch.object(module, "_scheduled_workflows", return_value=workflows), \
                mock.patch.object(module, "_last_success", return_value=last), \
                mock.patch.object(module, "_open_health_issue", return_value=None), \
                mock.patch.object(module, "_create_issue") as create_mock:
            module.stale(14)

        create_mock.assert_called_once()

    def test_staleness_never_reports_the_health_workflow_against_itself(self) -> None:
        # Its staleness pass runs before that same run can succeed, so it would
        # report itself as never having succeeded; and nothing would close the
        # issue, because recovery is only detected through workflow_run, which
        # it deliberately does not receive for itself.
        module = _load_health_script()

        self.assertNotIn(".github/workflows/workflow-health.yml", module._scheduled_workflow_paths())


if __name__ == "__main__":
    unittest.main()


class ProviderMatrixWorkflowTests(unittest.TestCase):
    def setUp(self) -> None:
        self.workflows = {
            path.name: _workflow(path.read_text(encoding="utf-8"))
            for path in (REPO_ROOT / ".github/workflows").glob("*.yml")
        }
        self.callers = {
            filename: workflow for filename, workflow in self.workflows.items()
            if any(job.get("uses") == "./.github/workflows/_sync-provider.yml"
                   for job in workflow["jobs"].values())
        }
        self.reusable = self.workflows["_sync-provider.yml"]

    def test_every_provider_has_exactly_one_sync_owner(self) -> None:
        providers = ["moex"]  # Its full, probe, and audit procedures remain dedicated.
        scheduled_slots = 0
        manual_providers = set()
        quarantined = quarantined_provider_ids(REPO_ROOT, site_id="default")
        for filename, workflow in self.callers.items():
            job = workflow["jobs"]["sync"]
            with self.subTest(workflow=filename):
                if "schedule" in workflow["on"]:
                    scheduled_slots += 1
                    self.assertIn("workflow_dispatch", workflow["on"])
                    self.assertFalse(job["strategy"]["fail-fast"])
                    self.assertEqual(job["strategy"]["max-parallel"], 3)
                    self.assertIn("matrix.provider_id", job["name"])
                    rows = job["strategy"]["matrix"]["include"]
                    for key in ("provider_id", "cache_prefix", "timeout_minutes", "publish", "prune_orphaned_mirror"):
                        self.assertEqual(job["with"][key], "${{ matrix." + key + " }}")
                else:
                    self.assertEqual(set(workflow["on"]), {"workflow_dispatch"})
                    rows = [job["with"]]
                    manual_providers.add(rows[0]["provider_id"])
                for row in rows:
                    provider = row["provider_id"]
                    providers.append(provider)
                    self.assertEqual(row["cache_prefix"], provider.replace("_", "-"))
                    self.assertIsInstance(row["timeout_minutes"], int)
                    self.assertGreater(row["timeout_minutes"], 0)
                    self.assertLessEqual(row["timeout_minutes"], 360)
                    self.assertIsInstance(row["publish"], bool)
                    expected_publish = provider not in quarantined and "schedule" in workflow["on"]
                    self.assertEqual(row["publish"], expected_publish, provider)
                    self.assertEqual(row.get("prune_orphaned_mirror", False), provider == "hakka_cert")
                    if provider == "hakka_cert":
                        self.assertEqual(row["timeout_minutes"], 360)
        self.assertEqual(scheduled_slots, 4)
        self.assertEqual(manual_providers, {"taisugar_recruit", "teacher_recruit_central_alliance"})
        self.assertEqual(len(providers), len(set(providers)), "A provider has multiple sync owners")
        self.assertEqual(set(providers), set(_PROVIDER_FACTORIES))

    def test_sync_persists_downloads_and_failure_evidence_before_failing(self) -> None:
        job = self.reusable["jobs"]["sync"]
        self.assertEqual(self.reusable["permissions"]["contents"], "read")
        self.assertEqual(job["concurrency"]["group"], "sync-${{ inputs.provider_id }}")
        self.assertEqual(job["concurrency"]["queue"], "max")
        steps = job["steps"]
        sync = next(step for step in steps if step.get("id") == "sync")
        artifact = next(step for step in steps if step.get("id") == "snapshot")
        save = next(step for step in steps if step.get("uses", "").startswith("actions/cache/save@"))
        restore = next(step for step in steps if step.get("uses", "").startswith("actions/cache/restore@"))
        fail = next(step for step in steps if step.get("name") == "Surface sync failure")
        self.assertTrue(sync["continue-on-error"])
        self.assertIn("!cancelled()", save["if"])
        self.assertIn("steps.sync.outcome != 'skipped'", save["if"])
        self.assertEqual(restore["with"]["path"], "mirror")
        self.assertEqual(restore["with"]["key"], save["with"]["key"])
        self.assertEqual(save["with"]["key"], "${{ steps.baseline.outputs.cache_key }}")
        baseline = next(step for step in steps if step.get("id") == "baseline")
        self.assertIn("github.run_id", baseline["env"]["CACHE_KEY"])
        self.assertIn("github.run_attempt", baseline["env"]["CACHE_KEY"])
        self.assertIn("inputs.cache_prefix", restore["with"]["restore-keys"])
        self.assertEqual(artifact["with"]["if-no-files-found"], "error")
        self.assertTrue(artifact["with"]["include-hidden-files"])
        self.assertEqual(set(artifact["with"]["path"].splitlines()), {
            "data/providers/${{ inputs.provider_id }}", ".tmp/site-publish-plan.json",
        })
        self.assertIn("steps.sync.outcome != 'skipped'", artifact["if"])
        self.assertLess(steps.index(sync), steps.index(save))
        self.assertLess(steps.index(save), steps.index(artifact))
        self.assertLess(steps.index(artifact), steps.index(fail))
        self.assertEqual(fail["if"], "${{ steps.sync.outcome == 'failure' }}")
        self.assertEqual(fail["run"], "exit 1")
        self.assertFalse(any("commit-and-push" in step.get("run", "") for step in steps))

    def test_all_site_writers_queue_against_current_main(self) -> None:
        job = self.reusable["jobs"]["publish"]
        self.assertEqual(job["needs"], "sync")
        self.assertIn("needs.sync.result == 'success' || !inputs.publish", job["if"])
        self.assertIn("needs.sync.outputs.snapshot == 'true'", job["if"])
        self.assertEqual(job["permissions"]["contents"], "write")
        writers = [job] + [self.workflows[name] for name in (
            "sync-full.yml", "sync-incremental.yml", "audit-recent.yml",
        )]
        for writer in writers:
            self.assertEqual(writer["concurrency"]["group"], "site-publication-default")
            self.assertEqual(writer["concurrency"]["queue"], "max")
            self.assertFalse(writer["concurrency"]["cancel-in-progress"])
            steps = writer.get("steps") or next(iter(writer["jobs"].values()))["steps"]
            checkout = next(step for step in steps if step.get("uses", "").startswith("actions/checkout@"))
            self.assertEqual(checkout["with"]["ref"], "main")
            self.assertEqual(checkout["with"]["fetch-depth"], 0)

    def test_publication_uploads_before_committing_the_matching_site_state(self) -> None:
        steps = self.reusable["jobs"]["publish"]["steps"]
        snapshot = next(step for step in steps if "apply_provider_snapshot.py" in step.get("run", ""))
        mirror = next(step for step in steps if step.get("uses", "").startswith("actions/cache/restore@"))
        publish = next(step for step in steps if "app publish-site" in step.get("run", ""))
        ensure = next(step for step in steps if "release_assets.py ensure" in step.get("run", ""))
        upload = next(step for step in steps if step.get("id") == "upload")
        commits = [step for step in steps if "commit-and-push.sh" in step.get("run", "")]
        self.assertIn("needs.sync.outputs.base", snapshot["env"]["BASE_SHA"])
        self.assertTrue(mirror["with"]["fail-on-cache-miss"])
        self.assertNotIn("restore-keys", mirror["with"])
        sync_save = next(step for step in self.reusable["jobs"]["sync"]["steps"]
                         if step.get("uses", "").startswith("actions/cache/save@"))
        self.assertEqual(mirror["with"]["key"], "${{ needs.sync.outputs.cache_key }}")
        self.assertEqual(sync_save["with"]["key"], "${{ steps.baseline.outputs.cache_key }}")
        self.assertEqual(self.reusable["jobs"]["sync"]["outputs"]["cache_key"], sync_save["with"]["key"])
        artifact = next(step for step in steps if step.get("uses", "").startswith("actions/download-artifact@"))
        self.assertEqual(artifact["with"]["name"], "${{ needs.sync.outputs.artifact_name }}")
        ordered = [snapshot, mirror, publish, ensure, upload, *commits]
        self.assertEqual([steps.index(step) for step in ordered], sorted(steps.index(step) for step in ordered))
        self.assertIn("steps.upload.outcome == 'success'", commits[0]["if"])
        self.assertIn("data/sites/default", commits[0]["run"])
        self.assertEqual(commits[1]["if"], "${{ !inputs.publish }}")
        self.assertNotIn("data/sites/", commits[1]["run"])
        self.assertFalse(any("release_assets.py prune" in step.get("run", "") for step in steps))

    def test_every_runner_job_has_a_timeout_and_actions_use_one_version(self) -> None:
        versions = {}
        for filename, workflow in self.workflows.items():
            for job in workflow["jobs"].values():
                with self.subTest(workflow=filename):
                    if "runs-on" in job:
                        budget = job["timeout-minutes"]
                        self.assertTrue(isinstance(budget, int) or budget == "${{ inputs.timeout_minutes }}")
                    for step in job.get("steps", []):
                        action = step.get("uses", "")
                        if "@" in action:
                            name, version = action.split("@", 1)
                            versions.setdefault(name, set()).add(version)
        self.assertTrue(versions)
        self.assertEqual({name: seen for name, seen in versions.items() if len(seen) != 1}, {})

    def test_release_recovery_steps_have_a_github_token(self) -> None:
        count = 0
        for workflow in self.workflows.values():
            for job in workflow["jobs"].values():
                for step in job.get("steps", []):
                    if "--download-affected-bundles" in step.get("run", ""):
                        count += 1
                        self.assertIn("GH_TOKEN", step.get("env", {}))
        self.assertGreaterEqual(count, 3)
