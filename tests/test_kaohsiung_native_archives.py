import hashlib
import io
import stat
import unittest
import warnings
import zipfile
from dataclasses import replace

from app.providers.base import UnreviewedSourceFile
from app.providers.teacher_recruit_kaohsiung.archives import (
    NativeZip,
    NativeZipMember,
    attachment_notice,
    validate_zip_contract,
    verified_zip_members,
)


def zip_bytes(files: list[tuple[str | zipfile.ZipInfo, bytes]]) -> bytes:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        for name, data in files:
            archive.writestr(name, data)
    return buffer.getvalue()


def contract(data: bytes, files: list[tuple[str, bytes]]) -> NativeZip:
    return NativeZip(
        "initial-questions",
        "https://bulletin.kh.edu.tw/attachment.php?b_sn=B115002760&a_sn=126646",
        len(data),
        hashlib.sha256(data).hexdigest(),
        tuple(
            NativeZipMember(name, len(payload), hashlib.sha256(payload).hexdigest())
            for name, payload in files
        ),
    )


class KaohsiungNativeArchiveTests(unittest.TestCase):
    def setUp(self) -> None:
        self.files = [("普通科目.pdf", b"%PDF-1.4 common"), ("視覺藝術.pdf", b"%PDF-1.4 art")]
        self.data = zip_bytes(self.files)
        self.archive = contract(self.data, self.files)

    def test_exact_native_paths_and_bytes_survive_in_memory_import(self) -> None:
        self.assertEqual(verified_zip_members(self.archive, self.data), dict(self.files))
        locator = self.archive.locator(self.archive.members[0])
        self.assertIn("#archive-sha256=" + self.archive.checksum, locator)
        self.assertIn("&member=%E6%99%AE", locator)
        with self.assertRaisesRegex(ValueError, "Unreviewed"):
            self.archive.locator(NativeZipMember("unknown.pdf", 1, "0" * 64))

    def test_changed_parent_or_member_bytes_are_rejected(self) -> None:
        with self.assertRaisesRegex(UnreviewedSourceFile, "ZIP changed"):
            verified_zip_members(self.archive, self.data + b"changed")
        changed = zip_bytes([(self.files[0][0], b"%PDF-1.4 changed"), self.files[1]])
        moved_parent = replace(
            self.archive, size=len(changed), checksum=hashlib.sha256(changed).hexdigest()
        )
        with self.assertRaisesRegex(ValueError, "size|reviewed parent"):
            verified_zip_members(moved_parent, changed)

    def test_unexpected_missing_and_duplicate_members_fail_before_any_import(self) -> None:
        for files in [
            self.files[:1],
            self.files + [("extra.pdf", b"%PDF-1.4 extra")],
            self.files + self.files[:1],
        ]:
            with self.subTest(names=[name for name, _ in files]):
                with warnings.catch_warnings():
                    warnings.simplefilter("ignore", UserWarning)
                    data = zip_bytes(files)
                changed = replace(
                    self.archive, size=len(data), checksum=hashlib.sha256(data).hexdigest()
                )
                with self.assertRaisesRegex(UnreviewedSourceFile, "population changed"):
                    verified_zip_members(changed, data)

    def test_non_pdf_payload_is_rejected_even_if_reviewed_checksum_matches(self) -> None:
        files = [("not-a-pdf.pdf", b"<html>source error</html>")]
        data = zip_bytes(files)
        with self.assertRaisesRegex(ValueError, "native PDF"):
            verified_zip_members(contract(data, files), data)

    def test_symlink_metadata_is_rejected(self) -> None:
        link = zipfile.ZipInfo("ordinary.pdf")
        link.create_system = 3
        link.external_attr = (stat.S_IFLNK | 0o777) << 16
        data = zip_bytes([(link, b"%PDF-1.4 target")])
        with self.assertRaisesRegex(ValueError, "Unsafe"):
            verified_zip_members(contract(data, [(link.filename, b"%PDF-1.4 target")]), data)

    def test_unsafe_and_portably_colliding_reviewed_paths_are_rejected(self) -> None:
        for paths in [
            ("../escape.pdf",),
            ("/absolute.pdf",),
            ("bad\\name.pdf",),
            ("NUL.pdf",),
            ("Paper.pdf", "paper.pdf"),
            ("e\u0301.pdf", "é.pdf"),
        ]:
            with self.subTest(paths=paths):
                members = tuple(NativeZipMember(path, 1, "0" * 64) for path in paths)
                with self.assertRaisesRegex(ValueError, "Unsafe|competing"):
                    validate_zip_contract(replace(self.archive, members=members))

    def test_attachment_identity_rejects_competing_query_keys_and_fragments(self) -> None:
        self.assertEqual(attachment_notice(self.archive.source_url), "B115002760")
        for url in [
            self.archive.source_url + "&a_sn=2",
            self.archive.source_url + "#member=x",
            self.archive.source_url.replace("https://", "http://"),
            self.archive.source_url.replace("bulletin.kh.edu.tw", "example.test"),
        ]:
            with self.subTest(url=url):
                with self.assertRaisesRegex(ValueError, "attachment URL"):
                    attachment_notice(url)
