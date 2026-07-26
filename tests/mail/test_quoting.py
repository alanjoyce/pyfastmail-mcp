"""Tests for quoted-reply trimming (Watson carry-patch)."""

from pyfastmail_mcp.tools.mail.quoting import strip_quoted_reply


def test_gmail_style_attribution():
    body = (
        "Thanks — Tuesday works.\n"
        "\n"
        "On Mon, May 3, 2026 at 4:00 PM Alan <alan@example.com> wrote:\n"
        "> Can you make Tuesday?\n"
    )
    trimmed, stripped = strip_quoted_reply(body)
    assert trimmed == "Thanks — Tuesday works."
    assert stripped is True


def test_outlook_style_underline_and_headers():
    body = (
        "Confirmed for Tuesday.\n"
        "\n"
        "________________\n"
        "From: Alan Joyce <alan@example.com>\n"
        "Sent: Monday, May 3, 2026\n"
    )
    trimmed, stripped = strip_quoted_reply(body)
    assert trimmed == "Confirmed for Tuesday."
    assert stripped is True


def test_bare_quoted_lines():
    body = "Sounds good.\n> original message\n> second line\n"
    trimmed, stripped = strip_quoted_reply(body)
    assert trimmed == "Sounds good."
    assert stripped is True


def test_no_delimiter_returns_whole_body_trimmed():
    body = "\n  Just a plain message with no quoting.  \n"
    trimmed, stripped = strip_quoted_reply(body)
    assert trimmed == "Just a plain message with no quoting."
    assert stripped is False


def test_attribution_wins_over_earlier_quote_marker():
    """Pattern order is deliberate: a ">" inside new text above the
    attribution line must not truncate the sender's actual content."""
    body = (
        "You asked:\n"
        "> should we reschedule?\n"
        "Yes — let's move it to Tuesday.\n"
        "\n"
        "On Mon, May 3, 2026 at 4:00 PM Alan <alan@example.com> wrote:\n"
        "> original\n"
    )
    trimmed, stripped = strip_quoted_reply(body)
    assert trimmed.endswith("Yes — let's move it to Tuesday.")
    assert "should we reschedule?" in trimmed
    assert stripped is True


def test_body_that_is_entirely_quoted_is_preserved():
    """Trimming to nothing is worse than returning the quote — a bare
    quoted body still carries information."""
    body = "> the whole message is quoted\n> nothing new above it\n"
    trimmed, stripped = strip_quoted_reply(body)
    assert trimmed == body.strip()
    assert stripped is False


def test_empty_body():
    assert strip_quoted_reply("") == ("", False)


def test_attribution_requires_surrounding_newlines():
    """A single-line body mentioning 'wrote:' shouldn't be trimmed."""
    body = "I saw what Alan wrote: he agreed."
    trimmed, stripped = strip_quoted_reply(body)
    assert trimmed == body
    assert stripped is False
