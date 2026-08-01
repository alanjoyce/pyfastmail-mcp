"""CalDAV write tools — create, update, delete events."""

import json
import os
import uuid
from datetime import date, datetime
from urllib.parse import quote
from zoneinfo import ZoneInfo

import icalendar
import requests
from mcp.server.fastmcp import FastMCP

from pyfastmail_mcp.dav_client import CALDAV_BASE, DAVClient
from pyfastmail_mcp.exceptions import FastmailError


# Watson carry-patch: naive ISO datetimes used to be written as *floating*
# times (DTSTART with no TZID and no Z). RFC 5545 says a floating time is
# interpreted in the viewer's local zone, but Fastmail/Google — and any
# consumer that expands the calendar server-side — read it as UTC, so an event
# created as "2026-08-03T14:00:00" surfaced at 07:00 Pacific. A 7-hour error,
# silent, on every write that didn't spell out an offset.
#
# Anchor naive input to a real zone instead. Callers that DO pass an offset
# ("...T14:00:00-07:00") are already unambiguous and pass through untouched.
_DEFAULT_TZ = ZoneInfo(os.environ.get("PYFASTMAIL_DEFAULT_TZ", "America/Los_Angeles"))


def _parse_dt(value: str) -> datetime:
    """Parse an ISO datetime, localising naive input to the default zone."""
    dt = datetime.fromisoformat(value)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=_DEFAULT_TZ)
    return dt


def _build_vevent(
    uid: str,
    title: str,
    start: str,
    end: str,
    location: str = "",
    description: str = "",
    all_day: bool = False,
) -> icalendar.Calendar:
    cal = icalendar.Calendar()
    cal.add("prodid", "-//pyfastmail-mcp//EN")
    cal.add("version", "2.0")

    event = icalendar.Event()
    event.add("uid", uid)
    event.add("summary", title)

    if all_day:
        event.add("dtstart", date.fromisoformat(start[:10]))
        event.add("dtend", date.fromisoformat(end[:10]))
    else:
        event.add("dtstart", _parse_dt(start))
        event.add("dtend", _parse_dt(end))

    if location:
        event.add("location", location)
    if description:
        event.add("description", description)

    cal.add_component(event)
    return cal


def _event_url(calendar_href: str, uid: str) -> str:
    base = (
        f"{CALDAV_BASE}{calendar_href}"
        if not calendar_href.startswith("http")
        else calendar_href
    )
    return base.rstrip("/") + f"/{quote(uid, safe='')}.ics"


def register(server: FastMCP, dav_client: DAVClient) -> None:
    @server.tool()
    async def calendar_create_event(
        calendar_href: str,
        title: str,
        start: str,
        end: str,
        location: str = "",
        description: str = "",
        all_day: bool = False,
    ) -> str:
        """Create a new event in a CalDAV calendar.

        Args:
            calendar_href: The href of the calendar (from calendar_list_calendars).
            title: Event title/summary.
            start: Start datetime as ISO string (YYYY-MM-DDTHH:MM:SS or YYYY-MM-DD for all-day).
            end: End datetime as ISO string.
            location: Optional location string.
            description: Optional description.
            all_day: If True, treat start/end as dates (not datetimes).
        """
        try:
            uid = str(uuid.uuid4())
            cal = _build_vevent(uid, title, start, end, location, description, all_day)
            url = _event_url(calendar_href, uid)
            dav_client.validate_dav_url(url)
            dav_client.put(url, cal.to_ical().decode(), "text/calendar")
            return json.dumps({"uid": uid, "href": url.replace(CALDAV_BASE, "")})
        except (FastmailError, requests.RequestException, ValueError) as exc:
            return json.dumps({"error": str(exc)})

    @server.tool()
    async def calendar_update_event(
        href: str,
        title: str | None = None,
        start: str | None = None,
        end: str | None = None,
        location: str | None = None,
        description: str | None = None,
    ) -> str:
        """Update fields on an existing CalDAV event.

        Args:
            href: The href of the .ics resource (from calendar_list_events).
            title: New summary/title (omit to keep existing).
            start: New start datetime ISO string (omit to keep existing).
            end: New end datetime ISO string (omit to keep existing).
            location: New location (omit to keep existing).
            description: New description (omit to keep existing).
        """
        try:
            url = href if href.startswith("http") else f"{CALDAV_BASE}{href}"
            dav_client.validate_dav_url(url)
            resp = dav_client.get(url)
            etag = resp.headers.get("ETag")

            cal = icalendar.Calendar.from_ical(resp.text)
            new_cal = icalendar.Calendar()
            for key, val in cal.items():
                new_cal.add(key, val)

            for comp in cal.walk():
                if comp.name != "VEVENT":
                    continue
                event = icalendar.Event()
                for key, val in comp.items():
                    event.add(key, val)
                if title is not None:
                    event["SUMMARY"] = icalendar.vText(title)
                if start is not None:
                    event["DTSTART"] = icalendar.vDatetime(_parse_dt(start))
                if end is not None:
                    event["DTEND"] = icalendar.vDatetime(_parse_dt(end))
                if location is not None:
                    event["LOCATION"] = icalendar.vText(location)
                if description is not None:
                    event["DESCRIPTION"] = icalendar.vText(description)
                new_cal.add_component(event)
                break

            dav_client.put(url, new_cal.to_ical().decode(), "text/calendar", etag=etag)
            return json.dumps({"href": href, "updated": True})
        except (FastmailError, requests.RequestException, ValueError) as exc:
            return json.dumps({"error": str(exc)})

    @server.tool()
    async def calendar_delete_event(href: str) -> str:
        """Delete a CalDAV event by its href.

        Args:
            href: The href of the .ics resource (from calendar_list_events).
        """
        try:
            url = href if href.startswith("http") else f"{CALDAV_BASE}{href}"
            dav_client.validate_dav_url(url)
            dav_client.delete(url)
            return json.dumps({"href": href, "deleted": True})
        except (FastmailError, requests.RequestException, ValueError) as exc:
            return json.dumps({"error": str(exc)})
