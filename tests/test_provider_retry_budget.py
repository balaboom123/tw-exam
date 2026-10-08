from unittest.mock import patch
from urllib.error import URLError

import pytest

from app.providers.http import Http
from app.providers.tii_cert.client import TiiCertClient
from app.providers.wdasec_skill.client import WdasecSkillClient
from app.sync import retry_network


@pytest.mark.parametrize("client_type", [TiiCertClient, WdasecSkillClient])
def test_sync_retry_budget_is_not_multiplied_by_transport(client_type):
    client = client_type()
    with (
        patch.object(client.http, "_open", side_effect=URLError("timed out")) as opened,
        patch("app.providers.http.time.sleep"),
        patch("app.sync.time.sleep"),
    ):
        with pytest.raises(URLError, match="GET https://example.test/listing"):
            retry_network(lambda: client.http.get_text("https://example.test/listing"))
    assert opened.call_count == 3


@pytest.mark.parametrize("error", [URLError("timed out"), TimeoutError("timed out")])
def test_terminal_network_error_identifies_request_without_form_or_query_secrets(error):
    client = Http("sample", max_attempts=2)
    with (
        patch.object(client, "_open", side_effect=error) as opened,
        patch("app.providers.http.time.sleep"),
    ):
        with pytest.raises(type(error)) as caught:
            client._request(
                "https://example.test/listing?token=private-query#private-fragment",
                method="POST",
                data=b"session=private-form",
            )
    assert opened.call_count == 2
    assert "POST https://example.test/listing:" in str(caught.value)
    assert "timed out" in str(caught.value)
    assert "private" not in str(caught.value)
    assert caught.value.__cause__ is error


def test_failed_wda_post_keeps_session_state_through_bounded_sync_retries():
    client = WdasecSkillClient()
    client._hidden_fields = {"__VIEWSTATE": "retained", "gvData$ctl02$hdfPLAID": "native-id"}
    original = dict(client._hidden_fields)
    with (
        patch.object(client.http, "_open", side_effect=URLError("timed out")) as opened,
        patch("app.providers.http.time.sleep"),
        patch("app.sync.time.sleep"),
    ):
        with pytest.raises(URLError, match="POST"):
            retry_network(
                lambda: client._post({"__EVENTTARGET": "gvData", "__EVENTARGUMENT": "Page$2"})
            )
    assert opened.call_count == 3
    assert len({call.args[0].data for call in opened.call_args_list}) == 1
    assert client._hidden_fields == original
