# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Running the Application

```bash
python main.py
# Serves at http://localhost:8000
```

There are no tests, no linter configuration, and no `requirements.txt` / `pyproject.toml`. Dependencies must be inferred from imports.

## Architecture

This is "Shilpi" — a Swedish-language personal AI agent built with **Python/FastAPI** (backend) and **vanilla JS** (frontend). All state is in-memory; there is no database. Uses **Microsoft Calendar** (Graph API) as the calendar provider with per-user OAuth2 and multi-user support.

### Entry Points

- **`main.py`** — The application. Runs the chat UI (`POST /chat`, `GET /events`, `GET /`), Microsoft OAuth (`/auth/login`, `/auth/callback`, `/auth/status`), reminder state machine, calendar question handling, and a background multi-user calendar watcher thread that polls every 30 seconds.
- **`find_slots.py`** — Importable module exporting `find_slots_for_day(day, busy_blocks)` and constants (`WORKDAY_START`, `WORKDAY_END`, `SLOT_LENGTH`, `DAYS_AHEAD`). Can also run standalone as a Google Calendar free-slot service via `python find_slots.py`.

### Multi-User Model

Each user gets a UUID (`shilpi_user_id` cookie) assigned at OAuth login. Per-user state:
- `user_adapters` — cached `MicrosoftCalendarAdapter` instances
- `user_reminders`, `user_reminder_state` — independent reminder state machines
- `user_events` — per-user push event queues
- `user_calendar_snapshots`, `user_reported_conflicts` — per-user watcher state

### Reminder State Machine

Defined in `main.py`. States: `active` → `triggered_once` (+15 min) → `reminded_twice` (+15 min) → cleared. The chat endpoint handles a two-step flow: first detect the task ("påminn mig att..."), then ask for and parse the time. Stop words (`klar`, `ok`, `tack`, etc.) clear all reminders.

### Calendar Adapter

**`microsoft_calendar_adapter.py`** — `MicrosoftCalendarAdapter` with per-user token storage in `tokens/{user_id}.json`, automatic token refresh with thread-safe locking, and `get_events(start_utc, end_utc)` returning normalized dicts: `{id, summary, start, end, all_day, response, organizer_email}`. Uses Graph API `/me/calendarview` with UTC timezone preference.

### Calendar Questions in Chat

`is_calendar_question()` detects calendar-related queries. `handle_calendar_question()` handles:
- "luckor"/"ledig" — finds free 1-hour slots using `find_slots_for_day`
- "nästa möte" — shows next upcoming meeting
- General calendar questions — shows today's schedule

### Background Calendar Watcher

`calendar_watcher()` in `main.py` runs as a daemon thread. It loops over all connected users, polls Microsoft Calendar, detects conflicts between pending invites and accepted meetings, pushes notifications to per-user event queues, and processes reminder timers.

### Frontend

`index.html` — chat interface with auth bar (login/status indicator), `POST`s to `/chat`, polls `GET /events` every 2 seconds and `GET /auth/status` every 30 seconds.

## Key Conventions

- **Language**: All user-facing text, time parsing, and intent detection use **Swedish** (weekday names, keywords like "påminn", "luckor", "imorgon", stop words).
- **Time parsing** (`parse_time_expression`): Handles "om X min", "HH:MM", "idag"/"imorgon", weekday names, "nästa [weekday]".
- **Secrets**: `.env` and `tokens/` are gitignored. Microsoft auth needs env vars `MICROSOFT_CLIENT_ID`, `MICROSOFT_CLIENT_SECRET`, `MICROSOFT_TENANT_ID`, `MICROSOFT_REDIRECT_URI`.
