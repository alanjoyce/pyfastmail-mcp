"""Optional outbound disclosure footer injection.

Watson-specific carry-patch. When the household deploys this MCP behind an
AI agent (Watson), every outbound message addressed to someone outside the
household must carry an AI-agent disclosure footer. Compliance with that
rule via prompt alone is best-effort; this module makes it deterministic.

The injection is **opt-in via environment variables** so the upstream
pyfastmail-mcp tools stay general-purpose. Without ``PYFASTMAIL_DISCLOSURE_TEXT``
set, every call is a no-op.

Configuration:
    PYFASTMAIL_RESIDENT_ADDRESSES   Comma-separated list of "inside the
                                    household" email addresses. Messages
                                    whose recipient set is a subset of
                                    these get no disclosure (they're
                                    household-internal). Whitespace and
                                    case-insensitive.
    PYFASTMAIL_DISCLOSURE_TEXT      The disclosure line for plain-text
                                    bodies. Appended after a blank line.
    PYFASTMAIL_DISCLOSURE_HTML      The disclosure line for HTML bodies.
                                    Appended as ``<p><em>...</em></p>`` at
                                    the very end of html_body. If unset,
                                    falls back to PYFASTMAIL_DISCLOSURE_TEXT
                                    (escaped).

Idempotency: if the disclosure substring is already present in the body,
the injection is skipped — so a caller that composes the disclosure in
prose (during a transitional period, or in error) doesn't get a doubled
footer.
"""

from __future__ import annotations

import html
import os
from typing import Iterable


def _resident_addresses() -> set[str]:
    raw = os.environ.get("PYFASTMAIL_RESIDENT_ADDRESSES", "")
    return {a.strip().lower() for a in raw.split(",") if a.strip()}


def _normalize(addrs: Iterable[str] | None) -> set[str]:
    if not addrs:
        return set()
    return {a.strip().lower() for a in addrs if a and a.strip()}


def _has_non_resident(
    to: Iterable[str] | None,
    cc: Iterable[str] | None = None,
    bcc: Iterable[str] | None = None,
) -> bool:
    """Return True if any of the recipient addresses falls outside the
    resident set. An empty resident set means the feature is unconfigured
    (no disclosure ever fires)."""
    residents = _resident_addresses()
    if not residents:
        return False
    recipients = _normalize(to) | _normalize(cc) | _normalize(bcc)
    return bool(recipients - residents)


def maybe_inject_disclosure(
    text_body: str | None,
    html_body: str | None,
    to: Iterable[str] | None,
    cc: Iterable[str] | None = None,
    bcc: Iterable[str] | None = None,
) -> tuple[str | None, str | None]:
    """Append the disclosure footer to text_body and html_body when
    appropriate. Returns the (possibly modified) (text_body, html_body)
    tuple. Safe to call unconditionally — gated by env vars and recipient
    check internally.
    """
    disclosure_text = os.environ.get("PYFASTMAIL_DISCLOSURE_TEXT", "").strip()
    if not disclosure_text:
        return text_body, html_body

    if not _has_non_resident(to, cc, bcc):
        return text_body, html_body

    new_text = text_body
    if text_body is not None and disclosure_text not in text_body:
        # Two blank lines before the disclosure so it sits clearly below
        # any signature block the caller composed.
        joiner = "" if text_body.endswith("\n\n") else (
            "\n" if text_body.endswith("\n") else "\n\n"
        )
        new_text = f"{text_body}{joiner}{disclosure_text}"

    new_html = html_body
    if html_body is not None:
        disclosure_html_raw = os.environ.get("PYFASTMAIL_DISCLOSURE_HTML", "").strip()
        disclosure_html = disclosure_html_raw or html.escape(disclosure_text)
        # Idempotency: skip if either the raw HTML disclosure (whole or
        # near-whole), or the plain disclosure_text, already appears.
        if disclosure_text not in html_body and (
            not disclosure_html_raw or disclosure_html_raw not in html_body
        ):
            new_html = f"{html_body}\n<p><em>{disclosure_html}</em></p>"

    return new_text, new_html
