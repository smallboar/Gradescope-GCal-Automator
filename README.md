# Gradescope → Google Calendar Sync

Scrapes assignments from Gradescope and syncs them to Google Calendar as all-day events. Creates, updates, and deletes events to keep your calendar in sync.

## Setup

### 1. Install dependencies

```bash
python -m venv venv
source venv/bin/activate
pip install -r requirements.txt
```

### 2. Gradescope credentials

Copy `.env.example` to `.env` and fill in your Gradescope login:

```bash
cp .env.example .env
```

### 3. Google Calendar API

1. Go to the [Google Cloud Console](https://console.cloud.google.com/)
2. Create a project (or use an existing one)
3. Enable the **Google Calendar API**
4. Create **OAuth 2.0 Client ID** credentials (Desktop app type)
5. Download the JSON and save it as `credentials.json` in the project root
6. Run the one-time auth flow:

```bash
python auth_google.py
```

This opens a browser for OAuth consent and saves `token.json`.

## Usage

```bash
python sync.py
```

Output:

```
2026-04-02 10:00:00 INFO Logged in to Gradescope
2026-04-02 10:00:02 INFO Fetched 12 assignments from Gradescope
2026-04-02 10:00:03 INFO Created: [CS101] Homework 3 (due 2026-04-10)
2026-04-02 10:00:04 INFO Sync complete — 1 created, 0 updated, 0 deleted
```

## Scheduling with cron (macOS/Linux)

Run every 6 hours:

```bash
crontab -e
```

Add:

```
0 */6 * * * /path/to/venv/bin/python /path/to/sync.py >> /path/to/sync.log 2>&1
```

Replace paths with your actual install location.

## Scheduling with GitHub Actions (free cloud alternative)

Create `.github/workflows/sync.yml`:

```yaml
name: Gradescope Calendar Sync
on:
  schedule:
    - cron: "0 */6 * * *"
  workflow_dispatch:

jobs:
  sync:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - uses: actions/setup-python@v5
        with:
          python-version: "3.11"
      - run: pip install -r requirements.txt
      - run: python sync.py
        env:
          GRADESCOPE_EMAIL: ${{ secrets.GRADESCOPE_EMAIL }}
          GRADESCOPE_PASSWORD: ${{ secrets.GRADESCOPE_PASSWORD }}
```

Store your credentials as **GitHub Actions secrets** in the repo settings. For the Google token, base64-encode `token.json` and store it as a secret, then decode it in a step before running `sync.py`:

```yaml
      - run: echo "${{ secrets.GOOGLE_TOKEN_JSON }}" | base64 -d > token.json
```

Do the same for `credentials.json` if needed.
