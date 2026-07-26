"""Email read tools."""

import json
from datetime import datetime, timedelta, timezone

import requests
from mcp.server.fastmcp import FastMCP

from pyfastmail_mcp.client import JMAPClient
from pyfastmail_mcp.exceptions import FastmailError
from pyfastmail_mcp.tools.mail.quoting import strip_quoted_reply

_EMAIL_PROPS = [
    "id",
    "subject",
    "from",
    "to",
    "cc",
    "bcc",
    "replyTo",
    "receivedAt",
    "keywords",
    "bodyValues",
    "textBody",
    "htmlBody",
    "attachments",
    "hasAttachment",
]


_SUMMARY_PROPS = ["id", "subject", "from", "receivedAt", "keywords"]


def register(server: FastMCP, client: JMAPClient) -> None:
    @server.tool()
    async def mail_search_emails(
        text: str | None = None,
        from_: str | None = None,
        to: str | None = None,
        subject: str | None = None,
        has_attachment: bool | None = None,
        in_mailbox: str | None = None,
        limit: int = 20,
        newest_first: bool = True,
    ) -> str:
        """Search emails with optional filters. Returns id, subject, from, date.

        Args:
            text: Full-text search query.
            from_: Filter by sender address.
            to: Filter by recipient address.
            subject: Filter by subject text.
            has_attachment: Filter by attachment presence.
            in_mailbox: Mailbox ID to restrict search to. Use mail_list_mailboxes to get the ID.
            limit: Max results (default 20).
            newest_first: Sort newest first (default True).
        """
        try:
            filter_: dict = {}
            if text:
                filter_["text"] = text
            if from_:
                filter_["from"] = from_
            if to:
                filter_["to"] = to
            if subject:
                filter_["subject"] = subject
            if has_attachment is not None:
                filter_["hasAttachment"] = has_attachment
            if in_mailbox:
                filter_["inMailbox"] = in_mailbox

            sort = [{"property": "receivedAt", "isAscending": not newest_first}]
            emails = client.query_and_get(
                "Email",
                filter_ or None,
                _SUMMARY_PROPS,
                sort=sort,
                limit=limit,
            )
            return json.dumps(emails, indent=2)
        except (FastmailError, requests.RequestException, ValueError) as exc:
            return json.dumps({"error": str(exc)})

    @server.tool()
    async def mail_get_recent_emails(days: int = 7, limit: int = 20) -> str:
        """Get emails received in the last N days.

        Args:
            days: How many days back to look (default 7).
            limit: Max results (default 20).
        """
        try:
            since = (datetime.now(timezone.utc) - timedelta(days=days)).strftime(
                "%Y-%m-%dT%H:%M:%SZ"
            )
            emails = client.query_and_get(
                "Email",
                {"after": since},
                _SUMMARY_PROPS,
                sort=[{"property": "receivedAt", "isAscending": False}],
                limit=limit,
            )
            return json.dumps(emails, indent=2)
        except (FastmailError, requests.RequestException, ValueError) as exc:
            return json.dumps({"error": str(exc)})

    @server.tool()
    async def mail_get_email(
        email_id: str,
        prefer_html: bool = False,
        headers: list[str] | None = None,
        include_quoted_text: bool = False,
    ) -> str:
        """Get a single email by ID with full body content and attachment metadata.

        By default the quoted reply chain is trimmed from a plain-text body, so
        you see only this message's new content; `quotedTextStripped` in the
        response tells you whether anything was removed. Pass
        include_quoted_text to get the raw body with the quoted original
        intact, or use mail_get_email_thread to read the prior messages
        properly rather than as quoted copies.

        Optionally fetch specific headers by name. JMAP requires headers to be
        requested by name — no wildcard fetch is supported. Use mail_export_email
        to retrieve all raw headers.

        Common useful headers:
          - X-Delivered-To: original envelope recipient
          - X-SimpleLogin-Envelope-To: SimpleLogin alias that received the email
          - X-SimpleLogin-Original-From: real sender behind a SimpleLogin reverse alias

        Args:
            email_id: The JMAP email ID.
            prefer_html: Return HTML body if available; defaults to plain text.
            headers: Optional list of header names to fetch (e.g. ["X-Delivered-To"]).
            include_quoted_text: Return the raw body including the quoted reply
                chain. Defaults to False. Ignored for HTML bodies, which are
                never trimmed.
        """
        try:
            account_id = client.account_id
            props = list(_EMAIL_PROPS)
            if headers:
                props += [f"header:{h}" for h in headers]
            responses = client.call(
                ["urn:ietf:params:jmap:core", "urn:ietf:params:jmap:mail"],
                [
                    [
                        "Email/get",
                        {
                            "accountId": account_id,
                            "ids": [email_id],
                            "properties": props,
                            "fetchAllBodyValues": True,
                        },
                        "g",
                    ]
                ],
            )
            _, data, _ = responses[0]
            emails = data.get("list", [])
            if not emails:
                return json.dumps({"error": f"Email {email_id!r} not found"})

            email = emails[0]
            body = _extract_body(email, prefer_html)
            # Only plain text is trimmable — the delimiters below are text
            # heuristics, and HTML clients quote with <blockquote> instead.
            quoted_stripped = False
            if (
                body
                and not include_quoted_text
                and not _body_is_html(email, prefer_html)
            ):
                body, quoted_stripped = strip_quoted_reply(body)
            attachments = [
                {"name": a.get("name"), "type": a.get("type"), "size": a.get("size")}
                for a in (email.get("attachments") or [])
            ]
            result: dict = {
                "id": email.get("id"),
                "subject": email.get("subject"),
                "from": email.get("from"),
                "to": email.get("to"),
                "cc": email.get("cc"),
                "receivedAt": email.get("receivedAt"),
                "keywords": email.get("keywords", {}),
                "body": body,
                "quotedTextStripped": quoted_stripped,
                "hasAttachment": email.get("hasAttachment"),
                "attachments": attachments,
            }
            if headers:
                result["headers"] = {h: email.get(f"header:{h}") for h in headers}
            return json.dumps(result, indent=2)
        except (FastmailError, requests.RequestException, ValueError) as exc:
            return json.dumps({"error": str(exc)})


def _body_is_html(email: dict, prefer_html: bool) -> bool:
    """Whether _extract_body would have returned an HTML part.

    Mirrors _extract_body's selection rule, including its fallback to
    textBody when prefer_html is set but no HTML part resolves.
    """
    if not prefer_html:
        return False
    body_values = email.get("bodyValues") or {}
    return any(
        part.get("partId") in body_values
        for part in (email.get("htmlBody") or [])
        if part.get("partId")
    )


def _extract_body(email: dict, prefer_html: bool) -> str | None:
    """Return the best available body text from an email."""
    body_values = email.get("bodyValues") or {}
    parts = email.get("htmlBody" if prefer_html else "textBody") or []
    if not parts and prefer_html:
        parts = email.get("textBody") or []
    for part in parts:
        part_id = part.get("partId")
        if part_id and part_id in body_values:
            return body_values[part_id].get("value")
    return None
