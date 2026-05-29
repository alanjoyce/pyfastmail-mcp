"""Optional outbound disclosure footer injection.

Watson-specific carry-patch. When the household deploys this MCP behind an
AI agent (Watson), every outbound message must carry an AI-agent disclosure
line. Compliance with that rule via prompt alone is best-effort; this
module makes it deterministic.

The injection is **opt-in via environment variables** so the upstream
pyfastmail-mcp tools stay general-purpose. Without ``PYFASTMAIL_DISCLOSURE_TEXT``
set, every call is a no-op.

Configuration:
    PYFASTMAIL_DISCLOSURE_TEXT      The disclosure line for plain-text
                                    bodies. Attached as a single new line
                                    immediately under the body's final
                                    line — designed to sit inside the
                                    caller's signature block, directly
                                    below the address line, with no blank
                                    line separator.
    PYFASTMAIL_DISCLOSURE_HTML      The disclosure line for HTML bodies.
                                    Inserted inside the **final** ``<p>``
                                    of html_body (before its ``</p>``)
                                    wrapped in ``<br><small><em>…</em></small>``
                                    so it renders as a small italicised
                                    last line of the caller's signature
                                    paragraph. If unset, falls back to
                                    PYFASTMAIL_DISCLOSURE_TEXT (escaped).

The injection fires for **every** outbound — there is no recipient
classification. This is intentional: even when the recipients are the
household's own residents, the consistent presence of the disclosure
keeps Watson's identity unambiguous across the full mail trail (briefings,
replies, vendor outreach all carry the same footer). The recipient
arguments are still accepted for backward-compatibility with callers but
are not inspected.

Idempotency: if the disclosure substring is already present in the body,
the injection is skipped — so a caller that composes the disclosure in
prose (during a transitional period, in a proposal email's quoted draft,
or in error) doesn't get a doubled footer.

Fallback for HTML bodies with no ``<p>`` element: the disclosure is
appended as a new ``<p><small><em>…</em></small></p>`` paragraph at the
end of html_body. This keeps the footer present even if the caller's
HTML doesn't use the conventional paragraph structure.
"""

from __future__ import annotations

import html
import os
from typing import Iterable


def _inject_text(body: str, disclosure: str) -> str:
    """Attach the disclosure as a single new line directly under the
    body's final line. Strips any trailing whitespace from the body
    first, then adds ``\\n{disclosure}`` — producing a single line break
    between the body's last printed line and the disclosure. This is the
    canonical signature-block layout: address line, then disclosure
    line, no blank line between them."""
    stripped = body.rstrip()
    return f"{stripped}\n{disclosure}"


def _strip_cdata_wrapper(body: str) -> str:
    """Strip an XML ``<![CDATA[ … ]]>`` wrapper if present.

    Some LLM-driven callers occasionally wrap an HTML body in CDATA out
    of XML reflex — but mail clients render the literal ``]]>`` as text
    because CDATA isn't HTML syntax. Strip the wrapper if both the
    opening and closing markers are present at the body's extremities.

    Conservative: requires both ``<![CDATA[`` at the start (after any
    leading whitespace) and ``]]>`` at the end (after any trailing
    whitespace). If only one is present, the body is left untouched —
    that's a malformed input the caller should fix, not something to
    silently massage.
    """
    stripped = body.strip()
    if stripped.startswith("<![CDATA[") and stripped.endswith("]]>"):
        return stripped[len("<![CDATA["):-len("]]>")]
    return body


def _inject_html(body: str, disclosure_html: str) -> str:
    """Insert the disclosure as the last line of the final ``<p>`` in
    body. Strategy:

    0. Strip any XML ``<![CDATA[ … ]]>`` wrapper from the body — see
       ``_strip_cdata_wrapper``.
    1. Find the *last* ``</p>`` in body (after stripping any trailing
       whitespace).
    2. If found, insert ``<br><small><em>{disclosure}</em></small>``
       immediately before that ``</p>`` — the disclosure becomes the
       final line of whatever paragraph that is, typically the signature
       block.
    3. If no ``</p>`` is found, fall back to appending a new
       ``<p><small><em>{disclosure}</em></small></p>`` paragraph at the
       end of body.

    The ``<small>`` and ``<em>`` wrappers give the disclosure the
    typographical register of fine print: smaller, italic, visibly a
    meta-line rather than body content.
    """
    body = _strip_cdata_wrapper(body)
    stripped = body.rstrip()
    wrap = f"<small><em>{disclosure_html}</em></small>"
    end_idx = stripped.rfind("</p>")
    if end_idx == -1:
        return f"{stripped}\n<p>{wrap}</p>"
    return f"{stripped[:end_idx]}<br>{wrap}{stripped[end_idx:]}"


def maybe_inject_disclosure(
    text_body: str | None,
    html_body: str | None,
    to: Iterable[str] | None = None,
    cc: Iterable[str] | None = None,
    bcc: Iterable[str] | None = None,
) -> tuple[str | None, str | None]:
    """Append the disclosure footer to text_body and html_body when
    configured. Returns the (possibly modified) (text_body, html_body)
    tuple. Safe to call unconditionally — gated by env vars internally.

    Recipient arguments (``to``, ``cc``, ``bcc``) are accepted for
    backward-compatibility with callers and reserved for future use, but
    are not currently inspected — the disclosure fires on every outbound
    when the env vars are set.
    """
    disclosure_text = os.environ.get("PYFASTMAIL_DISCLOSURE_TEXT", "").strip()
    if not disclosure_text:
        return text_body, html_body

    new_text = text_body
    if text_body is not None and disclosure_text not in text_body:
        new_text = _inject_text(text_body, disclosure_text)

    new_html = html_body
    if html_body is not None:
        disclosure_html_raw = os.environ.get("PYFASTMAIL_DISCLOSURE_HTML", "").strip()
        disclosure_html = disclosure_html_raw or html.escape(disclosure_text)
        # Strip CDATA wrapper before the idempotency check, so a body
        # that arrives as ``<![CDATA[<p>…<em>{disclosure}</em>…</p>]]>``
        # is correctly recognised as already-containing the disclosure.
        cdata_stripped = _strip_cdata_wrapper(html_body)
        # Idempotency: skip if either the raw HTML disclosure form, or
        # the plain disclosure_text, already appears in the body.
        if disclosure_text not in cdata_stripped and (
            not disclosure_html_raw or disclosure_html_raw not in cdata_stripped
        ):
            new_html = _inject_html(html_body, disclosure_html)
        elif cdata_stripped is not html_body and cdata_stripped != html_body:
            # Body had a CDATA wrapper but the disclosure was already
            # inside it — return the unwrapped body anyway so the wire
            # form is correct HTML.
            new_html = cdata_stripped

    return new_text, new_html
