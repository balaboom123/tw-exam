import io
import tempfile
import unittest
import zipfile
from pathlib import Path

from app.models import ParsedPaper, SourceExamPage
from app.providers.base import DownloadedFile
from app.storage import MirrorStore
from app.sync import _validated_extension, sync_exam_pages


def zip_bytes(entries):
    stream = io.BytesIO()
    with zipfile.ZipFile(stream, "w") as archive:
        for name, data in entries.items():
            archive.writestr(name, data)
    return stream.getvalue()


class ZipFormatTests(unittest.TestCase):
    def test_unnamed_archives_keep_zip_extensions_across_material_roles(self):
        data = zip_bytes({"native/question.pdf": b"%PDF-1.7 paper"})
        for role in ("question", "answer", "question_alt", "question_answer", "corrected_answer"):
            with self.subTest(role=role):
                self.assertEqual(
                    _validated_extension(role, data, "application/octet-stream", "Get"), ".zip"
                )

    def test_unnamed_documents_use_native_parts_instead_of_role_order(self):
        formats = [
            ({"word/document.xml": b"<document/>"}, ".docx"),
            ({"xl/workbook.xml": b"<workbook/>"}, ".xlsx"),
            ({"mimetype": b"application/vnd.oasis.opendocument.spreadsheet",
              "content.xml": b"<document/>"}, ".ods"),
        ]
        for entries, extension in formats:
            with self.subTest(extension=extension):
                self.assertEqual(
                    _validated_extension("question_answer", zip_bytes(entries), "", "download"),
                    extension,
                )

    def test_document_markers_must_belong_to_the_outer_container(self):
        data = zip_bytes({"old/word/document.xml": b"<document/>",
                          "old/mimetype": b"application/vnd.oasis.opendocument.spreadsheet"})
        self.assertEqual(_validated_extension("question_answer", data, "", "download"), ".zip")

    def test_an_unnamed_ordinary_archive_cannot_be_a_transcript_document(self):
        data = zip_bytes({"question.pdf": b"%PDF-1.7 paper"})
        with self.assertRaisesRegex(RuntimeError, "container is not supported"):
            _validated_extension("listening_transcript", data, "", "download")

    def test_an_unnamed_spreadsheet_cannot_be_a_transcript_document(self):
        data = zip_bytes({"xl/workbook.xml": b"<workbook/>"})
        with self.assertRaisesRegex(RuntimeError, "container is not supported"):
            _validated_extension("listening_transcript", data, "", "download")

    def test_crc_validation_precedes_native_format_detection(self):
        payload = b"<document/>"
        data = bytearray(zip_bytes({"word/document.xml": payload}))
        data[data.index(payload)] ^= 1
        with self.assertRaisesRegex(RuntimeError, "Invalid ZIP container"):
            _validated_extension("question_answer", bytes(data), "", "Get")

    def test_a_fresh_unnamed_question_answer_archive_is_stored_as_zip(self):
        data = zip_bytes({"native/question-answer.pdf": b"%PDF-1.7 paper"})

        class Client:
            provider_id = "twc_recruit"

            def fetch_exam_page(self, exam_code, year_ad):
                return SourceExamPage(
                    source_exam_id=exam_code, year_ad=year_ad, year_roc=year_ad - 1911,
                    exam_name_raw="115年試題(解答).zip", provider_id=self.provider_id,
                    attachments=[], papers=[ParsedPaper(
                        category_raw="台水評價職位人員甄試", category_code="115",
                        subject_code="twc-recruit-115-bundle", subject_name_raw="115年試題(解答).zip",
                        files={"question_answer": "https://example.test/Get"},
                    )],
                )

            def download_file(self, url):
                return DownloadedFile(data, "application/octet-stream", "Get")

        with tempfile.TemporaryDirectory() as tmp_dir:
            root = Path(tmp_dir)
            pages, catalog, failures = sync_exam_pages(
                Client(), [("twc-recruit-115", 2026)], MirrorStore(root), [], ""
            )
            self.assertEqual(failures, [])
            key = pages[0].papers[0].mirror_files["question_answer"]["storage_key"]
            self.assertEqual(Path(key).suffix, ".zip")
            self.assertEqual((root / key).read_bytes(), data)
            self.assertEqual(catalog.papers[0].storage_key, key)
