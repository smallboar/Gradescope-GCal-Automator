# Gradescope → Google Calendar Sync

Scrapes assignments from Gradescope and syncs them to Google Calendar as all-day events. Creates a separate calendar per course, and shares them with configured subscribers. Runs daily via GitHub Actions.

## Setup

### 1. Install dependencies

```bash
python -m venv venv
source venv/bin/activate
pip install -r requirements.txt
```

### 2. Google Calendar API

1. Go to the [Google Cloud Console](https://console.cloud.google.com/)
2. Create a project and enable the **Google Calendar API**
3. Create **OAuth 2.0 Client ID** credentials (Desktop app type)
4. Download the JSON and save it as `credentials.json` in the project root
5. Run the one-time auth flow:

```bash
python auth_google.py
```

This opens a browser for OAuth consent and saves `token.json`.

### 3. GitHub Actions secrets

Add these secrets in **Settings → Secrets and variables → Actions**:

| Secret | Description |
|--------|-------------|
| `GOOGLE_CREDENTIALS_JSON` | Contents of `credentials.json` |
| `GOOGLE_TOKEN_JSON` | Contents of `token.json` |
| `GRADESCOPE_EMAIL` | Gradescope login email |
| `GRADESCOPE_PASSWORD` | Gradescope password |
| `GRADESCOPE_TERM` | Term filter, e.g. `Spring 2026` |

The workflow runs daily at 8:00 AM PST and can be triggered manually from the Actions tab.

## Managing subscribers

Subscribers are stored in `subscribers.json`. Use `*` to subscribe to all courses, or specify a course name for per-course subscriptions.

```bash
# Add a subscriber to all courses
python manage.py add "*" alice@gmail.com

# Add a subscriber to a specific course
python manage.py add "CSE 452" bob@gmail.com

# Remove a subscriber from a specific course
python manage.py remove "CSE 452" bob@gmail.com

# Remove a subscriber from all courses
python manage.py remove-all bob@gmail.com

# List all subscribers
python manage.py list

# List subscribers for a specific course
python manage.py list "CSE 452"
```

Commit and push `subscribers.json` after making changes — the next sync run will pick them up.

## Running locally

```bash
# Create a .env file with your Gradescope credentials
# GRADESCOPE_EMAIL=...
# GRADESCOPE_PASSWORD=...
# GRADESCOPE_TERM=Spring 2026

python sync.py
```

## Cleanup

To delete all synced events and calendars:

```bash
python delete_all.py
```
