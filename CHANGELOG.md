# Changelog

## 0.3.7 (2026-07-26)

### Added (Watson carry-patch)
- `tools/mail/quoting.py` — `mail_get_email` now trims the quoted reply
  chain from plain-text bodies by default, returning only the message's
  new content. Pass `include_quoted_text=True` for the raw body; the
  response carries `quotedTextStripped` so a caller can tell whether
  anything was removed and re-fetch if it needs the original. Idea taken
  from Fastmail's official MCP `read_email`, which does the same; the
  delimiters are ported from Watson's own `stripQuotedReply()`
  (`src/lib/email-formatter.ts`) so the MCP read path and Watson's
  inbound-email pipeline agree on where new content ends. Keep the two in
  sync if either changes.

  Deliberately conservative in three ways. Delimiters are checked in
  order rather than by earliest position, so the strong
  "On &lt;date&gt; … wrote:" attribution wins over the weak leading-"&gt;"
  heuristic — a "&gt;" quoted inline above the attribution line is part of
  the sender's new text and must not truncate it. HTML bodies are never
  trimmed, since the delimiters are text heuristics and HTML clients quote
  with `<blockquote>` (`_body_is_html` mirrors `_extract_body`'s selection
  rule, including its fallback to text when `prefer_html` finds no HTML
  part — that fallback *is* trimmed). And a body that trims to nothing is
  returned whole: a bare quote carries more than an empty string. This
  last case is where the Python and TypeScript versions differ in route
  though not in output — the TS patterns require a leading newline and so
  simply don't match a body that opens on a quote, while these anchor at
  string start and fall through the empty-result guard. 8 quoting tests,
  6 new `mail_get_email` tests, 413 in the full suite.

## 0.3.6 (2026-06-06)

### Changed (Watson carry-patch)
- `tools/mail/disclosure.py` — disclosure placement now adapts to whether
  the body ends with the caller's signature block. When a signature is
  present (detected via the distinctive sign-off markers in
  `_ends_with_signature` — "Watson Baxter", "House Manager", "— Watson"),
  the disclosure attaches tight as the block's final line, unchanged from
  before. When there's **no** signature — e.g. a terse one-line reply that
  skipped the block — the disclosure now lands on its own separated line
  (blank line in text; a standalone `<p><small><em>…</em></small></p>` in
  HTML) instead of being jammed onto the last body sentence, where it read
  as an awkward run-on. Tests updated to cover both branches; 19 disclosure
  tests, 398 in the full suite.

### Fixed (Watson carry-patch)
- `tools/mail/disclosure.py` strips a complete
  `<![CDATA[ … ]]>` wrapper from `html_body` before injecting the
  disclosure. CDATA is XML syntax; mail clients render the closing
  `]]>` as literal text. This guard catches the (occasional) case where
  an LLM caller wraps its HTML body in CDATA by reflex, so the
  wire-line message is clean HTML regardless. Only a complete
  wrapper triggers stripping — a half-present marker is left alone
  (malformed input belongs to the caller). 17 disclosure tests, 396 in
  the full suite.

## 0.3.4 (2026-05-28)

### Changed (Watson carry-patch)
- `tools/mail/disclosure.py` — two changes:
  - **Placement.** Disclosure now sits inside the caller's signature
    block, not below it as a separate paragraph:
    - **Text:** attaches as a single new line under the body's last
      printed line (after rstrip), so it sits directly under the
      address line with no blank-line gap.
    - **HTML:** inserted before the **final** `</p>` in html_body,
      wrapped in `<br><small><em>…</em></small>`. Renders as a small
      italicised last line of the caller's signature paragraph —
      typographically registered as fine-print, not body content.
    - **Fallback** (no `</p>` in html_body): appended as a new
      `<p><small><em>…</em></small></p>` paragraph at the end, so the
      disclosure is still present for non-paragraph HTML.
  - **No more recipient classification.** The previous gating —
    "skip when every recipient is in `PYFASTMAIL_RESIDENT_ADDRESSES`"
    — is removed. The disclosure fires on every outbound when the env
    vars are set. Rationale: a consistent footer across the full mail
    trail (briefings, resident replies, vendor outreach) keeps the
    agent's identity unambiguous in every inbox. The
    `PYFASTMAIL_RESIDENT_ADDRESSES` env var is no longer read; the
    recipient args to `maybe_inject_disclosure` are accepted for
    backward-compatibility but not inspected.

  Test suite updated to reflect both changes; 14 tests for the
  disclosure module, 393 in the full suite.

## 0.3.3 (2026-05-28)

### Added (Watson carry-patch)
- `tools/mail/disclosure.py` — opt-in AI-agent disclosure footer injection
  for `mail_send_email`, `mail_reply_to_email`, and `mail_forward_email`.
  When `PYFASTMAIL_DISCLOSURE_TEXT` is set, every outbound whose recipient
  set includes a non-resident (per `PYFASTMAIL_RESIDENT_ADDRESSES`) gets
  the disclosure appended to both `text_body` and `html_body`. The HTML
  form is wrapped in `<p><em>…</em></p>` at the end; if
  `PYFASTMAIL_DISCLOSURE_HTML` is unset, the text form is HTML-escaped
  and used. Idempotent — skips injection when the disclosure substring
  is already present in the body (so callers transitioning from prose
  to runtime enforcement don't double up).
- 15 unit tests in `tests/mail/test_disclosure.py` covering the recipient
  classifier, env-var gating, idempotency, HTML/text variants, and
  whitespace / case normalisation.

Without env vars set the module is a no-op — upstream and other consumers
of this fork get no behavioural change. Carry-patch retained on the
`alanjoyce/pyfastmail-mcp` fork; not for upstream PR.

## 0.3.2 (2026-03-31)

### Documentation
- Verified all module and function docstrings follow Wholeshoot convention
- All public functions documented with imperative summaries and parameter descriptions
- No changes to code logic or behavior

## 0.3.1 (2026-03-24)

### Fixed
- Removed dead CardDAV code from `DAVClient` (constants, discovery methods, SSRF allowlist entry)

## 0.3.0 (2026-03-23)

### Added
- `mail_search_emails` — `in_mailbox` parameter to filter search by mailbox (JMAP `inMailbox`)

### Fixed
- `DAVClient` now honors explicit empty strings for email/password instead of falling through to env vars
- `__version__` now reads from package metadata (single source of truth in `pyproject.toml`)

## 0.2.1 (2026-03-23)

### Breaking Changes
- Contacts migrated from CardDAV to JMAP (RFC 9610)
- CalDAV/WebDAV now optional — server starts with just mail + contacts if no app password set

### Added
- `mail_pin_email` — pin/unpin emails
- `mail_search_snippets` — highlighted search result snippets
- `mail_export_email` — download raw `.eml` files
- `mail_import_email` — import `.eml` into mail store
- `mail_parse_email` — parse a blob as email without importing
- `mail_set_identity` — create/update/delete sender identities
- `mail_get_email` — optional `headers` param (e.g. SimpleLogin headers)

## 0.1.0 (2026-03-22)

Initial release. 42 tools across JMAP (mail), CardDAV (contacts), CalDAV (calendars), and WebDAV (files).
