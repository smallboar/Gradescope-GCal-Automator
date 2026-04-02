"""Sync Gradescope assignments to Google Calendar."""

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
GS_KEY_PATTERN = re.compile(r"\[GS:(\w+:\w+)\]")


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


def fetch_gs_events(service):
    """Fetch all upcoming calendar events that were created by this sync.

    Returns a dict mapping GS key (e.g. '12345:67890') to the event resource.
    """
    now = datetime.utcnow().isoformat() + "Z"
    events_map = {}
    page_token = None

    while True:
        result = api_call_with_retry(
            lambda pt=page_token: service.events()
            .list(
                calendarId="primary",
                timeMin=now,
                singleEvents=True,
                maxResults=2500,
                pageToken=pt,
            )
            .execute()
        )
        for event in result.get("items", []):
            desc = event.get("description", "")
            match = GS_KEY_PATTERN.search(desc)
            if match:
                events_map[match.group(1)] = event
        page_token = result.get("nextPageToken")
        if not page_token:
            break

    return events_map


# ---------------------------------------------------------------------------
# Gradescope helpers
# ---------------------------------------------------------------------------

GCAL_COLOR_NAMES = {
    "lavender": "1", "sage": "2", "grape": "3", "flamingo": "4",
    "banana": "5", "tangerine": "6", "peacock": "7", "graphite": "8",
    "blueberry": "9", "basil": "10", "tomato": "11",
}
GCAL_NUM_COLORS = 11


def _parse_color_map():
    """Parse GRADESCOPE_COLORS into a dict of lowercase course name → colorId.

    Format: 'CSE 452:tomato,CSE 447:peacock'
    Accepts color names or numeric IDs (1–11).
    """
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
    """Return a Google Calendar colorId for a course.

    Uses explicit mapping if set, otherwise auto-assigns deterministically.
    """
    key = course_name.lower()
    if key in color_map:
        return color_map[key]
    return str((hash(key) % GCAL_NUM_COLORS) + 1)


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
    """Log in to Gradescope and return a list of assignment dicts.

    Each dict has keys: name, course_name, course_id, assignment_id, due_date, gs_key.
    """
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
    existing = fetch_gs_events(service)
    color_map = _parse_color_map()

    gs_keys = {a["gs_key"] for a in assignments}
    created = updated = deleted = 0

    # Create or update
    for a in assignments:
        key = a["gs_key"]
        due_str = a["due_date"].strftime("%Y-%m-%d")
        target_color = _get_color_id(a["course_name"], color_map)

        if key not in existing:
            body = make_event_body(a, color_map)
            api_call_with_retry(
                lambda b=body: service.events()
                .insert(calendarId="primary", body=b)
                .execute()
            )
            log.info("Created: [%s] %s (due %s)", a["course_name"], a["name"], due_str)
            created += 1
        else:
            event = existing[key]
            existing_date = event.get("start", {}).get("date", "")
            existing_color = event.get("colorId", "")
            if existing_date != due_str or existing_color != target_color:
                body = make_event_body(a, color_map)
                api_call_with_retry(
                    lambda eid=event["id"], b=body: service.events()
                    .patch(calendarId="primary", eventId=eid, body=b)
                    .execute()
                )
                log.info("Updated: [%s] %s", a["course_name"], a["name"])
                updated += 1

    # Delete events no longer in Gradescope
    for key, event in existing.items():
        if key not in gs_keys:
            api_call_with_retry(
                lambda eid=event["id"]: service.events()
                .delete(calendarId="primary", eventId=eid)
                .execute()
            )
            log.info("Deleted: %s", event.get("summary", key))
            deleted += 1

    log.info("Sync complete — %d created, %d updated, %d deleted", created, updated, deleted)


if __name__ == "__main__":
    sync()
