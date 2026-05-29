"""Forward email tool."""

import json

import requests
from mcp.server.fastmcp import FastMCP

from pyfastmail_mcp.client import USING_SUBMISSION, JMAPClient
from pyfastmail_mcp.exceptions import FastmailError, IdentityNotFoundError
from pyfastmail_mcp.tools.mail.actions import (
    _build_submission_args,
    _humanize_submission_errors,
)
from pyfastmail_mcp.tools.mail.disclosure import maybe_inject_disclosure
from pyfastmail_mcp.tools.mail.identities import _find_identity
from pyfastmail_mcp.tools.mail.reply import _get_email, _quote_body


def register(server: FastMCP, client: JMAPClient) -> None:
    @server.tool()
    async def mail_forward_email(
        email_id: str,
        to: list[str],
        text_body: str = "",
        identity_id: str | None = None,
        save_to_sent: bool = True,
    ) -> str:
        """Forward an email to one or more recipients, preserving the original content.

        Args:
            email_id: ID of the email to forward.
            to: List of recipient email addresses.
            text_body: Optional introductory text prepended before the quoted original.
            identity_id: Sender identity ID; auto-selects first if omitted.
            save_to_sent: If True (default), a copy of the forwarded message is
                saved to the account's Sent mailbox. If False, the draft is
                destroyed after send. See ``mail_send_email`` for details.
        """
        try:
            original = _get_email(client, email_id)
            if not original:
                return json.dumps({"error": f"Email {email_id!r} not found"})

            identity = _find_identity(client, identity_id)
            account_id = client.account_id

            subject = original.get("subject", "")
            if not subject.lower().startswith("fwd:"):
                subject = f"Fwd: {subject}"

            text_body, _ = maybe_inject_disclosure(text_body, None, to=to)

            quoted = _quote_body(original)
            full_body = f"{text_body}\n\n{quoted}".strip() if quoted else text_body

            drafts = client.get_mailbox_by_role("drafts")
            email_obj: dict = {
                "from": [
                    {"email": identity["email"], "name": identity.get("name", "")}
                ],
                "to": [{"email": addr} for addr in to],
                "subject": subject,
                "keywords": {"$draft": True},
                "mailboxIds": {drafts["id"]: True},
                "bodyValues": {"body": {"value": full_body, "charset": "utf-8"}},
                "textBody": [{"partId": "body", "type": "text/plain"}],
            }

            submission_args, mailbox_result = _build_submission_args(
                client,
                account_id=account_id,
                identity_id=identity["id"],
                drafts_id=drafts["id"],
                save_to_sent=save_to_sent,
                from_email=identity["email"],
                recipient_emails=list(to),
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
