import hashlib
import importlib.util
import json
import re
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest import mock

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


def _workflow_run_workflows(workflow: str) -> list[str]:
    names: list[str] = []
    inside_trigger = False
    inside_workflows = False

    for line in workflow.splitlines():
        stripped = line.lstrip()
        indent = len(line) - len(stripped)

        if stripped == "workflow_run:" and indent == 2:
            inside_trigger = True
            inside_workflows = False
            continue

        if inside_trigger and stripped and not stripped.startswith("#") and indent <= 2:
            inside_trigger = False
            inside_workflows = False

        if not inside_trigger:
            continue

        if stripped == "workflows:" and indent == 4:
            inside_workflows = True
            continue

        if inside_workflows and stripped.startswith("- ") and indent == 6:
            names.append(stripped[2:])
            continue

        if inside_workflows and stripped and indent <= 4:
            inside_workflows = False

    return names


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
    paths: list[str] = []
    inside_push = False
    inside_paths = False

    for line in workflow.splitlines():
        stripped = line.lstrip()
        indent = len(line) - len(stripped)

        if stripped == "push:" and indent == 2:
            inside_push = True
            inside_paths = False
            continue

        if inside_push and stripped and indent <= 2:
            inside_push = False
            inside_paths = False

        if not inside_push:
            continue

        if stripped == "paths:" and indent == 4:
            inside_paths = True
            continue

        if inside_paths and stripped.startswith("- ") and indent == 6:
            paths.append(stripped[2:])
            continue

        if inside_paths and stripped and indent <= 4:
            inside_paths = False

    return paths


class WorkflowTests(unittest.TestCase):
    def test_incremental_workflow_fails_fast_when_release_is_incomplete_on_hosted_ci(self) -> None:
        workflow_path = REPO_ROOT / ".github" / "workflows" / "sync-incremental.yml"
        workflow = workflow_path.read_text(encoding="utf-8")

        self.assertIn("release_assets.py coverage", workflow)
        self.assertIn("bootstrap_required", workflow)
        self.assertNotIn("python -m app sync-full", workflow)
        self.assertIn('python -m app publish-site --site-id default --repository "${{ github.repository }}"', workflow)
        self.assertIn("steps.release_state.outputs.bootstrap_required == 'true'", workflow)
        self.assertIn("Hosted bootstrap is unsupported on GitHub-hosted runners.", workflow)

    def test_incremental_workflow_probes_before_syncing(self) -> None:
        workflow_path = REPO_ROOT / ".github" / "workflows" / "sync-incremental.yml"
        workflow = workflow_path.read_text(encoding="utf-8")

        self.assertIn("python -m app probe-latest --provider moex --years 2 --manifest data/providers/moex/source-manifest.json", workflow)
        self.assertIn(
            "python -m app sync-targeted --provider moex --probe .tmp/source-probe.json --manifest data/providers/moex/source-manifest.json --download-affected-bundles --publish-plan-output .tmp/site-publish-plan.json",
            workflow,
        )
        self.assertIn(
            'python -m app publish-site --site-id default --repository "${{ github.repository }}" --publish-plan .tmp/site-publish-plan.json',
            workflow,
        )
        self.assertLess(workflow.index("python -m app probe-latest"), workflow.index("python -m app sync-targeted"))
        self.assertLess(workflow.index("python -m app sync-targeted"), workflow.index("python -m app publish-site"))

    def test_incremental_workflow_can_exit_before_heavy_steps_when_unchanged(self) -> None:
        workflow_path = REPO_ROOT / ".github" / "workflows" / "sync-incremental.yml"
        workflow = workflow_path.read_text(encoding="utf-8")

        self.assertIn("steps.probe.outputs.should_sync == 'true'", workflow)
        self.assertIn(".tmp/source-probe.json", workflow)
        self.assertIn("steps.probe.outputs.should_sync != 'true'", workflow)
        self.assertIn("commit-and-push.sh \"chore: refresh source manifest\" data/providers/moex/source-manifest.json", workflow)

    def test_incremental_workflow_downloads_only_affected_release_bundles_via_targeted_sync(self) -> None:
        workflow_path = REPO_ROOT / ".github" / "workflows" / "sync-incremental.yml"
        workflow = workflow_path.read_text(encoding="utf-8")

        self.assertNotIn('gh release download "$MOEX_RELEASE_TAG" --pattern "*.zip" --dir bundles', workflow)
        self.assertIn("--download-affected-bundles", workflow)
        self.assertIn("--publish-plan-output .tmp/site-publish-plan.json", workflow)

    def test_monthly_audit_workflow_exists(self) -> None:
        workflow_path = REPO_ROOT / ".github" / "workflows" / "audit-recent.yml"
        workflow = workflow_path.read_text(encoding="utf-8")

        self.assertIn('- cron: "45 3 1 * *"', workflow)
        self.assertIn("release_assets.py coverage", workflow)
        self.assertIn("bootstrap_required", workflow)
        self.assertNotIn("python -m app sync-full --provider moex --write-manifest --manifest data/providers/moex/source-manifest.json", workflow)
        self.assertIn("Hosted bootstrap is unsupported on GitHub-hosted runners.", workflow)
        self.assertIn(
            "python -m app sync-incremental --provider moex --years 2 --write-manifest --manifest data/providers/moex/source-manifest.json --download-affected-bundles --publish-plan-output .tmp/site-publish-plan.json",
            workflow,
        )
        self.assertIn(
            'python -m app publish-site --site-id default --repository "${{ github.repository }}" --publish-plan .tmp/site-publish-plan.json',
            workflow,
        )
        self.assertIn("--write-manifest", workflow)
        self.assertIn("release_assets.py prune", workflow)

    def test_workflows_prune_stale_assets_via_shared_script(self) -> None:
        workflows_dir = REPO_ROOT / ".github" / "workflows"
        for workflow_name in ("sync-full.yml", "sync-incremental.yml", "audit-recent.yml"):
            workflow = (workflows_dir / workflow_name).read_text(encoding="utf-8")
            self.assertIn("release_assets.py upload", workflow)
            self.assertIn("release_assets.py prune", workflow)

    def test_ceec_ast_sync_publishes_its_affected_bundles(self) -> None:
        caller = (REPO_ROOT / ".github" / "workflows" / "sync-ceec-ast.yml").read_text(encoding="utf-8")
        workflow = (REPO_ROOT / ".github" / "workflows" / "_sync-provider.yml").read_text(encoding="utf-8")

        self.assertIn("uses: ./.github/workflows/_sync-provider.yml", caller)
        self.assertIn("provider_id: ceec_ast", caller)
        self.assertIn("cache_prefix: ceec-ast", caller)
        self.assertIn("publish: true", caller)

        steps = (
            "--publish-plan-output .tmp/site-publish-plan.json",
            "python -m app publish-site --site-id default",
            "release_assets.py ensure",
            "release_assets.py upload",
            "commit-and-push.sh",
        )
        self.assertEqual(list(map(workflow.index, steps)), sorted(map(workflow.index, steps)))
        self.assertIn("--publish-plan .tmp/site-publish-plan.json", workflow)
        self.assertIn('"data/providers/$PROVIDER_ID" data/sites/default', workflow)
        self.assertNotIn("release_assets.py prune", workflow)
        self.assertLess(workflow.index("actions/cache/restore@"), workflow.index("Run provider sync"))
        self.assertLess(workflow.index("actions/cache/save@"), workflow.index("Publish affected default-site bundles"))
        self.assertIn("steps.sync.outcome != 'skipped'", workflow)
        self.assertIn("continue-on-error: true", workflow)
        self.assertIn("inputs.publish && steps.sync.outcome == 'success' && steps.upload.outcome == 'success'", workflow)
        self.assertIn("Surface sync failure", workflow)
        commit_step = workflow[workflow.index("- name: Commit provider and site state"):]
        self.assertNotIn("if: '!cancelled()'", commit_step)

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

    def test_every_action_is_pinned_to_one_version_across_all_workflows(self) -> None:
        # checkout drifted to v4 in forty workflows while deploy-pages moved on
        # to v6, so most of CI kept running the Node 20 runtime GitHub has
        # deprecated. Splitting an action across majors is how that goes
        # unnoticed; requiring one version per action makes an upgrade
        # all-or-nothing.
        versions: dict[str, set[str]] = {}
        for path in sorted((REPO_ROOT / ".github" / "workflows").glob("*.yml")):
            for match in re.finditer(
                r"uses: ([\w.-]+/[\w.-]+)@(v[\d.]+)", path.read_text(encoding="utf-8")
            ):
                versions.setdefault(match.group(1), set()).add(match.group(2))

        self.assertTrue(versions)
        split = {action: sorted(seen) for action, seen in versions.items() if len(seen) > 1}
        self.assertEqual(split, {})

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
        for workflow_name in ("sync-full.yml", "sync-incremental.yml", "audit-recent.yml", "sync-ceec-gsat.yml"):
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

    def test_provider_workflows_save_fresh_mirror_cache_after_sync_attempts(self) -> None:
        workflows_dir = REPO_ROOT / ".github" / "workflows"
        for workflow_path in sorted(workflows_dir.glob("sync-*.yml")):
            workflow = workflow_path.read_text(encoding="utf-8")
            if re.search(r"(?m)^      - name: Commit regenerated .* provider data$", workflow) is None:
                continue
            with self.subTest(workflow=workflow_path.name):
                self.assertEqual(workflow.count("actions/cache/restore@v6"), 1)
                self.assertEqual(workflow.count("actions/cache/save@v6"), 1)
                self.assertEqual(workflow.count("github.run_id"), 2)
                self.assertEqual(workflow.count("github.run_attempt"), 2)
                self.assertIn("if: '!cancelled()'\n        uses: actions/cache/save@v6", workflow)
                self.assertLess(workflow.index("Restore mirror directory"), workflow.index("Run "))
                self.assertLess(workflow.index("Run "), workflow.index("Save mirror directory"))
                self.assertLess(workflow.index("Save mirror directory"), workflow.index("Commit regenerated"))

    def test_workflows_define_timeout_and_concurrency_controls(self) -> None:
        workflows_dir = REPO_ROOT / ".github" / "workflows"
        for workflow_name in ("sync-full.yml", "sync-incremental.yml", "audit-recent.yml"):
            workflow = (workflows_dir / workflow_name).read_text(encoding="utf-8")
            self.assertIn("concurrency:", workflow)
            self.assertIn("timeout-minutes:", workflow)

    def test_every_workflow_job_has_an_explicit_timeout(self) -> None:
        workflows_dir = REPO_ROOT / ".github" / "workflows"

        for workflow_path in sorted(workflows_dir.glob("*.yml")):
            workflow = workflow_path.read_text(encoding="utf-8")
            with self.subTest(workflow=workflow_path.name):
                self.assertEqual(
                    workflow.count("runs-on:"),
                    workflow.count("timeout-minutes:"),
                    f"{workflow_path.name} has a job without an explicit timeout",
                )

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
        for workflow_name in ("sync-full.yml", "sync-incremental.yml", "audit-recent.yml", "sync-hakka-cert.yml"):
            workflow = (workflows_dir / workflow_name).read_text(encoding="utf-8")
            self.assertIn("timeout-minutes: 360", workflow, workflow_name)

    def test_shared_commit_commands_are_valid_yaml_block_scalars(self) -> None:
        workflows_dir = REPO_ROOT / ".github" / "workflows"
        for workflow_path in sorted(workflows_dir.glob("*.yml")):
            lines = workflow_path.read_text(encoding="utf-8").splitlines()
            for index, line in enumerate(lines):
                if "commit-and-push.sh" not in line:
                    continue
                self.assertGreater(index, 0, workflow_path.name)
                self.assertEqual(lines[index - 1].strip(), "run: >-", workflow_path.name)
                self.assertFalse(line.lstrip().startswith("run:"), workflow_path.name)

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
            self.assertIn("python -m app publish-site --site-id default", workflow)
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
        workflow = (REPO_ROOT / ".github" / "workflows" / "sync-full.yml").read_text(encoding="utf-8")

        self.assertIn("allow_unsupported_hosted_bootstrap", workflow)
        self.assertIn("Hosted bootstrap is unsupported on GitHub-hosted runners.", workflow)
        self.assertIn("python -m app sync-full --provider moex --write-manifest --manifest data/providers/moex/source-manifest.json", workflow)

    def test_sync_ceec_gsat_workflow_is_provider_only_until_default_site_publication_is_safe(self) -> None:
        workflow = (REPO_ROOT / ".github" / "workflows" / "sync-ceec-gsat.yml").read_text(encoding="utf-8")

        self.assertIn('- cron: "20 3 * * 6"', workflow)
        self.assertIn("python -m app sync-full --provider ceec_gsat --site-id default", workflow)
        self.assertNotIn('python -m app publish-site --site-id default --repository "${{ github.repository }}"', workflow)
        self.assertNotIn("release_assets.py ensure", workflow)
        self.assertNotIn("release_assets.py upload", workflow)
        self.assertNotIn("release_assets.py prune", workflow)

    def test_taisugar_sync_is_manual_until_reviewed_migration_can_publish(self) -> None:
        workflow = (REPO_ROOT / ".github" / "workflows" / "sync-taisugar-recruit.yml").read_text(encoding="utf-8")
        health = (REPO_ROOT / ".github" / "workflows" / "workflow-health.yml").read_text(encoding="utf-8")

        self.assertIn("workflow_dispatch:", workflow)
        self.assertNotIn("schedule:", workflow)
        self.assertNotIn("sync-taisugar-recruit", _workflow_run_workflows(health))

    def test_readme_documents_human_friendly_bundle_assets(self) -> None:
        readme = (REPO_ROOT / "README.md").read_text(encoding="utf-8")

        self.assertIn("Bundle filenames use Chinese display names plus canonical IDs.", readme)
        self.assertIn("Release assets can include legacy compatibility alias names during migration.", readme)
        self.assertIn("Bundle asset: `\u8b77\u7406\u5e2b__nurse.zip`", readme)
        self.assertIn(
            "Archive entry: `115/115030_\u8b77\u7406\u5e2b/101_0101_\u57fa\u790e\u91ab\u5b78_\u8a66\u984c.pdf`",
            readme,
        )
        self.assertNotIn("optimize-mirror-pdfs", readme)


class FinancialCertWorkflowTests(unittest.TestCase):
    def test_sync_sfi_cert_workflow_is_provider_only(self) -> None:
        workflow = (REPO_ROOT / ".github" / "workflows" / "sync-sfi-cert.yml").read_text(encoding="utf-8")

        self.assertIn("python -m app sync-full --provider sfi_cert --site-id default", workflow)
        self.assertNotIn('python -m app publish-site --site-id default --repository "${{ github.repository }}"', workflow)
        self.assertNotIn("release_assets.py", workflow)

    def test_sync_tabf_cert_workflow_is_provider_only(self) -> None:
        workflow = (REPO_ROOT / ".github" / "workflows" / "sync-tabf-cert.yml").read_text(encoding="utf-8")

        self.assertIn("python -m app sync-full --provider tabf_cert --site-id default", workflow)
        self.assertNotIn('python -m app publish-site --site-id default --repository "${{ github.repository }}"', workflow)
        self.assertNotIn("release_assets.py", workflow)

    def test_sync_tii_cert_workflow_is_provider_only(self) -> None:
        workflow = (REPO_ROOT / ".github" / "workflows" / "sync-tii-cert.yml").read_text(encoding="utf-8")

        self.assertIn("python -m app sync-full --provider tii_cert --site-id default", workflow)
        self.assertNotIn('python -m app publish-site --site-id default --repository "${{ github.repository }}"', workflow)
        self.assertNotIn("release_assets.py", workflow)

    def test_financial_cert_workflows_have_schedule(self) -> None:
        workflows_dir = REPO_ROOT / ".github" / "workflows"
        for name in ("sync-sfi-cert.yml", "sync-tabf-cert.yml", "sync-tii-cert.yml"):
            with self.subTest(name=name):
                workflow = (workflows_dir / name).read_text(encoding="utf-8")
                self.assertIn("schedule:", workflow)
                self.assertIn("cron:", workflow)
                self.assertIn("workflow_dispatch:", workflow)


class RequestedTopicWorkflowTests(unittest.TestCase):
    def test_sync_teacher_recruit_tainan_workflow_is_provider_only(self) -> None:
        workflow = (REPO_ROOT / ".github" / "workflows" / "sync-teacher-recruit-tainan.yml").read_text(encoding="utf-8")

        self.assertIn('- cron: "25 5 * * 2"', workflow)
        self.assertNotIn('- cron: "35 5 * * 2"', workflow)
        self.assertIn("python -m app sync-full --provider teacher_recruit_tainan --site-id default", workflow)
        self.assertIn("schedule:", workflow)
        self.assertIn("workflow_dispatch:", workflow)
        self.assertNotIn('python -m app publish-site --site-id default --repository "${{ github.repository }}"', workflow)
        self.assertNotIn("release_assets.py", workflow)

    def test_sync_teacher_recruit_taipei_junior_workflow_is_provider_only(self) -> None:
        workflow = (REPO_ROOT / ".github" / "workflows" / "sync-teacher-recruit-taipei-junior.yml").read_text(encoding="utf-8")

        self.assertIn("python -m app sync-full --provider teacher_recruit_taipei_junior --site-id default", workflow)
        self.assertIn("schedule:", workflow)
        self.assertIn("workflow_dispatch:", workflow)
        self.assertNotIn('python -m app publish-site --site-id default --repository "${{ github.repository }}"', workflow)
        self.assertNotIn("release_assets.py", workflow)

    def test_sync_teacher_recruit_taipei_elementary_workflow_is_provider_only(self) -> None:
        workflow = (REPO_ROOT / ".github" / "workflows" / "sync-teacher-recruit-taipei-elementary.yml").read_text(encoding="utf-8")

        self.assertIn('- cron: "55 5 * * 2"', workflow)
        self.assertIn("python -m app sync-full --provider teacher_recruit_taipei_elementary --site-id default", workflow)
        self.assertIn("schedule:", workflow)
        self.assertIn("workflow_dispatch:", workflow)
        self.assertNotIn('python -m app publish-site --site-id default --repository "${{ github.repository }}"', workflow)
        self.assertNotIn("release_assets.py", workflow)

    def test_sync_newtaipei_teacher_recruit_workflow_is_provider_only(self) -> None:
        workflow = (REPO_ROOT / ".github" / "workflows" / "sync-teacher-recruit-newtaipei.yml").read_text(encoding="utf-8")

        self.assertIn('- cron: "5 5 * * 2"', workflow)
        self.assertIn("python -m app sync-full --provider teacher_recruit_newtaipei --site-id default", workflow)
        self.assertIn("schedule:", workflow)
        self.assertIn("workflow_dispatch:", workflow)
        self.assertNotIn('python -m app publish-site --site-id default --repository "${{ github.repository }}"', workflow)
        self.assertNotIn("release_assets.py", workflow)

    def test_sync_taoyuan_teacher_recruit_workflow_is_provider_only(self) -> None:
        workflow = (REPO_ROOT / ".github" / "workflows" / "sync-teacher-recruit-taoyuan-elementary.yml").read_text(encoding="utf-8")

        self.assertIn('- cron: "5 6 * * 2"', workflow)
        self.assertIn("python -m app sync-full --provider teacher_recruit_taoyuan_elementary --site-id default", workflow)
        self.assertIn("schedule:", workflow)
        self.assertIn("workflow_dispatch:", workflow)
        self.assertNotIn('python -m app publish-site --site-id default --repository "${{ github.repository }}"', workflow)
        self.assertNotIn("release_assets.py", workflow)

    def test_sync_kaohsiung_teacher_recruit_workflow_is_provider_only(self) -> None:
        workflow = (REPO_ROOT / ".github" / "workflows" / "sync-teacher-recruit-kaohsiung.yml").read_text(encoding="utf-8")

        self.assertIn('- cron: "25 6 * * 2"', workflow)
        self.assertIn("python -m app sync-full --provider teacher_recruit_kaohsiung --site-id default", workflow)
        self.assertIn("schedule:", workflow)
        self.assertIn("workflow_dispatch:", workflow)
        self.assertNotIn('python -m app publish-site --site-id default --repository "${{ github.repository }}"', workflow)
        self.assertNotIn("release_assets.py", workflow)

    def test_sync_central_alliance_is_manual_while_its_2026_source_is_expired(self) -> None:
        workflow = (REPO_ROOT / ".github" / "workflows" / "sync-teacher-recruit-central-alliance.yml").read_text(encoding="utf-8")
        health = (REPO_ROOT / ".github" / "workflows" / "workflow-health.yml").read_text(encoding="utf-8")

        self.assertIn("python -m app sync-full --provider teacher_recruit_central_alliance --site-id default", workflow)
        self.assertNotIn("schedule:", workflow)
        self.assertIn("workflow_dispatch:", workflow)
        self.assertNotIn("sync-teacher-recruit-central-alliance", _workflow_run_workflows(health))
        self.assertNotIn('python -m app publish-site --site-id default --repository "${{ github.repository }}"', workflow)
        self.assertNotIn("release_assets.py", workflow)


class LaunchCITest(unittest.TestCase):
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


class FailedSyncCommitGuardTest(unittest.TestCase):
    # Provider sync commands can write partial output before exiting nonzero.
    # Their commit step still runs so the shared publisher can inspect the
    # staged tree, but its preflight must refuse non-deployable output.
    def _provider_sync_workflows(self) -> list[Path]:
        workflows_dir = REPO_ROOT / ".github" / "workflows"
        return [
            path
            for path in sorted(workflows_dir.glob("sync-*.yml"))
            if path.name not in {"sync-incremental.yml", "sync-full.yml", "sync-ceec-ast.yml"}
        ]

    def test_provider_sync_workflows_route_partial_results_through_shared_guard(self) -> None:
        workflows = self._provider_sync_workflows()
        self.assertTrue(workflows)

        for path in workflows:
            lines = path.read_text(encoding="utf-8").splitlines()
            commit_indexes = [i for i, line in enumerate(lines) if "commit-and-push.sh" in line]
            with self.subTest(workflow=path.name):
                self.assertEqual(len(commit_indexes), 1)
                preceding = lines[: commit_indexes[0]]
                self.assertIn("        if: '!cancelled()'", preceding)

    def test_publishing_workflows_stay_fail_closed_on_a_partial_sync(self) -> None:
        # These workflows synchronize and publish in one job. Their
        # commit step stays success-gated so a failed sync cannot combine new
        # provider state with a stale site projection or release plan.
        workflows_dir = REPO_ROOT / ".github" / "workflows"
        validator = (REPO_ROOT / "scripts" / "validate_publication.py").read_text(encoding="utf-8")
        self.assertIn("normalized catalog and public site eligibility differ", validator)

        for name in ("sync-incremental.yml", "audit-recent.yml", "sync-full.yml", "sync-ceec-ast.yml"):
            lines = _workflow_implementation(workflows_dir / name).splitlines()
            commit_indexes = [i for i, line in enumerate(lines) if "commit-and-push.sh" in line]
            with self.subTest(workflow=name):
                self.assertTrue(commit_indexes)
                for index in commit_indexes:
                    self.assertNotIn("        if: '!cancelled()'", lines[max(0, index - 6):index])

    def test_steps_that_download_affected_bundles_carry_a_github_token(self) -> None:
        # --download-affected-bundles makes a python step shell out to
        # `gh release download`, which is not obvious from reading the step.
        # Without GH_TOKEN gh exits "could not find any host configurations" on
        # the first bundle. That is what failed sync-incremental on 2026-07-20,
        # 07-27 and 08-06 and audit-recent on 08-01 - each time after the MOEX
        # sync itself had already succeeded, so a full hour of work was thrown
        # away at the last moment.
        workflows_dir = REPO_ROOT / ".github" / "workflows"
        checked = 0

        for path in sorted(workflows_dir.glob("*.yml")):
            lines = path.read_text(encoding="utf-8").splitlines()
            for index, line in enumerate(lines):
                if "--download-affected-bundles" not in line:
                    continue
                checked += 1
                start = index
                while start > 0 and not lines[start].lstrip().startswith("- name:"):
                    start -= 1
                step = "\n".join(lines[start:index + 1])
                with self.subTest(workflow=path.name, step=lines[start].strip()):
                    self.assertIn("GH_TOKEN", step)

        self.assertGreaterEqual(checked, 2)

    def test_provider_sync_workflows_route_partial_state_through_all_shared_gates(self) -> None:
        # A failed provider step still reaches the shared publisher, whose
        # complete preflight keeps both truncated and otherwise invalid state
        # out of main.
        script = (REPO_ROOT / ".github" / "scripts" / "commit-and-push.sh").read_text(encoding="utf-8")
        for gate in ("scripts/check_sync_floor.py", "scripts/validate_publication.py"):
            self.assertIn(gate, script)

        for path in self._provider_sync_workflows():
            with self.subTest(workflow=path.name):
                self.assertIn(".github/scripts/commit-and-push.sh", path.read_text(encoding="utf-8"))


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
