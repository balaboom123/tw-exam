"""Persist provider mirrors as public, chunked GitHub Release snapshots.

Run with the gh CLI authenticated. Releases are transport backups, not site
publication: quarantined files remain excluded from the site's bundle inventory.
"""

from __future__ import annotations

import argparse
import gzip
import hashlib
import io
import json
import os
import re
import shutil
import stat
import subprocess
import sys
import tarfile
import tempfile
import uuid
from collections.abc import Callable, Iterable
from datetime import UTC, datetime
from pathlib import Path, PurePosixPath

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
from app.providers.registry import get_provider  # noqa: E402 - bootstrap repository imports

CHUNK_BYTES = 1_900_000_000  # Strictly below GitHub's 2 GiB per-asset limit.
BLOCK_BYTES = 1024 * 1024
GENERATION = re.compile(r"[a-zA-Z0-9][a-zA-Z0-9_-]{0,79}")
ORIGIN_NAME = ".snapshot-origin.json"
CACHE_PAYLOAD_LIMIT = 8_000_000_000


def source_files(source: Path):
    if not source.is_dir() or source.is_symlink():
        raise ValueError("No regular provider mirror directory")
    for path in sorted(source.rglob("*")):
        if path.parent == source and (
            path.name == ORIGIN_NAME or path.name.startswith(ORIGIN_NAME + ".")
        ):
            continue
        if path.is_symlink():
            raise ValueError(f"Mirror contains a symbolic link: {path}")
        if path.is_dir():
            continue
        if not stat.S_ISREG(path.stat().st_mode):
            raise ValueError(f"Mirror contains a non-regular file: {path}")
        yield path


def write_origin(root: Path, provider: str, pointer: dict, manifest: dict):
    origin = root / "mirror/providers" / provider / ORIGIN_NAME
    temporary = origin.with_name(ORIGIN_NAME + "." + uuid.uuid4().hex)
    temporary.write_text(
        json.dumps(
            {
                "pointer": pointer,
                "file_count": manifest["file_count"],
                "unpacked_bytes": manifest["unpacked_bytes"],
            }
        ),
        encoding="utf-8",
    )
    temporary.replace(origin)


def cacheable(root: Path, provider: str) -> bool:
    """Reserve cache headroom and count shared payloads only once."""
    seen = set()
    total = 0
    for path in source_files(root / "mirror/providers" / provider):
        info = path.stat()
        key = (info.st_dev, info.st_ino)
        if key not in seen:
            seen.add(key)
            total += info.st_size
            if total > CACHE_PAYLOAD_LIMIT:
                return False
    return True


def checked_generation(value: str) -> str:
    if not GENERATION.fullmatch(value):
        raise ValueError("Invalid mirror snapshot generation")
    return value


def digest(path: Path) -> str:
    result = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(BLOCK_BYTES), b""):
            result.update(block)
    return result.hexdigest()


class ChunkWriter:
    def __init__(
        self, directory: Path, generation: str, limit: int, *, keep_chunks=True, on_chunk=None
    ):
        self.directory = directory
        self.generation = generation
        self.limit = limit
        self.stream = None
        self.paths = []
        self.chunks = []
        self.current = None
        self.checksum = None
        self.keep_chunks = keep_chunks
        self.on_chunk = on_chunk
        self.failure = None
        self.size = 0

    def write(self, data: bytes) -> int:
        if self.failure is not None:
            raise self.failure
        view = memoryview(data)
        written = len(view)
        while view:
            if self.current is None or self.size == self.limit:
                self._finish_chunk()
                path = (
                    self.directory / f"snapshot-{self.generation}.tar.gz.part{len(self.paths):04d}"
                )
                self.paths.append(path)
                self.current = path
                self.stream = path.open("wb") if self.keep_chunks else None
                self.checksum = hashlib.sha256()
                self.size = 0
            length = min(len(view), self.limit - self.size)
            if self.stream is not None:
                self.stream.write(view[:length])
            self.checksum.update(view[:length])
            self.size += length
            view = view[length:]
        return written

    def flush(self) -> None:
        if self.stream is not None:
            self.stream.flush()

    def close(self) -> None:
        if self.failure is not None:
            self.abort()
            return
        self._finish_chunk()

    def abort(self) -> None:
        if self.stream is not None:
            self.stream.close()
        self.current = self.stream = None

    def _finish_chunk(self) -> None:
        if self.current is None:
            return
        if self.stream is not None:
            self.stream.close()
        path = self.current
        chunk = {"name": path.name, "size": self.size, "sha256": self.checksum.hexdigest()}
        self.current = self.stream = None
        self.chunks.append(chunk)
        if self.on_chunk is not None:
            try:
                self.on_chunk(path, chunk)
            except Exception as exc:
                self.failure = exc
                raise


class ChunkReader(io.RawIOBase):
    def __init__(self, paths: Iterable[Path]):
        super().__init__()
        self.paths = iter(paths)
        self.stream = None

    def readable(self) -> bool:
        return True

    def readinto(self, buffer) -> int:
        while True:
            if self.stream is None:
                path = next(self.paths, None)
                if path is None:
                    return 0
                self.stream = path.open("rb")
            count = self.stream.readinto(buffer)
            if count:
                return count
            self.stream.close()
            self.stream = None

    def close(self) -> None:
        if self.stream is not None:
            self.stream.close()
        close_paths = getattr(self.paths, "close", None)
        if close_paths is not None:
            close_paths()
        super().close()


def pack(
    root: Path,
    provider: str,
    generation: str,
    output: Path,
    *,
    chunk_bytes: int = CHUNK_BYTES,
    keep_chunks: bool = True,
    on_chunk: Callable[[Path, dict], None] | None = None,
) -> Path:
    get_provider(provider)
    checked_generation(generation)
    if not 0 < chunk_bytes <= CHUNK_BYTES:
        raise ValueError("Invalid snapshot chunk size")
    source = root / "mirror/providers" / provider
    if not source.is_dir() or source.is_symlink():
        raise ValueError(f"No regular mirror directory for {provider}")
    output.mkdir(parents=True, exist_ok=True)
    if any(output.iterdir()):
        raise ValueError("Snapshot output directory must be empty")
    writer = ChunkWriter(
        output, generation, chunk_bytes, keep_chunks=keep_chunks, on_chunk=on_chunk
    )
    count = total = payload_bytes = 0
    inodes = {}
    try:
        with gzip.GzipFile(
            filename="", mode="wb", fileobj=writer, compresslevel=1, mtime=0
        ) as compressed:
            with tarfile.open(fileobj=compressed, mode="w|") as archive:
                for path in source_files(source):
                    metadata = path.stat()
                    if not stat.S_ISREG(metadata.st_mode):
                        raise ValueError(f"Mirror contains a non-regular file: {path}")
                    info = tarfile.TarInfo(path.relative_to(source).as_posix())
                    info.size = metadata.st_size
                    info.mode = 0o644
                    inode = (metadata.st_dev, metadata.st_ino)
                    if metadata.st_nlink > 1 and inode in inodes:
                        info.type = tarfile.LNKTYPE
                        info.linkname = inodes[inode]
                        info.size = 0
                        archive.addfile(info)
                    else:
                        if metadata.st_nlink > 1:
                            inodes[inode] = info.name
                        with path.open("rb") as stream:
                            archive.addfile(info, stream)
                        payload_bytes += metadata.st_size
                    count += 1
                    total += metadata.st_size
    except BaseException:
        writer.abort()
        raise
    finally:
        writer.close()
    if not count:
        raise ValueError("Refusing to replace a snapshot with an empty mirror")
    manifest = {
        "version": 2,
        "provider_id": provider,
        "generation": generation,
        "format": "tar.gz",
        "created_at": datetime.now(UTC).isoformat(),
        "file_count": count,
        "unpacked_bytes": total,
        "payload_bytes": payload_bytes,
        "chunks": writer.chunks,
    }
    path = output / f"snapshot-{generation}.json"
    path.write_text(json.dumps(manifest, separators=(",", ":")) + "\n", encoding="utf-8")
    return path


def load_manifest(path: Path, provider: str, generation: str) -> dict:
    checked_generation(generation)
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError("Invalid mirror snapshot manifest")
    if (
        type(payload.get("version")) is not int
        or payload.get("version") not in (1, 2)
        or payload.get("provider_id") != provider
        or payload.get("generation") != generation
        or payload.get("format") != "tar.gz"
    ):
        raise ValueError("Mirror snapshot manifest has a different owner, generation, or format")
    for key in ("file_count", "unpacked_bytes"):
        if type(payload.get(key)) is not int or payload[key] < (1 if key == "file_count" else 0):
            raise ValueError(f"Invalid mirror snapshot {key}")
    chunks = payload.get("chunks")
    if payload["version"] == 2 and (
        type(payload.get("payload_bytes")) is not int
        or not 0 <= payload["payload_bytes"] <= payload["unpacked_bytes"]
    ):
        raise ValueError("Invalid mirror snapshot payload_bytes")
    if not isinstance(chunks, list) or not 0 < len(chunks) < 999:
        raise ValueError("Invalid mirror snapshot chunk inventory")
    for index, chunk in enumerate(chunks):
        if not isinstance(chunk, dict):
            raise ValueError("Invalid mirror snapshot chunk")
        if (
            chunk.get("name") != f"snapshot-{generation}.tar.gz.part{index:04d}"
            or type(chunk.get("size")) is not int
            or not 0 < chunk["size"] <= CHUNK_BYTES
            or not re.fullmatch(r"[0-9a-f]{64}", str(chunk.get("sha256", "")))
        ):
            raise ValueError("Invalid mirror snapshot chunk")
    return payload


def unpack(
    root: Path, provider: str, manifest_path: Path, generation: str, *, chunk_loader=None
) -> dict:
    get_provider(provider)
    manifest = load_manifest(manifest_path, provider, generation)

    def verified(path, chunk):
        if (
            not path.is_file()
            or path.is_symlink()
            or path.stat().st_size != chunk["size"]
            or digest(path) != chunk["sha256"]
        ):
            raise ValueError(f"Mirror snapshot checksum/size mismatch: {path.name}")
        return path

    if chunk_loader is None:
        paths = [
            verified(manifest_path.parent / chunk["name"], chunk) for chunk in manifest["chunks"]
        ]
    else:

        def streamed_paths():
            for chunk in manifest["chunks"]:
                path = verified(chunk_loader(chunk), chunk)
                try:
                    yield path
                finally:
                    path.unlink(missing_ok=True)

        paths = streamed_paths()
    parent = root / "mirror/providers"
    parent.mkdir(parents=True, exist_ok=True)
    destination = parent / provider
    if destination.is_symlink() or (destination.exists() and any(destination.iterdir())):
        raise ValueError("Restore requires an empty provider mirror; existing files were preserved")
    count = total = 0
    names = set()
    regular_files = {}
    payload_bytes = 0
    with tempfile.TemporaryDirectory(prefix=f".{provider}-restore-", dir=parent) as temporary:
        stage = Path(temporary) / provider
        stage.mkdir()
        with io.BufferedReader(ChunkReader(paths)) as stream:
            with gzip.GzipFile(fileobj=stream, mode="rb") as compressed:
                with tarfile.open(fileobj=compressed, mode="r|") as archive:
                    for member in archive:
                        name = PurePosixPath(member.name)
                        if (
                            not (member.isfile() or (manifest["version"] == 2 and member.islnk()))
                            or name.is_absolute()
                            or ".." in name.parts
                            or not name.parts
                            or name.as_posix() != member.name
                            or "\\" in member.name
                            or member.name in names
                        ):
                            raise ValueError(f"Unsafe mirror snapshot member: {member.name}")
                        linked = regular_files.get(member.linkname) if member.islnk() else None
                        if member.islnk() and (linked is None or member.size != 0):
                            raise ValueError(f"Unsafe mirror snapshot hard link: {member.name}")
                        count += 1
                        total += linked.stat().st_size if linked is not None else member.size
                        if count > manifest["file_count"] or total > manifest["unpacked_bytes"]:
                            raise ValueError("Mirror snapshot exceeds its declared inventory")
                        names.add(member.name)
                        target = stage / name
                        target.parent.mkdir(parents=True, exist_ok=True)
                        if linked is not None:
                            os.link(linked, target)
                            continue
                        incoming = archive.extractfile(member)
                        if incoming is None:
                            raise ValueError("Missing mirror snapshot payload")
                        with incoming, target.open("xb") as output:
                            shutil.copyfileobj(incoming, output, BLOCK_BYTES)
                        regular_files[member.name] = target
                        payload_bytes += member.size
                # Read through the trailer so gzip CRC/truncation errors are surfaced.
                while compressed.read(BLOCK_BYTES):
                    pass
        if count != manifest["file_count"] or total != manifest["unpacked_bytes"]:
            raise ValueError("Mirror snapshot is missing declared files or bytes")
        if manifest["version"] == 2 and payload_bytes != manifest["payload_bytes"]:
            raise ValueError("Mirror snapshot payload bytes disagree with its manifest")
        if destination.exists():
            destination.rmdir()
        stage.rename(destination)
    # The root dedupe index is derived and may reference a different cache generation.
    (root / "mirror/.mirror-dedupe-index.json").unlink(missing_ok=True)
    return manifest


def gh(*args: str) -> str:
    return subprocess.check_output(["gh", *args], text=True, encoding="utf-8")


def release(repository: str, provider: str, *, allow_missing: bool = False) -> dict | None:
    result = subprocess.run(
        ["gh", "api", f"repos/{repository}/releases/tags/mirror-{provider}"],
        capture_output=True,
        text=True,
        check=False,
    )
    if result.returncode:
        if allow_missing and "(HTTP 404)" in result.stderr:
            return None
        raise RuntimeError(result.stderr.strip())
    payload = json.loads(result.stdout)
    if not payload.get("prerelease") or payload.get("draft"):
        raise ValueError("Mirror storage must be a published prerelease")
    return payload


def assets(repository: str, release_id: int) -> list[dict]:
    result = []
    page = 1
    while True:
        batch = json.loads(
            gh(
                "api",
                f"repos/{repository}/releases/{release_id}/assets?per_page=100&page={page}",
            )
        )
        result.extend(batch)
        if len(batch) < 100:
            return result
        page += 1


def current_pointer(remote: dict, provider: str) -> dict:
    try:
        pointer = json.loads(remote.get("body") or "")
        if pointer["version"] != 1 or pointer["provider_id"] != provider:
            raise ValueError("Wrong mirror snapshot pointer owner/version")
        checked_generation(pointer["generation"])
        if not re.fullmatch(r"[0-9a-f]{64}", pointer["manifest_sha256"]):
            raise ValueError("Invalid mirror manifest digest")
        return pointer
    except (ValueError, KeyError, TypeError) as exc:
        raise ValueError("Mirror release has no valid committed snapshot pointer") from exc


def download(repository: str, provider: str, name: str, directory: Path) -> Path:
    gh(
        "release",
        "download",
        f"mirror-{provider}",
        "--repo",
        repository,
        "--pattern",
        name,
        "--dir",
        str(directory),
    )
    return directory / name


def restore(
    root: Path,
    repository: str,
    provider: str,
    *,
    generation: str | None = None,
    manifest_sha256: str | None = None,
    allow_missing: bool = False,
) -> dict | None:
    get_provider(provider)
    remote = release(repository, provider, allow_missing=allow_missing and generation is None)
    if remote is None:
        print(f"No durable snapshot yet for {provider}; source bootstrap is required")
        return None
    if generation is None:
        pointer = current_pointer(remote, provider)
        generation, manifest_sha256 = pointer["generation"], pointer["manifest_sha256"]
    checked_generation(generation)
    if not manifest_sha256 or not re.fullmatch(r"[0-9a-f]{64}", manifest_sha256):
        raise ValueError("An exact generation restore requires its manifest SHA256")
    root.joinpath(".tmp").mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="mirror-download-", dir=root / ".tmp") as temporary:
        directory = Path(temporary)
        path = download(repository, provider, f"snapshot-{generation}.json", directory)
        if digest(path) != manifest_sha256:
            raise ValueError("Mirror snapshot manifest checksum mismatch")
        manifest = load_manifest(path, provider, generation)
        required = (
            (manifest["payload_bytes"] if manifest["version"] == 2 else manifest["unpacked_bytes"])
            + max(c["size"] for c in manifest["chunks"])
            + manifest["file_count"] * 4096
        )
        available = shutil.disk_usage(root).free
        if required > available:
            raise ValueError(
                f"Mirror restore needs {required} free bytes; only {available} available"
            )
        result = unpack(
            root,
            provider,
            path,
            generation,
            chunk_loader=lambda chunk: download(repository, provider, chunk["name"], directory),
        )
        write_origin(
            root,
            provider,
            {
                "version": 1,
                "provider_id": provider,
                "generation": generation,
                "manifest_sha256": manifest_sha256,
            },
            manifest,
        )
    print(
        f"Restored {provider} snapshot {generation}: "
        f"{result['file_count']} files, {result['unpacked_bytes']} bytes"
    )
    return result


def save(root: Path, repository: str, provider: str, generation: str) -> dict:
    get_provider(provider)
    checked_generation(generation)
    root.joinpath(".tmp").mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="mirror-upload-", dir=root / ".tmp") as temporary:
        directory = Path(temporary)
        # Hash a deterministic archive without storing compressed bytes. Usually
        # the mirror is unchanged and no second pass or upload is needed.
        probe_path = pack(
            root,
            provider,
            generation,
            directory / "probe",
            chunk_bytes=CHUNK_BYTES,
            keep_chunks=False,
        )
        manifest = load_manifest(probe_path, provider, generation)
        remote = release(repository, provider, allow_missing=True)
        if remote is None:
            gh(
                "release",
                "create",
                f"mirror-{provider}",
                "--repo",
                repository,
                "--prerelease",
                "--latest=false",
                "--target",
                "main",
                "--title",
                f"Provider mirror backup: {provider}",
                "--notes",
                "Snapshot upload in progress",
            )
            remote = release(repository, provider)
        original_body = remote.get("body")
        inventory = assets(repository, remote["id"])
        existing = {asset["name"]: asset for asset in inventory}
        if remote.get("body") != "Snapshot upload in progress":
            previous = current_pointer(remote, provider)
            previous_dir = directory / "previous"
            previous_dir.mkdir()
            previous_path = download(
                repository, provider, f"snapshot-{previous['generation']}.json", previous_dir
            )
            if digest(previous_path) != previous["manifest_sha256"]:
                raise ValueError("Committed mirror manifest checksum mismatch")
            old = load_manifest(previous_path, provider, previous["generation"])
            if (
                old["file_count"] == manifest["file_count"]
                and old["unpacked_bytes"] == manifest["unpacked_bytes"]
                and [(c["size"], c["sha256"]) for c in old["chunks"]]
                == [(c["size"], c["sha256"]) for c in manifest["chunks"]]
            ):
                if any(
                    existing.get(c["name"], {}).get("digest") != f"sha256:{c['sha256']}"
                    for c in old["chunks"]
                ):
                    raise ValueError("Committed mirror snapshot is missing verified chunks")
                print(f"Reused unchanged {provider} snapshot {previous['generation']}")
                write_origin(root, provider, previous, manifest)
                return previous
        expected = {c["name"]: c for c in manifest["chunks"]}
        names = [*expected, probe_path.name]
        if len(inventory) + sum(name not in existing for name in names) > 1000:
            raise ValueError(
                "Mirror release asset limit reached; remove expired generations before retrying"
            )

        def upload(path, checksum):
            if path.name in existing:
                if existing[path.name].get("digest") != f"sha256:{checksum}":
                    raise ValueError(
                        "A different snapshot already uses this generation; choose a new generation"
                    )
                return
            gh("release", "upload", f"mirror-{provider}", str(path), "--repo", repository)

        def upload_chunk(path, chunk):
            if expected.get(path.name) != chunk:
                raise ValueError("Mirror changed while packing; snapshot pointer was preserved")
            upload(path, chunk["sha256"])
            path.unlink()

        # Changed mirrors get a second compression pass. Upload and discard each
        # verified completed chunk before writing the next one, bounding staging.
        manifest_path = pack(
            root,
            provider,
            generation,
            directory / "payload",
            chunk_bytes=CHUNK_BYTES,
            on_chunk=upload_chunk,
        )
        packed = load_manifest(manifest_path, provider, generation)
        if any(
            packed[key] != manifest[key]
            for key in ("file_count", "unpacked_bytes", "payload_bytes", "chunks")
        ):
            raise ValueError("Mirror changed while packing; snapshot pointer was preserved")
        manifest = packed
        manifest_checksum = digest(manifest_path)
        upload(manifest_path, manifest_checksum)
        uploaded = {asset["name"]: asset for asset in assets(repository, remote["id"])}
        checksums = {c["name"]: c["sha256"] for c in manifest["chunks"]}
        checksums[manifest_path.name] = manifest_checksum
        if any(
            uploaded.get(name, {}).get("digest") != f"sha256:{checksum}"
            for name, checksum in checksums.items()
        ):
            raise ValueError("GitHub has not confirmed every uploaded snapshot digest")
        pointer = {
            "version": 1,
            "provider_id": provider,
            "generation": generation,
            "manifest_sha256": manifest_checksum,
        }
        latest = release(repository, provider)
        if latest["id"] != remote["id"] or latest.get("body") != original_body:
            raise ValueError("Mirror pointer changed during upload; newer pointer was preserved")
        request = directory / "pointer-request.json"
        request.write_text(
            json.dumps({"body": json.dumps(pointer, separators=(",", ":"))}), encoding="utf-8"
        )
        gh(
            "api",
            "--method",
            "PATCH",
            f"repos/{repository}/releases/{remote['id']}",
            "--input",
            str(request),
        )
        write_origin(root, provider, pointer, manifest)
    print(
        f"Saved {provider} snapshot {generation}: {manifest['file_count']} files, "
        f"{manifest['unpacked_bytes']} bytes"
    )
    return pointer


def hydrate(root: Path, repository: str, provider: str):
    """Refresh an Actions cache from the committed durable snapshot when needed."""
    get_provider(provider)
    if (
        os.environ.get("GITHUB_ACTIONS") != "true"
        or Path(os.environ.get("GITHUB_WORKSPACE", "")).resolve() != root.resolve()
    ):
        raise ValueError("Cache hydration is restricted to an Actions workspace")
    remote = release(repository, provider, allow_missing=True)
    if remote is None:
        print(
            f"No durable snapshot yet for {provider}; retained cache/source bootstrap is required"
        )
        return None
    pointer = current_pointer(remote, provider)
    parent = root / "mirror/providers"
    source = parent / provider
    origin = source / ORIGIN_NAME
    if origin.is_file() and not origin.is_symlink():
        try:
            cached = json.loads(origin.read_text(encoding="utf-8"))
            files = list(source_files(source))
            if (
                isinstance(cached, dict)
                and cached.get("pointer") == pointer
                and type(cached.get("file_count")) is int
                and cached["file_count"] > 0
                and type(cached.get("unpacked_bytes")) is int
                and cached["unpacked_bytes"] >= 0
                and len(files) >= cached["file_count"]
                and sum(p.stat().st_size for p in files) >= cached["unpacked_bytes"]
            ):
                print(f"Warm {provider} mirror matches durable generation {pointer['generation']}")
                return pointer
        except (ValueError, OSError):
            pass
    parent.mkdir(parents=True, exist_ok=True)
    if source.is_symlink():
        raise ValueError("Provider mirror cache is a symbolic link")
    with tempfile.TemporaryDirectory(prefix=f".{provider}-hydrate-", dir=parent) as temporary:
        stage_root = Path(temporary)
        restore(
            stage_root,
            repository,
            provider,
            generation=pointer["generation"],
            manifest_sha256=pointer["manifest_sha256"],
        )
        incoming = stage_root / "mirror/providers" / provider
        # Keep newly acquired files from a failed backup. The committed snapshot
        # supplies overlapping paths, avoiding replay of an older cache's bytes.
        if source.exists():
            for path in source_files(source):
                target = incoming / path.relative_to(source)
                if not target.exists():
                    target.parent.mkdir(parents=True, exist_ok=True)
                    os.link(path, target)
        latest = release(repository, provider)
        if latest["id"] != remote["id"] or current_pointer(latest, provider) != pointer:
            raise ValueError(
                "Durable snapshot changed during cache hydration; retained cache was preserved"
            )
        previous = stage_root / "previous-cache"
        if source.exists():
            source.rename(previous)
        try:
            incoming.rename(source)
        except BaseException:
            if previous.exists():
                previous.rename(source)
            raise
    (root / "mirror/.mirror-dedupe-index.json").unlink(missing_ok=True)
    print(f"Refreshed {provider} cache from durable generation {pointer['generation']}")
    return pointer


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=("save", "restore", "hydrate"))
    parser.add_argument("--repo-root", type=Path, default=ROOT)
    parser.add_argument("--repository", default=os.environ.get("GITHUB_REPOSITORY"))
    parser.add_argument("--provider", required=True)
    parser.add_argument("--generation")
    parser.add_argument("--manifest-sha256")
    parser.add_argument("--allow-missing", action="store_true")
    args = parser.parse_args()
    if not args.repository or not re.fullmatch(r"[\w.-]+/[\w.-]+", args.repository):
        parser.error("--repository owner/repo is required")
    try:
        if args.command == "save":
            get_provider(args.provider)
            if output := os.environ.get("GITHUB_OUTPUT"):
                with open(output, "a", encoding="utf-8") as stream:
                    stream.write(
                        f"cacheable={str(cacheable(args.repo_root, args.provider)).lower()}\n"
                    )
            generation = args.generation or uuid.uuid4().hex
            pointer = save(args.repo_root, args.repository, args.provider, generation)
            if output := os.environ.get("GITHUB_OUTPUT"):
                with open(output, "a", encoding="utf-8") as stream:
                    stream.write(
                        f"generation={pointer['generation']}\nmanifest_sha256={pointer['manifest_sha256']}\n"
                    )
        elif args.command == "hydrate":
            if args.generation or args.manifest_sha256 or args.allow_missing:
                raise ValueError(
                    "hydrate uses the latest durable pointer; restore owns pinned recovery"
                )
            hydrate(args.repo_root, args.repository, args.provider)
        else:
            restore(
                args.repo_root,
                args.repository,
                args.provider,
                generation=args.generation,
                manifest_sha256=args.manifest_sha256,
                allow_missing=args.allow_missing,
            )
    except (
        EOFError,
        ValueError,
        RuntimeError,
        OSError,
        tarfile.TarError,
        subprocess.CalledProcessError,
    ) as exc:
        print(str(exc), file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
