"""GEPT source roles, stable storage keys, native practice sets and provenance."""

from unittest.mock import Mock, patch

import pytest

from app.providers.base import DownloadedFile
from app.providers.gept_cert.client import (
    GeptCertClient,
    parse_intro_downloads,
    parse_practice_audio,
)
from app.providers.gept_cert.identity import STORAGE_YEAR_AD
from app.sync import _validated_extension

INTRO = """<a href="../2022/geptpracticee.htm">練習題</a>
<a href="../2022/geptscoreremark/初級作文.pdf">※ 各級分範例 ※</a>
<a href="../WebFile/GEPTCBTPracticeTest.zip">點此下載</a>
<a href="ETSampleTest.pdf">安裝說明</a>"""
PRACTICE = """<h2>全民英語能力分級檢定測驗(初級)練習題</h2>
<td>一、聽力測驗：</td><a href="javascript:playAudio('realplayer/el_web_1.mp3');">播放</a>
<td>四、口說能力測驗</td><a href="javascript:playAudio('realplayer/el_web_5.mp3');">播放</a>"""
ELEMENTARY = ("elementary", "初級", "https://www.gept.org.tw/Exam_Intro/t01_introduction.asp")


def test_intro_keeps_scored_examples_and_software_out_of_question_roles():
    downloads, pages = parse_intro_downloads(INTRO, base_url=ELEMENTARY[2], level_code="elementary")
    assert [r.file_type for r in downloads] == [
        "scoring_examples",
        "source_archive",
        "instructions",
    ]
    assert [r.attachment for r in downloads] == [False, True, True]
    assert all(r.material.kind == "reference" for r in downloads)
    assert all(r.material.date.basis == "undated" for r in downloads)
    assert downloads[0].url.endswith("%E5%88%9D%E7%B4%9A%E4%BD%9C%E6%96%87.pdf")
    assert pages == ["https://www.gept.org.tw/2022/geptpracticee.htm"]


def test_unreviewed_pdf_role_is_not_guessed_from_its_extension():
    with pytest.raises(ValueError, match="Unreviewed GEPT download role"):
        parse_intro_downloads(
            '<a href="unknown.pdf">下載資料</a>', base_url=ELEMENTARY[2], level_code="elementary"
        )


def test_practice_audio_uses_the_source_skill_section():
    audio = parse_practice_audio(
        PRACTICE, base_url="https://www.gept.org.tw/2022/geptpracticee.htm", level_code="elementary"
    )
    assert [r.file_type for r in audio] == ["listening_audio", "speaking_audio"]
    assert all(r.material.kind == "practice_collection" for r in audio)
    assert all(r.material.date.year_ad is None for r in audio)
    assert audio[1].label.startswith("口說練習")


def test_audio_without_a_native_skill_heading_requires_review():
    with pytest.raises(ValueError, match="lacks a reviewed"):
        parse_practice_audio(
            "<a href=\"javascript:playAudio('realplayer/a.mp3');\">播放</a>",
            base_url=ELEMENTARY[2],
            level_code="elementary",
        )


def test_discovery_partition_is_compatibility_state_not_a_url_or_runtime_date():
    client = GeptCertClient()
    client._fetch_text = Mock(side_effect=AssertionError("Discovery must not manufacture dates"))
    assert client.discover_available_years() == [STORAGE_YEAR_AD]
    assert len(client.discover_exams(STORAGE_YEAR_AD)) == 5
    assert client.discover_exams(2027) == []


@pytest.mark.parametrize(
    "event,year",
    [
        ("gept-cert-materials", 2026),
        ("gept-cert-elementary-2027", 2027),
        ("gept-cert-unknown-2022", 2022),
    ],
)
def test_unknown_events_are_rejected_before_source_requests(event, year):
    client = GeptCertClient()
    client._fetch_text = Mock(side_effect=AssertionError("Unexpected request"))
    with pytest.raises(ValueError, match="Unsupported GEPT event"):
        client.fetch_exam_page(event, year)


def test_source_pages_and_assets_are_retained_as_attachments_with_papers():
    client = GeptCertClient()
    client.LEVELS = (ELEMENTARY,)
    html = PRACTICE + '<img src="images/question.png"><script src="js/native.js"></script>'
    client._fetch_text = Mock(
        side_effect=lambda url: html if url.endswith("geptpracticee.htm") else INTRO
    )
    page = client.fetch_exam_page("gept-cert-elementary-2022", 2022)
    assert len(page.papers) == 3
    assert {next(iter(p.files)) for p in page.papers} == {
        "scoring_examples",
        "listening_audio",
        "speaking_audio",
    }
    assert {a.file_type for a in page.attachments} == {
        "source_page",
        "source_asset",
        "source_archive",
        "instructions",
    }
    assert any(a.download_url_source.endswith("images/question.png") for a in page.attachments)
    assert any(a.download_url_source.endswith("js/native.js") for a in page.attachments)
    assert all(p.source_material.date.basis == "undated" for p in page.papers)
    client.fetch_exam_page("gept-cert-elementary-2022", 2022)
    assert client._fetch_text.call_count == 2


def test_repeated_anchor_labels_get_distinct_stable_source_keys():
    client = GeptCertClient()
    client.LEVELS = (
        ("intermediate", "中級", "https://www.gept.org.tw/Exam_Intro/t02_introduction.asp"),
    )
    client._fetch_text = Mock(
        return_value="""
      <a href="../2022/geptscoreremark/itranslation.pdf">※ 各級分範例 ※</a>
      <a href="../2022/geptscoreremark/icomposition.pdf">※ 各級分範例 ※</a>"""
    )
    page = client.fetch_exam_page("gept-cert-intermediate-2022", 2022)
    assert len(page.papers) == len({p.subject_code for p in page.papers}) == 2
    assert len({p.subject_name_raw for p in page.papers}) == 2
    assert all(p.subject_code.startswith("resource-") for p in page.papers)


@pytest.mark.parametrize("charset", ["utf-8", "big5"])
def test_retained_native_html_bytes_and_decoding_share_one_response(charset):
    body = "<html>全民英檢初級練習題</html>".encode(charset)
    file = DownloadedFile(body, "text/html; charset=" + charset, "source.htm")
    client = GeptCertClient()
    with patch.object(client.http, "download", return_value=file) as request:
        assert "全民英檢" in client._fetch_text(ELEMENTARY[2])
        assert client.download_file(ELEMENTARY[2]).data == body
    request.assert_called_once()


@pytest.mark.parametrize(
    "prefix",
    [b"", b'\r\n<Script Language="JavaScript">' + b"// native setting\n" * 15 + b"</Script>\r\n"],
)
def test_raw_source_html_has_an_explicit_role_and_does_not_weaken_paper_validation(prefix):
    body = prefix + b"<!doctype html><html><h2>Native practice page</h2></html>"
    assert _validated_extension("source_page", body, "text/html", "page.asp") == ".html"
    for role in ["question", "listening_audio", "source_asset"]:
        with pytest.raises(RuntimeError, match="HTML placeholder"):
            _validated_extension(role, body, "text/html", "page.html")
    with pytest.raises(RuntimeError, match="does not match"):
        _validated_extension("source_page", b"%PDF-1.7 paper", "application/pdf", "paper.pdf")


def advanced_client(home='formCode:2}, "練習題 1"; formCode:3}, "練習題 2"'):
    intro_url = "https://www.gept.org.tw/Exam_Intro/t04_introduction.asp"
    base = "https://www.gept.org.tw/2022/geptpracticead/"
    intro = '<a href="' + base + 'Form/2/Listening">線上練習題1</a>'
    intro += '<a href="' + base + 'Form/3/Listening">線上練習題2</a>'
    shell = '<html><script src="/2022/geptpracticead/assets/index-demo.js"></script></html>'
    app = """formNo:2,type:"listening"; formNo:3,type:"listening";
    "assets/HomeView-demo.js";
    const show = file => window.open(`/transcripts/${file}`, "_blank");
    Form_${state.form}_L_Transcript.pdf;
    "https://portal.lttc.org.tw/audio/GEPTAdvanced/01/02_Q01.mp3";
    "https://portal.lttc.org.tw/audio/GEPTAdvanced/02/02_Q01.mp3";
    "/2022/geptpracticead/assets/question.png";"""
    sources = {
        intro_url: intro,
        base + "Form/2/Listening": shell,
        base + "Form/3/Listening": shell,
        base + "assets/index-demo.js": app,
        base + "assets/HomeView-demo.js": home,
    }
    client = GeptCertClient()
    client.LEVELS = (("advanced", "高級", intro_url),)
    client._fetch_text = Mock(side_effect=sources.__getitem__)
    return client


def test_advanced_application_resources_keep_both_visible_practice_sets():
    page = advanced_client().fetch_exam_page("gept-cert-advanced-2022", 2022)
    assert len(page.papers) == 4
    assert {p.category_code for p in page.papers} == {"advanced-practice-1", "advanced-practice-2"}
    assert {next(iter(p.files)) for p in page.papers} == {"listening_audio", "listening_transcript"}
    transcripts = [
        p.files["listening_transcript"] for p in page.papers if "listening_transcript" in p.files
    ]
    assert transcripts == [
        "https://www.gept.org.tw/transcripts/Form_2_L_Transcript.pdf",
        "https://www.gept.org.tw/transcripts/Form_3_L_Transcript.pdf",
    ]
    assert any(a.download_url_source.endswith("assets/question.png") for a in page.attachments)
    assert any(a.download_url_source.endswith("assets/index-demo.js") for a in page.attachments)


def test_advanced_form_mapping_changes_require_an_explicit_review():
    client = advanced_client('formCode:2}, "練習題 2"; formCode:3}, "練習題 1"')
    with pytest.raises(ValueError, match="no longer match visible"):
        client.fetch_exam_page("gept-cert-advanced-2022", 2022)
