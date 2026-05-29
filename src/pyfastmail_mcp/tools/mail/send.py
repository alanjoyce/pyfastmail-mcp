"""Send email tool."""

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

_MAX_RECIPIENTS = 50


def register(server: FastMCP, client: JMAPClient) -> None:
    @server.tool()
    async def mail_send_email(
        to: list[str],
        subject: str,
        text_body: str,
        cc: list[str] | None = None,
        bcc: list[str] | None = None,
        html_body: str | None = None,
        identity_id: str | None = None,
        save_to_sent: bool = True,
    ) -> str:
        """Send an email via Fastmail.

        Args:
            to: List of recipient email addresses.
            subject: Email subject line.
            text_body: Plain-text body content.
            cc: Optional list of CC addresses.
            bcc: Optional list of BCC addresses.
            html_body: Optional HTML body content. Passed verbatim to the JMAP
                API with no sanitisation. When this tool is driven by an AI
                agent that processes external content, ensure the html_body
                value originates from a trusted source to prevent prompt-
                injection attacks from causing malicious emails to be sent.
            identity_id: Sender identity ID; auto-selects first identity if omitted.
            save_to_sent: If True (default), a copy of the outgoing message is
                saved to the account's Sent mailbox, exactly as the Fastmail
                web UI does. If False, the draft is destroyed after SMTP
                hand-off and no trace is kept. The response's ``mailbox``
                field reports what happened: ``"Sent"``, ``"destroyed"``, or
                ``null`` if the account has no Sent role mailbox.
        """
        try:
            total_recipients = len(to) + len(cc or []) + len(bcc or [])
            if total_recipients > _MAX_RECIPIENTS:
                return json.dumps(
                    {
                        "error": (
                            f"Too many recipients ({total_recipients}); "
                            f"limit is {_MAX_RECIPIENTS}"
                        )
                    }
                )
            identity = _find_identity(client, identity_id)
            account_id = client.account_id

            text_body, html_body = maybe_inject_disclosure(
                text_body, html_body, to=to, cc=cc, bcc=bcc
            )

            def _addrs(addrs: list[str]) -> list[dict]:
                return [{"email": a} for a in addrs]

            drafts = client.get_mailbox_by_role("drafts")
            email_obj: dict = {
                "from": [
                    {"email": identity["email"], "name": identity.get("name", "")}
                ],
                "to": _addrs(to),
                "subject": subject,
                "keywords": {"$draft": True},
                "mailboxIds": {drafts["id"]: True},
                "bodyValues": {"body": {"value": text_body, "charset": "utf-8"}},
                "textBody": [{"partId": "body", "type": "text/plain"}],
            }
            if cc:
                email_obj["cc"] = _addrs(cc)
            if bcc:
                email_obj["bcc"] = _addrs(bcc)
            if html_body:
                email_obj["bodyValues"]["htmlBody"] = {
                    "value": html_body,
                    "charset": "utf-8",
                }
                email_obj["htmlBody"] = [{"partId": "htmlBody", "type": "text/html"}]

            submission_args, mailbox_result = _build_submission_args(
                client,
                account_id=account_id,
                identity_id=identity["id"],
                drafts_id=drafts["id"],
                save_to_sent=save_to_sent,
                from_email=identity["email"],
                recipient_emails=[*to, *(cc or []), *(bcc or [])],
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
                # Draft was created but SMTP hand-off failed. Leave the draft
                # in Drafts so the caller can retry (matches human-client UX).
                return json.dumps(
                    {"error": _humanize_submission_errors(not_created)}
                )

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
