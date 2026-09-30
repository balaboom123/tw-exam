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
