import os
import subprocess
from pathlib import Path

SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "republish.sh"
ORDER = [
    "-m app migrate-catalog",
    "-m app publish-site",
    "-m app audit-files",
    "-m app audit-catalog",
    "scripts/validate_publication.py",
    "release_assets.py ensure",
    "release_assets.py upload",
    "release_assets.py prune",
]


def test_republish_runs_every_stage_in_the_safe_order():
    text = SCRIPT.read_text(encoding="utf-8")
    positions = [text.index(stage) for stage in ORDER]
    assert positions == sorted(positions)
    assert "set -euo pipefail" in text
    assert "git commit" not in text
    assert "git push" not in text


def test_republish_is_valid_bash():
    subprocess.run(["bash", "-n", str(SCRIPT)], check=True)


def test_republish_rejects_unknown_or_extra_arguments_before_running_commands(tmp_path):
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    calls = tmp_path / "calls"
    for command in ("git", "gh", "uv"):
        stub = bin_dir / command
        stub.write_text(f"#!/bin/sh\nprintf '%s\\n' '{command}' >> '{calls}'\n", encoding="utf-8")
        stub.chmod(0o755)

    env = os.environ.copy()
    env["PATH"] = f"{bin_dir}:{env['PATH']}"
    for args in (("--unknown",), ("--download", "extra")):
        result = subprocess.run(
            ["bash", str(SCRIPT), *args],
            cwd=tmp_path,
            env=env,
            capture_output=True,
            text=True,
            check=False,
        )
        assert result.returncode != 0
        assert not calls.exists()


def _publication_stub_environment(tmp_path, *, gh_script, uv_script):
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    (bin_dir / "git").write_text("#!/bin/sh\nprintf '%s\\n' \"$PWD\"\n", encoding="utf-8")
    (bin_dir / "gh").write_text(gh_script, encoding="utf-8")
    (bin_dir / "uv").write_text(uv_script, encoding="utf-8")
    for stub in bin_dir.iterdir():
        stub.chmod(0o755)
    env = os.environ.copy()
    env["PATH"] = f"{bin_dir}:{env['PATH']}"
    env.pop("GITHUB_REPOSITORY", None)
    env["RUN_LOG"] = str(tmp_path / "commands.log")
    return env


def test_republish_stops_when_repository_lookup_fails(tmp_path):
    env = _publication_stub_environment(
        tmp_path,
        gh_script='#!/bin/sh\necho gh >> "$RUN_LOG"\nexit 23\n',
        uv_script='#!/bin/sh\necho uv >> "$RUN_LOG"\nexit 0\n',
    )

    result = subprocess.run(
        ["bash", str(SCRIPT)],
        cwd=tmp_path,
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode != 0
    assert "uv" not in (tmp_path / "commands.log").read_text(encoding="utf-8")


def test_republish_rejects_empty_repository_lookup_result(tmp_path):
    env = _publication_stub_environment(
        tmp_path,
        gh_script='#!/bin/sh\necho gh >> "$RUN_LOG"\nexit 0\n',
        uv_script='#!/bin/sh\necho uv >> "$RUN_LOG"\nexit 0\n',
    )

    result = subprocess.run(
        ["bash", str(SCRIPT)],
        cwd=tmp_path,
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode != 0
    assert "uv" not in (tmp_path / "commands.log").read_text(encoding="utf-8")


def test_republish_uses_explicit_repository_without_lookup(tmp_path):
    env = _publication_stub_environment(
        tmp_path,
        gh_script='#!/bin/sh\necho gh >> "$RUN_LOG"\nexit 23\n',
        uv_script='#!/bin/sh\nprintf "uv:%s\\n" "$GITHUB_REPOSITORY" >> "$RUN_LOG"\nexit 7\n',
    )
    env["GITHUB_REPOSITORY"] = "example/project"

    result = subprocess.run(
        ["bash", str(SCRIPT)],
        cwd=tmp_path,
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode == 7
    assert (tmp_path / "commands.log").read_text(encoding="utf-8") == "uv:example/project\n"
