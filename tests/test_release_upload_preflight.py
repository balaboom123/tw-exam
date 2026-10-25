"""Invalid later inputs must not replace any earlier live ZIP."""

import subprocess
from unittest import mock

import pytest

from tests.test_workflows import _EMPTY_ZIP, _EMPTY_ZIP_DIGEST, _load_release_script


@pytest.mark.parametrize("same_shard", [True, False], ids=["same-shard", "later-shard"])
@pytest.mark.parametrize("problem", ["missing", "corrupt", "oversize", "capacity"])
def test_invalid_later_asset_prevents_every_upload(tmp_path, same_shard, problem):
    module = _load_release_script()
    first = tmp_path / "first.zip"
    first.write_bytes(_EMPTY_ZIP)
    last = tmp_path / "last.zip"
    if problem != "missing":
        last.write_bytes(b"bad bytes" if problem == "corrupt" else _EMPTY_ZIP)
    if problem == "oversize":
        last.write_bytes(b"x" * 64)
    last_tag = "bundles-a" if same_shard else "bundles-b"
    assets = [
        {
            "storage_key": str(first),
            "asset_name": first.name,
            "release_tag": "bundles-a",
            "checksum": _EMPTY_ZIP_DIGEST,
        },
        {
            "storage_key": str(last),
            "asset_name": last.name,
            "release_tag": last_tag,
            "checksum": _EMPTY_ZIP_DIGEST,
        },
    ]

    def remote(tag, *, allow_missing=False):
        if problem == "capacity" and tag == last_tag:
            return {f"retained-{i}.zip": "old" for i in range(1000)}
        return {}

    with (
        mock.patch.object(module, "_local_assets", return_value=assets),
        mock.patch.object(module, "_release_zip_digests", side_effect=remote),
        mock.patch.object(module, "GITHUB_RELEASE_ASSET_BYTE_LIMIT", 32),
        mock.patch.object(module.subprocess, "run") as upload,
    ):
        assert module.upload() == 1
    upload.assert_not_called()


def test_later_remote_inventory_failure_prevents_every_upload(tmp_path):
    module = _load_release_script()
    local = tmp_path / "paper.zip"
    local.write_bytes(_EMPTY_ZIP)
    assets = [
        {
            "storage_key": str(local),
            "asset_name": f"{tag}.zip",
            "release_tag": tag,
            "checksum": _EMPTY_ZIP_DIGEST,
        }
        for tag in ("bundles-a", "bundles-b")
    ]
    with (
        mock.patch.object(module, "_local_assets", return_value=assets),
        mock.patch.object(
            module,
            "_release_zip_digests",
            side_effect=[{}, subprocess.CalledProcessError(1, "read later release")],
        ),
        mock.patch.object(module.subprocess, "run") as upload,
        pytest.raises(subprocess.CalledProcessError),
    ):
        module.upload()
    upload.assert_not_called()


def test_failed_release_lookup_is_not_treated_as_an_empty_inventory(tmp_path):
    module = _load_release_script()
    local = tmp_path / "paper.zip"
    local.write_bytes(_EMPTY_ZIP)
    assets = [
        {
            "storage_key": str(local),
            "asset_name": f"{tag}.zip",
            "release_tag": tag,
            "checksum": _EMPTY_ZIP_DIGEST,
        }
        for tag in ("bundles-a", "bundles-b")
    ]
    with (
        mock.patch.object(module, "_local_assets", return_value=assets),
        mock.patch.object(
            module.subprocess,
            "check_output",
            side_effect=["101", "", subprocess.CalledProcessError(1, "unreadable release")],
        ),
        mock.patch.object(module.subprocess, "run") as upload,
        pytest.raises(subprocess.CalledProcessError),
    ):
        module.upload()
    upload.assert_not_called()


def test_all_shards_are_checked_before_upload_and_batches_keep_every_asset(tmp_path):
    module = _load_release_script()
    local = tmp_path / "paper.zip"
    local.write_bytes(_EMPTY_ZIP)
    assets = [
        {
            "storage_key": str(local),
            "asset_name": f"{tag}-{i}.zip",
            "release_tag": tag,
            "checksum": _EMPTY_ZIP_DIGEST,
        }
        for tag in ("bundles-a", "bundles-b")
        for i in range(5)
    ]
    events = []
    calls = []

    def remote(tag, *, allow_missing=False):
        events.append(("read", tag))
        return {}

    def upload(command, *, check):
        assert check
        events.append(("upload", command[3]))
        calls.append(command)

    with (
        mock.patch.object(module, "_local_assets", return_value=assets),
        mock.patch.object(module, "_release_zip_digests", side_effect=remote),
        mock.patch.object(module, "UPLOAD_BATCH_SIZE", 2),
        mock.patch.object(module.subprocess, "run", side_effect=upload),
    ):
        assert module.upload() == 0
    assert events[:2] == [("read", "bundles-a"), ("read", "bundles-b")]
    assert all(kind == "upload" for kind, _ in events[2:])
    assert [len(command[4:-1]) for command in calls] == [2, 2, 1, 2, 2, 1]
    assert [spec for command in calls for spec in command[4:-1]] == [
        f"{local}#{asset['asset_name']}" for asset in assets
    ]
