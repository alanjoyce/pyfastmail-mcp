"""CalDAV tools — calendars via CalDAV (RFC 4791)."""

import json
from datetime import date, datetime, timedelta, timezone
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

import defusedxml.ElementTree as ET
import icalendar
import recurring_ical_events
import requests
from mcp.server.fastmcp import FastMCP

from pyfastmail_mcp.dav_client import CALDAV_BASE, DAVClient
from pyfastmail_mcp.exceptions import FastmailError

_DAV_NS = "DAV:"
_CAL_NS = "urn:ietf:params:xml:ns:caldav"
_CS_NS = "http://calendarserver.org/ns/"
_APPLE_NS = "http://apple.com/ns/ical/"

_PROPFIND_CALENDARS = """<?xml version="1.0" encoding="UTF-8"?>
<D:propfind xmlns:D="DAV:" xmlns:C="urn:ietf:params:xml:ns:caldav"
            xmlns:A="http://apple.com/ns/ical/">
  <D:prop>
    <D:displayname/>
    <D:resourcetype/>
    <C:calendar-description/>
    <A:calendar-color/>
  </D:prop>
</D:propfind>"""


def _tag(ns: str, local: str) -> str:
    return f"{{{ns}}}{local}"


def _parse_calendars(xml_text: str) -> list[dict]:
    root = ET.fromstring(xml_text)
    results = []
    for response in root.iter(_tag(_DAV_NS, "response")):
        href_el = response.find(_tag(_DAV_NS, "href"))
        href = href_el.text.strip() if href_el is not None and href_el.text else ""

        resourcetype = response.find(f".//{_tag(_DAV_NS, 'resourcetype')}")
        if resourcetype is None:
            continue
        if resourcetype.find(_tag(_CAL_NS, "calendar")) is None:
            continue

        displayname_el = response.find(f".//{_tag(_DAV_NS, 'displayname')}")
        displayname = (
            displayname_el.text.strip()
            if displayname_el is not None and displayname_el.text
            else ""
        )

        desc_el = response.find(f".//{_tag(_CAL_NS, 'calendar-description')}")
        description = (
            desc_el.text.strip() if desc_el is not None and desc_el.text else ""
        )

        color_el = response.find(f".//{_tag(_APPLE_NS, 'calendar-color')}")
        color = color_el.text.strip() if color_el is not None and color_el.text else ""

        results.append(
            {
                "href": href,
                "displayname": displayname,
                "description": description,
                "color": color,
            }
        )
    return results


def _calendar_query_xml(start: str, end: str) -> str:
    return f"""<?xml version="1.0" encoding="UTF-8"?>
<C:calendar-query xmlns:D="DAV:" xmlns:C="urn:ietf:params:xml:ns:caldav">
  <D:prop>
    <D:getetag/>
    <C:calendar-data/>
  </D:prop>
  <C:filter>
    <C:comp-filter name="VCALENDAR">
      <C:comp-filter name="VEVENT">
        <C:time-range start="{start}" end="{end}"/>
      </C:comp-filter>
    </C:comp-filter>
  </C:filter>
</C:calendar-query>"""


def _parse_events(
    xml_text: str,
    window_start: datetime,
    window_end: datetime,
) -> list[dict]:
    """Parse a CalDAV REPORT response and emit one row per actual event
    occurrence inside ``[window_start, window_end)``.

    A single iCalendar resource (one ``<C:calendar-data>`` block) may contain
    a master VEVENT plus any number of ``RECURRENCE-ID`` overrides. The CalDAV
    server's ``time-range`` filter only decides whether to *include* the
    resource; it does not expand the RRULE. We expand it here using
    ``recurring_ical_events`` so each returned row corresponds to a real
    occurrence with the correct ``dtstart``, with ``EXDATE`` exclusions and
    per-instance overrides honoured.
    """
    root = ET.fromstring(xml_text)
    results: list[dict] = []
    for response in root.iter(_tag(_DAV_NS, "response")):
        href_el = response.find(_tag(_DAV_NS, "href"))
        href = href_el.text.strip() if href_el is not None and href_el.text else ""

        cal_data_el = response.find(f".//{_tag(_CAL_NS, 'calendar-data')}")
        if cal_data_el is None or not cal_data_el.text:
            continue

        try:
            cal = icalendar.Calendar.from_ical(cal_data_el.text)
            occurrences = recurring_ical_events.of(cal).between(
                window_start, window_end
            )
        except (FastmailError, requests.RequestException, ValueError):
            continue

        for comp in occurrences:
            if comp.name != "VEVENT":
                continue
            dtstart = comp.get("DTSTART")
            dtend = comp.get("DTEND")
            results.append(
                {
                    "href": href,
                    "uid": str(comp.get("UID", "")),
                    "summary": str(comp.get("SUMMARY", "")),
                    "dtstart": str(dtstart.dt) if dtstart else "",
                    "dtend": str(dtend.dt) if dtend else "",
                    "location": str(comp.get("LOCATION", "")),
                    "description": str(comp.get("DESCRIPTION", "")),
                }
            )
    results.sort(key=lambda e: e["dtstart"])
    return results


def _parse_window(
    start_date: str | None,
    end_date: str | None,
    tz: str,
) -> tuple[datetime, datetime]:
    """Convert the user-supplied window into a ``[start_dt, end_dt)`` UTC pair.

    Bare-date strings (``YYYY-MM-DD``) are anchored at midnight in ``tz``,
    not UTC. Without this, a Pacific-time caller passing
    ``end_date="2026-05-13"`` would lose every event between 17:00 PT on
    2026-05-12 and 23:59 PT on 2026-05-13 — about a day's worth of
    real-world events at the boundary of the requested window.

    ``end_date`` is treated as **inclusive**: the upper bound becomes
    midnight of ``end_date + 1 day`` in ``tz``. So a query for
    ``start_date="2026-05-06", end_date="2026-05-13"`` with
    ``tz="America/Los_Angeles"`` covers the entire week from 2026-05-06
    00:00 PT through 2026-05-13 23:59 PT.

    If a caller passes a full ISO datetime (``YYYY-MM-DDTHH:MM:SS`` or
    ``...±HH:MM``) the value is honoured as-is — its own offset wins, and
    if it's tz-naive it's localized in ``tz``.
    """
    try:
        zone = ZoneInfo(tz)
    except ZoneInfoNotFoundError as exc:
        raise ValueError(f"Unknown timezone: {tz}") from exc

    def _localize(s: str, *, anchor_to_next_day: bool) -> datetime:
        parsed = datetime.fromisoformat(s)
        is_date_only = "T" not in s and " " not in s
        if is_date_only and anchor_to_next_day:
            parsed = parsed + timedelta(days=1)
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=zone)
        return parsed.astimezone(timezone.utc)

    if start_date:
        start_dt = _localize(start_date, anchor_to_next_day=False)
    else:
        now_utc = datetime.now(tz=timezone.utc)
        local_today = now_utc.astimezone(zone).replace(
            hour=0, minute=0, second=0, microsecond=0
        )
        start_dt = local_today.astimezone(timezone.utc)

    if end_date:
        end_dt = _localize(end_date, anchor_to_next_day=True)
    else:
        end_dt = start_dt + timedelta(days=7)

    return start_dt, end_dt


def register(server: FastMCP, dav_client: DAVClient) -> None:
    @server.tool()
    async def calendar_list_calendars() -> str:
        """List all CalDAV calendars for the authenticated Fastmail account."""
        try:
            home_url = dav_client.discover_caldav_home()
            dav_client.validate_dav_url(home_url)
            resp = dav_client.propfind(home_url, depth="1", body=_PROPFIND_CALENDARS)
            calendars = _parse_calendars(resp.text)
            return json.dumps(calendars, indent=2)
        except (FastmailError, requests.RequestException, ValueError) as exc:
            return json.dumps({"error": str(exc)})

    @server.tool()
    async def calendar_list_events(
        calendar_href: str,
        start_date: str | None = None,
        end_date: str | None = None,
        tz: str = "UTC",
    ) -> str:
        """List events in a CalDAV calendar within a date range.

        Args:
            calendar_href: The href of the calendar (from calendar_list_calendars).
            start_date: ISO date string. Bare dates (YYYY-MM-DD) are anchored
                at midnight in ``tz``. Full datetimes are honoured as given.
                Defaults to today (in ``tz``).
            end_date: ISO date string. Bare dates are **inclusive** — the
                upper bound becomes midnight of (end_date + 1 day) in ``tz``.
                Defaults to seven days after ``start_date``.
            tz: IANA timezone name used to anchor bare dates (e.g.
                ``"America/Los_Angeles"``, ``"Europe/London"``). Defaults to
                ``"UTC"``. Has no effect on ISO datetimes that already carry
                an offset.
        """
        try:
            start_dt, end_dt = _parse_window(start_date, end_date, tz)

            start_str = start_dt.strftime("%Y%m%dT%H%M%SZ")
            end_str = end_dt.strftime("%Y%m%dT%H%M%SZ")

            url = (
                f"{CALDAV_BASE}{calendar_href}"
                if not calendar_href.startswith("http")
                else calendar_href
            )
            dav_client.validate_dav_url(url)
            body = _calendar_query_xml(start_str, end_str)
            resp = dav_client.report(url, body)
            events = _parse_events(resp.text, start_dt, end_dt)
            return json.dumps(events, indent=2)
        except (FastmailError, requests.RequestException, ValueError) as exc:
            return json.dumps({"error": str(exc)})
