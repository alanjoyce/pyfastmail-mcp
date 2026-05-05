"""Tests for mail_reply_to_email in tools/mail/reply.py."""

import json
from unittest.mock import MagicMock

import requests
from mcp.server.fastmcp import FastMCP

from pyfastmail_mcp.tools.mail.reply import register


def mock_client():
    client = MagicMock()
    client.account_id = "acc99"
    return client


def _tool(client, name):
    server = FastMCP("test")
    register(server, client)
    return server._tool_manager._tools[name].fn


_IDENTITY = {"id": "ident1", "name": "Alice", "email": "alice@example.com"}

_ORIGINAL_EMAIL = {
    "id": "orig1",
    "subject": "Hello",
    "from": [{"email": "bob@example.com", "name": "Bob"}],
    "to": [{"email": "alice@example.com"}],
    "cc": [],
    "replyTo": None,
    "messageId": ["<msg1@example.com>"],
    "references": ["<ref1@example.com>"],
    "sentAt": "2026-05-04T18:47:00Z",
    "receivedAt": "2026-05-04T18:47:30Z",
    "bodyValues": {"1": {"value": "Original text"}},
    "textBody": [{"partId": "1", "type": "text/plain"}],
}


def _email_get_response(email=None):
    return [("Email/get", {"list": [email or _ORIGINAL_EMAIL]}, "g")]


def _identity_response():
    return [("Identity/get", {"list": [_IDENTITY]}, "i")]


def _send_response(email_id="e2", sub_id="s2"):
    return [
        ("Email/set", {"created": {"draft": {"id": email_id}}}, "e"),
        ("EmailSubmission/set", {"created": {"sub": {"id": sub_id}}}, "s"),
    ]


async def test_reply_ok():
    client = mock_client()
    client.call.side_effect = [
        _email_get_response(),
        _identity_response(),
        _send_response(),
    ]
    result = await _tool(client, "mail_reply_to_email")(
        email_id="orig1", text_body="My reply"
    )
    data = json.loads(result)
    assert data == {
        "sent": True,
        "emailId": "e2",
        "submissionId": "s2",
        "mailbox": "Sent",
    }


async def test_reply_adds_re_prefix():
    client = mock_client()
    client.call.side_effect = [
        _email_get_response(),
        _identity_response(),
        _send_response(),
    ]
    await _tool(client, "mail_reply_to_email")(email_id="orig1", text_body="reply")
    email_obj = client.call.call_args_list[2][0][1][0][1]["create"]["draft"]
    assert email_obj["subject"] == "Re: Hello"


async def test_reply_no_double_re_prefix():
    email = {**_ORIGINAL_EMAIL, "subject": "Re: Hello"}
    client = mock_client()
    client.call.side_effect = [
        _email_get_response(email),
        _identity_response(),
        _send_response(),
    ]
    await _tool(client, "mail_reply_to_email")(email_id="orig1", text_body="reply")
    email_obj = client.call.call_args_list[2][0][1][0][1]["create"]["draft"]
    assert email_obj["subject"] == "Re: Hello"


async def test_reply_threading_headers():
    client = mock_client()
    client.call.side_effect = [
        _email_get_response(),
        _identity_response(),
        _send_response(),
    ]
    await _tool(client, "mail_reply_to_email")(email_id="orig1", text_body="reply")
    email_obj = client.call.call_args_list[2][0][1][0][1]["create"]["draft"]
    assert email_obj["inReplyTo"] == ["<msg1@example.com>"]
    assert "<ref1@example.com>" in email_obj["references"]
    assert "<msg1@example.com>" in email_obj["references"]


async def test_reply_quotes_original():
    client = mock_client()
    client.call.side_effect = [
        _email_get_response(),
        _identity_response(),
        _send_response(),
    ]
    await _tool(client, "mail_reply_to_email")(email_id="orig1", text_body="My reply")
    email_obj = client.call.call_args_list[2][0][1][0][1]["create"]["draft"]
    body = email_obj["bodyValues"]["body"]["value"]
    assert "My reply" in body
    assert "> Original text" in body
    # Attribution preamble — Gmail / Apple Mail / Fastmail recognise this
    # pattern and collapse the quote into a "show trimmed content" affordance.
    assert "Bob <bob@example.com> wrote:" in body
    assert "On Mon, 4 May 2026 at 18:47 UTC" in body


async def test_reply_attribution_falls_back_to_received_at():
    """When the original has no sentAt, attribution uses receivedAt."""
    email = {**_ORIGINAL_EMAIL}
    email.pop("sentAt")
    client = mock_client()
    client.call.side_effect = [
        _email_get_response(email),
        _identity_response(),
        _send_response(),
    ]
    await _tool(client, "mail_reply_to_email")(email_id="orig1", text_body="reply")
    email_obj = client.call.call_args_list[2][0][1][0][1]["create"]["draft"]
    body = email_obj["bodyValues"]["body"]["value"]
    assert "On Mon, 4 May 2026 at 18:47 UTC" in body  # from receivedAt


async def test_reply_attribution_handles_missing_dates():
    """No sentAt and no receivedAt → graceful fallback, no crash."""
    email = {**_ORIGINAL_EMAIL}
    email.pop("sentAt")
    email.pop("receivedAt")
    client = mock_client()
    client.call.side_effect = [
        _email_get_response(email),
        _identity_response(),
        _send_response(),
    ]
    await _tool(client, "mail_reply_to_email")(email_id="orig1", text_body="reply")
    email_obj = client.call.call_args_list[2][0][1][0][1]["create"]["draft"]
    body = email_obj["bodyValues"]["body"]["value"]
    assert "Bob <bob@example.com> wrote:" in body
    assert "> Original text" in body


async def test_reply_with_html_body():
    """When html_body is provided, the draft has a multipart text+HTML body."""
    client = mock_client()
    client.call.side_effect = [
        _email_get_response(),
        _identity_response(),
        _send_response(),
    ]
    await _tool(client, "mail_reply_to_email")(
        email_id="orig1",
        text_body="My plain reply",
        html_body="<p>My <strong>HTML</strong> reply</p>",
    )
    email_obj = client.call.call_args_list[2][0][1][0][1]["create"]["draft"]
    # Both bodies present.
    assert "body" in email_obj["bodyValues"]
    assert "htmlBody" in email_obj["bodyValues"]
    assert email_obj["textBody"] == [{"partId": "body", "type": "text/plain"}]
    assert email_obj["htmlBody"] == [{"partId": "htmlBody", "type": "text/html"}]
    # HTML body has the user's content + the attributed blockquote.
    html_value = email_obj["bodyValues"]["htmlBody"]["value"]
    assert "<p>My <strong>HTML</strong> reply</p>" in html_value
    assert "Bob &lt;bob@example.com&gt; wrote:" in html_value
    assert '<blockquote type="cite"' in html_value


async def test_reply_html_quote_prefers_original_html_body():
    """If the original had an HTML body, we reuse it inside the blockquote."""
    email = {
        **_ORIGINAL_EMAIL,
        "bodyValues": {
            "1": {"value": "Original text"},
            "2": {"value": "<p>Original <em>html</em></p>"},
        },
        "htmlBody": [{"partId": "2", "type": "text/html"}],
    }
    client = mock_client()
    client.call.side_effect = [
        _email_get_response(email),
        _identity_response(),
        _send_response(),
    ]
    await _tool(client, "mail_reply_to_email")(
        email_id="orig1", text_body="reply", html_body="<p>my reply</p>"
    )
    email_obj = client.call.call_args_list[2][0][1][0][1]["create"]["draft"]
    html_value = email_obj["bodyValues"]["htmlBody"]["value"]
    assert "<p>Original <em>html</em></p>" in html_value


async def test_reply_html_quote_falls_back_to_text_body():
    """When the original lacks an HTML body, the quote escapes the text body."""
    client = mock_client()
    client.call.side_effect = [
        _email_get_response(),
        _identity_response(),
        _send_response(),
    ]
    await _tool(client, "mail_reply_to_email")(
        email_id="orig1", text_body="reply", html_body="<p>my reply</p>"
    )
    email_obj = client.call.call_args_list[2][0][1][0][1]["create"]["draft"]
    html_value = email_obj["bodyValues"]["htmlBody"]["value"]
    assert "<p>Original text</p>" in html_value


async def test_reply_without_html_body_stays_text_only():
    """Backwards-compatible: omitting html_body produces a text-only reply."""
    client = mock_client()
    client.call.side_effect = [
        _email_get_response(),
        _identity_response(),
        _send_response(),
    ]
    await _tool(client, "mail_reply_to_email")(email_id="orig1", text_body="reply")
    email_obj = client.call.call_args_list[2][0][1][0][1]["create"]["draft"]
    assert "htmlBody" not in email_obj
    assert "htmlBody" not in email_obj["bodyValues"]


async def test_reply_all_adds_cc():
    email = {
        **_ORIGINAL_EMAIL,
        "to": [{"email": "alice@example.com"}, {"email": "carol@example.com"}],
        "cc": [{"email": "dave@example.com"}],
    }
    client = mock_client()
    client.call.side_effect = [
        _email_get_response(email),
        _identity_response(),
        _send_response(),
    ]
    await _tool(client, "mail_reply_to_email")(
        email_id="orig1", text_body="reply", reply_all=True
    )
    email_obj = client.call.call_args_list[2][0][1][0][1]["create"]["draft"]
    cc_emails = {a["email"] for a in email_obj.get("cc", [])}
    assert "carol@example.com" in cc_emails
    assert "dave@example.com" in cc_emails
    assert "alice@example.com" not in cc_emails


async def test_reply_email_not_found():
    client = mock_client()
    client.call.return_value = [("Email/get", {"list": []}, "g")]
    result = await _tool(client, "mail_reply_to_email")(
        email_id="missing", text_body="reply"
    )
    data = json.loads(result)
    assert "error" in data
    assert "missing" in data["error"]


async def test_reply_identity_not_found():
    client = mock_client()
    client.call.side_effect = [
        _email_get_response(),
        [("Identity/get", {"list": []}, "i")],
    ]
    result = await _tool(client, "mail_reply_to_email")(
        email_id="orig1", text_body="reply"
    )
    data = json.loads(result)
    assert "error" in data


async def test_reply_submission_not_created():
    client = mock_client()
    client.call.side_effect = [
        _email_get_response(),
        _identity_response(),
        [
            ("Email/set", {"created": {"draft": {"id": "e2"}}}, "e"),
            (
                "EmailSubmission/set",
                {"notCreated": {"sub": {"type": "forbidden"}}},
                "s",
            ),
        ],
    ]
    result = await _tool(client, "mail_reply_to_email")(
        email_id="orig1", text_body="reply"
    )
    data = json.loads(result)
    assert "error" in data


async def test_reply_client_error():
    client = mock_client()
    client.call.side_effect = requests.RequestException("network failure")
    result = await _tool(client, "mail_reply_to_email")(
        email_id="orig1", text_body="reply"
    )
    data = json.loads(result)
    assert "error" in data
    assert "network failure" in data["error"]


async def test_reply_submission_error_surfaced():
    client = mock_client()
    client.call.side_effect = [
        _email_get_response(),
        _identity_response(),
        [
            ("Email/set", {"created": {"draft": {"id": "e1"}}}, "e"),
            (
                "EmailSubmission/set",
                {"notCreated": {"sub": {"type": "forbiddenFrom"}}},
                "s",
            ),
        ],
    ]
    result = json.loads(
        await _tool(client, "mail_reply_to_email")(email_id="orig1", text_body="reply")
    )
    assert "Not permitted to send from this address" in result["error"]
