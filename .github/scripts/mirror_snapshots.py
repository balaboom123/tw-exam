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
from datetime import UTC, datetime
from pathlib import Path, PurePosixPath

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
from app.providers.registry import get_provider  # noqa: E402 - bootstrap repository imports

CHUNK_BYTES = 1_900_000_000  # Strictly below GitHub's 2 GiB per-asset limit.
BLOCK_BYTES = 1024 * 1024
GENERATION = re.compile(r"[a-zA-Z0-9][a-zA-Z0-9_-]{0,79}")


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
    def __init__(self, directory: Path, generation: str, limit: int):
        self.directory = directory
        self.generation = generation
        self.limit = limit
        self.stream = None
        self.paths = []
        self.size = 0

    def write(self, data: bytes) -> int:
        view = memoryview(data)
        written = len(view)
        while view:
            if self.stream is None or self.size == self.limit:
                if self.stream is not None:
                    self.stream.close()
                path = (
                    self.directory
                    / f"snapshot-{self.generation}.tar.gz.part{len(self.paths):04d}"
                )
                self.paths.append(path)
                self.stream = path.open("wb")
                self.size = 0
            length = min(len(view), self.limit - self.size)
            self.stream.write(view[:length])
            self.size += length
            view = view[length:]
        return written

    def flush(self) -> None:
        if self.stream is not None:
            self.stream.flush()

    def close(self) -> None:
        if self.stream is not None:
            self.stream.close()


class ChunkReader(io.RawIOBase):
    def __init__(self, paths: list[Path]):
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
        super().close()


def pack(
    root: Path,
    provider: str,
    generation: str,
    output: Path,
    *,
    chunk_bytes: int = CHUNK_BYTES,
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
    writer = ChunkWriter(output, generation, chunk_bytes)
    count = total = 0
    try:
        with gzip.GzipFile(
            filename="", mode="wb", fileobj=writer, compresslevel=1, mtime=0
        ) as compressed:
            with tarfile.open(fileobj=compressed, mode="w|") as archive:
                for path in sorted(source.rglob("*")):
                    if path.is_symlink():
                        raise ValueError(f"Mirror contains a symbolic link: {path}")
                    if path.is_dir():
                        continue
                    metadata = path.stat()
                    if not stat.S_ISREG(metadata.st_mode):
                        raise ValueError(f"Mirror contains a non-regular file: {path}")
                    info = tarfile.TarInfo(path.relative_to(source).as_posix())
                    info.size = metadata.st_size
                    info.mode = 0o644
                    with path.open("rb") as stream:
                        archive.addfile(info, stream)
                    count += 1
                    total += info.size
    finally:
        writer.close()
    if not count:
        raise ValueError("Refusing to replace a snapshot with an empty mirror")
    manifest = {
        "version": 1,
        "provider_id": provider,
        "generation": generation,
        "format": "tar.gz",
        "created_at": datetime.now(UTC).isoformat(),
        "file_count": count,
        "unpacked_bytes": total,
        "chunks": [
            {"name": p.name, "size": p.stat().st_size, "sha256": digest(p)}
            for p in writer.paths
        ],
    }
    path = output / f"snapshot-{generation}.json"
    path.write_text(
        json.dumps(manifest, separators=(",", ":")) + "\n", encoding="utf-8"
    )
    return path


def load_manifest(path: Path, provider: str, generation: str) -> dict:
    checked_generation(generation)
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError("Invalid mirror snapshot manifest")
    if (
        payload.get("version") != 1
        or payload.get("provider_id") != provider
        or payload.get("generation") != generation
        or payload.get("format") != "tar.gz"
    ):
        raise ValueError(
            "Mirror snapshot manifest has a different owner, generation, or format"
        )
    for key in ("file_count", "unpacked_bytes"):
        if type(payload.get(key)) is not int or payload[key] < (
            1 if key == "file_count" else 0
        ):
            raise ValueError(f"Invalid mirror snapshot {key}")
    chunks = payload.get("chunks")
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


def unpack(root: Path, provider: str, manifest_path: Path, generation: str) -> dict:
    get_provider(provider)
    manifest = load_manifest(manifest_path, provider, generation)
    paths = [manifest_path.parent / chunk["name"] for chunk in manifest["chunks"]]
    for path, chunk in zip(paths, manifest["chunks"], strict=True):
        if (
            not path.is_file()
            or path.is_symlink()
            or path.stat().st_size != chunk["size"]
            or digest(path) != chunk["sha256"]
        ):
            raise ValueError(f"Mirror snapshot checksum/size mismatch: {path.name}")
    parent = root / "mirror/providers"
    parent.mkdir(parents=True, exist_ok=True)
    destination = parent / provider
    if destination.is_symlink() or (
        destination.exists() and any(destination.iterdir())
    ):
        raise ValueError(
            "Restore requires an empty provider mirror; existing files were preserved"
        )
    count = total = 0
    names = set()
    with tempfile.TemporaryDirectory(
        prefix=f".{provider}-restore-", dir=parent
    ) as temporary:
        stage = Path(temporary) / provider
        stage.mkdir()
        with io.BufferedReader(ChunkReader(paths)) as stream:
            with gzip.GzipFile(fileobj=stream, mode="rb") as compressed:
                with tarfile.open(fileobj=compressed, mode="r|") as archive:
                    for member in archive:
                        name = PurePosixPath(member.name)
                        if (
                            not member.isfile()
                            or name.is_absolute()
                            or ".." in name.parts
                            or not name.parts
                            or name.as_posix() != member.name
                            or "\\" in member.name
                            or member.name in names
                        ):
                            raise ValueError(
                                f"Unsafe mirror snapshot member: {member.name}"
                            )
                        count += 1
                        total += member.size
                        if (
                            count > manifest["file_count"]
                            or total > manifest["unpacked_bytes"]
                        ):
                            raise ValueError(
                                "Mirror snapshot exceeds its declared inventory"
                            )
                        names.add(member.name)
                        target = stage / name
                        target.parent.mkdir(parents=True, exist_ok=True)
                        incoming = archive.extractfile(member)
                        if incoming is None:
                            raise ValueError("Missing mirror snapshot payload")
                        with incoming, target.open("xb") as output:
                            shutil.copyfileobj(incoming, output, BLOCK_BYTES)
                # Read through the trailer so gzip CRC/truncation errors are surfaced.
                while compressed.read(BLOCK_BYTES):
                    pass
        if count != manifest["file_count"] or total != manifest["unpacked_bytes"]:
            raise ValueError("Mirror snapshot is missing declared files or bytes")
        if destination.exists():
            destination.rmdir()
        stage.rename(destination)
    # The root dedupe index is derived and may reference a different cache generation.
    (root / "mirror/.mirror-dedupe-index.json").unlink(missing_ok=True)
    return manifest


def gh(*args: str) -> str:
    return subprocess.check_output(["gh", *args], text=True, encoding="utf-8")


def release(
    repository: str, provider: str, *, allow_missing: bool = False
) -> dict | None:
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
        raise ValueError(
            "Mirror release has no valid committed snapshot pointer"
        ) from exc


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
    remote = release(
        repository, provider, allow_missing=allow_missing and generation is None
    )
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
    with tempfile.TemporaryDirectory(
        prefix="mirror-download-", dir=root / ".tmp"
    ) as temporary:
        directory = Path(temporary)
        path = download(repository, provider, f"snapshot-{generation}.json", directory)
        if digest(path) != manifest_sha256:
            raise ValueError("Mirror snapshot manifest checksum mismatch")
        manifest = load_manifest(path, provider, generation)
        for chunk in manifest["chunks"]:
            download(repository, provider, chunk["name"], directory)
        result = unpack(root, provider, path, generation)
    print(
        f"Restored {provider} snapshot {generation}: "
        f"{result['file_count']} files, {result['unpacked_bytes']} bytes"
    )
    return result


def save(root: Path, repository: str, provider: str, generation: str) -> dict:
    get_provider(provider)
    checked_generation(generation)
    root.joinpath(".tmp").mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(
        prefix="mirror-upload-", dir=root / ".tmp"
    ) as temporary:
        directory = Path(temporary)
        manifest_path = pack(root, provider, generation, directory)
        manifest = load_manifest(manifest_path, provider, generation)
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
        inventory = assets(repository, remote["id"])
        existing = {asset["name"]: asset for asset in inventory}
        # Deterministic archives let unchanged syncs reuse the exact durable
        # generation instead of uploading another copy of every payload.
        if remote.get("body") != "Snapshot upload in progress":
            previous = current_pointer(remote, provider)
            previous_dir = directory / "previous"
            previous_dir.mkdir()
            previous_path = download(
                repository,
                provider,
                f"snapshot-{previous['generation']}.json",
                previous_dir,
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
                    raise ValueError(
                        "Committed mirror snapshot is missing verified chunks"
                    )
                print(f"Reused unchanged {provider} snapshot {previous['generation']}")
                return previous
        paths = [directory / chunk["name"] for chunk in manifest["chunks"]] + [
            manifest_path
        ]
        if len(inventory) + sum(path.name not in existing for path in paths) > 1000:
            raise ValueError(
                "Mirror release asset limit reached; remove expired generations before retrying"
            )
        for path in paths:
            checksum = digest(path)
            if path.name in existing:
                if existing[path.name].get("digest") != f"sha256:{checksum}":
                    raise ValueError(
                        "A different snapshot already uses this generation; choose a new generation"
                    )
                continue
            gh(
                "release",
                "upload",
                f"mirror-{provider}",
                str(path),
                "--repo",
                repository,
            )
        uploaded = {asset["name"]: asset for asset in assets(repository, remote["id"])}
        if any(
            uploaded.get(path.name, {}).get("digest") != f"sha256:{digest(path)}"
            for path in paths
        ):
            raise ValueError("GitHub has not confirmed every uploaded snapshot digest")
        pointer = {
            "version": 1,
            "provider_id": provider,
            "generation": generation,
            "manifest_sha256": digest(manifest_path),
        }
        request = directory / "pointer-request.json"
        request.write_text(
            json.dumps({"body": json.dumps(pointer, separators=(",", ":"))}),
            encoding="utf-8",
        )
        # A single metadata PATCH advances the pointer only after all assets exist.
        gh(
            "api",
            "--method",
            "PATCH",
            f"repos/{repository}/releases/{remote['id']}",
            "--input",
            str(request),
        )
    print(
        f"Saved {provider} snapshot {generation}: "
        f"{manifest['file_count']} files, {manifest['unpacked_bytes']} bytes"
    )
    return pointer


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=("save", "restore"))
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
            generation = args.generation or uuid.uuid4().hex
            pointer = save(args.repo_root, args.repository, args.provider, generation)
            if output := os.environ.get("GITHUB_OUTPUT"):
                with open(output, "a", encoding="utf-8") as stream:
                    stream.write(
                        f"generation={pointer['generation']}\nmanifest_sha256={pointer['manifest_sha256']}\n"
                    )
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
