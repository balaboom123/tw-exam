"""Native postal grades, job applicability, written stage and original roles."""

import copy
import hashlib
import json
from collections import Counter
from dataclasses import replace
from pathlib import Path
from unittest.mock import Mock

import pytest

from app.bundler import build_bundles, validate_public_materials
from app.models import NormalizedCatalog, ParsedPaper, SourceExamPage
from app.normalizer import normalize_papers
from app.providers.base import DownloadedFile
from app.providers.post_recruit.client import parse_history_page, source_file_code
from app.providers.post_recruit.evidence import (
    annual_exams, native_papers_path, project_native_page, read_annual_exams,
    validate_native_papers, validate_public_papers, reviewed_native_revisions,
)
from app.providers.post_recruit.provider import PostRecruitProvider
from app.storage import MirrorStore
from app.sync import sync_exam_pages

ROOT = Path(__file__).resolve().parents[1]
BASE = 'https://svc.tabf.org.tw/115post02/Paper/History?EPID=10315'


def acquisition_page(exam):
    return SourceExamPage(
        exam.source_exam_id, exam.programme_year_roc + 1911, exam.programme_year_roc,
        f'{exam.programme_year_roc}年中華郵政職階人員甄試', [],
        [ParsedPaper(p.source_context, 'post-recruit', source_file_code(p.source_urls[0]),
                     p.source_label, {'question': p.source_urls[0]}) for p in exam.files],
        provider_id='post_recruit',
    )


def projected_page(exam):
    return project_native_page(acquisition_page(exam), {
        p.source_urls[0]: p.checksum for p in exam.files
    })


def normalize(page, checksums):
    metadata = {
        (p.category_code, p.subject_code, role): {'checksum': checksums[url]}
        for p in page.papers for role, url in p.files.items()
    }
    return normalize_papers(
        page.source_exam_id, page.year_ad, page.exam_name_raw, page.papers, [], '', metadata,
        provider_id='post_recruit',
    )


def reviewed_catalog():
    rows = []
    for exam in annual_exams():
        catalog = normalize(projected_page(exam), {p.source_urls[0]: p.checksum for p in exam.files})
        assert not catalog.review_queue
        rows.extend(catalog.papers)
    return rows


def test_native_contexts_produce_complete_job_sets_without_mixing_grades_or_programmes():
    rows = reviewed_catalog()
    assert len(rows) == 321
    assert len({p.checksum for p in rows}) == 167
    assert len({p.bundle_id for p in rows}) == 40
    assert len({p.bundle_name for p in rows}) == 40
    assert Counter(e.programme_year_roc for e in annual_exams()) == {111:1,112:1,113:1,114:1}
    assert Counter(p.year_roc for p in rows) == {111:111,112:90,113:18,114:102}
    assert {p.stage_id for p in rows} == {'stage-1'}
    assert {p.level_id for p in rows} == {
        'postal-operational', 'postal-professional-1', 'postal-professional-2'
    }
    assert {p.classification_confidence for p in rows} == {'high'}
    assert all(not p.variant_ids for p in rows)
    assert all('第一試（筆試）' in p.bundle_name for p in rows)
    assert {p.source_material.date.year_ad for p in rows if p.year_roc == 112} == {2024}
    assert {p.exam_series_id for p in rows if p.year_roc == 113} == {'postal-information-recruitment'}
    assert {p.exam_series_id for p in rows if p.year_roc != 113} == {'postal-recruitment'}
    validate_public_papers(rows, complete_history=True)
    validate_public_materials(rows)


def test_shared_papers_keep_distinct_jobs_and_one_acquisition_locator():
    exam = annual_exams()[-1]
    native = next(p for p in exam.files if p.subject_name_raw == '基本電學')
    page = projected_page(exam)
    shared = [p for p in page.papers if native.source_urls[0] in p.files.values()]
    assert len(shared) == 2
    assert {p.category_raw for p in shared} == {'專業職(一)／電力工程','專業職(一)／電子修護'}
    assert {p.source_category_code for p in shared} == {'post-recruit'}
    assert len({p.subject_code for p in shared}) == 1
    outdoor = [j for j in exam.jobs if j.grade_scope.endswith('外勤')]
    assert {j.occupation for j in outdoor} == {'郵遞業務','運輸業務'}
    assert len({j.track_id for j in outdoor}) == 2


def test_printed_answers_and_corrections_are_preserved_with_original_questions():
    exams = annual_exams()
    assert Counter(p.file_type for e in exams for p in e.files) == {
        'question':129, 'question_answer':38
    }
    corrected = [p for e in exams for p in e.files if p.answer_notes]
    assert len(corrected) == 2
    assert all(p.file_type == 'question_answer' for p in corrected)
    assert sum(len(p.answer_notes) for p in corrected) == 3
    assert any('改2' in note for p in corrected for note in p.answer_notes)
    assert all('●' not in p.subject_name_raw for e in exams for p in e.files)
    assert any('程式設計概要' in p.subject_name_raw for p in exams[2].files)


@pytest.mark.parametrize('field', [
    'checksum','URL','download-URL','event','partition','category','subject','subject-label','programme','material','role'
])
def test_unreviewed_native_facts_cannot_inherit_known_identity(field):
    exam = annual_exams()[-1]
    rows = normalize(projected_page(exam), {p.source_urls[0]:p.checksum for p in exam.files}).papers
    original = rows[0]
    values = {
        'checksum': dict(checksum='a'*64),
        'download-URL': dict(download_url_source='https://example.com/foreign.pdf'),
        'URL': dict(source_material=replace(original.source_material, evidence_url=original.download_url_source+'?changed=1')),
        'event': dict(source_exam_id='post-recruit-other-114'),
        'partition': dict(year_roc=115),
        'category': dict(category_raw='營運職／法務'),
        'subject': dict(subject_code='paper-001'),
        'subject-label': dict(subject_name_raw='其他科目'),
        'programme': dict(exam_name_raw='114年非郵政甄試'),
        'material': dict(source_material=replace(original.source_material, date=replace(original.source_material.date,year_ad=2024))),
        'role': dict(file_type='answer'),
    }
    changed = replace(original, **values[field])
    with pytest.raises(ValueError,match='Postal publication'):
        validate_public_materials([changed])


def test_changed_live_bytes_and_source_context_stay_in_review():
    exam = annual_exams()[-1]
    raw = acquisition_page(exam)
    raw.papers = raw.papers[:1]
    url = raw.papers[0].files['question']
    changed = project_native_page(raw, {url:'a'*64})
    assert len(changed.papers) == 3  # same live applicability, unapproved bytes
    catalog = normalize(changed, {url:'a'*64})
    assert catalog.review_queue
    assert {p.level_id for p in catalog.papers} == {'unknown'}
    assert {p.stage_id for p in catalog.papers} == {'unknown'}
    assert {p.source_material.kind for p in catalog.papers} == {'unknown'}
    raw.papers[0].category_raw = 'unreviewed categories'
    unknown = project_native_page(raw, {url:exam.files[0].checksum})
    assert len(unknown.papers) == 1
    assert unknown.papers[0].category_code == 'post-recruit'
    assert unknown.papers[0].source_material.needs_review


def test_complete_history_guard_rejects_missing_job_or_entire_event_and_partial_bundle_is_valid(tmp_path):
    rows = reviewed_catalog()
    with pytest.raises(ValueError,match='omitted'):
        validate_public_papers([p for p in rows if p.year_roc != 113], complete_history=True)
    missing_job = rows[0].category_code
    with pytest.raises(ValueError,match='omitted'):
        validate_public_papers([p for p in rows if p.category_code != missing_job],complete_history=True)
    identity = rows[0].bundle_id
    partial = [p for p in rows if p.bundle_id == identity]
    validate_public_materials(partial)
    with pytest.raises(ValueError,match='omitted'):
        build_bundles(tmp_path/'must-not-exist', tmp_path/'mirror',
                      NormalizedCatalog(partial[1:],[]), '')
    assert not (tmp_path/'must-not-exist').exists()
    with pytest.raises(ValueError,match='Duplicate postal'):
        validate_public_materials([*rows,rows[0]])


def test_native_source_ids_survive_reordering_and_insertion_and_keep_common_applicability():
    one = '<li><a href="/_File/Download/115post02/HistoryPaper/one.pdf">共同科目</a>專業職(二)外勤-郵遞業務(X001)'
    two = '<li><a href="/_File/Download/115post02/HistoryPaper/two.pdf">共同科目</a>專業職(二)內勤-櫃台業務(X002)'
    first = parse_history_page('<ul>'+one+two+'</ul>',BASE)
    reordered = parse_history_page('<ul>'+two+one+'</ul>',BASE)
    assert {p.files['question']:p.subject_code for p in first} == {
        p.files['question']:p.subject_code for p in reordered
    }
    assert first[0].category_raw.endswith('郵遞業務(X001)')
    assert first[1].category_raw.endswith('櫃台業務(X002)')
    assert len(parse_history_page('<ul>'+one+one+'</ul>',BASE)) == 1
    with pytest.raises(ValueError,match='conflicting'):
        parse_history_page('<ul>'+one+one.replace('郵遞業務','運輸業務')+'</ul>',BASE)


@pytest.mark.parametrize('url', [
    'http://svc.tabf.org.tw/_File/Download/a/HistoryPaper/a.pdf',
    'https://example.com/_File/Download/a/HistoryPaper/a.pdf',
    'https://svc.tabf.org.tw/_File/Download/a/HistoryPaper/a.pdf#page=1',
    'https://svc.tabf.org.tw/_File/Download/a/Readme/a.pdf',
])
def test_source_ids_reject_non_native_locators(url):
    with pytest.raises(ValueError,match='native PDF'):
        source_file_code(url)


def test_old_generic_reference_review_requires_exact_programme_url_and_native_bytes():
    exam=annual_exams()[0]
    paper=exam.files[0]
    original=dict(year_roc=exam.programme_year_roc,source_exam_id=exam.source_exam_id,
                  download_url_source=paper.source_urls[0],checksum=paper.checksum,
                  file_type='question',subject_code='paper-001',category_raw='old generic label')
    entry=dict(id='old-original',record_type='paper',source_record=original)
    assert reviewed_native_revisions([entry])==['old-original']
    for field,value in [('year_roc',114),('source_exam_id','another-programme'),
                        ('checksum','f'*64),('download_url_source','https://example.com/a.pdf')]:
        altered=dict(entry,source_record=dict(original,**{field:value}))
        assert reviewed_native_revisions([altered])==[]
    assert original['category_raw']=='old generic label'


def test_provider_fetches_each_native_payload_once_per_command_and_warm_mirror_uses_fresh_bytes(tmp_path,monkeypatch):
    exam = annual_exams()[2]
    payloads = {p.source_urls[0]: b'%PDF-1.7 fixture '+str(i).encode()
                for i,p in enumerate(exam.files)}
    altered_files = tuple(replace(p,checksum=hashlib.sha256(payloads[p.source_urls[0]]).hexdigest()) for p in exam.files)
    altered = replace(exam,files=altered_files)
    monkeypatch.setattr('app.providers.post_recruit.evidence.annual_exams', lambda:(altered,))
    client = Mock()
    client.fetch_exam_page.side_effect = lambda *_: acquisition_page(altered)
    client.download_file.side_effect = lambda u: DownloadedFile(payloads[u],'application/pdf','native.pdf')
    for _ in range(2):
        provider = PostRecruitProvider(client)
        pages, catalog, failures = sync_exam_pages(
            provider, [(exam.source_exam_id,exam.programme_year_roc+1911)],
            MirrorStore(tmp_path/'mirror'), [], '', download_attachments=False,
        )
        assert not failures
        assert len(pages[0].papers) == len(catalog.papers) == 18
        assert not catalog.review_queue
        assert len({p.storage_key for p in catalog.papers}) == 13
    assert client.download_file.call_count == 26
    assert Counter(call.args[0] for call in client.download_file.call_args_list) == {
        u:2 for u in payloads
    }


def test_evidence_rejects_missing_slots_and_conflicting_grade_scope(tmp_path):
    original=json.loads(native_papers_path(ROOT).read_text())
    target=tmp_path/'native.json'
    for change in ['missing-slot','cross-grade','date','duplicate']:
        document=copy.deepcopy(original)
        event=document['events'][0]
        if change=='missing-slot': event['files'].pop(0)
        elif change=='cross-grade': event['files'][0]['applies_to'].append(event['jobs'][0]['category_code'])
        elif change=='date': event['written_test_date']='2025-01-01'
        else: event['files'].append(event['files'][0])
        target.write_text(json.dumps(document))
        with pytest.raises(ValueError):
            read_annual_exams(target)


@pytest.mark.repo_data
def test_all_reviewed_native_payloads_have_current_or_retained_source_anchors():
    assert validate_native_papers(ROOT) == 167
