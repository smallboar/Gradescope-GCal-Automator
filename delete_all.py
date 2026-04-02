"""Delete all Google Calendar events created by the Gradescope sync script.

Only removes events whose description contains a [GS:...] tag.
"""

import os
import re
import time
import logging
from datetime import datetime

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


def main():
    creds = Credentials.from_authorized_user_file(TOKEN_FILE, SCOPES)
    if creds.expired and creds.refresh_token:
        creds.refresh(Request())
        with open(TOKEN_FILE, "w") as f:
            f.write(creds.to_json())
    service = build("calendar", "v3", credentials=creds)

    # Collect all GS-tagged events (past and future)
    gs_events = []
    page_token = None
    while True:
        result = api_call_with_retry(
            lambda pt=page_token: service.events()
            .list(
                calendarId="primary",
                singleEvents=True,
                maxResults=2500,
                pageToken=pt,
            )
            .execute()
        )
        for event in result.get("items", []):
            if GS_KEY_PATTERN.search(event.get("description", "")):
                gs_events.append(event)
        page_token = result.get("nextPageToken")
        if not page_token:
            break

    if not gs_events:
        log.info("No Gradescope sync events found. Nothing to delete.")
        return

    log.info("Found %d Gradescope sync events to delete", len(gs_events))

    deleted = 0
    for event in gs_events:
        summary = event.get("summary", "(no title)")
        api_call_with_retry(
            lambda eid=event["id"]: service.events()
            .delete(calendarId="primary", eventId=eid)
            .execute()
        )
        log.info("Deleted: %s", summary)
        deleted += 1

    log.info("Done — %d events deleted", deleted)


if __name__ == "__main__":
    main()
