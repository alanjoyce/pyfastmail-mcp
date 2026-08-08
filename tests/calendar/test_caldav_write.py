"""Tests for calendar_create_event, calendar_update_event, calendar_delete_event."""

import json
from unittest.mock import MagicMock, patch

import requests
from mcp.server.fastmcp import FastMCP

from pyfastmail_mcp.dav_client import CALDAV_BASE
from pyfastmail_mcp.tools.calendar.caldav_write import register


def _client():
    c = MagicMock()
    c.email = "user@example.com"
    return c


def _tool(client, name):
    server = FastMCP("test")
    register(server, client)
    return server._tool_manager._tools[name].fn


def _mock_response(text: str = "", headers: dict | None = None):
    resp = MagicMock(spec=requests.Response)
    resp.text = text
    resp.headers = headers or {}
    resp.raise_for_status = MagicMock()
    return resp


_CAL_HREF = "/dav/calendars/user/user@example.com/default/"
_EVENT_HREF = "/dav/calendars/user/user@example.com/default/abc-123.ics"

_ICAL_EVENT = """\
BEGIN:VCALENDAR
VERSION:2.0
PRODID:-//test//EN
BEGIN:VEVENT
UID:abc-123
SUMMARY:Old Title
DTSTART:20260401T140000Z
DTEND:20260401T150000Z
LOCATION:Old Room
DESCRIPTION:Old desc
END:VEVENT
END:VCALENDAR"""


# --- calendar_create_event ---


async def test_create_event_calls_put_with_ical():
    client = _client()
    client.put.return_value = _mock_response()
    fn = _tool(client, "calendar_create_event")

    with patch(
        "pyfastmail_mcp.tools.calendar.caldav_write.uuid.uuid4", return_value="uid-1"
    ):
        result = json.loads(
            await fn(
                calendar_href=_CAL_HREF,
                title="Team Sync",
                start="2026-04-01T14:00:00",
                end="2026-04-01T15:00:00",
            )
        )

    assert result["uid"] == "uid-1"
    assert "uid-1.ics" in result["href"]
    client.put.assert_called_once()
    url, content, ctype = client.put.call_args[0]
    assert "uid-1.ics" in url
    assert "Team Sync" in content
    assert ctype == "text/calendar"


async def test_create_event_all_day():
    client = _client()
    client.put.return_value = _mock_response()
    fn = _tool(client, "calendar_create_event")

    with patch(
        "pyfastmail_mcp.tools.calendar.caldav_write.uuid.uuid4", return_value="uid-2"
    ):
        result = json.loads(
            await fn(
                calendar_href=_CAL_HREF,
                title="Holiday",
                start="2026-04-01",
                end="2026-04-02",
                all_day=True,
            )
        )

    assert result["uid"] == "uid-2"
    _, content, _ = client.put.call_args[0]
    # All-day events use DATE not DATETIME
    assert "VALUE=DATE" in content or "20260401" in content


async def test_create_event_prepends_caldav_base_for_relative_href():
    client = _client()
    client.put.return_value = _mock_response()
    fn = _tool(client, "calendar_create_event")

    with patch(
        "pyfastmail_mcp.tools.calendar.caldav_write.uuid.uuid4", return_value="uid-3"
    ):
        await fn(
            calendar_href=_CAL_HREF,
            title="X",
            start="2026-04-01T10:00:00",
            end="2026-04-01T11:00:00",
        )

    url = client.put.call_args[0][0]
    assert url.startswith(CALDAV_BASE)


async def test_create_event_returns_error_on_exception():
    client = _client()
    client.put.side_effect = requests.RequestException("server error")
    fn = _tool(client, "calendar_create_event")

    result = json.loads(
        await fn(
            calendar_href=_CAL_HREF,
            title="X",
            start="2026-04-01T10:00:00",
            end="2026-04-01T11:00:00",
        )
    )

    assert "error" in result
    assert "server error" in result["error"]


# --- calendar_update_event ---


async def test_update_event_patches_title():
    client = _client()
    client.get.return_value = _mock_response(_ICAL_EVENT, headers={"ETag": '"etag-1"'})
    client.put.return_value = _mock_response()
    fn = _tool(client, "calendar_update_event")

    result = json.loads(await fn(href=_EVENT_HREF, title="New Title"))

    assert result == {"href": _EVENT_HREF, "updated": True}
    _, content, _ = client.put.call_args[0]
    assert "New Title" in content


async def test_update_event_sends_etag():
    client = _client()
    client.get.return_value = _mock_response(_ICAL_EVENT, headers={"ETag": '"etag-42"'})
    client.put.return_value = _mock_response()
    fn = _tool(client, "calendar_update_event")

    await fn(href=_EVENT_HREF, title="X")

    kwargs = client.put.call_args[1]
    assert kwargs.get("etag") == '"etag-42"'


async def test_update_event_prepends_caldav_base():
    client = _client()
    client.get.return_value = _mock_response(_ICAL_EVENT, headers={})
    client.put.return_value = _mock_response()
    fn = _tool(client, "calendar_update_event")

    await fn(href=_EVENT_HREF, title="X")

    get_url = client.get.call_args[0][0]
    assert get_url.startswith(CALDAV_BASE)


async def test_update_event_returns_error_on_exception():
    client = _client()
    client.get.side_effect = requests.RequestException("not found")
    fn = _tool(client, "calendar_update_event")

    result = json.loads(await fn(href=_EVENT_HREF, title="X"))

    assert "error" in result
    assert "not found" in result["error"]


# --- calendar_delete_event ---


async def test_delete_event_calls_delete():
    client = _client()
    client.delete.return_value = _mock_response()
    fn = _tool(client, "calendar_delete_event")

    result = json.loads(await fn(href=_EVENT_HREF))

    assert result == {"href": _EVENT_HREF, "deleted": True}
    client.delete.assert_called_once()


async def test_delete_event_prepends_caldav_base():
    client = _client()
    client.delete.return_value = _mock_response()
    fn = _tool(client, "calendar_delete_event")

    await fn(href=_EVENT_HREF)

    url = client.delete.call_args[0][0]
    assert url == f"{CALDAV_BASE}{_EVENT_HREF}"


async def test_delete_event_absolute_href_unchanged():
    abs_href = f"{CALDAV_BASE}{_EVENT_HREF}"
    client = _client()
    client.delete.return_value = _mock_response()
    fn = _tool(client, "calendar_delete_event")

    await fn(href=abs_href)

    url = client.delete.call_args[0][0]
    assert url == abs_href


async def test_delete_event_returns_error_on_exception():
    client = _client()
    client.delete.side_effect = requests.RequestException("forbidden")
    fn = _tool(client, "calendar_delete_event")

    result = json.loads(await fn(href=_EVENT_HREF))

    assert "error" in result
    assert "forbidden" in result["error"]


# --- timezone anchoring (Watson carry-patch) ---
#
# Every accepted input shape must land on a named IANA zone. A fixed-offset
# TZID like "UTC-07:00" has no VTIMEZONE to define it: clients that fall back
# to local time show the right hour by luck, ones that fall back to UTC are
# 7 hours out, and Fastmail's JMAP layer drops the event from its index.


async def _created_ical(start: str, end: str) -> str:
    client = _client()
    client.put.return_value = _mock_response()
    fn = _tool(client, "calendar_create_event")
    await fn(calendar_href=_CAL_HREF, title="Delivery", start=start, end=end)
    return client.put.call_args[0][1]


async def test_create_event_offset_aware_uses_named_zone():
    ical = await _created_ical("2026-08-08T14:00:00-07:00", "2026-08-08T18:00:00-07:00")

    assert "DTSTART;TZID=America/Los_Angeles:20260808T140000" in ical
    assert "DTEND;TZID=America/Los_Angeles:20260808T180000" in ical
    assert 'TZID="' not in ical


async def test_create_event_naive_is_read_as_local_wall_clock():
    ical = await _created_ical("2026-08-08T14:00:00", "2026-08-08T18:00:00")

    assert "DTSTART;TZID=America/Los_Angeles:20260808T140000" in ical
    assert "DTEND;TZID=America/Los_Angeles:20260808T180000" in ical


async def test_create_event_utc_input_converts_to_named_zone():
    ical = await _created_ical("2026-08-08T21:00:00Z", "2026-08-09T01:00:00Z")

    # Same instant as 2pm-6pm Pacific, expressed in the named zone.
    assert "DTSTART;TZID=America/Los_Angeles:20260808T140000" in ical
    assert "DTEND;TZID=America/Los_Angeles:20260808T180000" in ical


async def test_update_event_offset_aware_uses_named_zone():
    client = _client()
    client.get.return_value = _mock_response(_ICAL_EVENT, {"ETag": '"e1"'})
    client.put.return_value = _mock_response()
    fn = _tool(client, "calendar_update_event")

    await fn(
        href=_EVENT_HREF,
        start="2026-08-08T14:00:00-07:00",
        end="2026-08-08T18:00:00-07:00",
    )

    ical = client.put.call_args[0][1]
    assert "DTSTART;TZID=America/Los_Angeles:20260808T140000" in ical
    assert 'TZID="' not in ical
