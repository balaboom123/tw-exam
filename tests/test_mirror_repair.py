import hashlib
from dataclasses import replace
from types import SimpleNamespace

import pytest

from app.mirror_repair import colliding_mirror_records, repair_mirror_collisions
from app.models import NormalizedCatalog, ParsedPaper, SourceExamPage
from app.paths import provider_paths
from app.providers.base import DownloadedFile
from app.publisher import write_provider_state
from app.state import load_provider_state
from tests.test_bundler import make_paper


@pytest.mark.parametrize('fail_second', [False, True])
def test_repair_refetches_each_url_and_preserves_state_on_failure(tmp_path, monkeypatch, fail_second):
    paper = make_paper(canonical_id='nurse', canonical_name='Nurse', year_roc=115,
                       source_exam_id='event', subject_code='s',
                       storage_key='providers/moex/115/event/101/s/question.pdf')
    paper.provider_id = 'moex'
    paper.checksum = hashlib.sha256(b'%PDF old').hexdigest()
    paper.download_url_source = 'https://example.test/a.pdf'
    other = replace(paper, download_url_source='https://example.test/b.pdf')
    pages = [SourceExamPage(source_exam_id='event', year_ad=2026, year_roc=115,
                           exam_name_raw='Exam', provider_id='moex', attachments=[], papers=[
        ParsedPaper(category_raw='Nurse', category_code='101', subject_code='s',
                    subject_name_raw='Subject', files={'question': p.download_url_source})
        for p in (paper, other)
    ])]
    paths = provider_paths(tmp_path, 'moex')
    write_provider_state(paths, pages, NormalizedCatalog([paper, other], []), [], [], None)
    before = (paths.papers_dir / '2026.json').read_bytes()

    class Client:
        provider_id = 'moex'
        max_concurrency = 1
        def download_file(self, url):
            if fail_second and url.endswith('b.pdf'):
                raise ValueError('source unavailable')
            return DownloadedFile(data=b'%PDF-1.7 ' + url.encode(),
                                  content_type='application/pdf', file_name='paper.pdf')

    monkeypatch.setattr('app.mirror_repair.get_site_config',
                        lambda _: SimpleNamespace(provider_ids=('moex',)))
    monkeypatch.setattr('app.mirror_repair.get_provider', lambda _: Client())
    report = repair_mirror_collisions(tmp_path, site_id='default', apply=True)
    raw, catalog, _ = load_provider_state(paths)
    if fail_second:
        assert report['errors']
        assert (paths.papers_dir / '2026.json').read_bytes() == before
        assert len(colliding_mirror_records(catalog.papers)) == 1
    else:
        assert not report['errors']
        assert not colliding_mirror_records(catalog.papers)
        assert len({p.checksum for p in catalog.papers}) == 2
        for actual, source in zip(catalog.papers, raw[0].papers):
            assert source.mirror_files['question']['storage_key'] == actual.storage_key
            assert (tmp_path / 'mirror' / actual.storage_key).read_bytes().endswith(actual.download_url_source.encode())


def test_strict_catalog_gate_rejects_mirror_locator_collisions():
    from app.audit import audit_exit_code
    assert audit_exit_code({'mirror_locator_collisions': [{'storage_key': 'shared.pdf'}]}, strict=True) == 1
