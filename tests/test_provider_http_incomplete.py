"""Truncated response bodies use bounded retries and never replace native bytes."""

import io
from http.client import HTTPResponse, IncompleteRead
from unittest.mock import call, patch
from urllib.error import HTTPError, URLError

import pytest

from app.providers.http import Http
from app.providers.tii_cert.client import TiiCertClient
from app.providers.wdasec_skill.client import WdasecSkillClient
from app.storage import MirrorStore
from app.sync import retry_network, sync_exam_pages
from tests.test_sync_retained_refresh import RefreshClient


class MemorySocket:
    def __init__(self, wire):
        self.wire = wire

    def makefile(self, *_args, **_kwargs):
        return io.BytesIO(self.wire)


def native_response(body, *, declared_length=None, filename="complete.pdf"):
    length = len(body) if declared_length is None else declared_length
    wire = (
        "HTTP/1.1 200 OK\r\n"
        f"Content-Length: {length}\r\n"
        "Content-Type: application/pdf\r\n"
        f'Content-Disposition: attachment; filename="{filename}"\r\n\r\n'
    ).encode("ascii") + body
    response = HTTPResponse(MemorySocket(wire))
    response.begin()
    return response


def test_truncated_download_restarts_and_returns_only_the_complete_body_and_headers():
    partial = native_response(b"%PDF-1.7 partial", declared_length=100, filename="partial.pdf")
    body = b"%PDF-1.7 complete native source"
    complete = native_response(body)
    client = Http("sample", max_attempts=3)
    with (
        patch.object(client, "_open", side_effect=[partial, complete]) as opened,
        patch("app.providers.http.random.uniform", return_value=0),
        patch("app.providers.http.time.sleep") as sleep,
    ):
        downloaded = client.download("https://example.test/native.pdf")
    assert opened.call_count == 2
    assert sleep.call_args_list == [call(1)]
    assert downloaded.data == body and downloaded.file_name == "complete.pdf"
    assert downloaded.content_type == "application/pdf"
    assert partial.isclosed() and complete.isclosed()
    assert all(c.kwargs == {} and c.args[1] == 120 for c in opened.call_args_list)


@pytest.mark.parametrize("attempts", [1, 3])
def test_exhausted_incomplete_body_is_a_contextual_network_error_without_partial_data(attempts):
    responses = [native_response(b"private-response", declared_length=100) for _ in range(attempts)]
    client = Http("sample", max_attempts=attempts)
    with (
        patch.object(client, "_open", side_effect=responses) as opened,
        patch("app.providers.http.time.sleep"),
        pytest.raises(URLError) as error,
    ):
        client._request(
            "https://example.test/native.pdf?token=private-query#private-fragment",
            method="POST",
            data=b"session=private-form",
        )
    assert opened.call_count == attempts
    assert all(response.isclosed() for response in responses)
    assert isinstance(error.value.__cause__, IncompleteRead)
    assert error.value.__cause__.partial == b"private-response"
    assert "POST https://example.test/native.pdf:" in str(error.value)
    assert "IncompleteRead" in str(error.value) and "private" not in str(error.value)
    assert {c.args[0].data for c in opened.call_args_list} == {b"session=private-form"}


@pytest.mark.parametrize("client_type", [TiiCertClient, WdasecSkillClient])
def test_incomplete_read_obeys_single_attempt_adapters_outer_sync_budget(client_type):
    client = client_type()
    responses = [native_response(b"partial", declared_length=100) for _ in range(3)]
    with (
        patch.object(client.http, "_open", side_effect=responses) as opened,
        patch("app.providers.http.time.sleep"),
        pytest.raises(URLError),
    ):
        retry_network(lambda: client.http.download("https://example.test/native.pdf"))
    assert opened.call_count == 3
    assert all(response.isclosed() for response in responses)


@pytest.mark.parametrize(
    "error", [ValueError("bad URL"), HTTPError("url", 404, "missing", {}, None)]
)
def test_unrelated_errors_and_permanent_http_failures_do_not_gain_retries(error):
    client = Http("sample", max_attempts=3)
    with (
        patch.object(client, "_open", side_effect=error) as opened,
        patch("app.providers.http.time.sleep") as sleep,
        pytest.raises(type(error)),
    ):
        client.download("https://example.test/native.pdf")
    assert opened.call_count == 1
    sleep.assert_not_called()


class HttpRefreshClient(RefreshClient):
    def __init__(self):
        super().__init__()
        self.http = Http(self.provider_id, max_attempts=1)

    def download_file(self, url):
        if url.endswith(".html"):
            return super().download_file(url)
        return self.http.download(url)


@pytest.mark.parametrize("retained_original", [False, True], ids=["cold", "refresh"])
def test_sync_never_installs_truncated_pdf_and_retains_only_verified_previous_bytes(
    tmp_path, retained_original
):
    client = HttpRefreshClient()
    store = MirrorStore(tmp_path / "mirror")
    previous = []
    original = b"%PDF-1.7 verified earlier source"
    old = None
    if retained_original:
        with patch.object(client.http, "_open", return_value=native_response(original)):
            previous, old, failures = sync_exam_pages(
                client, [("gept-cert-elementary-2022", 2022)], store, [], ""
            )
        assert not failures and len(old.papers) == 1
    partial = b"%PDF-1.7 clipped but recognizable signature"
    responses = [native_response(partial, declared_length=100) for _ in range(3)]
    with (
        patch.object(client.http, "_open", side_effect=responses) as opened,
        patch("app.providers.http.time.sleep"),
    ):
        pages, catalog, failures = sync_exam_pages(
            client,
            [("gept-cert-elementary-2022", 2022)],
            store,
            [],
            "",
            refresh_files=True,
            previous_pages=previous,
        )
    assert opened.call_count == 3 and all(response.isclosed() for response in responses)
    assert len(failures) == 1 and failures[0].stage == "download"
    assert "IncompleteRead" in failures[0].message
    if retained_original:
        assert len(catalog.papers) == 1
        assert catalog.papers[0].checksum == old.papers[0].checksum
        assert pages[0].papers[0].mirror_files == previous[0].papers[0].mirror_files
        assert (store.root / old.papers[0].storage_key).read_bytes() == original
    else:
        assert not catalog.papers and not list(store.root.rglob("*.pdf"))
    assert not list((store.root / "providers/gept_cert/recovery/source-revisions").glob("*"))
