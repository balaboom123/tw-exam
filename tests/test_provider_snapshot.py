import importlib.util
import json
import subprocess
import shutil
from pathlib import Path

import pytest

from app.models import NormalizedPaper
from app.paths import provider_paths
from app.source_revisions import retain_superseded_sources, revision_journal_path

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location(
    'apply_provider_snapshot', ROOT / '.github/scripts/apply_provider_snapshot.py',
)
snapshot_module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(snapshot_module)


def git(root: Path, *args: str) -> str:
    return subprocess.check_output(['git', *args], cwd=root, text=True).strip()


@pytest.fixture
def snapshot_repository(tmp_path):
    root = tmp_path / 'repo'
    root.mkdir()
    git(root, 'init', '-q')
    git(root, 'config', 'user.email', 'snapshot-test@example.test')
    git(root, 'config', 'user.name', 'Snapshot test')
    provider = root / 'data/providers/ceec_ast'
    provider.mkdir(parents=True)
    (provider / 'index.json').write_text('{"papers": ["original"]}')
    (provider / 'obsolete.json').write_text('old generated file')
    site = root / 'data/sites/default/bundles.json'
    site.parent.mkdir(parents=True)
    site.write_text('original site')
    git(root, 'add', 'data')
    git(root, 'commit', '-qm', 'baseline')
    base = git(root, 'rev-parse', 'HEAD')
    artifact = tmp_path / 'artifact'
    incoming = artifact / 'data/providers/ceec_ast'
    incoming.mkdir(parents=True)
    (incoming / 'index.json').write_text('{"papers": ["refreshed"]}')
    plan = artifact / '.tmp/site-publish-plan.json'
    plan.parent.mkdir()
    plan.write_text(json.dumps({'site_id': 'default', 'affected_canonical_ids': ['ceec-ast']}))
    return root, artifact, base


def test_snapshot_preserves_later_updates_to_other_providers_and_site(snapshot_repository):
    root, artifact, base = snapshot_repository
    other = root / 'data/providers/ceec_gsat/index.json'
    other.parent.mkdir()
    other.write_text('new gsat state')
    site = root / 'data/sites/default/bundles.json'
    site.write_text('site after gsat publication')
    git(root, 'add', 'data')
    git(root, 'commit', '-qm', 'other provider publication')

    snapshot_module.apply_snapshot(root, artifact, 'ceec_ast', base, require_publish_plan=True)

    assert json.loads((root / 'data/providers/ceec_ast/index.json').read_text()) == {'papers': ['refreshed']}
    assert not (root / 'data/providers/ceec_ast/obsolete.json').exists()
    assert other.read_text() == 'new gsat state'
    assert site.read_text() == 'site after gsat publication'
    assert json.loads((root / '.tmp/site-publish-plan.json').read_text())['affected_canonical_ids'] == ['ceec-ast']


def test_snapshot_refuses_to_overwrite_a_newer_sync_of_its_provider(snapshot_repository):
    root, artifact, base = snapshot_repository
    index = root / 'data/providers/ceec_ast/index.json'
    index.write_text('{"papers": ["newer sync"]}')
    git(root, 'add', 'data')
    git(root, 'commit', '-qm', 'newer AST sync')

    with pytest.raises(ValueError, match='changed after this sync started'):
        snapshot_module.apply_snapshot(root, artifact, 'ceec_ast', base, require_publish_plan=True)

    assert json.loads(index.read_text()) == {'papers': ['newer sync']}
    assert (root / 'data/providers/ceec_ast/obsolete.json').exists()
    assert not (root / '.tmp/site-publish-plan.json').exists()


def test_missing_publication_plan_fails_before_mutating_provider_state(snapshot_repository):
    root, artifact, base = snapshot_repository
    (artifact / '.tmp/site-publish-plan.json').unlink()
    with pytest.raises(ValueError, match='no site publication plan'):
        snapshot_module.apply_snapshot(root, artifact, 'ceec_ast', base, require_publish_plan=True)
    assert json.loads((root / 'data/providers/ceec_ast/index.json').read_text()) == {'papers': ['original']}


def test_provider_only_snapshot_removes_a_stale_publication_plan(snapshot_repository):
    root, artifact, base = snapshot_repository
    (artifact / '.tmp/site-publish-plan.json').unlink()
    stale = root / '.tmp/site-publish-plan.json'
    stale.parent.mkdir()
    stale.write_text('stale plan from another sync')
    snapshot_module.apply_snapshot(root, artifact, 'ceec_ast', base)
    assert not stale.exists()


@pytest.mark.parametrize('disposition', ['deleted', 'rewritten', 'preserved'])
def test_snapshot_preserves_earlier_revision_journal(snapshot_repository, disposition):
    root, artifact, _base = snapshot_repository
    provider = provider_paths(root, 'ceec_ast')
    old = NormalizedPaper(
        provider_id='ceec_ast', canonical_id='ast', canonical_name='分科測驗',
        year_roc=115, exam_name_raw='分科測驗', category_raw='物理', subject_name_raw='物理',
        paper_code='physics-question', file_type='question',
        download_url_source='https://example.test/physics.pdf', source_exam_id='115-ast',
    )
    retain_superseded_sources(provider, [], [old], [], [])
    journal = revision_journal_path(provider)
    original = journal.read_bytes()
    git(root, 'add', 'data')
    git(root, 'commit', '-qm', 'retain earlier source reference')
    base = git(root, 'rev-parse', 'HEAD')
    if disposition != 'deleted':
        target = revision_journal_path(provider_paths(artifact, 'ceec_ast'))
        shutil.copyfile(journal, target)
        if disposition == 'rewritten':
            payload = json.loads(target.read_text())
            payload['revisions'][0]['retained_at'] = '2026-10-07T00:00:00+00:00'
            target.write_text(json.dumps(payload))
    if disposition == 'preserved':
        snapshot_module.apply_snapshot(root, artifact, 'ceec_ast', base)
    else:
        with pytest.raises(ValueError, match='would lose source revision'):
            snapshot_module.apply_snapshot(root, artifact, 'ceec_ast', base)
        assert json.loads((provider.data_dir / 'index.json').read_text()) == {'papers': ['original']}
    assert journal.read_bytes() == original
