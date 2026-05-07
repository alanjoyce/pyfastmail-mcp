"""Tests for mail_forward_email in tools/mail/forward.py."""

import json
from unittest.mock import MagicMock

import requests
from mcp.server.fastmcp import FastMCP

from pyfastmail_mcp.tools.mail.forward import register


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
    "messageId": ["<msg1@example.com>"],
    "references": [],
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


async def test_forward_ok():
    client = mock_client()
    client.call.side_effect = [
        _email_get_response(),
        _identity_response(),
        _send_response(),
    ]
    result = await _tool(client, "mail_forward_email")(
        email_id="orig1", to=["carol@example.com"]
    )
    data = json.loads(result)
    assert data == {
        "sent": True,
        "emailId": "e2",
        "submissionId": "s2",
        "mailbox": "Sent",
    }


async def test_forward_adds_fwd_prefix():
    client = mock_client()
    client.call.side_effect = [
        _email_get_response(),
        _identity_response(),
        _send_response(),
    ]
    await _tool(client, "mail_forward_email")(
        email_id="orig1", to=["carol@example.com"]
    )
    email_obj = client.call.call_args_list[2][0][1][0][1]["create"]["draft"]
    assert email_obj["subject"] == "Fwd: Hello"


async def test_forward_no_double_fwd_prefix():
    email = {**_ORIGINAL_EMAIL, "subject": "Fwd: Hello"}
    client = mock_client()
    client.call.side_effect = [
        _email_get_response(email),
        _identity_response(),
        _send_response(),
    ]
    await _tool(client, "mail_forward_email")(
        email_id="orig1", to=["carol@example.com"]
    )
    email_obj = client.call.call_args_list[2][0][1][0][1]["create"]["draft"]
    assert email_obj["subject"] == "Fwd: Hello"


async def test_forward_multiple_recipients():
    client = mock_client()
    client.call.side_effect = [
        _email_get_response(),
        _identity_response(),
        _send_response(),
    ]
    await _tool(client, "mail_forward_email")(
        email_id="orig1", to=["carol@example.com", "dave@example.com"]
    )
    email_obj = client.call.call_args_list[2][0][1][0][1]["create"]["draft"]
    to_emails = [a["email"] for a in email_obj["to"]]
    assert to_emails == ["carol@example.com", "dave@example.com"]


async def test_forward_quotes_original():
    client = mock_client()
    client.call.side_effect = [
        _email_get_response(),
        _identity_response(),
        _send_response(),
    ]
    await _tool(client, "mail_forward_email")(
        email_id="orig1", to=["carol@example.com"], text_body="See below"
    )
    email_obj = client.call.call_args_list[2][0][1][0][1]["create"]["draft"]
    body = email_obj["bodyValues"]["body"]["value"]
    assert "See below" in body
    assert "> Original text" in body


async def test_forward_email_not_found():
    client = mock_client()
    client.call.return_value = [("Email/get", {"list": []}, "g")]
    result = await _tool(client, "mail_forward_email")(
        email_id="missing", to=["carol@example.com"]
    )
    data = json.loads(result)
    assert "error" in data
    assert "missing" in data["error"]


async def test_forward_identity_not_found():
    client = mock_client()
    client.call.side_effect = [
        _email_get_response(),
        [("Identity/get", {"list": []}, "i")],
    ]
    result = await _tool(client, "mail_forward_email")(
        email_id="orig1", to=["carol@example.com"]
    )
    data = json.loads(result)
    assert "error" in data


async def test_forward_submission_not_created():
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
    result = await _tool(client, "mail_forward_email")(
        email_id="orig1", to=["carol@example.com"]
    )
    data = json.loads(result)
    assert "error" in data


async def test_forward_client_error():
    client = mock_client()
    client.call.side_effect = requests.RequestException("network failure")
    result = await _tool(client, "mail_forward_email")(
        email_id="orig1", to=["carol@example.com"]
    )
    data = json.loads(result)
    assert "error" in data
    assert "network failure" in data["error"]


async def test_forward_submission_error_surfaced():
    client = mock_client()
    client.call.side_effect = [
        _email_get_response(),
        _identity_response(),
        [
            ("Email/set", {"created": {"draft": {"id": "e1"}}}, "e"),
            (
                "EmailSubmission/set",
                {"notCreated": {"sub": {"type": "forbiddenToSend"}}},
                "s",
            ),
        ],
    ]
    result = json.loads(
        await _tool(client, "mail_forward_email")(
            email_id="orig1", to=["carol@example.com"]
        )
    )
    assert "Sending is not permitted for this account" in result["error"]
