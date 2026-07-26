"""Quoted-reply trimming for retrieved email bodies.

Ported from Watson's ``stripQuotedReply()`` (``src/lib/email-formatter.ts``) so
the MCP read path and Watson's inbound-email pipeline agree on where a
message's new content ends. Keep the two in sync if either changes.
"""

import re

# Checked in order — the first pattern to match anywhere wins, rather than the
# earliest match by position. That deliberately prefers the strong
# "On <date>, <name> wrote:" attribution over the weak leading-">" heuristic:
# a ">" can legitimately appear inside new text above the attribution line, and
# trimming at the earliest position would eat the sender's actual content.
#
# Each delimiter also anchors at the start of the body (``^`` without
# MULTILINE, so it means start-of-string). A message that opens straight into
# a quote has no new content at all, and the empty-result guard below turns
# that into "return the whole thing untouched" rather than a silent miss.
_QUOTE_DELIMITERS: tuple[re.Pattern[str], ...] = (
    # "On Mon, May 3, 2026 at 4:00 PM Alan <alan@example.com> wrote:"
    re.compile(r"(?:^|\n)\s*On\s.{1,200}\swrote:\s*\n", re.IGNORECASE),
    # Outlook-style: a long underline followed by the original headers.
    re.compile(r"(?:^|\n)_{5,}\s*\nFrom:\s", re.IGNORECASE),
    # First ">"-quoted line.
    re.compile(r"(?:^|\n)>{1,}\s"),
)


def strip_quoted_reply(body: str) -> tuple[str, bool]:
    """Trim the quoted reply chain from a plain-text body.

    Returns ``(body, stripped)``. Conservative in two ways: it only trims when
    it finds a clear delimiter, and it keeps the full body when trimming would
    leave nothing behind — a bare quote is more useful than an empty string.
    """
    for pattern in _QUOTE_DELIMITERS:
        match = pattern.search(body)
        if match:
            trimmed = body[: match.start()].strip()
            if trimmed:
                return trimmed, True
            return body.strip(), False
    return body.strip(), False
