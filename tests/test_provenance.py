import json
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import pytest

from app.paths import provider_paths
from app.provenance import add_frontend_provenance, load_sync_status, record_successful_sync
from scripts.validate_publication import validate_publication


def page(event):
    return SimpleNamespace(source_exam_id=event)


def test_successful_event_dates_advance_while_failed_and_untouched_dates_survive(tmp_path):
    provider = provider_paths(tmp_path, 'example')
    record_successful_sync(provider, [page('a'), page('b'), page('retained')], [], synced_at='2026-09-01T08:00:00+08:00')
    record_successful_sync(provider, [page('a'), page('b')], [page('b')], synced_at='2026-09-26T01:00:00Z')
    assert load_sync_status(provider) == {
        'a': '2026-09-26T01:00:00Z', 'b': '2026-09-01T00:00:00Z', 'retained': '2026-09-01T00:00:00Z',
    }
    before = provider.sync_status_path.read_bytes()
    record_successful_sync(provider, [page('b')], [page('b')], synced_at='2026-09-27T01:00:00Z')
    assert provider.sync_status_path.read_bytes() == before


def test_dates_are_not_invented_for_missing_receipts(tmp_path):
    assert load_sync_status(provider_paths(tmp_path, 'example')) == {}


@pytest.mark.parametrize('date', ['2026-09-26', '2026-09-26T00:00:00', 'invalid'])
def test_sync_receipts_reject_missing_timezone_or_invalid_timestamp(tmp_path, date):
    with pytest.raises(ValueError, match='timestamp'):
        record_successful_sync(provider_paths(tmp_path, 'example'), [page('a')], [], synced_at=date)


def test_sync_receipts_reject_a_different_provider(tmp_path):
    provider = provider_paths(tmp_path, 'example')
    provider.data_dir.mkdir(parents=True)
    provider.sync_status_path.write_text(json.dumps({'schema_version': 1, 'provider_id': 'other', 'events': {}}))
    with pytest.raises(ValueError, match='Invalid provider sync status'):
        load_sync_status(provider)


def test_site_projection_uses_only_events_that_contributed_to_each_bundle(tmp_path):
    inventory = tmp_path / 'catalog/source-inventory.json'
    inventory.parent.mkdir()
    inventory.write_text('{}')
    provider = provider_paths(tmp_path, 'example')
    record_successful_sync(provider, [page('old')], [], synced_at='2026-09-01T00:00:00Z')
    record_successful_sync(provider, [page('recent')], [], synced_at='2026-09-26T00:00:00Z')
    record_successful_sync(provider, [page('unrelated')], [], synced_at='2026-09-30T00:00:00Z')
    papers = [SimpleNamespace(provider_id='example', source_exam_id=event, bundle_id=bundle, canonical_id=bundle)
              for event, bundle in [('old', 'older'), ('recent', 'current'), ('missing', 'unknown')]]
    feed = [{'id': bundle} for bundle in ['older', 'current', 'unknown']]
    with patch('app.provenance.load_source_inventory', return_value={'providers': [{
        'provider_id': 'example', 'source_name': 'Official authority', 'official_source_urls': ['https://official.example/exams'],
    }]}):
        add_frontend_provenance(tmp_path, papers, feed)
    assert feed[0]['updated'] == '2026-09-01T00:00:00Z'
    assert feed[1]['updated'] == '2026-09-26T00:00:00Z'
    assert 'updated' not in feed[2]
    assert all(row['sources'] == [{'name': 'Official authority', 'url': 'https://official.example/exams'}] for row in feed)


@pytest.mark.repo_data
@pytest.mark.parametrize('field,value', [
    ('sources', [{'name': 'Unreviewed attribution', 'url': 'https://example.test/'}]),
    ('updated', '2099-01-01T00:00:00Z'),
])
def test_publication_rejects_provenance_that_has_no_authoritative_evidence(tmp_path, field, value):
    root = Path(__file__).resolve().parents[1]
    for directory in ('catalog', 'docs', 'schemas'):
        (tmp_path / directory).symlink_to(root / directory, target_is_directory=True)
    data = tmp_path / 'data'
    data.mkdir()
    (data / 'providers').symlink_to(root / 'data/providers', target_is_directory=True)
    site = data / 'sites/default'
    site.mkdir(parents=True)
    for filename in ('bundles.json', 'release-assets.json'):
        (site / filename).symlink_to(root / 'data/sites/default' / filename)
    feed = json.loads((root / 'data/sites/default/frontend-bundles.json').read_text())
    feed['bundles'][0][field] = value
    (site / 'frontend-bundles.json').write_text(json.dumps(feed))
    with pytest.raises(ValueError, match=f'{field} differs from reviewed sources'):
        validate_publication(tmp_path)
