"""Tests for the ipas_cert provider."""

import unittest
from dataclasses import asdict, replace
import hashlib
import json
from unittest.mock import Mock

import pytest

from app.classification import classify_paper
from app.providers.base import DownloadedFile
from app.providers.ipas_cert import evidence
from app.providers.ipas_cert.evidence import SourceReference, read_native_documents
from app.providers.ipas_cert.identity import PROGRAMMES, GRADE_LABELS
from app.source_material import SourceDate

from app.providers.ipas_cert.client import IpasCertClient, parse_certification_codes, parse_pdf_downloads


IPAS_HOME_HTML = """
<a href="/certification/ISE/news">資訊安全工程師</a>
<a href="https://www.ipas.org.tw/certification/AIAP/news">AI應用規劃師</a>
<a href="https://ipd.nat.gov.tw/ipas/certification/BIO/news">生技醫藥</a>
"""

IPAS_DOWNLOAD_HTML = """
<a href="https://www.ipas.org.tw/api/proxy/uploads/certification/ISE/115年度資訊安全工程師能力鑑定簡章.pdf">簡章</a>
<script>var x="https://www.ipas.org.tw/api/proxy/uploads/certification_attachment/a/iPAS疑義考題處理需知.pdf";</script>
"""

IPAS_LEARNING_RESOURCE_HTML = """
<section>歷屆考題</section>
<a href="/ipas/api/proxy/uploads/115-1公告試題_資訊安全管理概論.pdf">下載</a>
<a href="https://ipd.nat.gov.tw/ipas/api/proxy/uploads/iPAS經濟部產業人才能力鑑定_疑義考題處理需知.pdf">下載</a>
<a href="https://ipd.nat.gov.tw/ipas/api/proxy/uploads/iPAS經濟部產業人才能力鑑定_疑義考題處理需知.pdf">重複</a>
"""


class IpasCertParserTests(unittest.TestCase):
    def test_parse_certification_codes_extracts_codes_from_news_links(self) -> None:
        self.assertEqual(parse_certification_codes(IPAS_HOME_HTML), ["AIAP", "BIO", "ISE"])

    def test_parse_pdf_downloads_extracts_direct_pdf_refs(self) -> None:
        downloads = parse_pdf_downloads(IPAS_DOWNLOAD_HTML)

        self.assertEqual(len(downloads), 2)
        self.assertEqual(downloads[0].cert_code, "")
        self.assertIn("ipd.nat.gov.tw/ipas/api/proxy/uploads/", downloads[0].url)

    def test_parse_pdf_downloads_extracts_learning_resource_questions(self) -> None:
        downloads = parse_pdf_downloads(IPAS_LEARNING_RESOURCE_HTML, cert_code="ISE")

        labels = [download.label for download in downloads]
        self.assertEqual(len(downloads), 2)
        self.assertIn("115-1公告試題_資訊安全管理概論.pdf", labels)
        self.assertIn("iPAS經濟部產業人才能力鑑定_疑義考題處理需知.pdf", labels)
        self.assertTrue(all(download.cert_code == "ISE" for download in downloads))


@pytest.fixture
def snapshot(monkeypatch):
    original = evidence.native_documents()
    reference_base = next(d for d in original if not d.is_paper)
    paper_base = next(d for d in original if d.is_paper and d.sources[0].programme_code == 'ISE')
    facts = []
    payloads = {}
    by_code = {p.code: [] for p in PROGRAMMES}

    def add(code, *, kind='reference', grade='', year=2026, subject='來源文件', routes=()):
        sequence = len(facts)
        url = f'https://ipd.nat.gov.tw/ipas/api/proxy/uploads/certification/{code}/fixture-{sequence}.pdf'
        data = b'%PDF-1.7 reviewed native fixture ' + str(sequence).encode()
        checksum = hashlib.sha256(data).hexdigest()
        fact = replace(
            reference_base if kind == 'reference' else paper_base,
            id='ipas-native-' + checksum[:20], checksum=checksum,
            sources=(SourceReference(code, url),), kind=kind,
            file_type='source_asset' if kind == 'reference' else 'question_answer',
            grade=grade, session='1' if kind == 'administered' else '',
            subjects=() if kind == 'reference' else (subject,), routes=tuple(routes),
            date=SourceDate('exam_year' if kind == 'administered' else 'undated' if year is None else 'edition_year', year),
            edition_label='', label=subject, notes='',
        )
        facts.append(fact)
        payloads[url] = DownloadedFile(data, 'application/pdf', 'fixture.pdf')
        by_code[code].append(url)
        return fact

    for programme in PROGRAMMES:
        add(programme.code)
    older = add('ISE', kind='administered', grade='basic', year=2025, subject='資訊安全管理概論')
    common = add('AIAP', kind='administered', grade='intermediate', subject='人工智慧技術應用與規劃', routes=('data-analysis','machine-learning'))
    add('AIAP', kind='administered', grade='intermediate', subject='大數據處理分析與應用', routes=('data-analysis',))
    add('AIAP', kind='administered', grade='intermediate', subject='機器學習技術與應用', routes=('machine-learning',))
    undated = add('FQA', kind='sample', grade='basic', year=None, subject='食品品保樣題')
    monkeypatch.setattr(evidence, 'native_documents', lambda: tuple(facts))
    evidence._paper_contexts.cache_clear()
    home = ''.join(f'<a href="/certification/{p.code}/news">{p.label}</a>' for p in PROGRAMMES)
    client = IpasCertClient()

    def html(url):
        if url == client.HOME_URL:
            return home
        code = url.split('/certification/')[1].split('/')[0]
        return ''.join(f'<a href="{ref}">下載</a>' for ref in by_code[code])

    client._fetch_text = Mock(side_effect=html)
    client.http.download = Mock(side_effect=lambda url, **kwargs: payloads[url])
    yield client, facts, payloads, older, common, undated
    evidence._paper_contexts.cache_clear()


def test_snapshot_covers_every_native_programme_and_separates_reference_documents(snapshot):
    client, facts, _, older, _, undated = snapshot
    assert client.discover_available_years() == [2025, 2026]
    pages = [client.fetch_exam_page(e.code, y) for y in [2025,2026] for e in client.discover_exams(y)]
    assert sum(len(p.attachments) for p in pages) == 16
    assert sum(len(p.papers) for p in pages) == 6
    assert client.fetch_exam_page(older.event_code('ISE'), 2025).papers[0].source_material.date == SourceDate('exam_year',2025)
    sample = client.fetch_exam_page(undated.event_code('FQA'), 2026)
    assert sample.papers[0].source_material.date == SourceDate('undated',None)
    assert all(not p.papers for p in pages if p.attachments)
    assert client.http.download.call_count == len(facts)
    # Four source sections repeat the same links; neither requests nor records duplicate.
    assert client._fetch_text.call_count == 65
    for fact in facts:
        client.download_file(fact.sources[0].url)
    assert client.http.download.call_count == len(facts)


def test_native_qualification_routes_share_the_common_paper_without_merging_identities(snapshot):
    client, _, _, _, common, _ = snapshot
    page = client.fetch_exam_page(common.event_code('AIAP'), 2026)
    assert len(page.papers) == 4
    shared = [p for p in page.papers if p.subject_name_raw == common.subject_label]
    assert len(shared) == 2 and shared[0].files == shared[1].files
    identities = [classify_native(common,route) for route in common.routes]
    assert all(i.confidence == 'high' and i.level_id == 'ipas-intermediate' for i in identities)
    assert len({i.bundle_id for i in identities}) == 2
    assert '數據分析' in identities[0].bundle_name and '機器學習' in identities[1].bundle_name


def classify_native(document, route='', **overrides):
    source = document.sources[0]
    fields = dict(provider_id='ipas_cert',source_exam_id=document.event_code(source.programme_code),
                  year_ad=document.storage_year,category_raw='不能靠此標籤猜等級',exam_name_raw='不可靠的標籤',
                  canonical_id='old-compatibility',canonical_name='舊名稱',
                  subject_name_raw=document.subject_label,subject_code=document.subject_code,
                  category_code=document.category_code(source.programme_code,route),
                  source_checksum=document.checksum,source_material=document.material(source.programme_code,source.url))
    fields.update(overrides)
    return classify_paper(**fields)


@pytest.mark.parametrize('change',['checksum','event','category','subject','material'])
def test_changed_native_context_requires_review(snapshot,change):
    _, _, _, older, _, _ = snapshot
    overrides={'checksum':{'source_checksum':'0'*64},'event':{'source_exam_id':'ipas-cert-ise-basic-2026-1'},
               'category':{'category_code':'aiap-basic'},'subject':{'subject_code':'wrong'},
               'material':{'source_material':None}}[change]
    assert classify_native(older,**overrides).confidence == 'review'


def test_unknown_payload_fails_before_exposing_a_partial_provider_snapshot(snapshot):
    client, _, payloads, _, _, _ = snapshot
    url=next(iter(payloads));payloads[url]=DownloadedFile(b'%PDF-1.7 unreviewed replacement','application/pdf','new.pdf')
    with pytest.raises(ValueError,match='requires native review.*SHA256'):
        client.discover_available_years()
    assert client._pages is None


def test_html_download_cannot_become_a_reference_attachment(snapshot):
    client, _, payloads, _, _, _ = snapshot
    url=next(iter(payloads));payloads[url]=DownloadedFile(b'<html>maintenance</html>','text/html','guide.pdf')
    with pytest.raises(ValueError,match='not a native PDF'):
        client.discover_available_years()
    assert client._pages is None


@pytest.mark.parametrize('change',['membership','label'])
def test_homepage_drift_is_not_silently_accepted(snapshot,change):
    client, _, _, _, _, _ = snapshot
    client._fetch_text = Mock(return_value='<a href="/certification/UNREVIEWED/news">新項目</a>' if change=='membership' else ''.join(f'<a href="/certification/{p.code}/news">錯誤名稱</a>' for p in PROGRAMMES))
    with pytest.raises(ValueError,match='programme'):
        client.discover_available_years()
    client.http.download.assert_not_called()


def test_wrong_requested_event_year_does_not_reuse_another_years_papers(snapshot):
    client, _, _, older, _, _ = snapshot
    with pytest.raises(ValueError,match='event/year'):
        client.fetch_exam_page(older.event_code('ISE'),2026)


@pytest.mark.parametrize('change',['grade','year','owner','paper_as_reference','duplicate'])
def test_native_catalog_rejects_contradictory_contexts(tmp_path,change):
    fact=next(d for d in evidence.native_documents() if d.kind=='administered')
    row=asdict(fact)
    if change=='grade':row['grade']='advanced'
    elif change=='year':row['date']['year_ad']=2025 if fact.date.year_ad!=2025 else 2026
    elif change=='owner':row['sources'][0]['url']='https://foreign.test/ipas/api/proxy/uploads/paper.pdf'
    elif change=='paper_as_reference':row['kind']='reference'
    rows=[row,row] if change=='duplicate' else [row]
    path=tmp_path/'native.json';path.write_text(json.dumps({'schema_version':1,'provider_id':'ipas_cert','evidence_url':evidence.HOME_URL,'facts':rows}))
    with pytest.raises(ValueError):read_native_documents(path)


def test_pdf_parser_rejects_a_foreign_host_instead_of_rewriting_its_owner():
    with pytest.raises(ValueError,match='source owner'):
        parse_pdf_downloads('<a href="https://foreign.test/api/proxy/uploads/paper.pdf">下載</a>')


def test_unknown_direct_pdf_keeps_its_query_for_explicit_review():
    rows=parse_pdf_downloads('<a href="/ipas/api/proxy/uploads/paper.pdf?version=2&amp;kind=paper">下載</a>')
    assert rows[0].url.endswith('paper.pdf?version=2&kind=paper')


def test_graphical_drawing_solutions_and_old_curriculum_are_reviewed_native_facts():
    facts=evidence.native_documents()
    drawing=next(d for d in facts if d.review_basis=='native_text_and_visual_solutions')
    assert drawing.file_type=='question_answer' and drawing.inspected_pages==(1,2,3,4,5,6)
    colour=next(d for d in facts if d.sources[0].programme_code=='CPM' and d.is_paper)
    assert colour.subjects==('色彩學','色彩度量學') and colour.date==SourceDate('edition_year',2025)
    assert not colour.routes
    assert 'content-edition-114-0' in classify_native(colour).variant_ids


def office_payload(kind='odt'):
    import io
    import zipfile
    stream=io.BytesIO()
    with zipfile.ZipFile(stream,'w') as archive:
        subtype='text' if kind=='odt' else 'spreadsheet'
        archive.writestr('mimetype','application/vnd.oasis.opendocument.'+subtype)
        archive.writestr('content.xml','<document><p>iPAS報名申請表</p></document>')
    return stream.getvalue()


def test_editable_office_form_is_preserved_as_native_reference_not_an_exam(snapshot,tmp_path):
    from app.providers.ipas_cert.provider import IpasCertProvider
    from app.storage import MirrorStore
    from app.sync import sync_exam_pages
    client,facts,payloads,_,_,_=snapshot
    source='https://ipd.nat.gov.tw/ipas/api/proxy/uploads/certification/ISE/form.odt'
    data=office_payload();sha=hashlib.sha256(data).hexdigest()
    facts.append(replace(facts[0],id='ipas-native-'+sha[:20],checksum=sha,payload_format='odt',sources=(SourceReference('ISE',source),),label='報名申請表',page_count=None,inspected_pages=(),review_basis='native_office_content'))
    payloads[source]=DownloadedFile(data,'application/vnd.oasis.opendocument.text','form.odt')
    earlier=client._fetch_text.side_effect
    client._fetch_text.side_effect=lambda url:earlier(url)+(f'<a href="{source}">申請表</a>' if '/ISE/' in url else '')
    pages,catalog,failures=sync_exam_pages(IpasCertProvider(client),[('ipas-cert-ise-resources',2026)],MirrorStore(tmp_path/'mirror'),[],'',refresh_files=True)
    assert not failures and not catalog.papers
    form=next(a for a in pages[0].attachments if a.download_url_source==source)
    assert form.source_material.kind=='reference' and form.storage_key.endswith('.odt')
    assert (tmp_path/'mirror'/form.storage_key).read_bytes()==data


@pytest.mark.parametrize('declared,actual',[('odt','ods'),('ods','odt')])
def test_open_document_mimetype_cannot_be_relabelled_by_its_extension(declared,actual):
    from app.providers.ipas_cert.evidence import validated_payload_format
    with pytest.raises(ValueError,match='mimetype'):
        validated_payload_format('https://ipd.nat.gov.tw/ipas/api/proxy/uploads/form.'+declared,office_payload(actual))


def test_generic_zip_cannot_impersonate_a_native_office_document():
    import io
    import zipfile
    from app.providers.ipas_cert.evidence import validated_payload_format
    stream=io.BytesIO()
    with zipfile.ZipFile(stream,'w') as archive:archive.writestr('content.xml','<p>申請表</p>')
    with pytest.raises(ValueError,match='OpenDocument'):
        validated_payload_format('https://ipd.nat.gov.tw/ipas/api/proxy/uploads/form.odt',stream.getvalue())


def test_review_capture_keeps_unknown_documents_without_approving_new_facts(snapshot,tmp_path):
    import importlib.util
    from pathlib import Path
    path=Path(__file__).resolve().parents[1]/'scripts/capture_ipas_review.py'
    spec=importlib.util.spec_from_file_location('capture_ipas_for_test',path);module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)
    client,facts,payloads,_,_,_=snapshot
    url=next(iter(payloads));payloads[url]=DownloadedFile(b'%PDF-1.7 unreviewed revision','application/pdf','new.pdf')
    text=client._fetch_text.side_effect
    client.http.download=Mock(side_effect=lambda url,**kwargs:payloads[url] if url in payloads else DownloadedFile(text(url).encode(),'text/html; charset=utf-8','source.html'))
    before=tuple(facts);report=module.capture_review(tmp_path/'capture',client)
    assert report['capture_complete'] and not report['provider_state_changed']
    unknown=next(row for row in report['files'] if row['source_url']==url)
    assert 'native_review_required' in unknown and 'reviewed_native_context' not in unknown
    assert (tmp_path/'capture'/unknown['payload']).read_bytes()==payloads[url].data
    assert tuple(facts)==before and not (tmp_path/'data').exists()


@pytest.mark.parametrize('kind',['reference','administered'])
def test_old_generic_import_gets_an_independent_native_review_without_rewriting_it(kind):
    from pathlib import Path
    from copy import deepcopy
    document=next(d for d in evidence.native_documents() if d.kind==kind)
    source=document.sources[0]
    entry={'id':'test-original-revision','source_record':{
        'provider_id':'ipas_cert','source_exam_id':'ipas-cert-downloads',
        'category_code':source.programme_code.lower(),'download_url_source':source.url,
        'checksum':document.checksum,'file_type':'question','year_roc':115}}
    before=deepcopy(entry)
    views=evidence.reviewed_native_revisions([entry],repo_root=Path(__file__).resolve().parents[1])
    assert entry==before and len(views)==1
    view=views[0]
    assert view['material_kind']==kind and view['original_source_record_unchanged']
    assert view['grade']==(document.grade if document.is_paper else None)
    assert view['native_event']==(document.event_code(source.programme_code) if document.is_paper else None)
    entry['source_record']['checksum']='0'*64
    assert evidence.reviewed_native_revisions([entry],repo_root=Path(__file__).resolve().parents[1])==[]


@pytest.mark.repo_data
@pytest.mark.parametrize('change',['paper_role','reference_title','foreign_locator'])
def test_state_gate_rejects_role_and_locator_drift(monkeypatch,change):
    from pathlib import Path
    from copy import deepcopy
    from app.paths import provider_paths
    from app.state import load_provider_state
    root=Path(__file__).resolve().parents[1]
    pages,catalog,failures=deepcopy(load_provider_state(provider_paths(root,'ipas_cert')))
    if change=='paper_role':
        catalog.papers[0].file_type='answer'
    else:
        attachment=next(a for p in pages for a in p.attachments)
        if change=='reference_title':attachment.title='unsupported replacement title'
        else:attachment.storage_key=attachment.storage_key.replace('providers/ipas_cert/','providers/other/')
    monkeypatch.setattr('app.state.load_provider_state',lambda provider:(pages,catalog,failures))
    with pytest.raises(ValueError,match='contradicts|escapes'):
        evidence.validate_native_documents(root)
