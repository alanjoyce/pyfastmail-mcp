"""Email action tools (mark read, move, delete, archive)."""

import json

import requests
from mcp.server.fastmcp import FastMCP

from pyfastmail_mcp.client import JMAPClient
from pyfastmail_mcp.exceptions import FastmailError, MailboxNotFoundError

_SET_ERROR_MESSAGES = {
    "tooManyKeywords": "Too many keywords on this email (server limit reached)",
    "tooManyMailboxes": "Too many mailboxes for this email (server limit reached)",
    "blobNotFound": "One or more referenced blobs were not found",
}

_SUBMISSION_ERROR_MESSAGES = {
    "forbiddenFrom": "Not permitted to send from this address",
    "forbiddenToSend": "Sending is not permitted for this account",
    "forbiddenMailFrom": "Not permitted to use this envelope sender",
    "noRecipients": "No recipients specified",
    "invalidEmail": "Email is invalid",
}


def _humanize_errors(errors: dict) -> dict:
    """Replace raw JMAP SetError types with human-readable messages."""
    out = {}
    for eid, err in errors.items():
        etype = err.get("type", "")
        msg = _SET_ERROR_MESSAGES.get(etype)
        if msg:
            out[eid] = {"type": etype, "error": msg}
        else:
            out[eid] = err
    return out


def _humanize_submission_errors(not_created: dict) -> str:
    """Return a human-readable error string for EmailSubmission notCreated errors."""
    messages = []
    for _key, err in not_created.items():
        etype = err.get("type", "unknown")
        if etype == "tooManyRecipients":
            max_r = err.get("maxRecipients")
            msg = (
                f"Too many recipients (max: {max_r})"
                if max_r
                else "Too many recipients"
            )
        elif etype == "invalidRecipients":
            addrs = err.get("invalidRecipients") or []
            msg = f"Invalid recipient addresses: {addrs}"
        else:
            msg = _SUBMISSION_ERROR_MESSAGES.get(etype, f"Submission error: {etype}")
        messages.append(msg)
    return "; ".join(messages)


def _build_submission_args(
    client: JMAPClient,
    account_id: str,
    identity_id: str,
    drafts_id: str,
    save_to_sent: bool,
    from_email: str,
    recipient_emails: list[str],
) -> tuple[dict, str | None]:
    """Build the args dict for `EmailSubmission/set`.

    Centralises the save-to-sent logic shared by `mail_send_email`,
    `mail_reply_to_email`, and `mail_forward_email`. The caller is expected to
    have already created a draft in the Drafts mailbox with the client id
    ``draft``; this function builds the submission call that references it
    via ``#draft``.

    The submission ALWAYS carries an explicit ``envelope`` derived from
    ``from_email`` and ``recipient_emails``. JMAP allows the envelope to be
    omitted (servers must derive mailFrom from the From header and rcptTo
    from To/Cc/Bcc), but in practice relying on auto-derivation has produced
    submissions that report success while delivering to nobody. Real clients
    (including the Fastmail web UI) build the envelope explicitly, and so do
    we.

    Behaviour:

    - ``save_to_sent=True`` (default): emit an ``onSuccessUpdateEmail`` block
      that atomically moves the draft from Drafts to Sent and clears the
      ``$draft`` keyword once SMTP hand-off succeeds. Returns ``("Sent")``.
    - ``save_to_sent=True`` but the account has no mailbox with
      ``role == "sent"``: log a warning, fall back to ``onSuccessDestroyEmail``,
      and return ``mailbox=None`` so the caller can surface that no Sent copy
      was saved. This preserves send-success semantics for exotic accounts.
    - ``save_to_sent=False``: emit ``onSuccessDestroyEmail`` (matching the
      pre-change behaviour). Returns ``("destroyed")``.

    Args:
        from_email: The envelope sender address (MAIL FROM).
        recipient_emails: Every envelope recipient (RCPT TO). The caller is
            responsible for collecting To + Cc + Bcc and any other delivery
            targets. Duplicates are removed here, preserving first-seen order.

    Returns:
        A tuple of ``(submission_args, mailbox_result)`` where
        ``submission_args`` is the full args dict for the ``EmailSubmission/set``
        method call (including ``accountId`` and ``envelope``), and
        ``mailbox_result`` is one of ``"Sent"``, ``"destroyed"``, or ``None``.
    """
    seen: set[str] = set()
    deduped_rcpts: list[dict] = []
    for addr in recipient_emails:
        if not addr:
            continue
        key = addr.lower()
        if key in seen:
            continue
        seen.add(key)
        deduped_rcpts.append({"email": addr})

    args: dict = {
        "accountId": account_id,
        "create": {
            "sub": {
                "emailId": "#draft",
                "identityId": identity_id,
                "envelope": {
                    "mailFrom": {"email": from_email},
                    "rcptTo": deduped_rcpts,
                },
            }
        },
    }

    if not save_to_sent:
        args["onSuccessDestroyEmail"] = ["#sub"]
        return args, "destroyed"

    try:
        sent = client.get_mailbox_by_role("sent")
    except MailboxNotFoundError:
        # Exotic account without a Sent role mailbox. Preserve send success;
        # surface the skipped-save via mailbox=None in the caller's response.
        args["onSuccessDestroyEmail"] = ["#sub"]
        return args, None

    args["onSuccessUpdateEmail"] = {
        "#sub": {
            f"mailboxIds/{drafts_id}": None,
            f"mailboxIds/{sent['id']}": True,
            "keywords/$draft": None,
        }
    }
    return args, "Sent"


def register(server: FastMCP, client: JMAPClient) -> None:
    @server.tool()
    async def mail_move_email(email_ids: list[str], mailbox_name: str) -> str:
        """Move one or more emails to a mailbox identified by name.

        Args:
            email_ids: List of JMAP email IDs to move.
            mailbox_name: Name of the destination mailbox (case-insensitive).
        """
        try:
            mailbox = client.get_mailbox_by_name(mailbox_name)
            update = {eid: {"mailboxIds": {mailbox["id"]: True}} for eid in email_ids}
            data = client.set("Email", update=update)
            moved = list((data.get("updated") or {}).keys())
            result: dict = {"moved": moved, "mailboxId": mailbox["id"]}
            not_updated = data.get("notUpdated") or {}
            if not_updated:
                result["notUpdated"] = _humanize_errors(not_updated)
            return json.dumps(result, indent=2)
        except MailboxNotFoundError as exc:
            return json.dumps({"error": str(exc)})
        except (FastmailError, requests.RequestException, ValueError) as exc:
            return json.dumps({"error": str(exc)})

    @server.tool()
    async def mail_archive_email(email_ids: list[str]) -> str:
        """Move one or more emails to the Archive mailbox.

        Args:
            email_ids: List of JMAP email IDs to archive.
        """
        try:
            archive = client.get_mailbox_by_role("archive")
            update = {eid: {"mailboxIds": {archive["id"]: True}} for eid in email_ids}
            data = client.set("Email", update=update)
            archived = list((data.get("updated") or {}).keys())
            result: dict = {"archived": archived, "mailboxId": archive["id"]}
            not_updated = data.get("notUpdated") or {}
            if not_updated:
                result["notUpdated"] = _humanize_errors(not_updated)
            return json.dumps(result, indent=2)
        except MailboxNotFoundError as exc:
            return json.dumps({"error": str(exc)})
        except (FastmailError, requests.RequestException, ValueError) as exc:
            return json.dumps({"error": str(exc)})

    @server.tool()
    async def mail_mark_email_read(email_ids: list[str], read: bool = True) -> str:
        """Set or unset the $seen flag on one or more emails.

        Args:
            email_ids: List of JMAP email IDs to update.
            read: True to mark as read, False to mark as unread (default True).
        """
        try:
            update = {eid: {"keywords/$seen": read} for eid in email_ids}
            data = client.set("Email", update=update)
            updated = list((data.get("updated") or {}).keys())
            not_updated = data.get("notUpdated") or {}
            result: dict = {"updated": updated}
            if not_updated:
                result["notUpdated"] = _humanize_errors(not_updated)
            return json.dumps(result, indent=2)
        except (FastmailError, requests.RequestException, ValueError) as exc:
            return json.dumps({"error": str(exc)})

    @server.tool()
    async def mail_pin_email(email_ids: list[str], pin: bool = True) -> str:
        """Pin or unpin one or more emails (sets the $flagged keyword).

        Pinned emails appear with a flag/star/pin icon in the mail client.

        Args:
            email_ids: List of JMAP email IDs to pin or unpin.
            pin: True to pin, False to unpin (default True).
        """
        try:
            value = True if pin else None
            update = {eid: {"keywords/$flagged": value} for eid in email_ids}
            data = client.set("Email", update=update)
            updated = list((data.get("updated") or {}).keys())
            not_updated = data.get("notUpdated") or {}
            result: dict = {"updated": updated}
            if not_updated:
                result["notUpdated"] = _humanize_errors(not_updated)
            return json.dumps(result, indent=2)
        except (FastmailError, requests.RequestException, ValueError) as exc:
            return json.dumps({"error": str(exc)})

    @server.tool()
    async def mail_delete_email(email_ids: list[str], permanent: bool = False) -> str:
        """Delete one or more emails by moving to Trash, or permanently destroy them.

        Args:
            email_ids: List of JMAP email IDs to delete.
            permanent: If True, permanently destroy emails. Default moves to Trash.
        """
        try:
            if permanent:
                data = client.set("Email", destroy=email_ids)
                destroyed = data.get("destroyed") or []
                result: dict = {"destroyed": destroyed}
                not_destroyed = data.get("notDestroyed") or {}
                if not_destroyed:
                    result["notDestroyed"] = _humanize_errors(not_destroyed)
            else:
                trash = client.get_mailbox_by_role("trash")
                update = {eid: {"mailboxIds": {trash["id"]: True}} for eid in email_ids}
                data = client.set("Email", update=update)
                moved = list((data.get("updated") or {}).keys())
                result = {"movedToTrash": moved, "mailboxId": trash["id"]}
                not_updated = data.get("notUpdated") or {}
                if not_updated:
                    result["notUpdated"] = _humanize_errors(not_updated)
            return json.dumps(result, indent=2)
        except MailboxNotFoundError as exc:
            return json.dumps({"error": str(exc)})
        except (FastmailError, requests.RequestException, ValueError) as exc:
            return json.dumps({"error": str(exc)})
