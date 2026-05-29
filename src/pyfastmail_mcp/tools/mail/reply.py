"""Reply to email tool."""

import html
import json
from datetime import datetime, timezone

import requests
from mcp.server.fastmcp import FastMCP

from pyfastmail_mcp.client import USING_MAIL, USING_SUBMISSION, JMAPClient
from pyfastmail_mcp.exceptions import FastmailError, IdentityNotFoundError
from pyfastmail_mcp.tools.mail.actions import (
    _build_submission_args,
    _humanize_submission_errors,
)
from pyfastmail_mcp.tools.mail.disclosure import maybe_inject_disclosure
from pyfastmail_mcp.tools.mail.identities import _find_identity

_EMAIL_PROPS = [
    "id",
    "subject",
    "from",
    "to",
    "cc",
    "replyTo",
    "messageId",
    "references",
    "receivedAt",
    "sentAt",
    "bodyValues",
    "textBody",
    "htmlBody",
]


def _get_email(client: JMAPClient, email_id: str) -> dict:
    responses = client.call(
        USING_MAIL,
        [
            [
                "Email/get",
                {
                    "accountId": client.account_id,
                    "ids": [email_id],
                    "properties": _EMAIL_PROPS,
                    "fetchAllBodyValues": True,
                },
                "g",
            ]
        ],
    )
    _, data, _ = responses[0]
    items = data.get("list", [])
    return items[0] if items else {}


def _format_sender(email: dict) -> str:
    """Render the original email's first ``from`` address as ``Name <email>``."""
    addrs = email.get("from") or []
    if not addrs:
        return "the original sender"
    addr = addrs[0]
    name = (addr.get("name") or "").strip()
    address = (addr.get("email") or "").strip()
    if name and address:
        return f"{name} <{address}>"
    return address or name or "the original sender"


def _format_attribution(email: dict) -> str:
    """Build a Gmail-recognised attribution preamble for the quoted block.

    Returns a line like ``On Mon, 4 May 2026 at 18:47 UTC, Bob <bob@example.com> wrote:``.
    Mail clients (Gmail, Apple Mail, Fastmail web UI, Outlook) recognise this
    pattern as the start of a quoted reply and collapse the block below it
    into a "show trimmed content" affordance. Without this preamble, Gmail
    in particular treats ``> ``-prefixed lines as ordinary body text and
    leaves them inline.

    Date source preference: ``sentAt`` (when the original message was sent)
    falls back to ``receivedAt`` (when our server received it). Both are
    ISO 8601 UTC strings; we render in UTC with an explicit ``UTC`` marker
    so there's no ambiguity for cross-timezone correspondents.
    """
    sender = _format_sender(email)
    raw = email.get("sentAt") or email.get("receivedAt")
    if not raw:
        return f"On an earlier date, {sender} wrote:"
    try:
        # JMAP returns RFC 3339 strings — handle both the trailing-Z form and
        # explicit-offset form.
        dt = datetime.fromisoformat(raw.replace("Z", "+00:00")).astimezone(timezone.utc)
    except ValueError:
        return f"On an earlier date, {sender} wrote:"
    stamp = dt.strftime("%a, %-d %b %Y at %H:%M UTC")
    return f"On {stamp}, {sender} wrote:"


def _quote_body(email: dict) -> str:
    """Build the plain-text quoted block for a reply.

    Format: an attribution preamble, a blank line, then each line of the
    original text body prefixed with ``> ``. Gmail's quote-detector keys on
    the attribution + leading ``> `` pattern; other clients fall back to
    rendering the quoted block inline, which is also acceptable.
    """
    body_values = email.get("bodyValues") or {}
    text = ""
    for part in email.get("textBody") or []:
        val = body_values.get(part.get("partId", ""), {}).get("value", "")
        if val:
            text = val
            break
    if not text:
        return ""
    attribution = _format_attribution(email)
    quoted = "\n".join(f"> {line}" for line in text.splitlines())
    return f"{attribution}\n\n{quoted}"


def _quote_html(email: dict) -> str:
    """Build the HTML quoted block for a reply.

    Wraps the original message in a ``<blockquote>`` with an attribution
    paragraph. Prefers the original's HTML body when present; falls back
    to the text body, escaped and wrapped in ``<p>`` per blank-line break
    so the rendering doesn't collapse to a single run-on paragraph.

    Inline ``style`` on the blockquote is the conservative cross-client
    pattern (Apple Mail / Outlook strip ``class`` attributes, Gmail keeps
    them but doesn't require them for collapse).
    """
    body_values = email.get("bodyValues") or {}

    # Prefer original HTML body when available.
    html_text = ""
    for part in email.get("htmlBody") or []:
        val = body_values.get(part.get("partId", ""), {}).get("value", "")
        if val:
            html_text = val
            break

    if not html_text:
        # Fallback: use the text body, escape it, and break paragraphs on
        # blank lines so the rendering keeps some shape.
        text = ""
        for part in email.get("textBody") or []:
            val = body_values.get(part.get("partId", ""), {}).get("value", "")
            if val:
                text = val
                break
        if not text:
            return ""
        paragraphs = [p.strip() for p in text.split("\n\n") if p.strip()]
        html_text = "\n".join(
            "<p>" + html.escape(p).replace("\n", "<br>") + "</p>"
            for p in paragraphs
        )

    attribution = html.escape(_format_attribution(email))
    return (
        f'<p>{attribution}</p>\n'
        f'<blockquote type="cite" '
        f'style="margin:0 0 0 0.8ex;border-left:1px solid #ccc;padding-left:1ex">\n'
        f"{html_text}\n"
        f"</blockquote>"
    )


def register(server: FastMCP, client: JMAPClient) -> None:
    @server.tool()
    async def mail_reply_to_email(
        email_id: str,
        text_body: str,
        reply_all: bool = False,
        identity_id: str | None = None,
        save_to_sent: bool = True,
        html_body: str | None = None,
    ) -> str:
        """Reply to an email, preserving threading headers and quoting the original.

        The quoted block is preceded by a Gmail-recognised
        ``On <date>, <sender> wrote:`` attribution preamble so modern mail
        clients collapse the quote into a "show trimmed content" affordance
        rather than rendering it inline.

        Args:
            email_id: ID of the email to reply to.
            text_body: Plain-text reply body. The original message is appended
                as a quoted block beneath, with the attribution preamble.
            reply_all: If True, CC all original recipients.
            identity_id: Sender identity ID; auto-selects first if omitted.
            save_to_sent: If True (default), a copy of the reply is saved to
                the account's Sent mailbox and remains in the same conversation
                thread as the original. If False, the draft is destroyed after
                send and nothing is kept. See ``mail_send_email`` for details.
            html_body: Optional HTML reply body. When provided, the reply is
                sent as a multipart text+HTML message; the original message
                is appended as an HTML ``<blockquote>`` (preferring its HTML
                body, falling back to the escaped text body) beneath an
                attribution paragraph. The text_body still receives the
                plain-text quoted block, so plain-text-only clients see a
                useful fallback. As with ``mail_send_email``, the html_body
                is passed verbatim to JMAP with no sanitisation.
        """
        try:
            original = _get_email(client, email_id)
            if not original:
                return json.dumps({"error": f"Email {email_id!r} not found"})

            identity = _find_identity(client, identity_id)
            account_id = client.account_id

            orig_msg_ids = original.get("messageId") or []
            orig_refs = original.get("references") or []
            in_reply_to = orig_msg_ids[0] if orig_msg_ids else None
            references = orig_refs + orig_msg_ids

            reply_to_addrs = original.get("replyTo") or original.get("from") or []
            to_addrs = [{"email": a["email"]} for a in reply_to_addrs if a.get("email")]

            subject = original.get("subject", "")
            if not subject.lower().startswith("re:"):
                subject = f"Re: {subject}"

            # Compute the reply_all cc set up-front so the disclosure check
            # sees the full recipient set. The same list is reused below
            # when populating email_obj["cc"].
            reply_all_cc: list[str] = []
            if reply_all:
                orig_to = original.get("to") or []
                orig_cc = original.get("cc") or []
                my_email = identity["email"].lower()
                reply_all_cc = [
                    a["email"]
                    for a in orig_to + orig_cc
                    if a.get("email") and a["email"].lower() != my_email
                ]

            text_body, html_body = maybe_inject_disclosure(
                text_body,
                html_body,
                to=[a["email"] for a in to_addrs if a.get("email")],
                cc=reply_all_cc,
            )

            text_quote = _quote_body(original)
            full_text = f"{text_body}\n\n{text_quote}" if text_quote else text_body

            drafts = client.get_mailbox_by_role("drafts")
            email_obj: dict = {
                "from": [
                    {"email": identity["email"], "name": identity.get("name", "")}
                ],
                "to": to_addrs,
                "subject": subject,
                "keywords": {"$draft": True},
                "mailboxIds": {drafts["id"]: True},
                "bodyValues": {"body": {"value": full_text, "charset": "utf-8"}},
                "textBody": [{"partId": "body", "type": "text/plain"}],
            }

            if html_body:
                html_quote = _quote_html(original)
                full_html = f"{html_body}\n{html_quote}" if html_quote else html_body
                email_obj["bodyValues"]["htmlBody"] = {
                    "value": full_html,
                    "charset": "utf-8",
                }
                email_obj["htmlBody"] = [{"partId": "htmlBody", "type": "text/html"}]

            if in_reply_to:
                email_obj["inReplyTo"] = [in_reply_to]
            if references:
                email_obj["references"] = references
            if reply_all_cc:
                email_obj["cc"] = [{"email": e} for e in reply_all_cc]

            rcpts: list[str] = [a["email"] for a in to_addrs if a.get("email")]
            rcpts.extend(
                a["email"] for a in email_obj.get("cc", []) if a.get("email")
            )
            submission_args, mailbox_result = _build_submission_args(
                client,
                account_id=account_id,
                identity_id=identity["id"],
                drafts_id=drafts["id"],
                save_to_sent=save_to_sent,
                from_email=identity["email"],
                recipient_emails=rcpts,
            )

            responses = client.call(
                USING_SUBMISSION,
                [
                    [
                        "Email/set",
                        {"accountId": account_id, "create": {"draft": email_obj}},
                        "e",
                    ],
                    ["EmailSubmission/set", submission_args, "s"],
                ],
            )
            _, email_data, _ = responses[0]
            _, sub_data, _ = responses[1]

            not_created = sub_data.get("notCreated") or {}
            if not_created:
                return json.dumps({"error": _humanize_submission_errors(not_created)})

            created_email = (email_data.get("created") or {}).get("draft", {})
            created_sub = (sub_data.get("created") or {}).get("sub", {})
            return json.dumps(
                {
                    "sent": True,
                    "emailId": created_email.get("id"),
                    "submissionId": created_sub.get("id"),
                    "mailbox": mailbox_result,
                },
                indent=2,
            )
        except IdentityNotFoundError as exc:
            return json.dumps({"error": str(exc)})
        except (FastmailError, requests.RequestException, ValueError) as exc:
            return json.dumps({"error": str(exc)})
