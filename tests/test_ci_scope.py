import importlib.util
import os
import subprocess
import sys
import tempfile
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


class ShallowCiScopeTests(unittest.TestCase):
    """Exercise the actual selector against Git's shallow ancestry rules."""

    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.source = self.root / "source"
        self.source.mkdir()
        self.environment = dict(
            os.environ,
            GIT_CONFIG_NOSYSTEM="1",
            GIT_CONFIG_GLOBAL=os.devnull,
            GIT_AUTHOR_NAME="CI scope test",
            GIT_AUTHOR_EMAIL="scope@example.invalid",
            GIT_COMMITTER_NAME="CI scope test",
            GIT_COMMITTER_EMAIL="scope@example.invalid",
        )
        self.git(self.source, "init", "-b", "main")

    def git(self, directory: Path, *arguments: str) -> str:
        result = subprocess.run(
            ["git", *arguments], cwd=directory, env=self.environment,
            check=True, capture_output=True, text=True,
        )
        return result.stdout.strip()

    def commit(self, name: str, contents: str) -> str:
        target = self.source / name
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(contents)
        self.git(self.source, "add", name)
        self.git(self.source, "commit", "-m", name)
        return self.git(self.source, "rev-parse", "HEAD")

    def clone(self) -> Path:
        target = self.root / "checkout"
        self.git(self.root, "clone", "--depth", "2", self.source.as_uri(), str(target))
        self.assertEqual(self.git(target, "rev-parse", "--is-shallow-repository"), "true")
        return target

    def selection(self, checkout: Path, event: str, base: str) -> str:
        result = subprocess.run(
            [sys.executable, str(SCRIPT), "--event", event, "--base", base],
            cwd=checkout, env=self.environment, check=True,
            capture_output=True, text=True,
        )
        return result.stdout.strip()

    def test_available_push_parent_selects_frontend_only_checks(self) -> None:
        self.commit("app/classification.py", "retained catalog input")
        parent = self.commit("docs/README.md", "documentation")
        self.commit("frontend/src/App.tsx", "frontend change")
        checkout = self.clone()
        self.assertEqual(self.selection(checkout, "push", parent), "catalog=false")

    def test_missing_multi_commit_base_cannot_hide_an_earlier_catalog_change(self) -> None:
        previous_push = self.commit("docs/README.md", "before push")
        self.commit("app/classification.py", "catalog changed early in push")
        self.commit("docs/README.md", "later prose change")
        self.commit("frontend/src/App.tsx", "last frontend change")
        checkout = self.clone()
        missing = subprocess.run(
            ["git", "cat-file", "-e", previous_push], cwd=checkout,
            env=self.environment, capture_output=True,
        )
        self.assertNotEqual(missing.returncode, 0)
        self.assertEqual(self.selection(checkout, "push", previous_push), "catalog=true")

    def test_pull_request_merge_parents_select_catalog_without_full_history(self) -> None:
        self.commit("docs/README.md", "original base")
        self.git(self.source, "checkout", "-b", "feature")
        self.commit("app/classification.py", "feature catalog change")
        self.git(self.source, "checkout", "main")
        pull_request_base = self.commit("docs/README.md", "base branch advanced")
        self.git(self.source, "merge", "--no-ff", "feature", "-m", "pull request merge")
        checkout = self.clone()
        self.assertEqual(
            self.selection(checkout, "pull_request", pull_request_base), "catalog=true"
        )

    def test_missing_pull_request_merge_base_runs_every_catalog_gate(self) -> None:
        missing_base = self.commit("docs/README.md", "old base")
        self.commit("app/classification.py", "catalog change")
        self.commit("docs/README.md", "prose change")
        self.commit("frontend/src/App.tsx", "frontend change")
        checkout = self.clone()
        self.assertEqual(
            self.selection(checkout, "pull_request", missing_base), "catalog=true"
        )
