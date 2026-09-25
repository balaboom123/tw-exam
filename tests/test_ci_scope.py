import importlib.util
import subprocess
import unittest
from pathlib import Path
from unittest import mock


SCRIPT = Path(__file__).resolve().parents[1] / ".github/scripts/ci_scope.py"
SPEC = importlib.util.spec_from_file_location("ci_scope", SCRIPT)
assert SPEC and SPEC.loader
ci_scope = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(ci_scope)


class CiScopeTests(unittest.TestCase):
    def test_catalog_inputs_trigger_the_full_gate(self) -> None:
        for path in (
            "app/bundler.py", "catalog/registry.json", "data/sites/default/bundles.json",
            "schemas/bundles.schema.json", "scripts/render_docs.py", "tests/test_bundler.py",
            ".github/scripts/release_assets.py", ".github/workflows/ci.yml",
            "pyproject.toml", "uv.lock",
        ):
            with self.subTest(path=path):
                self.assertTrue(ci_scope.requires_catalog_gates([path]))

    def test_frontend_and_prose_changes_use_fast_gates(self) -> None:
        self.assertFalse(ci_scope.requires_catalog_gates([
            "frontend/src/App.tsx", "frontend/package-lock.json", "docs/operations/workflows.md",
        ]))

    def test_manual_and_unknown_base_run_the_full_gate(self) -> None:
        self.assertIsNone(ci_scope.changed_paths("workflow_dispatch", "abc123"))
        self.assertIsNone(ci_scope.changed_paths("push", ""))
        self.assertIsNone(ci_scope.changed_paths("push", "0" * 40))

    def test_pull_request_uses_merge_base_and_push_uses_previous_commit(self) -> None:
        result = subprocess.CompletedProcess(args=[], returncode=0, stdout="frontend/src/App.tsx\n")
        with mock.patch.object(ci_scope.subprocess, "run", return_value=result) as run:
            self.assertEqual(ci_scope.changed_paths("pull_request", "base123"), ["frontend/src/App.tsx"])
            self.assertEqual(run.call_args.args[0][-1], "base123...HEAD")
            self.assertEqual(ci_scope.changed_paths("push", "base123"), ["frontend/src/App.tsx"])
            self.assertEqual(run.call_args.args[0][-2:], ["base123", "HEAD"])

    def test_failed_comparison_runs_the_full_gate(self) -> None:
        with mock.patch.object(ci_scope.subprocess, "run", side_effect=subprocess.CalledProcessError(1, "git")):
            self.assertIsNone(ci_scope.changed_paths("push", "missing"))
