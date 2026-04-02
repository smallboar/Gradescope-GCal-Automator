"""Delete all Google Calendar events and calendars created by the Gradescope sync script.

Removes events whose description contains a [GS:...] tag and deletes the per-course calendars.
"""

import json
import os
import re
import time
import logging

from dotenv import load_dotenv
from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from googleapiclient.discovery import build
from googleapiclient.errors import HttpError

load_dotenv(os.path.join(os.path.dirname(__file__), ".env"))

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
log = logging.getLogger(__name__)

SCOPES = ["https://www.googleapis.com/auth/calendar"]
TOKEN_FILE = os.path.join(os.path.dirname(__file__), "token.json")
CALENDARS_FILE = os.path.join(os.path.dirname(__file__), "calendars.json")
GS_KEY_PATTERN = re.compile(r"\[GS:\w+:\w+\]")


def api_call_with_retry(fn, max_retries=3):
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


def _load_calendar_map():
    if os.path.exists(CALENDARS_FILE):
        with open(CALENDARS_FILE) as f:
            data = json.load(f)
        # Handle both new format (dict with "id") and legacy (plain string)
        for key, val in data.items():
            if isinstance(val, str):
                data[key] = {"id": val, "shared": []}
        return data
    return {}


def main():
    creds = Credentials.from_authorized_user_file(TOKEN_FILE, SCOPES)
    if creds.expired and creds.refresh_token:
        creds.refresh(Request())
        with open(TOKEN_FILE, "w") as f:
            f.write(creds.to_json())
    service = build("calendar", "v3", credentials=creds)

    cal_map = _load_calendar_map()
    # Scan all managed calendars plus primary
    cal_ids = list({entry["id"] for entry in cal_map.values()} | {"primary"})

    # Collect and delete all GS-tagged events
    deleted = 0
    for cal_id in cal_ids:
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
                if GS_KEY_PATTERN.search(event.get("description", "")):
                    summary = event.get("summary", "(no title)")
                    api_call_with_retry(
                        lambda cid=cal_id, eid=event["id"]: service.events()
                        .delete(calendarId=cid, eventId=eid)
                        .execute()
                    )
                    log.info("Deleted event: %s", summary)
                    deleted += 1
            page_token = result.get("nextPageToken")
            if not page_token:
                break

    log.info("Deleted %d events", deleted)

    # Delete the per-course calendars
    if cal_map:
        for course_name, entry in cal_map.items():
            cal_id = entry["id"]
            try:
                api_call_with_retry(
                    lambda cid=cal_id: service.calendars().delete(calendarId=cid).execute()
                )
                log.info("Deleted calendar: %s", course_name)
            except HttpError as e:
                if e.resp.status == 404:
                    log.info("Calendar %s already gone", course_name)
                else:
                    log.warning("Could not delete calendar %s: %s", course_name, e)

        os.remove(CALENDARS_FILE)
        log.info("Removed %s", CALENDARS_FILE)

    log.info("Done")


if __name__ == "__main__":
    main()
