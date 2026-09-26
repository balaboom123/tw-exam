import gzip
import importlib.util
import io
import json
import os
import subprocess
import tarfile
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location(
    "mirror_snapshots", ROOT / ".github/scripts/mirror_snapshots.py"
)
mirror = importlib.util.module_from_spec(spec)
spec.loader.exec_module(mirror)
PROVIDER = "ceec_ast"


@pytest.fixture
def packed(tmp_path):
    root = tmp_path / "source"
    provider = root / "mirror/providers" / PROVIDER
    provider.mkdir(parents=True)
    (provider / "111").mkdir()
    (provider / "111/數學.pdf").write_bytes(os.urandom(4096))
    (provider / "empty.pdf").write_bytes(b"")
    os.link(provider / "111/數學.pdf", provider / "hardlink.pdf")
    (root / "mirror/.mirror-dedupe-index.json").write_text("derived root cache")
    sibling = root / "mirror/providers/ceec_gsat"
    sibling.mkdir()
    (sibling / "excluded.pdf").write_bytes(b"sibling provider")
    manifest_path = mirror.pack(
        root, PROVIDER, "pilot-1", tmp_path / "snapshot", chunk_bytes=257
    )
    return root, manifest_path


def test_chunked_roundtrip_preserves_payloads_and_provider_ownership(packed, tmp_path):
    root, manifest_path = packed
    payload = mirror.load_manifest(manifest_path, PROVIDER, "pilot-1")
    assert len(payload["chunks"]) > 1
    assert all(chunk["size"] <= 257 for chunk in payload["chunks"])
    destination = tmp_path / "restored"
    (destination / "mirror/providers/ceec_gsat").mkdir(parents=True)
    sibling = destination / "mirror/providers/ceec_gsat/existing.pdf"
    sibling.write_bytes(b"preserved sibling")
    (destination / "mirror/.mirror-dedupe-index.json").write_text("stale derived cache")
    mirror.unpack(destination, PROVIDER, manifest_path, "pilot-1")
    incoming = root / "mirror/providers" / PROVIDER
    restored = destination / "mirror/providers" / PROVIDER
    assert {
        p.relative_to(restored): p.read_bytes()
        for p in restored.rglob("*")
        if p.is_file()
    } == {
        p.relative_to(incoming): p.read_bytes()
        for p in incoming.rglob("*")
        if p.is_file()
    }
    assert sibling.read_bytes() == b"preserved sibling"
    assert not (destination / "mirror/.mirror-dedupe-index.json").exists()
    assert not (restored / "excluded.pdf").exists()


def test_corrupt_chunk_fails_before_installing_any_files(packed, tmp_path):
    _, path = packed
    payload = json.loads(path.read_text())
    chunk = path.parent / payload["chunks"][-1]["name"]
    chunk.write_bytes(b"corrupt")
    destination = tmp_path / "restore"
    with pytest.raises(ValueError, match="checksum/size"):
        mirror.unpack(destination, PROVIDER, path, "pilot-1")
    assert not (destination / "mirror").exists()


def test_restore_never_overwrites_an_existing_provider(packed, tmp_path):
    _, path = packed
    destination = tmp_path / "restore"
    target = destination / "mirror/providers" / PROVIDER
    target.mkdir(parents=True)
    retained = target / "retained.pdf"
    retained.write_bytes(b"existing")
    with pytest.raises(ValueError, match="empty provider mirror"):
        mirror.unpack(destination, PROVIDER, path, "pilot-1")
    assert retained.read_bytes() == b"existing"


def handcrafted(tmp_path, members, *, count=None, total=None, truncate=False):
    archive_bytes = io.BytesIO()
    with tarfile.open(fileobj=archive_bytes, mode="w") as archive:
        for member in members:
            archive.addfile(
                member, io.BytesIO(b"x" * member.size) if member.isfile() else None
            )
    data = gzip.compress(archive_bytes.getvalue())
    if truncate:
        data = data[:-8]
    directory = tmp_path / "archive"
    directory.mkdir()
    chunk = directory / "snapshot-test.tar.gz.part0000"
    chunk.write_bytes(data)
    path = directory / "snapshot-test.json"
    path.write_text(
        json.dumps(
            {
                "version": 1,
                "provider_id": PROVIDER,
                "generation": "test",
                "format": "tar.gz",
                "file_count": count if count is not None else len(members),
                "unpacked_bytes": total
                if total is not None
                else sum(m.size for m in members),
                "chunks": [
                    {
                        "name": chunk.name,
                        "size": chunk.stat().st_size,
                        "sha256": mirror.digest(chunk),
                    }
                ],
            }
        )
    )
    return path


@pytest.mark.parametrize(
    "name,kind",
    [
        ("../escape.pdf", tarfile.REGTYPE),
        ("/absolute.pdf", tarfile.REGTYPE),
        ("normal/../../escape.pdf", tarfile.REGTYPE),
        ("windows\\escape", tarfile.REGTYPE),
        ("normal.pdf", tarfile.SYMTYPE),
        ("normal.pdf", tarfile.LNKTYPE),
        ("directory", tarfile.DIRTYPE),
        ("device", tarfile.CHRTYPE),
    ],
)
def test_unsafe_archive_members_are_rejected_atomically(tmp_path, name, kind):
    member = tarfile.TarInfo(name)
    member.type = kind
    member.size = 1 if member.isfile() else 0
    member.linkname = (
        "../escape.pdf" if kind in (tarfile.SYMTYPE, tarfile.LNKTYPE) else ""
    )
    path = handcrafted(tmp_path, [member])
    destination = tmp_path / "restore"
    with pytest.raises(ValueError, match="Unsafe"):
        mirror.unpack(destination, PROVIDER, path, "test")
    assert not (destination / "mirror/providers" / PROVIDER).exists()
    assert not list((destination / "mirror/providers").glob(".*-restore-*"))


@pytest.mark.parametrize("case", ["duplicate", "missing", "oversize", "truncated"])
def test_invalid_archive_inventory_and_gzip_trailer_cannot_install(tmp_path, case):
    member = tarfile.TarInfo("paper.pdf")
    member.size = 1
    options = {
        "duplicate": {"members": [member, member]},
        "missing": {"members": [member], "count": 2},
        "oversize": {"members": [member], "total": 0},
        "truncated": {"members": [member], "truncate": True},
    }[case]
    path = handcrafted(tmp_path, **options)
    destination = tmp_path / "restore"
    with pytest.raises((ValueError, EOFError)):
        mirror.unpack(destination, PROVIDER, path, "test")
    assert not (destination / "mirror/providers" / PROVIDER).exists()


@pytest.mark.parametrize(
    "field,value",
    [
        ("provider_id", "ceec_gsat"),
        ("generation", "other"),
        ("format", "zip"),
        ("version", 2),
        ("chunks", [{"name": "../../escape"}]),
        ("file_count", True),
    ],
)
def test_manifest_contract_is_validated(packed, field, value):
    _, path = packed
    payload = json.loads(path.read_text())
    payload[field] = value
    path.write_text(json.dumps(payload))
    with pytest.raises(ValueError):
        mirror.load_manifest(path, PROVIDER, "pilot-1")


@pytest.mark.parametrize(
    "generation", ["../escape", "a/b", "", "a\noutput=x", "x" * 81]
)
def test_invalid_generations_never_become_paths(generation):
    with pytest.raises(ValueError):
        mirror.checked_generation(generation)


def test_pack_refuses_symbolic_links_and_empty_mirrors(tmp_path):
    provider = tmp_path / "mirror/providers" / PROVIDER
    provider.mkdir(parents=True)
    with pytest.raises(ValueError, match="empty mirror"):
        mirror.pack(tmp_path, PROVIDER, "empty", tmp_path / "empty-output")
    (provider / "link").symlink_to(tmp_path / "outside.pdf")
    with pytest.raises(ValueError, match="symbolic link"):
        mirror.pack(tmp_path, PROVIDER, "link", tmp_path / "link-output")


@pytest.mark.parametrize("fail", ["upload", "digest", None])
def test_remote_pointer_advances_only_after_all_upload_digests_are_confirmed(
    tmp_path, monkeypatch, fail
):
    provider = tmp_path / "mirror/providers" / PROVIDER
    provider.mkdir(parents=True)
    (provider / "paper.pdf").write_bytes(b"%PDF-test")
    uploaded = {}
    patches = []
    old_root = tmp_path / "old-source"
    old_provider = old_root / "mirror/providers" / PROVIDER
    old_provider.mkdir(parents=True)
    (old_provider / "paper.pdf").write_bytes(b"old")
    old_manifest = mirror.pack(old_root, PROVIDER, "old", tmp_path / "old-snapshot")
    monkeypatch.setattr(mirror, "download", lambda *args: old_manifest)
    old_pointer = {
        "version": 1,
        "provider_id": PROVIDER,
        "generation": "old",
        "manifest_sha256": mirror.digest(old_manifest),
    }
    remote = {"id": 123, "prerelease": True, "body": json.dumps(old_pointer)}
    monkeypatch.setattr(mirror, "release", lambda *args, **kwargs: remote)
    monkeypatch.setattr(mirror, "assets", lambda *args: list(uploaded.values()))

    def fake_gh(*args):
        if args[:2] == ("release", "upload"):
            path = Path(args[3])
            if fail == "upload" and path.suffix == ".json":
                raise subprocess.CalledProcessError(1, ["gh"])
            uploaded[path.name] = {
                "name": path.name,
                "digest": "sha256:"
                + ("0" * 64 if fail == "digest" else mirror.digest(path)),
            }
        elif args[:3] == ("api", "--method", "PATCH"):
            patches.append(json.loads(Path(args[-1]).read_text()))
        else:
            pytest.fail(f"Unexpected external operation: {args}")
        return ""

    monkeypatch.setattr(mirror, "gh", fake_gh)
    if fail:
        with pytest.raises((ValueError, subprocess.CalledProcessError)):
            mirror.save(tmp_path, "owner/repo", PROVIDER, "new")
        assert not patches
        assert json.loads(remote["body"]) == old_pointer
    else:
        pointer = mirror.save(tmp_path, "owner/repo", PROVIDER, "new")
        assert pointer["generation"] == "new"
        assert len(uploaded) == 2
        assert json.loads(patches[0]["body"]) == pointer


def test_only_a_missing_release_allows_source_bootstrap(monkeypatch):
    monkeypatch.setattr(
        mirror.subprocess,
        "run",
        lambda *args, **kwargs: subprocess.CompletedProcess(
            args,
            1,
            stderr="gh: Not Found (HTTP 404)",
            stdout="",
        ),
    )
    assert mirror.release("owner/repo", PROVIDER, allow_missing=True) is None
    with pytest.raises(RuntimeError):
        mirror.release("owner/repo", PROVIDER)
    monkeypatch.setattr(
        mirror.subprocess,
        "run",
        lambda *args, **kwargs: subprocess.CompletedProcess(
            args,
            1,
            stderr="gh: server error (HTTP 503)",
            stdout="",
        ),
    )
    with pytest.raises(RuntimeError):
        mirror.release("owner/repo", PROVIDER, allow_missing=True)


def test_existing_release_without_a_committed_pointer_is_not_treated_as_empty(
    monkeypatch, tmp_path
):
    monkeypatch.setattr(
        mirror, "release", lambda *args, **kwargs: {"body": "upload interrupted"}
    )
    with pytest.raises(ValueError, match="committed snapshot pointer"):
        mirror.restore(tmp_path, "owner/repo", PROVIDER, allow_missing=True)


def test_publication_retry_restores_its_exact_generation_instead_of_latest(
    packed, tmp_path, monkeypatch
):
    _, path = packed
    pointer = {
        "version": 1,
        "provider_id": PROVIDER,
        "generation": "a-newer-run",
        "manifest_sha256": "0" * 64,
    }
    monkeypatch.setattr(
        mirror, "release", lambda *args, **kwargs: {"body": json.dumps(pointer)}
    )
    downloaded = []

    def fake_download(repository, provider, name, directory):
        downloaded.append(name)
        destination = directory / name
        destination.write_bytes((path.parent / name).read_bytes())
        return destination

    monkeypatch.setattr(mirror, "download", fake_download)
    result = mirror.restore(
        tmp_path / "cold-runner",
        "owner/repo",
        PROVIDER,
        generation="pilot-1",
        manifest_sha256=mirror.digest(path),
    )
    assert result["generation"] == "pilot-1"
    assert downloaded[0] == "snapshot-pilot-1.json"
    assert all("a-newer-run" not in name for name in downloaded)


def test_restore_validates_downloaded_manifest_before_fetching_payloads(
    packed, tmp_path, monkeypatch
):
    _, path = packed
    pointer = {
        "version": 1,
        "provider_id": PROVIDER,
        "generation": "pilot-1",
        "manifest_sha256": "0" * 64,
    }
    monkeypatch.setattr(
        mirror, "release", lambda *args, **kwargs: {"body": json.dumps(pointer)}
    )
    downloaded = []

    def fake_download(repository, provider, name, directory):
        downloaded.append(name)
        destination = directory / name
        destination.write_bytes(path.read_bytes())
        return destination

    monkeypatch.setattr(mirror, "download", fake_download)
    with pytest.raises(ValueError, match="manifest checksum"):
        mirror.restore(tmp_path / "restore", "owner/repo", PROVIDER)
    assert downloaded == [path.name]
    assert not (tmp_path / "restore/mirror").exists()


def test_unchanged_mirror_reuses_verified_generation_without_uploading(
    packed, tmp_path, monkeypatch
):
    root, _ = packed
    old_manifest = mirror.pack(root, PROVIDER, "old", tmp_path / "old-snapshot")
    old = json.loads(old_manifest.read_text())
    pointer = {
        "version": 1,
        "provider_id": PROVIDER,
        "generation": "old",
        "manifest_sha256": mirror.digest(old_manifest),
    }
    monkeypatch.setattr(
        mirror,
        "release",
        lambda *args, **kwargs: {"id": 123, "body": json.dumps(pointer)},
    )
    monkeypatch.setattr(mirror, "download", lambda *args: old_manifest)
    monkeypatch.setattr(
        mirror,
        "assets",
        lambda *args: [
            {"name": c["name"], "digest": "sha256:" + c["sha256"]}
            for c in old["chunks"]
        ],
    )
    monkeypatch.setattr(
        mirror, "gh", lambda *args: pytest.fail(f"Unexpected write: {args}")
    )
    assert mirror.save(root, "owner/repo", PROVIDER, "new") == pointer


def test_unchanged_snapshot_with_missing_remote_chunks_cannot_be_reused(
    packed, tmp_path, monkeypatch
):
    root, _ = packed
    path = mirror.pack(root, PROVIDER, "old", tmp_path / "old-snapshot")
    pointer = {
        "version": 1,
        "provider_id": PROVIDER,
        "generation": "old",
        "manifest_sha256": mirror.digest(path),
    }
    monkeypatch.setattr(
        mirror,
        "release",
        lambda *args, **kwargs: {"id": 123, "body": json.dumps(pointer)},
    )
    monkeypatch.setattr(mirror, "download", lambda *args: path)
    monkeypatch.setattr(mirror, "assets", lambda *args: [])
    monkeypatch.setattr(
        mirror, "gh", lambda *args: pytest.fail(f"Unexpected write: {args}")
    )
    with pytest.raises(ValueError, match="missing verified chunks"):
        mirror.save(root, "owner/repo", PROVIDER, "new")
