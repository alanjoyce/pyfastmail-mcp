"""Tests for the Watson-specific disclosure footer injection."""

import pytest

from pyfastmail_mcp.tools.mail.disclosure import maybe_inject_disclosure


RESIDENT_A = "alan@example.com"
RESIDENT_B = "ruey@example.com"
OUTSIDER = "vendor@example.org"

DISCLOSURE_TEXT = "I am an AI agent on behalf of the household."
DISCLOSURE_HTML = (
    "I am an AI agent on behalf of <strong>the household</strong>."
)


@pytest.fixture
def disclosure_env(monkeypatch):
    monkeypatch.setenv(
        "PYFASTMAIL_RESIDENT_ADDRESSES",
        f"{RESIDENT_A}, {RESIDENT_B}",
    )
    monkeypatch.setenv("PYFASTMAIL_DISCLOSURE_TEXT", DISCLOSURE_TEXT)
    monkeypatch.setenv("PYFASTMAIL_DISCLOSURE_HTML", DISCLOSURE_HTML)


@pytest.fixture
def disclosure_env_text_only(monkeypatch):
    """Disclosure configured but HTML var unset — text disclosure should be
    used (HTML-escaped) for the html_body footer too."""
    monkeypatch.setenv("PYFASTMAIL_RESIDENT_ADDRESSES", RESIDENT_A)
    monkeypatch.setenv(
        "PYFASTMAIL_DISCLOSURE_TEXT", 'AI agent — "household" speaking.'
    )
    monkeypatch.delenv("PYFASTMAIL_DISCLOSURE_HTML", raising=False)


def test_no_disclosure_when_feature_unconfigured(monkeypatch):
    """Without env vars, the function is a no-op even for outside recipients."""
    monkeypatch.delenv("PYFASTMAIL_DISCLOSURE_TEXT", raising=False)
    monkeypatch.delenv("PYFASTMAIL_RESIDENT_ADDRESSES", raising=False)
    text, html = maybe_inject_disclosure(
        "Body.", "<p>Body.</p>", to=[OUTSIDER]
    )
    assert text == "Body."
    assert html == "<p>Body.</p>"


def test_no_disclosure_for_residents_only(disclosure_env):
    text, html = maybe_inject_disclosure(
        "Body.", "<p>Body.</p>", to=[RESIDENT_A, RESIDENT_B]
    )
    assert text == "Body."
    assert html == "<p>Body.</p>"


def test_disclosure_injected_when_outsider_in_to(disclosure_env):
    text, html = maybe_inject_disclosure(
        "Body.", "<p>Body.</p>", to=[OUTSIDER]
    )
    assert text.endswith(DISCLOSURE_TEXT)
    assert "Body." in text
    assert html.endswith(f"<p><em>{DISCLOSURE_HTML}</em></p>")
    assert "<p>Body.</p>" in html


def test_disclosure_injected_when_outsider_in_cc(disclosure_env):
    text, html = maybe_inject_disclosure(
        "Body.", "<p>Body.</p>", to=[RESIDENT_A], cc=[OUTSIDER]
    )
    assert text.endswith(DISCLOSURE_TEXT)
    assert html.endswith(f"<p><em>{DISCLOSURE_HTML}</em></p>")


def test_disclosure_injected_when_outsider_in_bcc(disclosure_env):
    text, html = maybe_inject_disclosure(
        "Body.", "<p>Body.</p>", to=[RESIDENT_A], bcc=[OUTSIDER]
    )
    assert text.endswith(DISCLOSURE_TEXT)
    assert html.endswith(f"<p><em>{DISCLOSURE_HTML}</em></p>")


def test_resident_match_is_case_insensitive(disclosure_env):
    text, html = maybe_inject_disclosure(
        "Body.",
        "<p>Body.</p>",
        to=[RESIDENT_A.upper(), RESIDENT_B.title()],
    )
    assert text == "Body."
    assert html == "<p>Body.</p>"


def test_resident_match_strips_whitespace(monkeypatch):
    monkeypatch.setenv(
        "PYFASTMAIL_RESIDENT_ADDRESSES", f"  {RESIDENT_A}  ,  {RESIDENT_B}  "
    )
    monkeypatch.setenv("PYFASTMAIL_DISCLOSURE_TEXT", DISCLOSURE_TEXT)
    text, _ = maybe_inject_disclosure("Body.", None, to=[RESIDENT_A])
    assert text == "Body."


def test_idempotency_text_skips_when_disclosure_present(disclosure_env):
    body = f"Body.\n\n{DISCLOSURE_TEXT}"
    text, _ = maybe_inject_disclosure(body, None, to=[OUTSIDER])
    assert text == body
    assert text.count(DISCLOSURE_TEXT) == 1


def test_idempotency_html_skips_when_disclosure_present(disclosure_env):
    body = f"<p>Body.</p><p><em>{DISCLOSURE_HTML}</em></p>"
    _, html = maybe_inject_disclosure(None, body, to=[OUTSIDER])
    assert html == body
    assert html.count(DISCLOSURE_HTML) == 1


def test_idempotency_html_skips_when_only_text_form_present(disclosure_env):
    """If the plain-text disclosure happens to appear in the HTML body
    (e.g. caller inlined it), skip injection too — avoids any double-up
    even if formatting differs from the canonical HTML form."""
    body = f"<p>Body.</p><p>{DISCLOSURE_TEXT}</p>"
    _, html = maybe_inject_disclosure(None, body, to=[OUTSIDER])
    assert html == body


def test_html_fallback_uses_escaped_text_when_html_var_unset(
    disclosure_env_text_only,
):
    text, html = maybe_inject_disclosure(
        "Body.", "<p>Body.</p>", to=[OUTSIDER]
    )
    assert text.endswith('AI agent — "household" speaking.')
    # The HTML disclosure is escaped from the text form: quotes → &quot;
    assert "&quot;household&quot;" in html
    assert "<em>" in html and "</em>" in html


def test_text_none_html_only(disclosure_env):
    text, html = maybe_inject_disclosure(None, "<p>Body.</p>", to=[OUTSIDER])
    assert text is None
    assert html.endswith(f"<p><em>{DISCLOSURE_HTML}</em></p>")


def test_html_none_text_only(disclosure_env):
    text, html = maybe_inject_disclosure("Body.", None, to=[OUTSIDER])
    assert text.endswith(DISCLOSURE_TEXT)
    assert html is None


def test_text_joiner_handles_existing_trailing_newlines(disclosure_env):
    """Whether the body ends in \\n, \\n\\n, or nothing, the disclosure
    sits exactly one blank line below."""
    for ending in ("", "\n", "\n\n"):
        body = f"Body.{ending}"
        text, _ = maybe_inject_disclosure(body, None, to=[OUTSIDER])
        assert text.endswith(f"Body.\n\n{DISCLOSURE_TEXT}")


def test_mixed_recipient_set_outsider_overrides(disclosure_env):
    text, _ = maybe_inject_disclosure(
        "Body.",
        None,
        to=[RESIDENT_A, OUTSIDER, RESIDENT_B],
    )
    assert text.endswith(DISCLOSURE_TEXT)
