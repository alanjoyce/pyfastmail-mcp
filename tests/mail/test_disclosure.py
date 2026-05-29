"""Tests for the Watson-specific disclosure footer injection."""

import pytest

from pyfastmail_mcp.tools.mail.disclosure import maybe_inject_disclosure


RECIPIENT = "vendor@example.org"
RESIDENT = "alan@example.com"

DISCLOSURE_TEXT = "I am an AI agent for the residents."
DISCLOSURE_HTML = "I am an AI agent for the residents."


@pytest.fixture
def disclosure_env(monkeypatch):
    monkeypatch.setenv("PYFASTMAIL_DISCLOSURE_TEXT", DISCLOSURE_TEXT)
    monkeypatch.setenv("PYFASTMAIL_DISCLOSURE_HTML", DISCLOSURE_HTML)


@pytest.fixture
def disclosure_env_text_only(monkeypatch):
    """Disclosure configured but HTML var unset — text disclosure should be
    used (HTML-escaped) for the html_body footer too."""
    monkeypatch.setenv(
        "PYFASTMAIL_DISCLOSURE_TEXT", 'AI agent — "household" only.'
    )
    monkeypatch.delenv("PYFASTMAIL_DISCLOSURE_HTML", raising=False)


def test_no_disclosure_when_feature_unconfigured(monkeypatch):
    """Without env vars, the function is a no-op."""
    monkeypatch.delenv("PYFASTMAIL_DISCLOSURE_TEXT", raising=False)
    text, html = maybe_inject_disclosure(
        "Body.", "<p>Body.</p>", to=[RECIPIENT]
    )
    assert text == "Body."
    assert html == "<p>Body.</p>"


def test_disclosure_fires_for_any_outbound(disclosure_env):
    """The disclosure attaches regardless of recipient — no resident
    classification. The household-internal mailbox carries the same
    footer as an outbound to a vendor."""
    text, html = maybe_inject_disclosure(
        "Body.", "<p>Body.</p>", to=[RESIDENT]
    )
    assert text == f"Body.\n{DISCLOSURE_TEXT}"
    assert html == f"<p>Body.<br><small><em>{DISCLOSURE_HTML}</em></small></p>"


def test_disclosure_fires_with_no_recipient_args(disclosure_env):
    """Recipient args are optional — callers that don't pass them still
    get the disclosure (the feature is gated by env, not by recipients)."""
    text, html = maybe_inject_disclosure("Body.", "<p>Body.</p>")
    assert text == f"Body.\n{DISCLOSURE_TEXT}"
    assert html == f"<p>Body.<br><small><em>{DISCLOSURE_HTML}</em></small></p>"


def test_text_attached_tight_under_final_line(disclosure_env):
    """Text disclosure sits one line break below the body's last line —
    no blank line gap. This is the canonical signature-block layout."""
    body = (
        "Greetings.\n\n"
        "— Watson\n\n"
        "Watson Baxter, House Manager\n"
        "2459 Tamalpais St, Mountain View, CA"
    )
    text, _ = maybe_inject_disclosure(body, None, to=[RECIPIENT])
    expected_end = "2459 Tamalpais St, Mountain View, CA\n" + DISCLOSURE_TEXT
    assert text.endswith(expected_end)
    assert (
        "Mountain View, CA\n\n" + DISCLOSURE_TEXT
    ) not in text
    assert text.startswith("Greetings.\n\n— Watson\n\nWatson Baxter")


def test_text_rstrips_trailing_whitespace_before_attaching(disclosure_env):
    """Whether the body ends with no newline, one, or several, the
    disclosure sits one line below the last printed line."""
    for ending in ("", "\n", "\n\n", "\n\n\n  \t"):
        body = f"Final line.{ending}"
        text, _ = maybe_inject_disclosure(body, None, to=[RECIPIENT])
        assert text == f"Final line.\n{DISCLOSURE_TEXT}"


def test_html_inserted_inside_final_paragraph(disclosure_env):
    """HTML disclosure becomes the last line of the final <p> — visually
    part of the signature paragraph, not a free-floating block below."""
    body = (
        "<p>Greetings.</p>\n"
        "<p>— Watson</p>\n"
        "<p>Watson Baxter, House Manager<br>"
        "2459 Tamalpais St, Mountain View, CA</p>"
    )
    _, html = maybe_inject_disclosure(None, body, to=[RECIPIENT])
    expected_tail = (
        "2459 Tamalpais St, Mountain View, CA"
        f"<br><small><em>{DISCLOSURE_HTML}</em></small></p>"
    )
    assert html.endswith(expected_tail)
    assert f"</p>\n<p><em>{DISCLOSURE_HTML}</em></p>" not in html
    assert html.startswith("<p>Greetings.</p>\n<p>— Watson</p>\n<p>Watson Baxter")


def test_html_fallback_appends_paragraph_when_no_closing_p(disclosure_env):
    """If html_body has no </p>, append a small/em paragraph at the end
    as a graceful fallback so the disclosure is still present."""
    body = "Greetings.<br>— Watson"
    _, html = maybe_inject_disclosure(None, body, to=[RECIPIENT])
    assert html == (
        f"Greetings.<br>— Watson\n"
        f"<p><small><em>{DISCLOSURE_HTML}</em></small></p>"
    )


def test_html_strips_cdata_wrapper_before_injecting(disclosure_env):
    """An html_body wrapped in <![CDATA[ … ]]> (which mail clients render
    literally because CDATA isn't HTML syntax) gets the wrapper stripped
    before the disclosure is inserted, so the wire-line message is clean
    HTML."""
    body = (
        "<![CDATA[<p>Greetings.</p>\n"
        "<p>— Watson</p>\n"
        "<p>Watson Baxter, House Manager<br>"
        "2459 Tamalpais St, Mountain View, CA</p>]]>"
    )
    _, html = maybe_inject_disclosure(None, body, to=[RECIPIENT])
    assert "<![CDATA[" not in html
    assert "]]>" not in html
    assert html.endswith(
        f"<br><small><em>{DISCLOSURE_HTML}</em></small></p>"
    )


def test_html_strips_cdata_when_disclosure_already_inside(disclosure_env):
    """If the body's CDATA wrapper already contains the disclosure
    (idempotency case), the wrapper is still stripped so the wire form
    is clean HTML — even though injection itself is a no-op."""
    body = (
        f"<![CDATA[<p>Body.</p><p>Watson Baxter"
        f"<br><small><em>{DISCLOSURE_HTML}</em></small></p>]]>"
    )
    _, html = maybe_inject_disclosure(None, body, to=[RECIPIENT])
    assert "<![CDATA[" not in html
    assert "]]>" not in html
    assert html.count(DISCLOSURE_HTML) == 1


def test_html_leaves_partial_cdata_alone(disclosure_env):
    """Only a complete <![CDATA[ … ]]> wrapper triggers stripping. A
    half-present marker is treated as ordinary content (malformed
    input is left for the caller to fix, not silently massaged)."""
    body = "<p>Body with stray ]]> text.</p>"
    _, html = maybe_inject_disclosure(None, body, to=[RECIPIENT])
    assert "stray ]]> text" in html


def test_html_handles_trailing_whitespace_after_final_p(disclosure_env):
    body = "<p>— Watson</p>\n<p>Watson Baxter</p>   \n  "
    _, html = maybe_inject_disclosure(None, body, to=[RECIPIENT])
    assert html.endswith(
        f"<p>Watson Baxter<br><small><em>{DISCLOSURE_HTML}</em></small></p>"
    )
    assert not html.endswith(" ") and not html.endswith("\n")


def test_idempotency_text_skips_when_disclosure_present(disclosure_env):
    body = f"Body.\n\n{DISCLOSURE_TEXT}"
    text, _ = maybe_inject_disclosure(body, None, to=[RECIPIENT])
    assert text == body
    assert text.count(DISCLOSURE_TEXT) == 1


def test_idempotency_html_skips_when_disclosure_present(disclosure_env):
    body = (
        f"<p>Body.</p><p>Watson Baxter"
        f"<br><small><em>{DISCLOSURE_HTML}</em></small></p>"
    )
    _, html = maybe_inject_disclosure(None, body, to=[RECIPIENT])
    assert html == body
    assert html.count(DISCLOSURE_HTML) == 1


def test_idempotency_html_skips_when_only_text_form_present(disclosure_env):
    """If the plain-text disclosure happens to appear inside the HTML body
    (e.g. the caller inlined it), skip injection too — avoids any double-up
    even if formatting differs from the canonical HTML wrapping."""
    body = f"<p>Body.</p><p>{DISCLOSURE_TEXT}</p>"
    _, html = maybe_inject_disclosure(None, body, to=[RECIPIENT])
    assert html == body


def test_html_fallback_uses_escaped_text_when_html_var_unset(
    disclosure_env_text_only,
):
    text, html = maybe_inject_disclosure(
        "Body.", "<p>Body.</p>", to=[RECIPIENT]
    )
    assert text.endswith('AI agent — "household" only.')
    assert "&quot;household&quot;" in html
    assert "<small><em>" in html and "</em></small>" in html


def test_text_none_html_only(disclosure_env):
    text, html = maybe_inject_disclosure(None, "<p>Body.</p>", to=[RECIPIENT])
    assert text is None
    assert html == f"<p>Body.<br><small><em>{DISCLOSURE_HTML}</em></small></p>"


def test_html_none_text_only(disclosure_env):
    text, html = maybe_inject_disclosure("Body.", None, to=[RECIPIENT])
    assert text == f"Body.\n{DISCLOSURE_TEXT}"
    assert html is None
