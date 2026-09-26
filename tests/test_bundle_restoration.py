from argparse import Namespace
from dataclasses import replace
import hashlib
from pathlib import Path
from unittest.mock import Mock

from app.bundler import build_bundles
from app.cli import _restore_new_public_bundle_files
from app.providers.base import DownloadedFile
from app.models import BundleAsset, NormalizedCatalog, NormalizedPaper
from app.storage import MirrorStore
from app.sync import restore_catalog_files


PAYLOAD = b'%PDF-1.7 retained paper'


def paper(year=114, bundle_id='new-bundle'):
    return NormalizedPaper(
        provider_id='moex', canonical_id=bundle_id, canonical_name='Nurse',
        bundle_id=bundle_id, year_roc=year, exam_name_raw='Exam', category_raw='Nurse',
        subject_name_raw='Subject', paper_code='101-0101-question', file_type='question',
        category_code='101', subject_code='0101', source_exam_id=f'{year}140',
        download_url_source=f'https://example.test/{year}/{bundle_id}.pdf',
        storage_key=f'providers/moex/{year}/{bundle_id}/question.pdf',
        checksum=hashlib.sha256(PAYLOAD).hexdigest(),
    )


def client():
    source = Mock()
    source.download_file.return_value = DownloadedFile(
        data=PAYLOAD, content_type='application/pdf', file_name='question.pdf',
    )
    return source


def test_cold_mirror_restores_older_year_when_bundle_becomes_public(tmp_path):
    old, new = paper(), paper(115)
    store = MirrorStore(tmp_path / 'mirror')
    store.write_bytes(new.storage_key, PAYLOAD)
    source = client()
    catalog = NormalizedCatalog(papers=[old, new], review_queue=[])
    args = Namespace(site_id='default', mirror_dir=store.root)
    assert _restore_new_public_bundle_files(args, source, catalog, [], {'new-bundle'}) == []
    source.download_file.assert_called_once_with(old.download_url_source)
    result = build_bundles(tmp_path / 'bundles', store.root, catalog, '', min_years=2)
    assert result.failures == []
    assert len(result.bundles) == 1
    assert result.bundles[0].file_count == 2
    assert result.bundles[0].years == [115, 114]


def test_restoration_is_scoped_to_newly_public_affected_bundles(tmp_path):
    papers = [paper(114, key) for key in ('published', 'private', 'unaffected')]
    papers += [paper(115, key) for key in ('published', 'unaffected')]
    existing = BundleAsset(canonical_id='published', canonical_name='Nurse',
                           bundle_id='published', years=[114, 115], file_count=2,
                           storage_key='published.zip', asset_name='published.zip')
    source = client()
    args = Namespace(site_id='default', mirror_dir=tmp_path)
    assert _restore_new_public_bundle_files(
        args, source, NormalizedCatalog(papers=papers, review_queue=[]),
        [existing], {'published', 'private'},
    ) == []
    source.download_file.assert_not_called()


def test_restoration_rejects_changed_bytes_without_writing(tmp_path):
    retained = replace(paper(), checksum='different-checksum')
    failures = restore_catalog_files(client(), MirrorStore(tmp_path),
                                     NormalizedCatalog(papers=[retained], review_queue=[]))
    assert len(failures) == 1
    assert 'checksum changed' in failures[0].message
    assert not (tmp_path / retained.storage_key).exists()


def test_restoration_rejects_html_and_records_source_context(tmp_path):
    source = client()
    source.download_file.return_value = DownloadedFile(
        data=b'<html>unavailable</html>', content_type='text/html', file_name='question.pdf',
    )
    retained = paper()
    failures = restore_catalog_files(source, MirrorStore(tmp_path),
                                     NormalizedCatalog(papers=[retained], review_queue=[]))
    assert len(failures) == 1
    assert 'HTML placeholder' in failures[0].message
    assert failures[0].source_exam_id == '114140'
    assert failures[0].url == retained.download_url_source
    assert not (tmp_path / retained.storage_key).exists()


def test_targeted_sync_restoration_failure_does_not_write_state_or_plan(tmp_path):
    import json
    from unittest.mock import patch
    from app.cli import build_parser, run_sync_targeted
    from app.models import SourceExamPage, SyncFailure

    probe = tmp_path / 'probe.json'
    probe.write_text(json.dumps({'should_sync': True, 'provider_id': 'moex',
                                 'changed_exam_codes': ['115140'],
                                 'exam_years': {'115140': 2026}}))
    plan = tmp_path / 'plan.json'
    args = build_parser().parse_args([
        'sync-targeted', '--probe', str(probe), '--data-dir', str(tmp_path / 'data'),
        '--mirror-dir', str(tmp_path / 'mirror'), '--download-affected-bundles',
        '--publish-plan-output', str(plan),
    ])
    source = client()
    source.provider_id = 'moex'
    page = SourceExamPage(source_exam_id='115140', year_ad=2026, year_roc=115,
                          exam_name_raw='Exam', attachments=[], papers=[], provider_id='moex')
    catalog = NormalizedCatalog(papers=[paper(115)], review_queue=[])
    failure = SyncFailure(stage='download', source_exam_id='114140', year_roc=114,
                          paper_code='101-0101-question', file_type='question',
                          url='https://example.test/old.pdf', message='unavailable')
    with (
        patch('app.cli.sync_exam_pages', return_value=([page], catalog, [])),
        patch('app.cli.load_provider_state', return_value=([], catalog, [])),
        patch('app.cli.load_alias_rules', return_value=[]),
        patch('app.cli._download_affected_bundles'),
        patch('app.cli._restore_new_public_bundle_files', return_value=[failure]) as restore,
        patch('app.cli.write_provider_state') as write,
    ):
        assert run_sync_targeted(args, client=source) == 1
        restore.assert_called_once()
        write.assert_not_called()
        assert not plan.exists()
