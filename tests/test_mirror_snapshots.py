import gzip
import hashlib
import importlib.util
import io
import json
import os
import subprocess
import sys
import tarfile
from pathlib import Path

import pytest

from app.storage import MirrorStore
from app.source_revisions import revision_blob_key

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location(
    "mirror_snapshots", ROOT / ".github/scripts/mirror_snapshots.py"
)
mirror = importlib.util.module_from_spec(spec)
spec.loader.exec_module(mirror)
PROVIDER = "ceec_ast"


@pytest.mark.parametrize('provider', ['cpc_recruit', 'gept_cert', 'hakka_cert', 'jlpt_cert', 'tii_cert'])
def test_restricted_public_save_is_rejected_before_packing_or_remote_operations(tmp_path, monkeypatch, provider):
    def unexpected(*args, **kwargs):
        pytest.fail("A restricted provider reached public backup work")

    monkeypatch.setattr(mirror, "pack", unexpected)
    monkeypatch.setattr(mirror, "gh", unexpected)
    with pytest.raises(ValueError, match=f"Public mirror backups are disabled for {provider}"):
        mirror.save(tmp_path, "owner/repo", provider, "restricted")
    assert not (tmp_path / ".tmp").exists()


@pytest.mark.parametrize('provider', ['cpc_recruit', 'gept_cert', 'hakka_cert', 'jlpt_cert', 'tii_cert'])
def test_restricted_public_save_cli_checks_policy_before_cache_outputs(tmp_path, monkeypatch, capsys, provider):
    def unexpected(*args, **kwargs):
        pytest.fail("A restricted provider reached backup or cache work")

    output = tmp_path / "output"
    monkeypatch.setenv("GITHUB_OUTPUT", str(output))
    monkeypatch.setattr(mirror, "cacheable", unexpected)
    monkeypatch.setattr(mirror, "save", unexpected)
    monkeypatch.setattr(sys, "argv", [
        "mirror_snapshots.py", "save", "--repo-root", str(tmp_path),
        "--repository", "owner/repo", "--provider", provider,
    ])
    assert mirror.main() == 1
    assert f"Public mirror backups are disabled for {provider}" in capsys.readouterr().err
    assert not output.exists()


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
    manifest_path = mirror.pack(root, PROVIDER, "pilot-1", tmp_path / "snapshot", chunk_bytes=257)
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
        p.relative_to(restored): p.read_bytes() for p in restored.rglob("*") if p.is_file()
    } == {p.relative_to(incoming): p.read_bytes() for p in incoming.rglob("*") if p.is_file()}
    assert sibling.read_bytes() == b"preserved sibling"
    assert not (destination / "mirror/.mirror-dedupe-index.json").exists()
    assert not (restored / "excluded.pdf").exists()
    assert payload["version"] == 2
    assert payload["payload_bytes"] == 4096
    assert payload["unpacked_bytes"] == 8192
    assert (restored / "hardlink.pdf").samefile(restored / "111/數學.pdf")


def test_durable_roundtrip_preserves_independent_revision_bytes(tmp_path):
    source = tmp_path / 'source'
    store = MirrorStore(source / 'mirror')
    key = f'providers/{PROVIDER}/111/question.pdf'
    original = b'%PDF-1.7 old source revision'
    store.write_bytes(key, original)
    store.write_bytes(key, b'%PDF-1.7 current source revision', overwrite=True)
    blob_key = revision_blob_key(PROVIDER, hashlib.sha256(original).hexdigest(), '.pdf')
    manifest = mirror.pack(source, PROVIDER, 'with-revisions', tmp_path / 'snapshot', chunk_bytes=257)
    restored = tmp_path / 'restored'
    mirror.unpack(restored, PROVIDER, manifest, 'with-revisions')
    blob = restored / 'mirror' / blob_key
    current = restored / 'mirror' / key
    assert blob.read_bytes() == original
    assert not blob.samefile(current)
    current.write_bytes(b'recovered current file later changes')
    assert blob.read_bytes() == original


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


def handcrafted(tmp_path, members, *, count=None, total=None, truncate=False, version=1):
    archive_bytes = io.BytesIO()
    with tarfile.open(fileobj=archive_bytes, mode="w") as archive:
        for member in members:
            archive.addfile(member, io.BytesIO(b"x" * member.size) if member.isfile() else None)
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
                "version": version,
                "provider_id": PROVIDER,
                "generation": "test",
                "format": "tar.gz",
                "file_count": count if count is not None else len(members),
                "unpacked_bytes": total if total is not None else sum(m.size for m in members),
                "payload_bytes": sum(m.size for m in members if m.isfile()),
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
    member.linkname = "../escape.pdf" if kind in (tarfile.SYMTYPE, tarfile.LNKTYPE) else ""
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
        ("version", 3),
        ("version", True),
        ("payload_bytes", -1),
        ("payload_bytes", True),
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


@pytest.mark.parametrize("generation", ["../escape", "a/b", "", "a\noutput=x", "x" * 81])
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
                "digest": "sha256:" + ("0" * 64 if fail == "digest" else mirror.digest(path)),
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
    monkeypatch.setattr(mirror, "release", lambda *args, **kwargs: {"body": "upload interrupted"})
    with pytest.raises(ValueError, match="committed snapshot pointer"):
        mirror.restore(tmp_path, "owner/repo", PROVIDER, allow_missing=True)


def test_hydration_can_resume_after_an_interrupted_first_backup(tmp_path, monkeypatch):
    monkeypatch.setenv("GITHUB_ACTIONS", "true")
    monkeypatch.setenv("GITHUB_WORKSPACE", str(tmp_path))
    source = tmp_path / "mirror/providers" / PROVIDER / "retained.pdf"
    source.parent.mkdir(parents=True)
    source.write_bytes(b"retained progress")
    monkeypatch.setattr(
        mirror, "release", lambda *args, **kwargs: {"body": "Snapshot upload in progress"},
    )
    monkeypatch.setattr(mirror, "restore", lambda *args, **kwargs: pytest.fail("No committed snapshot"))
    assert mirror.hydrate(tmp_path, "owner/repo", PROVIDER) is None
    assert source.read_bytes() == b"retained progress"


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
    monkeypatch.setattr(mirror, "release", lambda *args, **kwargs: {"body": json.dumps(pointer)})
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
    monkeypatch.setattr(mirror, "release", lambda *args, **kwargs: {"body": json.dumps(pointer)})
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
            {"name": c["name"], "digest": "sha256:" + c["sha256"]} for c in old["chunks"]
        ],
    )
    monkeypatch.setattr(mirror, "gh", lambda *args: pytest.fail(f"Unexpected write: {args}"))
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
    monkeypatch.setattr(mirror, "gh", lambda *args: pytest.fail(f"Unexpected write: {args}"))
    with pytest.raises(ValueError, match="missing verified chunks"):
        mirror.save(root, "owner/repo", PROVIDER, "new")


def test_probe_hashes_archive_without_staging_chunks(packed, tmp_path):
    root, path = packed
    probe = mirror.pack(
        root, PROVIDER, "probe", tmp_path / "probe", chunk_bytes=257, keep_chunks=False
    )
    staged = json.loads(path.read_text())
    hashed = json.loads(probe.read_text())
    assert [(c["size"], c["sha256"]) for c in staged["chunks"]] == [
        (c["size"], c["sha256"]) for c in hashed["chunks"]
    ]
    assert list(probe.parent.iterdir()) == [probe]


@pytest.mark.parametrize("target", ["../escape", "/escape", "missing.pdf", "windows\\escape"])
def test_v2_hardlinks_cannot_escape_or_reference_unknown_payloads(tmp_path, target):
    paper = tarfile.TarInfo("paper.pdf")
    paper.size = 1
    link = tarfile.TarInfo("alias.pdf")
    link.type = tarfile.LNKTYPE
    link.linkname = target
    path = handcrafted(tmp_path, [paper, link], total=2, version=2)
    destination = tmp_path / "restore"
    with pytest.raises(ValueError, match="Unsafe.*hard link"):
        mirror.unpack(destination, PROVIDER, path, "test")
    assert not (destination / "mirror/providers" / PROVIDER).exists()


def test_v1_regular_archives_remain_readable(tmp_path):
    paper = tarfile.TarInfo("paper.pdf")
    paper.size = 3
    path = handcrafted(tmp_path, [paper])
    destination = tmp_path / "restore"
    mirror.unpack(destination, PROVIDER, path, "test")
    assert (destination / "mirror/providers" / PROVIDER / "paper.pdf").read_bytes() == b"xxx"


def test_streamed_restore_discards_each_verified_chunk_before_downloading_next(
    packed, tmp_path, monkeypatch
):
    root, source = packed
    pointer = {
        "version": 1,
        "provider_id": PROVIDER,
        "generation": "pilot-1",
        "manifest_sha256": mirror.digest(source),
    }
    monkeypatch.setattr(
        mirror, "release", lambda *args, **kwargs: {"id": 123, "body": json.dumps(pointer)}
    )
    downloads = []

    def download(repository, provider, name, directory):
        assert not list(directory.glob("*.part*"))
        target = directory / name
        target.write_bytes((source.parent / name).read_bytes())
        downloads.append(name)
        return target

    monkeypatch.setattr(mirror, "download", download)
    destination = tmp_path / "restore"
    mirror.restore(destination, "owner/repo", PROVIDER)
    assert len(downloads) > 2
    assert (destination / "mirror/providers" / PROVIDER / "hardlink.pdf").read_bytes() == (
        root / "mirror/providers" / PROVIDER / "hardlink.pdf"
    ).read_bytes()


def test_save_uploads_and_discards_one_chunk_at_a_time(packed, tmp_path, monkeypatch):
    root, source = packed
    pointer = {
        "version": 1,
        "provider_id": PROVIDER,
        "generation": "pilot-1",
        "manifest_sha256": mirror.digest(source),
    }
    remote = {"id": 123, "body": json.dumps(pointer)}
    monkeypatch.setattr(mirror, "release", lambda *args, **kwargs: remote)
    monkeypatch.setattr(mirror, "download", lambda *args: source)
    monkeypatch.setattr(mirror, "CHUNK_BYTES", 257)
    uploaded = {}
    patches = []
    monkeypatch.setattr(mirror, "assets", lambda *args: list(uploaded.values()))
    (root / "mirror/providers" / PROVIDER / "111/數學.pdf").write_bytes(os.urandom(4096))

    def gh(*args):
        if args[:2] == ("release", "upload"):
            path = Path(args[3])
            assert len(list(path.parent.glob("*.part*"))) <= 1
            uploaded[path.name] = {"name": path.name, "digest": "sha256:" + mirror.digest(path)}
        elif args[:3] == ("api", "--method", "PATCH"):
            patches.append(json.loads(Path(args[-1]).read_text()))
        else:
            pytest.fail(f"Unexpected operation: {args}")
        return ""

    monkeypatch.setattr(mirror, "gh", gh)
    result = mirror.save(root, "owner/repo", PROVIDER, "new")
    assert result["generation"] == "new"
    assert len(uploaded) > 2
    assert len(patches) == 1


def test_saving_two_generations_preserves_and_restores_a_pinned_older_snapshot(
    packed, tmp_path, monkeypatch
):
    root, source = packed
    pointer = {
        "version": 1, "provider_id": PROVIDER, "generation": "pilot-1",
        "manifest_sha256": mirror.digest(source),
    }
    remote = {"id": 123, "body": json.dumps(pointer)}
    stored = {p.name: p.read_bytes() for p in source.parent.iterdir() if p.is_file()}
    original = dict(stored)
    monkeypatch.setattr(mirror, "release", lambda *args, **kwargs: remote)

    def download(repository, provider, name, directory):
        path = directory / name
        path.write_bytes(stored[name])
        return path

    monkeypatch.setattr(mirror, "download", download)
    monkeypatch.setattr(mirror, "assets", lambda *args: [
        {"name": name, "digest": "sha256:" + hashlib.sha256(body).hexdigest()}
        for name, body in stored.items()
    ])

    def gh(*args):
        if args[:2] == ("release", "upload"):
            path = Path(args[3])
            assert path.name not in stored
            stored[path.name] = path.read_bytes()
        elif args[:3] == ("api", "--method", "PATCH"):
            remote.update(json.loads(Path(args[-1]).read_text()))
        else:
            pytest.fail(f"Saving a generation must not delete recovery inputs: {args}")
        return ""

    monkeypatch.setattr(mirror, "gh", gh)
    paper = root / "mirror/providers" / PROVIDER / "111/數學.pdf"
    for generation in ("second", "third"):
        paper.write_bytes(os.urandom(4096))
        result = mirror.save(root, "owner/repo", PROVIDER, generation)
        assert result["generation"] == generation
    assert json.loads(remote["body"])["generation"] == "third"
    assert all(stored[name] == body for name, body in original.items())
    destination = tmp_path / "pinned-restore"
    mirror.restore(destination, "owner/repo", PROVIDER, generation="pilot-1",
                   manifest_sha256=pointer["manifest_sha256"])
    assert (destination / "mirror/providers" / PROVIDER / "111/數學.pdf").read_bytes() != paper.read_bytes()
    assert (destination / "mirror/providers" / PROVIDER / "hardlink.pdf").samefile(
        destination / "mirror/providers" / PROVIDER / "111/數學.pdf"
    )


def test_changed_mirror_between_probe_and_upload_preserves_pointer(packed, tmp_path, monkeypatch):
    root, source = packed
    pointer = {
        "version": 1,
        "provider_id": PROVIDER,
        "generation": "pilot-1",
        "manifest_sha256": mirror.digest(source),
    }
    remote = {"id": 123, "body": json.dumps(pointer)}
    monkeypatch.setattr(mirror, "release", lambda *args, **kwargs: remote)
    monkeypatch.setattr(mirror, "download", lambda *args: source)
    monkeypatch.setattr(mirror, "assets", lambda *args: [])
    monkeypatch.setattr(mirror, "gh", lambda *args: pytest.fail(f"Unexpected write: {args}"))
    (root / "mirror/providers" / PROVIDER / "111/數學.pdf").write_bytes(b"changed before probe")
    pack = mirror.pack

    def changing_pack(*args, **kwargs):
        result = pack(*args, **kwargs)
        if kwargs.get("keep_chunks") is False:
            (root / "mirror/providers" / PROVIDER / "111/數學.pdf").write_bytes(
                b"changed after probe"
            )
        return result

    monkeypatch.setattr(mirror, "pack", changing_pack)
    with pytest.raises(ValueError, match="changed while packing"):
        mirror.save(root, "owner/repo", PROVIDER, "new")
    assert json.loads(remote["body"]) == pointer


@pytest.mark.parametrize("mode", ["forward", "chain"])
def test_v2_hardlinks_require_an_earlier_regular_member(tmp_path, mode):
    paper = tarfile.TarInfo("paper.pdf")
    paper.size = 1
    first = tarfile.TarInfo("first.pdf")
    first.type = tarfile.LNKTYPE
    first.linkname = "paper.pdf"
    second = tarfile.TarInfo("second.pdf")
    second.type = tarfile.LNKTYPE
    second.linkname = "first.pdf"
    members = [first, paper] if mode == "forward" else [paper, first, second]
    path = handcrafted(tmp_path, members, total=len(members), version=2)
    with pytest.raises(ValueError, match="Unsafe.*hard link"):
        mirror.unpack(tmp_path / "restore", PROVIDER, path, "test")
    assert not (tmp_path / "restore/mirror/providers" / PROVIDER).exists()


def test_v2_payload_size_disagreement_cannot_install(packed, tmp_path):
    _, path = packed
    payload = json.loads(path.read_text())
    payload["payload_bytes"] = 0
    path.write_text(json.dumps(payload))
    with pytest.raises(ValueError, match="payload bytes"):
        mirror.unpack(tmp_path / "restore", PROVIDER, path, "pilot-1")
    assert not (tmp_path / "restore/mirror/providers" / PROVIDER).exists()


def test_late_corrupt_download_leaves_no_installed_provider_or_staging(
    packed, tmp_path, monkeypatch
):
    _, source = packed
    payload = json.loads(source.read_text())
    pointer = {
        "version": 1,
        "provider_id": PROVIDER,
        "generation": "pilot-1",
        "manifest_sha256": mirror.digest(source),
    }
    monkeypatch.setattr(
        mirror, "release", lambda *args, **kwargs: {"id": 123, "body": json.dumps(pointer)}
    )

    def download(repository, provider, name, directory):
        target = directory / name
        target.write_bytes(
            b"corrupt"
            if name == payload["chunks"][-1]["name"]
            else (source.parent / name).read_bytes()
        )
        return target

    monkeypatch.setattr(mirror, "download", download)
    destination = tmp_path / "restore"
    sibling = destination / "mirror/providers/ceec_gsat/kept.pdf"
    sibling.parent.mkdir(parents=True)
    sibling.write_bytes(b"kept")
    with pytest.raises(ValueError, match="checksum/size"):
        mirror.restore(destination, "owner/repo", PROVIDER)
    assert not (destination / "mirror/providers" / PROVIDER).exists()
    assert sibling.read_bytes() == b"kept"
    assert not list((destination / ".tmp").iterdir())
    assert not list((destination / "mirror/providers").glob(".*-restore-*"))


def test_restore_rejects_insufficient_space_before_downloading_chunks(
    packed, tmp_path, monkeypatch
):
    _, source = packed
    pointer = {
        "version": 1,
        "provider_id": PROVIDER,
        "generation": "pilot-1",
        "manifest_sha256": mirror.digest(source),
    }
    monkeypatch.setattr(
        mirror, "release", lambda *args, **kwargs: {"id": 123, "body": json.dumps(pointer)}
    )
    downloaded = []

    def download(repository, provider, name, directory):
        downloaded.append(name)
        target = directory / name
        target.write_bytes((source.parent / name).read_bytes())
        return target

    monkeypatch.setattr(mirror, "download", download)
    monkeypatch.setattr(mirror.shutil, "disk_usage", lambda path: type("Usage", (), {"free": 0})())
    with pytest.raises(ValueError, match="free bytes"):
        mirror.restore(tmp_path / "restore", "owner/repo", PROVIDER)
    assert downloaded == [source.name]


def test_concurrent_pointer_change_is_not_overwritten(packed, tmp_path, monkeypatch):
    root, source = packed
    pointer = {
        "version": 1,
        "provider_id": PROVIDER,
        "generation": "pilot-1",
        "manifest_sha256": mirror.digest(source),
    }
    remote = {"id": 123, "body": json.dumps(pointer)}
    original_body = remote["body"]
    monkeypatch.setattr(mirror, "release", lambda *args, **kwargs: remote)
    monkeypatch.setattr(mirror, "download", lambda *args: source)
    uploaded = {}
    monkeypatch.setattr(mirror, "assets", lambda *args: list(uploaded.values()))
    (root / "mirror/providers" / PROVIDER / "empty.pdf").write_bytes(b"changed")

    def gh(*args):
        assert args[:2] == ("release", "upload"), "Concurrent pointer must not be overwritten"
        path = Path(args[3])
        uploaded[path.name] = {"name": path.name, "digest": "sha256:" + mirror.digest(path)}
        remote["body"] = json.dumps({**pointer, "generation": "concurrent"})
        return ""

    monkeypatch.setattr(mirror, "gh", gh)
    with pytest.raises(ValueError, match="pointer changed"):
        mirror.save(root, "owner/repo", PROVIDER, "new")
    assert remote["body"] != original_body
    assert json.loads(remote["body"])["generation"] == "concurrent"


def test_v1_space_preflight_ignores_untrusted_v2_payload_field(tmp_path, monkeypatch):
    paper = tarfile.TarInfo("paper.pdf")
    paper.size = 3
    source = handcrafted(tmp_path, [paper])
    payload = json.loads(source.read_text())
    payload["payload_bytes"] = 0
    source.write_text(json.dumps(payload))
    pointer = {
        "version": 1,
        "provider_id": PROVIDER,
        "generation": "test",
        "manifest_sha256": mirror.digest(source),
    }
    monkeypatch.setattr(
        mirror, "release", lambda *args, **kwargs: {"id": 123, "body": json.dumps(pointer)}
    )
    downloaded = []

    def download(repository, provider, name, directory):
        downloaded.append(name)
        target = directory / name
        target.write_bytes((source.parent / name).read_bytes())
        return target

    monkeypatch.setattr(mirror, "download", download)
    free = payload["chunks"][0]["size"] + 4096 + 2
    monkeypatch.setattr(
        mirror.shutil, "disk_usage", lambda path: type("Usage", (), {"free": free})()
    )
    with pytest.raises(ValueError, match="free bytes"):
        mirror.restore(tmp_path / "restore", "owner/repo", PROVIDER)
    assert downloaded == [source.name]


@pytest.fixture
def hydrating(packed, tmp_path, monkeypatch):
    _, archive = packed
    root = tmp_path / "actions"
    root.mkdir()
    monkeypatch.setenv("GITHUB_ACTIONS", "true")
    monkeypatch.setenv("GITHUB_WORKSPACE", str(root))
    pointer = {
        "version": 1,
        "provider_id": PROVIDER,
        "generation": "pilot-1",
        "manifest_sha256": mirror.digest(archive),
    }
    remote = {"id": 123, "body": json.dumps(pointer)}
    monkeypatch.setattr(mirror, "release", lambda *args, **kwargs: remote)
    downloads = []

    def download(repository, provider, name, directory):
        downloads.append(name)
        target = directory / name
        target.write_bytes((archive.parent / name).read_bytes())
        return target

    monkeypatch.setattr(mirror, "download", download)
    return root, pointer, archive, downloads, remote


def test_hydrate_replaces_partial_cache_and_preserves_new_files(hydrating):
    root, pointer, archive, downloads, _ = hydrating
    provider = root / "mirror/providers" / PROVIDER
    (provider / "111").mkdir(parents=True)
    (provider / "111/數學.pdf").write_bytes(b"old cache bytes")
    (provider / "new.pdf").write_bytes(b"new acquisition after failed backup")
    sibling = root / "mirror/providers/ceec_gsat/retained.pdf"
    sibling.parent.mkdir()
    sibling.write_bytes(b"sibling")
    (root / "mirror/.mirror-dedupe-index.json").write_text("old index")
    assert mirror.hydrate(root, "owner/repo", PROVIDER) == pointer
    assert (provider / "111/數學.pdf").stat().st_size == 4096
    assert (provider / "hardlink.pdf").samefile(provider / "111/數學.pdf")
    assert (provider / "new.pdf").read_bytes() == b"new acquisition after failed backup"
    assert sibling.read_bytes() == b"sibling"
    assert json.loads((provider / mirror.ORIGIN_NAME).read_text())["pointer"] == pointer
    assert len(downloads) > 2
    assert not list(provider.parent.glob(".*-hydrate-*"))
    assert not (root / "mirror/.mirror-dedupe-index.json").exists()


def test_current_marked_cache_skips_payload_downloads_and_keeps_progress(hydrating):
    root, pointer, _, downloads, _ = hydrating
    mirror.hydrate(root, "owner/repo", PROVIDER)
    downloads.clear()
    provider = root / "mirror/providers" / PROVIDER
    (provider / "new.pdf").write_bytes(b"progress")
    assert mirror.hydrate(root, "owner/repo", PROVIDER) == pointer
    assert downloads == []
    assert (provider / "new.pdf").read_bytes() == b"progress"


def test_aggregate_only_origin_marker_is_upgraded_by_verified_recovery(hydrating):
    root, _, _, downloads, _ = hydrating
    mirror.hydrate(root, "owner/repo", PROVIDER)
    origin = root / "mirror/providers" / PROVIDER / mirror.ORIGIN_NAME
    marker = json.loads(origin.read_text())
    marker.pop("files")
    origin.write_text(json.dumps(marker))
    downloads.clear()
    mirror.hydrate(root, "owner/repo", PROVIDER)
    assert downloads
    assert "files" in json.loads(origin.read_text())
    downloads.clear()
    mirror.hydrate(root, "owner/repo", PROVIDER)
    assert downloads == []


@pytest.mark.parametrize("extra_progress", [False, True])
def test_incomplete_marked_cache_is_refreshed(hydrating, extra_progress):
    root, _, _, downloads, _ = hydrating
    mirror.hydrate(root, "owner/repo", PROVIDER)
    provider = root / "mirror/providers" / PROVIDER
    (provider / "hardlink.pdf").unlink()
    if extra_progress:
        # New acquisitions must not mask a missing committed file merely by
        # making the aggregate count and byte totals look complete.
        (provider / "new.pdf").write_bytes(b"n" * 4096)
    downloads.clear()
    mirror.hydrate(root, "owner/repo", PROVIDER)
    assert downloads
    assert (provider / "hardlink.pdf").samefile(provider / "111/數學.pdf")
    if extra_progress:
        assert (provider / "new.pdf").read_bytes() == b"n" * 4096


@pytest.mark.parametrize("failure", ["corrupt", "concurrent"])
def test_failed_hydration_preserves_existing_cache(hydrating, monkeypatch, failure):
    root, pointer, archive, _, remote = hydrating
    provider = root / "mirror/providers" / PROVIDER
    provider.mkdir(parents=True)
    (provider / "retained.pdf").write_bytes(b"old cache")
    original_download = mirror.download

    def download(*args):
        path = original_download(*args)
        if path.name.endswith("part0000"):
            if failure == "corrupt":
                path.write_bytes(b"corrupt")
            else:
                remote["body"] = json.dumps({**pointer, "generation": "newer"})
        return path

    monkeypatch.setattr(mirror, "download", download)
    with pytest.raises(ValueError):
        mirror.hydrate(root, "owner/repo", PROVIDER)
    assert list(provider.iterdir()) == [provider / "retained.pdf"]
    assert (provider / "retained.pdf").read_bytes() == b"old cache"
    assert not list(provider.parent.glob(".*-hydrate-*"))


def test_missing_durable_snapshot_preserves_cache(hydrating, monkeypatch):
    root, _, _, downloads, _ = hydrating
    provider = root / "mirror/providers" / PROVIDER
    provider.mkdir(parents=True)
    (provider / "retained.pdf").write_bytes(b"bootstrap")
    monkeypatch.setattr(mirror, "release", lambda *args, **kwargs: None)
    assert mirror.hydrate(root, "owner/repo", PROVIDER) is None
    assert (provider / "retained.pdf").read_bytes() == b"bootstrap"
    assert not downloads


def test_hydrate_cannot_replace_local_operator_mirrors(hydrating, monkeypatch):
    root, _, _, downloads, _ = hydrating
    monkeypatch.delenv("GITHUB_ACTIONS")
    with pytest.raises(ValueError, match="restricted to an Actions workspace"):
        mirror.hydrate(root, "owner/repo", PROVIDER)
    assert not downloads


def _cache_reference_state(root, current_key, checksum, previous_keys=None):
    from app.manifest import SourceManifest, write_source_manifest
    from app.models import NormalizedPaper, to_plain_data
    from app.paths import provider_paths

    state = provider_paths(root, PROVIDER)
    state.papers_dir.mkdir(parents=True, exist_ok=True)
    paper = NormalizedPaper(
        canonical_id="math", canonical_name="數學", year_roc=111,
        exam_name_raw="分科測驗", category_raw="數學", subject_name_raw="數學",
        paper_code="math-question", file_type="question",
        download_url_source="https://official.example/math.pdf",
        provider_id=PROVIDER, source_exam_id="ceec-ast-111",
        category_code="math", subject_code="math",
        storage_key=current_key, checksum=checksum,
    )
    (state.papers_dir / "2022.json").write_text(json.dumps([to_plain_data(paper)]))
    if previous_keys is not None:
        write_source_manifest(state.source_manifest_path, SourceManifest(
            provider_id=PROVIDER,
            files={current_key: {
                "storage_key": current_key, "sha256": checksum,
                "previous_storage_keys": previous_keys,
            }},
        ))
    return state, paper


@pytest.mark.parametrize("warm", [False, True])
def test_hydrate_does_not_resurrect_verified_positional_aliases(hydrating, packed, warm):
    root, pointer, archive, _, _ = hydrating
    key = f"providers/{PROVIDER}/111/數學.pdf"
    old_key = f"providers/{PROVIDER}/obsolete-positional.pdf"
    if warm:
        mirror.hydrate(root, "owner/repo", PROVIDER)
    else:
        provider = root / "mirror/providers" / PROVIDER
        provider.mkdir(parents=True)
    provider = root / "mirror/providers" / PROVIDER
    payload = (packed[0] / "mirror" / key).read_bytes()
    checksum = hashlib.sha256(payload).hexdigest()
    _cache_reference_state(root, key, checksum, [old_key])
    (provider / "obsolete-positional.pdf").write_bytes(payload)
    (provider / "uncommitted-new-reference.pdf").write_bytes(payload)
    (provider / "new-version.pdf").write_bytes(b"new bytes from interrupted acquisition")
    orphan = provider / "recovery/source-revisions" / f"{checksum}.pdf"
    orphan.parent.mkdir(parents=True)
    orphan.write_bytes(payload)
    assert mirror.hydrate(root, "owner/repo", PROVIDER) == pointer
    assert not (provider / "obsolete-positional.pdf").exists()
    assert (provider / "uncommitted-new-reference.pdf").read_bytes() == payload
    assert (provider / "new-version.pdf").read_bytes() == b"new bytes from interrupted acquisition"
    assert orphan.read_bytes() == payload
    assert (provider / "111/數學.pdf").read_bytes() == payload


@pytest.mark.parametrize("protection", ["changed-cache", "changed-witness", "active-alias", "missing-state"])
def test_hydrate_preserves_aliases_without_complete_retirement_proof(hydrating, packed, protection):
    root, pointer, _, _, _ = hydrating
    key = f"providers/{PROVIDER}/111/數學.pdf"
    old_key = f"providers/{PROVIDER}/obsolete-positional.pdf"
    payload = (packed[0] / "mirror" / key).read_bytes()
    checksum = hashlib.sha256(payload).hexdigest()
    mirror.hydrate(root, "owner/repo", PROVIDER)
    provider = root / "mirror/providers" / PROVIDER
    if protection != "missing-state":
        active = old_key if protection == "active-alias" else key
        _cache_reference_state(root, active, checksum)
        from app.manifest import SourceManifest, write_source_manifest
        from app.paths import provider_paths
        evidence_checksum = "0" * 64 if protection == "changed-witness" else checksum
        write_source_manifest(provider_paths(root, PROVIDER).source_manifest_path, SourceManifest(
            provider_id=PROVIDER,
            files={key: {"storage_key": key, "sha256": evidence_checksum, "previous_storage_keys": [old_key]}},
        ))
    cached = b"different uncommitted revision" if protection == "changed-cache" else payload
    (provider / "obsolete-positional.pdf").write_bytes(cached)
    assert mirror.hydrate(root, "owner/repo", PROVIDER) == pointer
    assert (provider / "obsolete-positional.pdf").read_bytes() == cached


@pytest.mark.parametrize("warm", [False, True])
def test_retired_alias_already_in_committed_generation_is_removed(hydrating, packed, tmp_path, monkeypatch, warm):
    root, _, _, _, remote = hydrating
    key = f"providers/{PROVIDER}/111/數學.pdf"
    old_key = f"providers/{PROVIDER}/obsolete-positional.pdf"
    payload = (packed[0] / "mirror" / key).read_bytes()
    (packed[0] / "mirror" / old_key).write_bytes(payload)
    archive = mirror.pack(packed[0], PROVIDER, "with-retired", tmp_path / "with-retired", chunk_bytes=257)
    pointer = {"version": 1, "provider_id": PROVIDER, "generation": "with-retired", "manifest_sha256": mirror.digest(archive)}
    remote["body"] = json.dumps(pointer)

    def download(repository, provider, name, directory):
        target = directory / name
        target.write_bytes((archive.parent / name).read_bytes())
        return target

    monkeypatch.setattr(mirror, "download", download)
    if warm:
        # A marker legitimately covers the older committed alias. It must not
        # mask retirement once the matching current state is installed.
        mirror.hydrate(root, "owner/repo", PROVIDER)
    _cache_reference_state(root, key, hashlib.sha256(payload).hexdigest(), [old_key])
    assert mirror.hydrate(root, "owner/repo", PROVIDER) == pointer
    assert not (root / "mirror" / old_key).exists()
    assert (root / "mirror" / key).read_bytes() == payload


@pytest.mark.parametrize("bad_alias", ["../outside.pdf", f"providers/{PROVIDER}/../outside.pdf", "providers/other/paper.pdf", f"providers/{PROVIDER}/recovery/source-revisions/" + "a" * 64 + ".pdf"])
def test_invalid_alias_evidence_preserves_cache(hydrating, packed, bad_alias):
    root, _, _, _, _ = hydrating
    key = f"providers/{PROVIDER}/111/數學.pdf"
    payload = (packed[0] / "mirror" / key).read_bytes()
    _cache_reference_state(root, key, hashlib.sha256(payload).hexdigest(), [bad_alias])
    provider = root / "mirror/providers" / PROVIDER
    provider.mkdir(parents=True)
    (provider / "progress.pdf").write_bytes(b"preserve progress")
    with pytest.raises(ValueError, match="retired mirror locator"):
        mirror.hydrate(root, "owner/repo", PROVIDER)
    assert (provider / "progress.pdf").read_bytes() == b"preserve progress"


def test_retired_reference_original_requires_verified_independent_blob(tmp_path):
    from dataclasses import replace
    from app.models import NormalizedCatalog
    from app.publisher import write_provider_state
    from app.source_revisions import load_source_revisions

    root = tmp_path / "root"
    key = f"providers/{PROVIDER}/current.pdf"
    old_key = f"providers/{PROVIDER}/retired.pdf"
    payload = b"earlier verified payload"
    checksum = hashlib.sha256(payload).hexdigest()
    state, current = _cache_reference_state(root, old_key, checksum)
    original = root / "mirror" / old_key
    original.parent.mkdir(parents=True)
    original.write_bytes(payload)
    current = replace(current, storage_key=key, subject_code="current", download_url_source="https://official.example/current.pdf")
    write_provider_state(state, [], NormalizedCatalog([current], []), [], [], None)
    revision = load_source_revisions(state)[0]
    incoming = tmp_path / "incoming"
    incoming.mkdir()
    blob = incoming / Path(revision["blob_storage_key"]).relative_to(Path("providers") / PROVIDER)
    blob.parent.mkdir(parents=True)
    assert mirror._retired_cache_payloads(root, PROVIDER, incoming) == {}
    blob.write_bytes(b"wrong historical bytes")
    assert mirror._retired_cache_payloads(root, PROVIDER, incoming) == {}
    blob.write_bytes(payload)
    proofs = mirror._retired_cache_payloads(root, PROVIDER, incoming)
    assert proofs == {"retired.pdf": {checksum}}
    assert mirror._remove_retired_cache_paths(original.parent, proofs) == 1
    assert not original.exists() and blob.read_bytes() == payload


def test_origin_marker_is_excluded_from_snapshot_content(packed, tmp_path):
    root, path = packed
    manifest = mirror.load_manifest(path, PROVIDER, "pilot-1")
    mirror.write_origin(root, PROVIDER, {"generation": "ignored metadata"}, manifest)
    probe = mirror.pack(root, PROVIDER, "other", tmp_path / "probe", chunk_bytes=257)
    new = mirror.load_manifest(probe, PROVIDER, "other")
    assert new["file_count"] == manifest["file_count"]
    assert new["unpacked_bytes"] == manifest["unpacked_bytes"]
    assert [c["sha256"] for c in new["chunks"]] == [c["sha256"] for c in manifest["chunks"]]


def test_cache_budget_counts_shared_payload_only_once(packed, monkeypatch):
    root, _ = packed
    monkeypatch.setattr(mirror, "CACHE_PAYLOAD_LIMIT", 4096)
    assert mirror.cacheable(root, PROVIDER)
    monkeypatch.setattr(mirror, "CACHE_PAYLOAD_LIMIT", 4095)
    assert not mirror.cacheable(root, PROVIDER)


def test_cache_budget_output_survives_failed_backup(packed, tmp_path, monkeypatch):
    root, _ = packed
    output = tmp_path / "outputs"
    monkeypatch.setenv("GITHUB_OUTPUT", str(output))
    monkeypatch.setattr(mirror, "CACHE_PAYLOAD_LIMIT", 4095)
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "mirror_snapshots",
            "save",
            "--provider",
            PROVIDER,
            "--repository",
            "owner/repo",
            "--repo-root",
            str(root),
        ],
    )

    def failed(*args):
        raise RuntimeError("upload unavailable")

    monkeypatch.setattr(mirror, "save", failed)
    assert mirror.main() == 1
    assert output.read_text() == "cacheable=false\n"


def test_hydrate_cli_uses_latest_pointer_without_restore_flags(hydrating, monkeypatch):
    root, _, _, _, _ = hydrating
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "mirror_snapshots",
            "hydrate",
            "--provider",
            PROVIDER,
            "--repository",
            "owner/repo",
            "--repo-root",
            str(root),
        ],
    )
    assert mirror.main() == 0
    monkeypatch.setattr(sys, "argv", sys.argv + ["--generation", "pilot-1"])
    assert mirror.main() == 1


@pytest.mark.parametrize('provider', ['cpc_recruit', 'gept_cert', 'hakka_cert', 'jlpt_cert', 'tii_cert'])
def test_private_cache_policy_has_no_public_backup_or_network_work(tmp_path, monkeypatch, provider):
    source = tmp_path / 'mirror/providers' / provider
    source.mkdir(parents=True)
    (source / 'paper.pdf').write_bytes(b'original')
    os.link(source / 'paper.pdf', source / 'shared.pdf')
    output = tmp_path / 'outputs'
    monkeypatch.setenv('GITHUB_OUTPUT', str(output))
    monkeypatch.setattr(mirror, 'CACHE_PAYLOAD_LIMIT', len(b'original'))
    def unexpected(*args, **kwargs):
        pytest.fail('Cache policy reached public storage')
    monkeypatch.setattr(mirror, 'save', unexpected)
    monkeypatch.setattr(mirror, 'gh', unexpected)
    monkeypatch.setattr(mirror, 'pack', unexpected)
    monkeypatch.setattr(sys, 'argv', ['mirror_snapshots.py', 'cache-policy', '--repo-root', str(tmp_path), '--provider', provider])
    assert mirror.main() == 0
    assert output.read_text() == 'cacheable=true\n'
    monkeypatch.setattr(mirror, 'CACHE_PAYLOAD_LIMIT', len(b'original') - 1)
    output.unlink()
    assert mirror.main() == 0
    assert output.read_text() == 'cacheable=false\n'


def test_cache_policy_reports_missing_mirror_without_masking_invalid_owner(tmp_path, monkeypatch):
    output = tmp_path / 'outputs'
    monkeypatch.setenv('GITHUB_OUTPUT', str(output))
    monkeypatch.setattr(sys, 'argv', ['mirror_snapshots.py', 'cache-policy', '--repo-root', str(tmp_path), '--provider', 'tii_cert'])
    assert mirror.main() == 0
    assert output.read_text() == 'cacheable=false\n'
    monkeypatch.setattr(sys, 'argv', sys.argv[:-1] + ['not_a_provider'])
    assert mirror.main() == 1
