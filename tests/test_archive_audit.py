import hashlib
import json
import zipfile
from dataclasses import replace

import pytest

from app.archive_audit import inspect_archive, portable_archive_name
from app.bundler import _resolve_arcnames, build_bundles
from app.models import NormalizedCatalog
from tests.test_bundler import make_paper


def test_shared_payload_preserves_source_records_and_multipart_recovery(tmp_path):
    mirror = tmp_path / 'mirror'
    mirror.mkdir()
    payload = b'%PDF-1.7 same question' + b'x' * 5000
    (mirror / 'question.pdf').write_bytes(payload)
    original = make_paper(canonical_id='ast', canonical_name='分科測驗｜物理',
                          year_roc=115, source_exam_id='listing', subject_code='physics-01',
                          subject_name_raw='物理 試題內容', storage_key='question.pdf')
    original.checksum = hashlib.sha256(payload).hexdigest()
    notice = replace(original, source_exam_id='notice', subject_code='physics',
                     subject_name_raw='物理')
    previous = replace(original, year_roc=114, source_exam_id='previous')
    answer = replace(original, file_type='answer', subject_code='physics-answer')
    catalog = NormalizedCatalog([original, notice, previous, answer], [])
    kwargs = dict(bundle_dir=tmp_path / 'bundles', mirror_dir=mirror,
                  normalized=catalog, bundle_base_url='')
    first = build_bundles(**kwargs)
    asset = first.bundles[0]
    assert asset.file_count == 3
    manifest = inspect_archive(tmp_path / asset.storage_key, verify_content=True)
    assert len(manifest['papers']) == 4
    assert len({p['bundle_entry'] for p in manifest['papers']}) == 3
    # All source references survive reuse even after local mirrors disappear.
    (mirror / 'question.pdf').unlink()
    reused = build_bundles(**kwargs)
    assert reused.bundles[0].checksum == asset.checksum
    split = build_bundles(**kwargs, max_bundle_bytes=10000)
    assert not split.failures
    assert len(split.bundles) == 3
    manifests = [inspect_archive(tmp_path / b.storage_key, verify_content=True)
                 for b in split.bundles]
    assert sum(len(m['papers']) for m in manifests) == 4
    assert sum(m['file_count'] for m in manifests) == 3


def test_revised_payload_is_not_deduplicated(tmp_path):
    paper = make_paper(canonical_id='ast', canonical_name='AST', year_roc=115,
                       source_exam_id='exam', subject_code='physics',
                       storage_key='q.pdf', subject_name_raw='物理')
    paper.checksum = 'a' * 64
    corrected = replace(paper, source_exam_id='correction', checksum='b' * 64)
    assert len(set(_resolve_arcnames([paper, corrected]))) == 2


def test_paths_are_portable_even_with_unicode_and_case_collisions():
    paper = make_paper(canonical_id='ast', canonical_name='AST', year_roc=115,
                       source_exam_id='exam', subject_code='a', storage_key='q.pdf',
                       subject_name_raw='很長的科目名稱' * 20)
    names = _resolve_arcnames([paper, replace(paper, subject_code='A')])
    assert len({name.casefold() for name in names}) == 2
    assert all(portable_archive_name(name) for name in names)


@pytest.mark.parametrize('name', ['../a.pdf', '/a.pdf', '115/CON.foo.pdf',
                                  '115/a\\b.pdf', '115/a.pdf ', '115/' + '中' * 86])
def test_reject_nonportable_paths(name):
    assert not portable_archive_name(name)


def test_inspector_rejects_unmanifested_and_corrupt_content(tmp_path):
    path = tmp_path / 'test.zip'
    manifest = {'file_count': 1, 'papers': [{
        'source_exam_id': 'exam', 'category_code': 'c', 'subject_code': 's',
        'file_type': 'question', 'checksum': 'a' * 64, 'bundle_entry': '115/q.pdf',
    }]}
    with zipfile.ZipFile(path, 'w') as archive:
        archive.writestr('bundle.json', json.dumps(manifest))
        archive.writestr('115/q.pdf', b'bad payload')
    with pytest.raises(ValueError, match='payload checksum'):
        inspect_archive(path, verify_content=True)
    with zipfile.ZipFile(path, 'a') as archive:
        archive.writestr('115/extra.pdf', b'extra')
    with pytest.raises(ValueError, match='differ from manifest'):
        inspect_archive(path, verify_content=False)


def test_cleanup_removes_only_verified_redundancy(tmp_path, monkeypatch):
    import shutil
    from types import SimpleNamespace
    from app.archive_audit import prune_redundant_archives

    directory = tmp_path / 'bundles/sites/default'
    directory.mkdir(parents=True)
    payload = b'%PDF retained'
    manifest = {'file_count': 1, 'papers': [{
        'source_exam_id': 'exam', 'category_code': 'c', 'subject_code': 's',
        'file_type': 'question', 'checksum': hashlib.sha256(payload).hexdigest(),
        'bundle_entry': '115/q.pdf',
    }]}
    def write(name, event, data):
        manifest['papers'][0]['source_exam_id'] = event
        with zipfile.ZipFile(directory / name, 'w') as archive:
            archive.writestr('bundle.json', json.dumps(manifest))
            archive.writestr('115/q.pdf', data)
    write('active.zip', 'exam', payload)
    shutil.copyfile(directory / 'active.zip', directory / 'duplicate.zip')
    write('unique-history.zip', 'old-exam', payload)
    write('corrupt.zip', 'exam', b'corrupt')
    monkeypatch.setattr('app.archive_audit.load_site_bundles',
                        lambda _: [SimpleNamespace(asset_name='active.zip')])
    report = {'site_id': 'default', 'verified_content': False, 'errors': [],
              'unreferenced_archives': ['duplicate.zip', 'unique-history.zip', 'corrupt.zip']}
    with pytest.raises(ValueError, match='successful full content audit'):
        prune_redundant_archives(tmp_path, site_id='default', report=report)
    assert (directory / 'duplicate.zip').exists()
    report['verified_content'] = True
    prune_redundant_archives(tmp_path, site_id='default', report=report)
    assert report['removed_redundant_archives'] == ['duplicate.zip']
    assert (directory / 'active.zip').exists()
    assert (directory / 'unique-history.zip').exists()
    assert (directory / 'corrupt.zip').exists()


def test_shared_payload_can_be_supplied_by_later_source_reference(tmp_path):
    mirror = tmp_path / 'mirror'
    mirror.mkdir()
    payload = b'%PDF identical'
    (mirror / 'available.pdf').write_bytes(payload)
    missing = make_paper(canonical_id='ast', canonical_name='AST', year_roc=115,
                         source_exam_id='a', subject_code='physics',
                         subject_name_raw='物理', storage_key='missing.pdf')
    missing.checksum = hashlib.sha256(payload).hexdigest()
    available = replace(missing, source_exam_id='b', storage_key='available.pdf')
    result = build_bundles(bundle_dir=tmp_path / 'bundles', mirror_dir=mirror,
                          normalized=NormalizedCatalog([missing, available], []),
                          bundle_base_url='', published_checksums={})
    assert not result.failures
    assert result.bundles[0].file_count == 1
    manifest = inspect_archive(tmp_path / result.bundles[0].storage_key, verify_content=True)
    assert len(manifest['papers']) == 2


def test_display_label_truncation_does_not_merge_different_subjects():
    paper = make_paper(canonical_id='exam', canonical_name='Exam', year_roc=115,
                       source_exam_id='exam', subject_code='a', storage_key='q.pdf',
                       subject_name_raw='科' * 130 + '甲')
    paper.checksum = 'a' * 64
    other = replace(paper, subject_code='b', subject_name_raw='科' * 130 + '乙')
    assert len(set(_resolve_arcnames([paper, other]))) == 2


def test_reused_codes_keep_each_source_url_year_and_subject(tmp_path):
    mirror = tmp_path / 'mirror'
    mirror.mkdir()
    payload = b'%PDF same bytes'
    (mirror / 'paper.pdf').write_bytes(payload)
    paper = make_paper(canonical_id='tcte', canonical_name='統測', year_roc=115,
                       source_exam_id='event', subject_code='professional-2',
                       storage_key='paper.pdf', subject_name_raw='51電機電子群 專業科目(二)-電機類')
    paper.checksum = hashlib.sha256(payload).hexdigest()
    alias = replace(paper, download_url_source='https://source.example/alias.pdf')
    other_subject = replace(paper, download_url_source='https://source.example/electronics.pdf',
                            subject_name_raw='51電機電子群 專業科目(二)-電子類')
    other_year = replace(paper, year_roc=114)
    result = build_bundles(tmp_path / 'bundles', mirror,
                          NormalizedCatalog([paper, alias, other_subject, other_year], []), '')
    assert not result.failures
    assert result.bundles[0].file_count == 3
    manifest = inspect_archive(tmp_path / result.bundles[0].storage_key, verify_content=True)
    assert manifest['manifest_version'] == 3
    assert len(manifest['papers']) == 4
    assert len({(p['year_roc'], p['download_url_source']) for p in manifest['papers']}) == 4


def test_isolation_preserves_suspect_bytes_and_refuses_overwrite(tmp_path):
    from app.archive_audit import isolate_unreferenced_archives
    directory = tmp_path / 'bundles/sites/default'
    directory.mkdir(parents=True)
    source = directory / 'obsolete.zip'
    source.write_bytes(b'possibly damaged archive retained for recovery')
    report = {'site_id': 'default', 'verified_content': True, 'errors': [],
              'unreferenced_archives': ['obsolete.zip']}
    isolate_unreferenced_archives(tmp_path, site_id='default', report=report)
    assert not source.exists()
    recovered = directory / 'recovery/obsolete.zip'
    assert recovered.read_bytes() == b'possibly damaged archive retained for recovery'
    assert report['unreferenced_archives'] == []
    source.write_bytes(b'different version')
    report['unreferenced_archives'] = ['obsolete.zip']
    with pytest.raises(ValueError, match='refusing to overwrite'):
        isolate_unreferenced_archives(tmp_path, site_id='default', report=report)
    assert source.read_bytes() == b'different version'
    assert recovered.read_bytes() == b'possibly damaged archive retained for recovery'
