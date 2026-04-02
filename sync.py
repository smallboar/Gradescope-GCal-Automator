"""Sync Gradescope assignments to Google Calendar."""

import hashlib
import json
import os
import re
import time
import logging
from datetime import datetime, timedelta

from dotenv import load_dotenv
from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from googleapiclient.discovery import build
from googleapiclient.errors import HttpError
from gradescopeapi.classes.connection import GSConnection

load_dotenv(os.path.join(os.path.dirname(__file__), ".env"))

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
log = logging.getLogger(__name__)

SCOPES = ["https://www.googleapis.com/auth/calendar"]
TOKEN_FILE = os.path.join(os.path.dirname(__file__), "token.json")
CREDENTIALS_FILE = os.path.join(os.path.dirname(__file__), "credentials.json")
CALENDARS_FILE = os.path.join(os.path.dirname(__file__), "calendars.json")
GS_KEY_PATTERN = re.compile(r"\[GS:(\w+:\w+)\]")

GCAL_COLOR_NAMES = {
    "lavender": "1", "sage": "2", "grape": "3", "flamingo": "4",
    "banana": "5", "tangerine": "6", "peacock": "7", "graphite": "8",
    "blueberry": "9", "basil": "10", "tomato": "11",
}
# Auto-assign order: blueberry, basil, grape, peacock, tomato, lavender, sage, flamingo, banana, tangerine, graphite
GCAL_AUTO_COLORS = ["9", "10", "3", "7", "11", "1", "2", "4", "5", "6", "8"]


# ---------------------------------------------------------------------------
# Google Calendar helpers
# ---------------------------------------------------------------------------

def get_calendar_service():
    """Return an authenticated Google Calendar service."""
    creds = Credentials.from_authorized_user_file(TOKEN_FILE, SCOPES)
    if creds.expired and creds.refresh_token:
        creds.refresh(Request())
        with open(TOKEN_FILE, "w") as f:
            f.write(creds.to_json())
    return build("calendar", "v3", credentials=creds)


def api_call_with_retry(fn, max_retries=3):
    """Execute a Google API call with exponential backoff on rate limits."""
    for attempt in range(max_retries):
        try:
            return fn()
        except HttpError as e:
            if e.resp.status in (429, 500, 503) and attempt < max_retries - 1:
                wait = 2 ** attempt
                log.warning("API error %s, retrying in %ds…", e.resp.status, wait)
                time.sleep(wait)
            else:
                raise


# ---------------------------------------------------------------------------
# Per-course calendar management
# ---------------------------------------------------------------------------

def _load_calendar_map():
    """Load the course_name → calendar_id mapping from disk."""
    if os.path.exists(CALENDARS_FILE):
        with open(CALENDARS_FILE) as f:
            return json.load(f)
    return {}


def _save_calendar_map(cal_map):
    """Persist the course_name → calendar_id mapping to disk."""
    with open(CALENDARS_FILE, "w") as f:
        json.dump(cal_map, f, indent=2)


def _get_share_emails():
    """Parse GRADESCOPE_SHARE_EMAILS into a list of email addresses."""
    raw = os.environ.get("GRADESCOPE_SHARE_EMAILS", "").strip()
    if not raw:
        return []
    return [e.strip() for e in raw.split(",") if e.strip()]


def _share_calendar(service, calendar_id, emails):
    """Share a calendar with the given email addresses (reader role)."""
    for email in emails:
        try:
            api_call_with_retry(
                lambda e=email: service.acl()
                .insert(
                    calendarId=calendar_id,
                    body={"role": "reader", "scope": {"type": "user", "value": e}},
                )
                .execute()
            )
            log.info("Shared calendar %s with %s", calendar_id, email)
        except HttpError as e:
            if e.resp.status == 409:
                pass  # already shared
            else:
                log.warning("Could not share calendar with %s: %s", email, e)


def _calendar_exists(service, calendar_id):
    """Check if a calendar still exists on Google."""
    try:
        api_call_with_retry(
            lambda: service.calendars().get(calendarId=calendar_id).execute()
        )
        return True
    except HttpError as e:
        if e.resp.status == 404:
            return False
        raise


def get_or_create_calendar(service, course_name, cal_map, color_map):
    """Return the calendar ID for a course, creating it if needed."""
    if course_name in cal_map:
        cal_id = cal_map[course_name]
        if _calendar_exists(service, cal_id):
            return cal_id
        log.warning("Calendar for %s was deleted externally, recreating", course_name)

    body = {"summary": course_name, "timeZone": "America/Los_Angeles"}
    result = api_call_with_retry(
        lambda b=body: service.calendars().insert(body=b).execute()
    )
    cal_id = result["id"]
    log.info("Created calendar: %s (%s)", course_name, cal_id)

    # Set calendar color on the calendar list entry
    color_id = _get_color_id(course_name, color_map)
    try:
        api_call_with_retry(
            lambda: service.calendarList()
            .patch(
                calendarId=cal_id,
                body={"colorId": color_id},
            )
            .execute()
        )
    except HttpError:
        pass  # non-critical

    # Share with configured emails
    emails = _get_share_emails()
    if emails:
        _share_calendar(service, cal_id, emails)

    cal_map[course_name] = cal_id
    _save_calendar_map(cal_map)
    return cal_id


def fetch_gs_events(service, calendar_ids):
    """Fetch all events from the given calendars that were created by this sync.

    Returns a dict mapping GS key to (calendar_id, event) tuple.
    """
    events_map = {}

    for cal_id in calendar_ids:
        page_token = None
        while True:
            try:
                result = api_call_with_retry(
                    lambda cid=cal_id, pt=page_token: service.events()
                    .list(
                        calendarId=cid,
                        singleEvents=True,
                        maxResults=2500,
                        pageToken=pt,
                    )
                    .execute()
                )
            except HttpError as e:
                if e.resp.status == 404:
                    log.warning("Calendar %s not found, skipping", cal_id)
                    break
                raise
            for event in result.get("items", []):
                desc = event.get("description", "")
                match = GS_KEY_PATTERN.search(desc)
                if match:
                    events_map[match.group(1)] = (cal_id, event)
            page_token = result.get("nextPageToken")
            if not page_token:
                break

    return events_map


# ---------------------------------------------------------------------------
# Color helpers
# ---------------------------------------------------------------------------

def _parse_color_map():
    """Parse GRADESCOPE_COLORS into a dict of lowercase course name → colorId."""
    raw = os.environ.get("GRADESCOPE_COLORS", "").strip()
    if not raw:
        return {}
    color_map = {}
    for entry in raw.split(","):
        if ":" not in entry:
            continue
        course, color = entry.rsplit(":", 1)
        color = color.strip().lower()
        color_id = GCAL_COLOR_NAMES.get(color, color)
        color_map[course.strip().lower()] = color_id
    return color_map


def _get_color_id(course_name, color_map):
    """Return a Google Calendar colorId for a course."""
    key = course_name.lower()
    if key in color_map:
        return color_map[key]
    # Stable hash so color doesn't change between runs
    h = int(hashlib.md5(key.encode()).hexdigest(), 16)
    return GCAL_AUTO_COLORS[h % len(GCAL_AUTO_COLORS)]


# ---------------------------------------------------------------------------
# Gradescope helpers
# ---------------------------------------------------------------------------

def _parse_term_filter():
    """Parse GRADESCOPE_TERM into (semester, year) or return None."""
    term = os.environ.get("GRADESCOPE_TERM", "").strip()
    if not term:
        return None
    parts = term.rsplit(" ", 1)
    if len(parts) != 2:
        log.warning("GRADESCOPE_TERM=%r doesn't match 'Season Year' format, ignoring", term)
        return None
    return parts[0].lower(), parts[1]


def _parse_courses_filter():
    """Parse GRADESCOPE_COURSES into a set of lowercase course names, or None."""
    raw = os.environ.get("GRADESCOPE_COURSES", "").strip()
    if not raw:
        return None
    return {name.strip().lower() for name in raw.split(",") if name.strip()}


def _short_course_name(name):
    """Truncate course name at the first slash. 'CSE 452 / CSE M 552 -26wi' → 'CSE 452'."""
    return name.split("/")[0].strip()


def _course_matches(course, term_filter, courses_filter):
    """Return True if a course passes the active filter."""
    if term_filter:
        semester, year = term_filter
        return course.semester.lower() == semester and course.year == year
    if courses_filter:
        return course.name.lower() in courses_filter
    return True


def fetch_gradescope_assignments():
    """Log in to Gradescope and return a list of assignment dicts."""
    email = os.environ["GRADESCOPE_EMAIL"]
    password = os.environ["GRADESCOPE_PASSWORD"]

    conn = GSConnection()
    conn.login(email, password)
    log.info("Logged in to Gradescope")

    term_filter = _parse_term_filter()
    courses_filter = _parse_courses_filter()
    if term_filter:
        log.info("Filtering by term: %s %s", term_filter[0].title(), term_filter[1])
    elif courses_filter:
        log.info("Filtering by courses: %s", ", ".join(sorted(courses_filter)))
    else:
        log.info("No course filter set — syncing all courses")

    courses = conn.account.get_courses()
    assignments = []

    for role in ("instructor", "student"):
        for course_id, course in courses.get(role, {}).items():
            if not _course_matches(course, term_filter, courses_filter):
                continue

            try:
                course_assignments = conn.account.get_assignments(course_id)
            except Exception:
                log.warning("Could not fetch assignments for %s (id=%s), skipping", course.name, course_id)
                continue

            if not course_assignments:
                continue

            for a in course_assignments:
                if a.due_date is None:
                    continue
                assignments.append(
                    {
                        "name": a.name,
                        "course_name": _short_course_name(course.name),
                        "course_id": course_id,
                        "assignment_id": a.assignment_id,
                        "due_date": a.due_date,
                        "late_due_date": a.late_due_date,
                        "gs_key": f"{course_id}:{a.assignment_id}",
                    }
                )

    log.info("Fetched %d assignments from Gradescope", len(assignments))
    return assignments


# ---------------------------------------------------------------------------
# Sync logic
# ---------------------------------------------------------------------------

def _fmt_datetime(dt):
    """Format a datetime as 'YYYY/MM/DD HH:MM AM/PM'."""
    return dt.strftime("%Y/%m/%d %I:%M %p")


def make_event_body(a, color_map):
    """Build a Google Calendar event resource for an assignment."""
    due = a["due_date"]
    date_str = due.strftime("%Y-%m-%d")

    desc_lines = [f"Due: {_fmt_datetime(due)}"]
    if a.get("late_due_date"):
        desc_lines.append(f"Late Due: {_fmt_datetime(a['late_due_date'])}")
    desc_lines.append(f"Assignment: {a['name']}")
    desc_lines.append(f"Course: {a['course_name']}")
    desc_lines.append("")
    desc_lines.append(f"[GS:{a['gs_key']}]")

    return {
        "summary": f"[{a['course_name']}] {a['name']}",
        "description": "\n".join(desc_lines),
        "start": {"date": date_str},
        "end": {"date": date_str},
        "colorId": _get_color_id(a["course_name"], color_map),
        "reminders": {
            "useDefault": False,
            "overrides": [{"method": "popup", "minutes": 24 * 60}],
        },
    }


def sync():
    """Run a full sync from Gradescope to Google Calendar."""
    assignments = fetch_gradescope_assignments()
    service = get_calendar_service()
    color_map = _parse_color_map()
    cal_map = _load_calendar_map()

    # Determine which courses we have and ensure calendars exist
    course_names = {a["course_name"] for a in assignments}
    for course_name in course_names:
        get_or_create_calendar(service, course_name, cal_map, color_map)

    # Build a map of course_name → calendar_id for quick lookup
    course_to_cal = {name: cal_map[name] for name in course_names if name in cal_map}

    # Fetch existing GS events from all managed calendars
    all_cal_ids = list(set(cal_map.values()))
    existing = fetch_gs_events(service, all_cal_ids)

    gs_keys = {a["gs_key"] for a in assignments}
    created = updated = deleted = 0

    # Create or update
    for a in assignments:
        key = a["gs_key"]
        due_str = a["due_date"].strftime("%Y-%m-%d")
        target_color = _get_color_id(a["course_name"], color_map)
        target_cal_id = course_to_cal.get(a["course_name"], "primary")

        if key not in existing:
            body = make_event_body(a, color_map)
            api_call_with_retry(
                lambda cid=target_cal_id, b=body: service.events()
                .insert(calendarId=cid, body=b)
                .execute()
            )
            log.info("Created: [%s] %s (due %s)", a["course_name"], a["name"], due_str)
            created += 1
        else:
            old_cal_id, event = existing[key]
            existing_date = event.get("start", {}).get("date", "")
            existing_color = event.get("colorId", "")
            needs_update = existing_date != due_str or existing_color != target_color

            # If the event is in the wrong calendar, delete and recreate
            if old_cal_id != target_cal_id:
                api_call_with_retry(
                    lambda cid=old_cal_id, eid=event["id"]: service.events()
                    .delete(calendarId=cid, eventId=eid)
                    .execute()
                )
                body = make_event_body(a, color_map)
                api_call_with_retry(
                    lambda cid=target_cal_id, b=body: service.events()
                    .insert(calendarId=cid, body=b)
                    .execute()
                )
                log.info("Moved: [%s] %s → calendar %s", a["course_name"], a["name"], a["course_name"])
                updated += 1
            elif needs_update:
                body = make_event_body(a, color_map)
                api_call_with_retry(
                    lambda cid=old_cal_id, eid=event["id"], b=body: service.events()
                    .patch(calendarId=cid, eventId=eid, body=b)
                    .execute()
                )
                log.info("Updated: [%s] %s", a["course_name"], a["name"])
                updated += 1

    # Delete events no longer in Gradescope
    for key, (cal_id, event) in existing.items():
        if key not in gs_keys:
            api_call_with_retry(
                lambda cid=cal_id, eid=event["id"]: service.events()
                .delete(calendarId=cid, eventId=eid)
                .execute()
            )
            log.info("Deleted: %s", event.get("summary", key))
            deleted += 1

    log.info("Sync complete — %d created, %d updated, %d deleted", created, updated, deleted)


if __name__ == "__main__":
    sync()
