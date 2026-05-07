"""Tests for mail_send_email in tools/mail/send.py."""

import json
from unittest.mock import MagicMock

import requests
from mcp.server.fastmcp import FastMCP

from pyfastmail_mcp.tools.mail.send import register


def mock_client():
    client = MagicMock()
    client.account_id = "acc99"
    return client


def _tool(client, name):
    server = FastMCP("test")
    register(server, client)
    return server._tool_manager._tools[name].fn


_IDENTITY = {"id": "ident1", "name": "Alice", "email": "alice@example.com"}


def _identity_response():
    return [("Identity/get", {"list": [_IDENTITY]}, "i")]


def _send_response(email_id="e1", sub_id="s1"):
    return [
        ("Email/set", {"created": {"draft": {"id": email_id}}}, "e"),
        ("EmailSubmission/set", {"created": {"sub": {"id": sub_id}}}, "s"),
    ]


async def test_send_email_ok():
    client = mock_client()
    client.call.side_effect = [_identity_response(), _send_response()]
    result = await _tool(client, "mail_send_email")(
        to=["bob@example.com"], subject="Hi", text_body="Hello"
    )
    data = json.loads(result)
    assert data == {
        "sent": True,
        "emailId": "e1",
        "submissionId": "s1",
        "mailbox": "Sent",
    }


async def test_send_email_with_cc_bcc_html():
    client = mock_client()
    client.call.side_effect = [_identity_response(), _send_response()]
    result = await _tool(client, "mail_send_email")(
        to=["bob@example.com"],
        subject="Hi",
        text_body="Hello",
        cc=["cc@example.com"],
        bcc=["bcc@example.com"],
        html_body="<p>Hello</p>",
    )
    data = json.loads(result)
    assert data["sent"] is True
    email_set_call = client.call.call_args_list[1]
    email_obj = email_set_call[0][1][0][1]["create"]["draft"]
    assert email_obj["cc"] == [{"email": "cc@example.com"}]
    assert email_obj["bcc"] == [{"email": "bcc@example.com"}]
    assert "htmlBody" in email_obj


async def test_send_email_explicit_identity():
    client = mock_client()
    client.call.side_effect = [_identity_response(), _send_response()]
    result = await _tool(client, "mail_send_email")(
        to=["bob@example.com"], subject="Hi", text_body="Hello", identity_id="ident1"
    )
    assert json.loads(result)["sent"] is True


async def test_send_email_identity_not_found():
    client = mock_client()
    client.call.side_effect = [_identity_response()]
    result = await _tool(client, "mail_send_email")(
        to=["bob@example.com"], subject="Hi", text_body="Hello", identity_id="bad-id"
    )
    data = json.loads(result)
    assert "error" in data
    assert "bad-id" in data["error"]


async def test_send_email_no_identities():
    client = mock_client()
    client.call.return_value = [("Identity/get", {"list": []}, "i")]
    result = await _tool(client, "mail_send_email")(
        to=["bob@example.com"], subject="Hi", text_body="Hello"
    )
    data = json.loads(result)
    assert "error" in data


async def test_send_email_submission_not_created():
    client = mock_client()
    client.call.side_effect = [
        _identity_response(),
        [
            ("Email/set", {"created": {"draft": {"id": "e1"}}}, "e"),
            (
                "EmailSubmission/set",
                {"notCreated": {"sub": {"type": "forbidden"}}},
                "s",
            ),
        ],
    ]
    result = await _tool(client, "mail_send_email")(
        to=["bob@example.com"], subject="Hi", text_body="Hello"
    )
    data = json.loads(result)
    assert "error" in data


async def test_send_email_client_error():
    client = mock_client()
    client.call.side_effect = requests.RequestException("network failure")
    result = await _tool(client, "mail_send_email")(
        to=["bob@example.com"], subject="Hi", text_body="Hello"
    )
    data = json.loads(result)
    assert "error" in data
    assert "network failure" in data["error"]


def _submission_error_response(error_type, **extra):
    return [
        ("Email/set", {"created": {"draft": {"id": "e1"}}}, "e"),
        (
            "EmailSubmission/set",
            {"notCreated": {"sub": {"type": error_type, **extra}}},
            "s",
        ),
    ]


async def test_send_forbidden_from():
    client = mock_client()
    client.call.side_effect = [
        _identity_response(),
        _submission_error_response("forbiddenFrom"),
    ]
    result = json.loads(
        await _tool(client, "mail_send_email")(
            to=["x@y.com"], subject="s", text_body="b"
        )
    )
    assert "Not permitted to send from this address" in result["error"]


async def test_send_forbidden_to_send():
    client = mock_client()
    client.call.side_effect = [
        _identity_response(),
        _submission_error_response("forbiddenToSend"),
    ]
    result = json.loads(
        await _tool(client, "mail_send_email")(
            to=["x@y.com"], subject="s", text_body="b"
        )
    )
    assert "Sending is not permitted for this account" in result["error"]


async def test_send_forbidden_mail_from():
    client = mock_client()
    client.call.side_effect = [
        _identity_response(),
        _submission_error_response("forbiddenMailFrom"),
    ]
    result = json.loads(
        await _tool(client, "mail_send_email")(
            to=["x@y.com"], subject="s", text_body="b"
        )
    )
    assert "Not permitted to use this envelope sender" in result["error"]


async def test_send_too_many_recipients():
    client = mock_client()
    client.call.side_effect = [
        _identity_response(),
        _submission_error_response("tooManyRecipients", maxRecipients=10),
    ]
    result = json.loads(
        await _tool(client, "mail_send_email")(
            to=["x@y.com"], subject="s", text_body="b"
        )
    )
    assert "Too many recipients (max: 10)" in result["error"]


async def test_send_no_recipients():
    client = mock_client()
    client.call.side_effect = [
        _identity_response(),
        _submission_error_response("noRecipients"),
    ]
    result = json.loads(
        await _tool(client, "mail_send_email")(
            to=["x@y.com"], subject="s", text_body="b"
        )
    )
    assert "No recipients specified" in result["error"]


async def test_send_invalid_recipients():
    client = mock_client()
    client.call.side_effect = [
        _identity_response(),
        _submission_error_response("invalidRecipients", invalidRecipients=["bad@"]),
    ]
    result = json.loads(
        await _tool(client, "mail_send_email")(
            to=["x@y.com"], subject="s", text_body="b"
        )
    )
    assert "Invalid recipient addresses" in result["error"]
    assert "bad@" in result["error"]


async def test_send_invalid_email():
    client = mock_client()
    client.call.side_effect = [
        _identity_response(),
        _submission_error_response("invalidEmail"),
    ]
    result = json.loads(
        await _tool(client, "mail_send_email")(
            to=["x@y.com"], subject="s", text_body="b"
        )
    )
    assert "Email is invalid" in result["error"]


# ---------------------------------------------------------------------------
# save_to_sent: default flow uses onSuccessUpdateEmail to move Drafts -> Sent.
# ---------------------------------------------------------------------------

def _submission_args_from_call(client):
    """Extract the EmailSubmission/set args dict from the last client.call."""
    # client.call(using, method_calls) — we want the 2nd method call's args.
    method_calls = client.call.call_args_list[-1][0][1]
    return method_calls[1][1]


async def test_send_includes_explicit_envelope():
    """Submission carries an explicit envelope with mailFrom + dedup'd rcptTo.

    JMAP allows envelope to be omitted (server derives from headers), but in
    practice that produced submissions reporting success while delivering to
    nobody. We always send it explicitly, matching the Fastmail web client.
    """
    client = mock_client()
    client.call.side_effect = [_identity_response(), _send_response()]
    await _tool(client, "mail_send_email")(
        to=["bob@example.com", "BOB@example.com"],  # case-insensitive dup
        cc=["carol@example.com"],
        bcc=["dave@example.com"],
        subject="Hi",
        text_body="Hello",
    )
    sub_create = _submission_args_from_call(client)["create"]["sub"]
    envelope = sub_create["envelope"]
    assert envelope["mailFrom"] == {"email": "alice@example.com"}
    rcpt_emails = [r["email"] for r in envelope["rcptTo"]]
    # Dedup is case-insensitive, order is first-seen, Bcc IS included.
    assert rcpt_emails == ["bob@example.com", "carol@example.com", "dave@example.com"]


async def test_send_default_uses_on_success_update_email():
    """Default path: EmailSubmission/set carries onSuccessUpdateEmail."""
    client = mock_client()
    # Deterministic mailbox so we can assert on the patch paths.
    client.get_mailbox_by_role.side_effect = lambda role: {
        "drafts": {"id": "drafts-id"},
        "sent": {"id": "sent-id"},
    }[role]
    client.call.side_effect = [_identity_response(), _send_response()]

    result = await _tool(client, "mail_send_email")(
        to=["bob@example.com"], subject="Hi", text_body="Hello"
    )
    data = json.loads(result)
    assert data["mailbox"] == "Sent"

    sub_args = _submission_args_from_call(client)
    assert "onSuccessUpdateEmail" in sub_args
    assert "onSuccessDestroyEmail" not in sub_args
    patch = sub_args["onSuccessUpdateEmail"]["#sub"]
    assert patch["mailboxIds/drafts-id"] is None
    assert patch["mailboxIds/sent-id"] is True
    assert patch["keywords/$draft"] is None


async def test_send_save_to_sent_false_uses_destroy():
    """Opt-out: draft is destroyed after send, mailbox field reports so."""
    client = mock_client()
    client.get_mailbox_by_role.side_effect = lambda role: {
        "drafts": {"id": "drafts-id"},
        "sent": {"id": "sent-id"},
    }[role]
    client.call.side_effect = [_identity_response(), _send_response()]

    result = await _tool(client, "mail_send_email")(
        to=["bob@example.com"],
        subject="Hi",
        text_body="Hello",
        save_to_sent=False,
    )
    data = json.loads(result)
    assert data["mailbox"] == "destroyed"

    sub_args = _submission_args_from_call(client)
    assert sub_args["onSuccessDestroyEmail"] == ["#sub"]
    assert "onSuccessUpdateEmail" not in sub_args


async def test_send_missing_sent_role_falls_back_to_destroy():
    """If the account has no Sent mailbox, fall back and report mailbox=null."""
    from pyfastmail_mcp.exceptions import MailboxNotFoundError

    client = mock_client()

    def _role(role):
        if role == "drafts":
            return {"id": "drafts-id"}
        raise MailboxNotFoundError("no sent")

    client.get_mailbox_by_role.side_effect = _role
    client.call.side_effect = [_identity_response(), _send_response()]

    result = await _tool(client, "mail_send_email")(
        to=["bob@example.com"], subject="Hi", text_body="Hello"
    )
    data = json.loads(result)
    assert data["sent"] is True
    assert data["mailbox"] is None

    sub_args = _submission_args_from_call(client)
    assert sub_args["onSuccessDestroyEmail"] == ["#sub"]
    assert "onSuccessUpdateEmail" not in sub_args


async def test_send_submission_failure_leaves_draft_intact():
    """Draft retention: on submission failure, we do NOT issue a destroy call."""
    client = mock_client()
    client.get_mailbox_by_role.side_effect = lambda role: {
        "drafts": {"id": "drafts-id"},
        "sent": {"id": "sent-id"},
    }[role]
    client.call.side_effect = [
        _identity_response(),
        [
            ("Email/set", {"created": {"draft": {"id": "e1"}}}, "e"),
            (
                "EmailSubmission/set",
                {"notCreated": {"sub": {"type": "forbidden"}}},
                "s",
            ),
        ],
    ]

    result = await _tool(client, "mail_send_email")(
        to=["bob@example.com"], subject="Hi", text_body="Hello"
    )
    data = json.loads(result)
    assert "error" in data
    # Exactly two client.call invocations: Identity/get and the send batch.
    # No follow-up Email/set destroy was issued.
    assert client.call.call_count == 2
