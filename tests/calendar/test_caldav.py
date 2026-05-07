"""Tests for tools/calendar/caldav.py — calendar_list_calendars."""

import json
from unittest.mock import MagicMock

import requests
from mcp.server.fastmcp import FastMCP

from pyfastmail_mcp.tools.calendar.caldav import register


def _client():
    c = MagicMock()
    c.email = "user@example.com"
    return c


def _tool(client, name):
    server = FastMCP("test")
    register(server, client)
    return server._tool_manager._tools[name].fn


def _mock_response(xml_text: str):
    resp = MagicMock(spec=requests.Response)
    resp.text = xml_text
    resp.raise_for_status = MagicMock()
    return resp


_XML_TWO_CALENDARS = """<?xml version="1.0"?>
<D:multistatus xmlns:D="DAV:"
               xmlns:C="urn:ietf:params:xml:ns:caldav"
               xmlns:A="http://apple.com/ns/ical/">
  <D:response>
    <D:href>/dav/principals/user/user@example.com/</D:href>
    <D:propstat>
      <D:prop><D:displayname>Home</D:displayname></D:prop>
      <D:status>HTTP/1.1 200 OK</D:status>
    </D:propstat>
  </D:response>
  <D:response>
    <D:href>/dav/calendars/user/user@example.com/default/</D:href>
    <D:propstat>
      <D:prop>
        <D:displayname>Personal</D:displayname>
        <D:resourcetype><D:collection/><C:calendar/></D:resourcetype>
        <C:calendar-description>My calendar</C:calendar-description>
        <A:calendar-color>#FF0000</A:calendar-color>
      </D:prop>
      <D:status>HTTP/1.1 200 OK</D:status>
    </D:propstat>
  </D:response>
  <D:response>
    <D:href>/dav/calendars/user/user@example.com/work/</D:href>
    <D:propstat>
      <D:prop>
        <D:displayname>Work</D:displayname>
        <D:resourcetype><D:collection/><C:calendar/></D:resourcetype>
        <C:calendar-description></C:calendar-description>
        <A:calendar-color>#0000FF</A:calendar-color>
      </D:prop>
      <D:status>HTTP/1.1 200 OK</D:status>
    </D:propstat>
  </D:response>
</D:multistatus>"""

_XML_NO_CALENDARS = """<?xml version="1.0"?>
<D:multistatus xmlns:D="DAV:" xmlns:C="urn:ietf:params:xml:ns:caldav">
  <D:response>
    <D:href>/dav/principals/user/user@example.com/</D:href>
    <D:propstat>
      <D:prop><D:displayname>Home</D:displayname></D:prop>
      <D:status>HTTP/1.1 200 OK</D:status>
    </D:propstat>
  </D:response>
</D:multistatus>"""


async def test_list_calendars_returns_calendars():
    client = _client()
    home_url = "https://caldav.fastmail.com/dav/calendars/user/user@example.com/"
    client.discover_caldav_home.return_value = home_url
    client.propfind.return_value = _mock_response(_XML_TWO_CALENDARS)
    fn = _tool(client, "calendar_list_calendars")

    result = json.loads(await fn())

    client.discover_caldav_home.assert_called_once()
    assert len(result) == 2
    assert result[0]["href"] == "/dav/calendars/user/user@example.com/default/"
    assert result[0]["displayname"] == "Personal"
    assert result[0]["description"] == "My calendar"
    assert result[0]["color"] == "#FF0000"
    assert result[1]["displayname"] == "Work"
    assert result[1]["color"] == "#0000FF"


async def test_list_calendars_skips_non_calendar_resources():
    client = _client()
    home_url = "https://caldav.fastmail.com/dav/calendars/user/user@example.com/"
    client.discover_caldav_home.return_value = home_url
    client.propfind.return_value = _mock_response(_XML_NO_CALENDARS)
    fn = _tool(client, "calendar_list_calendars")

    result = json.loads(await fn())

    assert result == []


async def test_list_calendars_calls_propfind_with_correct_url():
    client = _client()
    home_url = "https://caldav.fastmail.com/dav/calendars/user/user@example.com/"
    client.discover_caldav_home.return_value = home_url
    client.propfind.return_value = _mock_response(_XML_TWO_CALENDARS)
    fn = _tool(client, "calendar_list_calendars")

    await fn()

    client.propfind.assert_called_once()
    call_args = client.propfind.call_args
    assert call_args[0][0] == home_url
    assert call_args[1]["depth"] == "1"


async def test_list_calendars_discovery_error():
    client = _client()
    import requests as req

    client.discover_caldav_home.side_effect = req.RequestException("timeout")
    fn = _tool(client, "calendar_list_calendars")

    result = json.loads(await fn())

    assert "error" in result
    assert "timeout" in result["error"]


async def test_list_calendars_returns_error_on_exception():
    client = _client()
    client.propfind.side_effect = requests.RequestException("connection refused")
    fn = _tool(client, "calendar_list_calendars")

    result = json.loads(await fn())

    assert "error" in result
    assert "connection refused" in result["error"]


async def test_list_calendars_missing_optional_fields():
    xml = """<?xml version="1.0"?>
<D:multistatus xmlns:D="DAV:" xmlns:C="urn:ietf:params:xml:ns:caldav">
  <D:response>
    <D:href>/dav/calendars/user/user@example.com/bare/</D:href>
    <D:propstat>
      <D:prop>
        <D:displayname></D:displayname>
        <D:resourcetype><D:collection/><C:calendar/></D:resourcetype>
      </D:prop>
      <D:status>HTTP/1.1 200 OK</D:status>
    </D:propstat>
  </D:response>
</D:multistatus>"""
    client = _client()
    client.propfind.return_value = _mock_response(xml)
    fn = _tool(client, "calendar_list_calendars")

    result = json.loads(await fn())

    assert len(result) == 1
    assert result[0]["displayname"] == ""
    assert result[0]["description"] == ""
    assert result[0]["color"] == ""


# --- calendar_list_events tests ---

_ICAL_ONE_EVENT = """\
BEGIN:VCALENDAR
VERSION:2.0
BEGIN:VEVENT
UID:abc-123@fastmail.com
SUMMARY:Team Meeting
DTSTART:20260325T100000Z
DTEND:20260325T110000Z
LOCATION:Conference Room
DESCRIPTION:Weekly sync
END:VEVENT
END:VCALENDAR"""

_XML_ONE_EVENT = f"""<?xml version="1.0"?>
<D:multistatus xmlns:D="DAV:" xmlns:C="urn:ietf:params:xml:ns:caldav">
  <D:response>
    <D:href>/dav/calendars/user/user@example.com/default/event1.ics</D:href>
    <D:propstat>
      <D:prop>
        <D:getetag>"etag1"</D:getetag>
        <C:calendar-data>{_ICAL_ONE_EVENT}</C:calendar-data>
      </D:prop>
      <D:status>HTTP/1.1 200 OK</D:status>
    </D:propstat>
  </D:response>
</D:multistatus>"""

_XML_NO_EVENTS = """<?xml version="1.0"?>
<D:multistatus xmlns:D="DAV:" xmlns:C="urn:ietf:params:xml:ns:caldav">
</D:multistatus>"""


async def test_list_events_returns_events():
    client = _client()
    client.report.return_value = _mock_response(_XML_ONE_EVENT)
    fn = _tool(client, "calendar_list_events")

    result = json.loads(
        await fn(
            calendar_href="/dav/calendars/user/user@example.com/default/",
            start_date="2026-03-01",
            end_date="2026-03-31",
        )
    )

    assert len(result) == 1
    assert result[0]["uid"] == "abc-123@fastmail.com"
    assert result[0]["summary"] == "Team Meeting"
    assert result[0]["location"] == "Conference Room"
    assert result[0]["description"] == "Weekly sync"
    assert "2026-03-25" in result[0]["dtstart"]


async def test_list_events_empty_calendar():
    client = _client()
    client.report.return_value = _mock_response(_XML_NO_EVENTS)
    fn = _tool(client, "calendar_list_events")

    result = json.loads(
        await fn(calendar_href="/dav/calendars/user/user@example.com/default/")
    )

    assert result == []


async def test_list_events_uses_explicit_dates():
    client = _client()
    client.report.return_value = _mock_response(_XML_NO_EVENTS)
    fn = _tool(client, "calendar_list_events")

    await fn(
        calendar_href="/dav/calendars/user/user@example.com/default/",
        start_date="2026-03-01",
        end_date="2026-03-31",
    )

    body = client.report.call_args[0][1]
    assert "20260301T000000Z" in body
    assert "20260331T000000Z" in body


async def test_list_events_prepends_caldav_base_for_relative_href():
    from pyfastmail_mcp.dav_client import CALDAV_BASE

    client = _client()
    client.report.return_value = _mock_response(_XML_NO_EVENTS)
    fn = _tool(client, "calendar_list_events")

    await fn(calendar_href="/dav/calendars/user/user@example.com/default/")

    url = client.report.call_args[0][0]
    assert url.startswith(CALDAV_BASE)


async def test_list_events_uses_absolute_href_unchanged():
    client = _client()
    client.report.return_value = _mock_response(_XML_NO_EVENTS)
    fn = _tool(client, "calendar_list_events")

    await fn(
        calendar_href="https://caldav.fastmail.com/dav/calendars/user/user@example.com/default/"
    )

    url = client.report.call_args[0][0]
    assert (
        url
        == "https://caldav.fastmail.com/dav/calendars/user/user@example.com/default/"
    )


async def test_list_events_returns_error_on_exception():
    client = _client()
    client.report.side_effect = requests.RequestException("timeout")
    fn = _tool(client, "calendar_list_events")

    result = json.loads(
        await fn(calendar_href="/dav/calendars/user/user@example.com/default/")
    )

    assert "error" in result
    assert "timeout" in result["error"]


async def test_list_events_sorted_by_dtstart():
    ical_two = """\
BEGIN:VCALENDAR
VERSION:2.0
BEGIN:VEVENT
UID:first@fastmail.com
SUMMARY:First
DTSTART:20260322T080000Z
DTEND:20260322T090000Z
END:VEVENT
BEGIN:VEVENT
UID:second@fastmail.com
SUMMARY:Second
DTSTART:20260321T080000Z
DTEND:20260321T090000Z
END:VEVENT
END:VCALENDAR"""
    xml = f"""<?xml version="1.0"?>
<D:multistatus xmlns:D="DAV:" xmlns:C="urn:ietf:params:xml:ns:caldav">
  <D:response>
    <D:href>/dav/calendars/user/user@example.com/default/two.ics</D:href>
    <D:propstat>
      <D:prop>
        <D:getetag>"etag2"</D:getetag>
        <C:calendar-data>{ical_two}</C:calendar-data>
      </D:prop>
      <D:status>HTTP/1.1 200 OK</D:status>
    </D:propstat>
  </D:response>
</D:multistatus>"""
    client = _client()
    client.report.return_value = _mock_response(xml)
    fn = _tool(client, "calendar_list_events")

    result = json.loads(
        await fn(
            calendar_href="/dav/calendars/user/user@example.com/default/",
            start_date="2026-03-01",
            end_date="2026-03-31",
        )
    )

    assert len(result) == 2
    assert result[0]["summary"] == "Second"
    assert result[1]["summary"] == "First"


# --- recurrence expansion tests ---
#
# A CalDAV time-range REPORT returns the master VEVENT (with its original
# DTSTART, possibly years before the requested window) when an RRULE produces
# any occurrence inside the window. The MCP must expand the rule and emit one
# row per real occurrence with the correct dtstart, honouring EXDATE
# exclusions and RECURRENCE-ID per-instance overrides.


def _xml_with_ical(ical_text: str, href: str = "/event.ics") -> str:
    return f"""<?xml version="1.0"?>
<D:multistatus xmlns:D="DAV:" xmlns:C="urn:ietf:params:xml:ns:caldav">
  <D:response>
    <D:href>{href}</D:href>
    <D:propstat>
      <D:prop>
        <D:getetag>"e"</D:getetag>
        <C:calendar-data>{ical_text}</C:calendar-data>
      </D:prop>
      <D:status>HTTP/1.1 200 OK</D:status>
    </D:propstat>
  </D:response>
</D:multistatus>"""


async def test_list_events_expands_weekly_rrule_with_master_before_window():
    """Master DTSTART is in 2019; RRULE produces weekly occurrences. A query
    for a single week in 2026 should return only that week's occurrence,
    with dtstart inside the window — not the master's 2019 dtstart."""
    ical = """\
BEGIN:VCALENDAR
VERSION:2.0
BEGIN:VEVENT
UID:garbage@example.com
SUMMARY:put garbage out
DTSTART:20190625T030000Z
DTEND:20190625T040000Z
RRULE:FREQ=WEEKLY;BYDAY=TU
END:VEVENT
END:VCALENDAR"""
    client = _client()
    client.report.return_value = _mock_response(_xml_with_ical(ical))
    fn = _tool(client, "calendar_list_events")

    result = json.loads(
        await fn(
            calendar_href="/dav/calendars/user/user@example.com/default/",
            start_date="2026-05-06",
            end_date="2026-05-13",
        )
    )

    assert len(result) == 1
    assert "2026-05" in result[0]["dtstart"]
    assert result[0]["uid"] == "garbage@example.com"


async def test_list_events_honours_exdate():
    ical = """\
BEGIN:VCALENDAR
VERSION:2.0
BEGIN:VEVENT
UID:cleaning@example.com
SUMMARY:Cleaning
DTSTART:20260504T180000Z
DTEND:20260504T200000Z
RRULE:FREQ=WEEKLY;COUNT=4
EXDATE:20260518T180000Z
END:VEVENT
END:VCALENDAR"""
    client = _client()
    client.report.return_value = _mock_response(_xml_with_ical(ical))
    fn = _tool(client, "calendar_list_events")

    result = json.loads(
        await fn(
            calendar_href="/dav/calendars/user/user@example.com/default/",
            start_date="2026-05-01",
            end_date="2026-06-01",
        )
    )

    starts = [e["dtstart"] for e in result]
    assert len(starts) == 3, (
        f"expected 4 weekly occurrences minus 1 EXDATE, got {starts}"
    )
    assert not any("2026-05-18" in s for s in starts)


async def test_list_events_applies_recurrence_id_override():
    """A RECURRENCE-ID override changes one occurrence's time and summary;
    the expanded result should reflect the override, not the master."""
    ical = """\
BEGIN:VCALENDAR
VERSION:2.0
BEGIN:VEVENT
UID:standup@example.com
SUMMARY:Standup
DTSTART:20260504T160000Z
DTEND:20260504T163000Z
RRULE:FREQ=WEEKLY;COUNT=3
END:VEVENT
BEGIN:VEVENT
UID:standup@example.com
RECURRENCE-ID:20260511T160000Z
SUMMARY:Standup (rescheduled)
DTSTART:20260511T173000Z
DTEND:20260511T180000Z
END:VEVENT
END:VCALENDAR"""
    client = _client()
    client.report.return_value = _mock_response(_xml_with_ical(ical))
    fn = _tool(client, "calendar_list_events")

    result = json.loads(
        await fn(
            calendar_href="/dav/calendars/user/user@example.com/default/",
            start_date="2026-05-01",
            end_date="2026-05-25",
        )
    )

    assert len(result) == 3
    overridden = [e for e in result if "rescheduled" in e["summary"]]
    assert len(overridden) == 1
    assert "2026-05-11" in overridden[0]["dtstart"]
    assert "17:30" in overridden[0]["dtstart"]


async def test_list_events_excludes_one_off_outside_window():
    """A non-recurring event whose DTSTART is outside the window must not be
    returned. Old behaviour ignored the window entirely."""
    ical = """\
BEGIN:VCALENDAR
VERSION:2.0
BEGIN:VEVENT
UID:past@example.com
SUMMARY:Old one-off
DTSTART:20200101T120000Z
DTEND:20200101T130000Z
END:VEVENT
END:VCALENDAR"""
    client = _client()
    client.report.return_value = _mock_response(_xml_with_ical(ical))
    fn = _tool(client, "calendar_list_events")

    result = json.loads(
        await fn(
            calendar_href="/dav/calendars/user/user@example.com/default/",
            start_date="2026-05-01",
            end_date="2026-06-01",
        )
    )

    assert result == []
