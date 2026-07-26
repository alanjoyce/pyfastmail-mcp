"""Tests for tools/mail/email.py."""

import json
from unittest.mock import MagicMock

import pytest
import requests
from mcp.server.fastmcp import FastMCP

from pyfastmail_mcp.tools.mail.email import _body_is_html, _extract_body, register


@pytest.fixture
def mock_client():
    client = MagicMock()
    client.account_id = "acc1"
    return client


def _tool(mock_client, name="mail_get_email"):
    server = FastMCP("test")
    register(server, mock_client)
    return server._tool_manager._tools[name].fn


EMAIL = {
    "id": "e1",
    "subject": "Hello",
    "from": [{"email": "a@example.com"}],
    "to": [{"email": "b@example.com"}],
    "cc": None,
    "receivedAt": "2026-01-01T00:00:00Z",
    "bodyValues": {"1": {"value": "plain text"}, "2": {"value": "<b>html</b>"}},
    "textBody": [{"partId": "1"}],
    "htmlBody": [{"partId": "2"}],
    "attachments": [{"name": "file.pdf", "type": "application/pdf", "size": 1024}],
    "hasAttachment": True,
}


async def test_get_email_ok(mock_client):
    mock_client.call.return_value = [["Email/get", {"list": [EMAIL]}, "g"]]
    result = json.loads(await _tool(mock_client)(email_id="e1"))
    assert result["id"] == "e1"
    assert result["subject"] == "Hello"
    assert result["body"] == "plain text"
    assert result["attachments"] == [
        {"name": "file.pdf", "type": "application/pdf", "size": 1024}
    ]
    assert result["hasAttachment"] is True


async def test_get_email_prefer_html(mock_client):
    mock_client.call.return_value = [["Email/get", {"list": [EMAIL]}, "g"]]
    result = json.loads(await _tool(mock_client)(email_id="e1", prefer_html=True))
    assert result["body"] == "<b>html</b>"


async def test_get_email_not_found(mock_client):
    mock_client.call.return_value = [["Email/get", {"list": []}, "g"]]
    result = json.loads(await _tool(mock_client)(email_id="missing"))
    assert "not found" in result["error"]


async def test_get_email_client_error(mock_client):
    mock_client.call.side_effect = requests.RequestException("network failure")
    result = json.loads(await _tool(mock_client)(email_id="e1"))
    assert "network failure" in result["error"]


async def test_get_email_no_attachments(mock_client):
    email = {**EMAIL, "attachments": None, "hasAttachment": False}
    mock_client.call.return_value = [["Email/get", {"list": [email]}, "g"]]
    result = json.loads(await _tool(mock_client)(email_id="e1"))
    assert result["attachments"] == []


def test_extract_body_text():
    assert _extract_body(EMAIL, prefer_html=False) == "plain text"


def test_extract_body_html():
    assert _extract_body(EMAIL, prefer_html=True) == "<b>html</b>"


def test_extract_body_html_fallback_to_text():
    email = {**EMAIL, "htmlBody": []}
    assert _extract_body(email, prefer_html=True) == "plain text"


def test_extract_body_missing():
    assert _extract_body({"bodyValues": {}, "textBody": []}, prefer_html=False) is None


SUMMARY = [
    {
        "id": "e1",
        "subject": "Hi",
        "from": [{"email": "a@b.com"}],
        "receivedAt": "2026-01-01T00:00:00Z",
    }
]


async def test_search_emails_no_filters(mock_client):
    mock_client.query_and_get.return_value = SUMMARY
    result = json.loads(await _tool(mock_client, "mail_search_emails")())
    assert result == SUMMARY
    mock_client.query_and_get.assert_called_once()
    call_args, call_kwargs = mock_client.query_and_get.call_args
    assert call_args[1] is None


async def test_search_emails_with_filters(mock_client):
    mock_client.query_and_get.return_value = SUMMARY
    await _tool(mock_client, "mail_search_emails")(
        text="hello",
        from_="a@b.com",
        to="c@d.com",
        subject="Hi",
        has_attachment=True,
        limit=5,
        newest_first=False,
    )
    call_args, call_kwargs = mock_client.query_and_get.call_args
    assert call_args[1] == {
        "text": "hello",
        "from": "a@b.com",
        "to": "c@d.com",
        "subject": "Hi",
        "hasAttachment": True,
    }
    assert call_kwargs["limit"] == 5
    assert call_kwargs["sort"] == [{"property": "receivedAt", "isAscending": True}]


async def test_search_emails_in_mailbox(mock_client):
    mock_client.query_and_get.return_value = SUMMARY
    await _tool(mock_client, "mail_search_emails")(
        text="hello", in_mailbox="mbox-inbox-id"
    )
    call_args, _ = mock_client.query_and_get.call_args
    assert call_args[1] == {"text": "hello", "inMailbox": "mbox-inbox-id"}


async def test_search_emails_error(mock_client):
    mock_client.query_and_get.side_effect = requests.RequestException("boom")
    result = json.loads(await _tool(mock_client, "mail_search_emails")())
    assert "boom" in result["error"]


async def test_get_recent_emails_ok(mock_client):
    mock_client.query_and_get.return_value = SUMMARY
    result = json.loads(await _tool(mock_client, "mail_get_recent_emails")())
    assert result == SUMMARY
    call_args, call_kwargs = mock_client.query_and_get.call_args
    assert "after" in call_args[1]
    assert call_kwargs["limit"] == 20


async def test_get_recent_emails_custom_days(mock_client):
    mock_client.query_and_get.return_value = []
    await _tool(mock_client, "mail_get_recent_emails")(days=3, limit=5)
    _, call_kwargs = mock_client.query_and_get.call_args
    assert call_kwargs["limit"] == 5


async def test_get_recent_emails_error(mock_client):
    mock_client.query_and_get.side_effect = requests.RequestException("timeout")
    result = json.loads(await _tool(mock_client, "mail_get_recent_emails")())
    assert "timeout" in result["error"]


async def test_get_email_with_headers(mock_client):
    email = {**EMAIL, "header:X-Delivered-To": "me@example.com", "header:X-Foo": None}
    mock_client.call.return_value = [["Email/get", {"list": [email]}, "g"]]
    result = json.loads(
        await _tool(mock_client)(email_id="e1", headers=["X-Delivered-To", "X-Foo"])
    )
    assert result["headers"] == {"X-Delivered-To": "me@example.com", "X-Foo": None}
    _, call_kwargs, _ = mock_client.call.call_args[0][1][0]
    assert "header:X-Delivered-To" in call_kwargs["properties"]
    assert "header:X-Foo" in call_kwargs["properties"]


async def test_get_email_headers_not_in_result_when_not_requested(mock_client):
    mock_client.call.return_value = [["Email/get", {"list": [EMAIL]}, "g"]]
    result = json.loads(await _tool(mock_client)(email_id="e1"))
    assert "headers" not in result


async def test_get_email_headers_appended_to_default_props(mock_client):
    mock_client.call.return_value = [["Email/get", {"list": [EMAIL]}, "g"]]
    await _tool(mock_client)(email_id="e1", headers=["X-Delivered-To"])
    _, call_kwargs, _ = mock_client.call.call_args[0][1][0]
    # default props still present
    assert "subject" in call_kwargs["properties"]
    assert "header:X-Delivered-To" in call_kwargs["properties"]


# --- quoted-reply trimming (Watson carry-patch) ---------------------------

QUOTED = "Tuesday works.\n\n> Can you make Tuesday?\n"

REPLY_EMAIL = {
    **EMAIL,
    "bodyValues": {
        "1": {"value": QUOTED},
        "2": {"value": "<p>Tuesday works.</p><blockquote>Can you?</blockquote>"},
    },
}


async def test_get_email_strips_quoted_text_by_default(mock_client):
    mock_client.call.return_value = [["Email/get", {"list": [REPLY_EMAIL]}, "g"]]
    result = json.loads(await _tool(mock_client)(email_id="e1"))
    assert result["body"] == "Tuesday works."
    assert result["quotedTextStripped"] is True


async def test_get_email_include_quoted_text_returns_raw_body(mock_client):
    mock_client.call.return_value = [["Email/get", {"list": [REPLY_EMAIL]}, "g"]]
    result = json.loads(
        await _tool(mock_client)(email_id="e1", include_quoted_text=True)
    )
    assert result["body"] == QUOTED
    assert result["quotedTextStripped"] is False


async def test_get_email_html_body_is_never_trimmed(mock_client):
    """Text delimiters don't apply to HTML, which quotes with <blockquote>."""
    mock_client.call.return_value = [["Email/get", {"list": [REPLY_EMAIL]}, "g"]]
    result = json.loads(await _tool(mock_client)(email_id="e1", prefer_html=True))
    assert result["body"] == REPLY_EMAIL["bodyValues"]["2"]["value"]
    assert result["quotedTextStripped"] is False


async def test_get_email_html_fallback_to_text_is_trimmed(mock_client):
    """prefer_html with no HTML part falls back to text, which is trimmable."""
    email = {**REPLY_EMAIL, "htmlBody": []}
    mock_client.call.return_value = [["Email/get", {"list": [email]}, "g"]]
    result = json.loads(await _tool(mock_client)(email_id="e1", prefer_html=True))
    assert result["body"] == "Tuesday works."
    assert result["quotedTextStripped"] is True


async def test_get_email_unquoted_body_reports_not_stripped(mock_client):
    mock_client.call.return_value = [["Email/get", {"list": [EMAIL]}, "g"]]
    result = json.loads(await _tool(mock_client)(email_id="e1"))
    assert result["body"] == "plain text"
    assert result["quotedTextStripped"] is False


async def test_get_email_missing_body_does_not_crash(mock_client):
    email = {**EMAIL, "textBody": [], "htmlBody": [], "bodyValues": {}}
    mock_client.call.return_value = [["Email/get", {"list": [email]}, "g"]]
    result = json.loads(await _tool(mock_client)(email_id="e1"))
    assert result["body"] is None
    assert result["quotedTextStripped"] is False


def test_body_is_html_matches_extract_body_selection():
    assert _body_is_html(EMAIL, prefer_html=True) is True
    assert _body_is_html(EMAIL, prefer_html=False) is False
    # prefer_html but no resolvable HTML part → _extract_body falls back to text
    assert _body_is_html({**EMAIL, "htmlBody": []}, prefer_html=True) is False
    assert _body_is_html({**EMAIL, "bodyValues": {}}, prefer_html=True) is False


def test_get_email_docstring_mentions_export_for_all_headers():
    from mcp.server.fastmcp import FastMCP

    from pyfastmail_mcp.tools.mail.email import register

    server = FastMCP("test")
    register(server, MagicMock())
    doc = server._tool_manager._tools["mail_get_email"].fn.__doc__
    assert "mail_export_email" in doc
