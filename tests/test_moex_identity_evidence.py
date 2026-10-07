import copy
import json
from pathlib import Path

import pytest

from app.classification import classify_normalized_paper, classify_paper
from app.models import NormalizedCatalog, ParsedPaper
from app.moex_identity_evidence import (
    moex_evidence_path, read_moex_category_identities, validate_moex_category_evidence,
)
from app.normalizer import normalize_papers, renormalize_catalog


def document():
    return {
        'schema_version': 1, 'catalog_version': 'exam-identity-v2', 'provider_id': 'moex',
        'owner': 'catalog maintainers', 'facts': [{
            'id': 'moex-100110-category-201', 'source_exam_id': '100110', 'year_ad': 2011,
            'category_code': '201', 'category_raw': '二等考試_刑事警察人員數位鑑識組',
            'exam_name_raw': '100年一般警察人員及警察人員考試',
            'series_id': 'special-general-police', 'level_id': 'grade-2',
            'reviewed_at': '2026-10-07', 'reason': 'Exact native context has an official grade header.',
            'evidence': [{
                'source_url': 'https://wwwq.moex.gov.tw/exam/wHandExamQandA_File.ashx?t=Q&code=100110&c=201&s=0101&q=1',
                'subject_code': '0101', 'checksum': 'a' * 64,
                'observation': 'Header identifies 二等一般警察人員考試.',
            }],
        }],
    }


def write_catalog(root, payload):
    path = moex_evidence_path(root)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False))
    return path


@pytest.mark.parametrize('field,value', [
    ('schema_version', 2), ('provider_id', 'other'), ('catalog_version', 'unsupported'),
])
def test_reader_rejects_unsupported_version_and_ownership(tmp_path, field, value):
    payload = document()
    payload[field] = value
    with pytest.raises(ValueError, match='unsupported'):
        read_moex_category_identities(write_catalog(tmp_path, payload))


def test_reader_rejects_conflicting_native_category_context(tmp_path):
    payload = document()
    conflict = copy.deepcopy(payload['facts'][0])
    conflict.update(id='conflicting-fact', series_id='special-police', category_raw='other wording')
    payload['facts'].append(conflict)
    with pytest.raises(ValueError, match='duplicate category context'):
        read_moex_category_identities(write_catalog(tmp_path, payload))


@pytest.mark.parametrize('change', ['missing', 'wrong_category', 'wrong_role', 'bad_digest'])
def test_reader_requires_checksum_bound_official_question_evidence(tmp_path, change):
    payload = document()
    fact = payload['facts'][0]
    anchor = fact['evidence'][0]
    if change == 'missing':
        fact['evidence'] = []
    elif change == 'wrong_category':
        anchor['source_url'] = anchor['source_url'].replace('c=201', 'c=501')
    elif change == 'wrong_role':
        anchor['source_url'] = anchor['source_url'].replace('t=Q', 't=S')
    else:
        anchor['checksum'] = 'unverified'
    with pytest.raises(ValueError, match='evidence'):
        read_moex_category_identities(write_catalog(tmp_path, payload))


@pytest.mark.parametrize('change', ['missing', 'context', 'revision', 'wrong_role'])
def test_evidence_gate_rejects_lost_changed_or_wrong_role_anchors(tmp_path, change):
    payload = document()
    write_catalog(tmp_path, payload)
    fact = payload['facts'][0]
    anchor = fact['evidence'][0]
    paper = {
        'source_exam_id': fact['source_exam_id'], 'year_roc': 100,
        'category_code': fact['category_code'], 'category_raw': fact['category_raw'],
        'exam_name_raw': fact['exam_name_raw'], 'file_type': 'question',
        'subject_code': anchor['subject_code'], 'download_url_source': anchor['source_url'],
        'checksum': anchor['checksum'],
    }
    directory = tmp_path / 'data/providers/moex/papers'
    directory.mkdir(parents=True)
    path = directory / '100.json'
    path.write_text(json.dumps([paper]))
    assert validate_moex_category_evidence(tmp_path) == 1
    if change == 'context':
        paper['category_raw'] += ' changed'
    elif change == 'revision':
        paper['checksum'] = 'b' * 64
    elif change == 'wrong_role':
        paper['file_type'] = 'answer'
    path.write_text(json.dumps([] if change == 'missing' else [paper]))
    with pytest.raises(ValueError, match='missing or changed|checksum/locator'):
        validate_moex_category_evidence(tmp_path)


def test_ingestion_migration_and_cached_classification_keep_native_category_code():
    title = '100年公務人員特種考試一般警察人員考試、100年公務人員特種考試警察人員考試、100年特種考試交通事業鐵路人員考試'
    category = '二等考試_刑事警察人員數位鑑識組'
    arguments = dict(provider_id='moex', source_exam_id='100110', year_ad=2011,
        category_raw=category, exam_name_raw=title, canonical_id='digital-forensics',
        canonical_name='刑事警察人員數位鑑識組', category_code='201')
    identity = classify_paper(**arguments)
    assert identity.exam_series_id == 'special-general-police'
    assert identity.level_id == 'grade-2'
    assert identity.confidence == 'high'
    assert 'moex-100110-category-201' in identity.reason
    for field, value in [('category_code', '501'), ('category_code', ''), ('year_ad', 2012),
                         ('category_raw', category + ' '), ('exam_name_raw', title + ' changed')]:
        changed = classify_paper(**dict(arguments, **{field: value}))
        assert changed.confidence == 'review'
        assert changed.exam_series_id == 'moex-unknown'
    parsed = ParsedPaper(category_raw=category, category_code='201', subject_code='0101',
        subject_name_raw='國文', files={'question': 'https://official.example/question.pdf'})
    normalized = normalize_papers(source_exam_id='100110', year_ad=2011, exam_name_raw=title,
        papers=[parsed], alias_rules=[], mirror_base_url='', mirror_metadata={}, provider_id='moex')
    paper = normalized.papers[0]
    assert paper.bundle_id == identity.bundle_id
    migrated = renormalize_catalog(NormalizedCatalog([paper], []), []).papers[0]
    assert migrated.bundle_id == identity.bundle_id
    assert classify_normalized_paper(migrated) == identity


@pytest.mark.repo_data
def test_all_reviewed_facts_match_retained_evidence_and_preserve_native_grades():
    root = Path(__file__).resolve().parents[1]
    facts = read_moex_category_identities(moex_evidence_path(root))
    assert facts
    assert validate_moex_category_evidence(root) == len(facts)
    for fact in facts:
        identity = classify_paper(provider_id='moex', source_exam_id=fact.source_exam_id,
            year_ad=fact.year_ad, category_code=fact.category_code, category_raw=fact.category_raw,
            exam_name_raw=fact.exam_name_raw, canonical_id='fixture', canonical_name='fixture')
        assert identity.exam_series_id == fact.series_id
        assert identity.level_id == fact.level_id
        assert identity.confidence == 'high'
