from datetime import datetime, timedelta, time, timezone

# --------------------------------------------------
# Configuration
# --------------------------------------------------

WORKDAY_START = time(9, 0)
WORKDAY_END = time(17, 0)
SLOT_LENGTH = timedelta(hours=1)
DAYS_AHEAD = 7

# --------------------------------------------------
# Core logic
# --------------------------------------------------

def to_local(dt: datetime) -> datetime:
    return dt.astimezone()


def parse_event_time(value: str) -> datetime:
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


def find_slots_for_day(day, busy_blocks):
    day_start = datetime.combine(day, WORKDAY_START).astimezone()
    day_end = datetime.combine(day, WORKDAY_END).astimezone()

    slots = []
    cursor = day_start

    for busy_start, busy_end in sorted(busy_blocks):
        if busy_start > cursor and cursor + SLOT_LENGTH <= busy_start:
            slots.append((cursor, cursor + SLOT_LENGTH))
        cursor = max(cursor, busy_end)

    if cursor + SLOT_LENGTH <= day_end:
        slots.append((cursor, cursor + SLOT_LENGTH))

    return slots


# --------------------------------------------------
# Google-specific + FastAPI (standalone mode only)
# --------------------------------------------------

if __name__ == "__main__":
    import json
    import os
    from fastapi import FastAPI
    from fastapi.responses import PlainTextResponse
    from googleapiclient.discovery import build
    from google.oauth2.credentials import Credentials

    TOKEN_FILE = "token.json"

    app = FastAPI()

    def load_credentials():
        if not os.path.exists(TOKEN_FILE):
            raise RuntimeError("token.json saknas.")

        with open(TOKEN_FILE, "r") as f:
            data = json.load(f)

        return Credentials(
            token=data["token"],
            refresh_token=data.get("refresh_token"),
            token_uri=data["token_uri"],
            client_id=data["client_id"],
            client_secret=data["client_secret"],
            scopes=data["scopes"],
        )

    def get_busy_blocks(service, start_utc, end_utc):
        events_result = service.events().list(
            calendarId="primary",
            timeMin=start_utc.isoformat(),
            timeMax=end_utc.isoformat(),
            singleEvents=True,
            orderBy="startTime",
        ).execute()

        busy = []

        for event in events_result.get("items", []):
            start_raw = event["start"].get("dateTime")
            end_raw = event["end"].get("dateTime")

            if not start_raw or not end_raw:
                continue

            start = to_local(parse_event_time(start_raw))
            end = to_local(parse_event_time(end_raw))

            busy.append((start, end))

        return busy

    def generate_agent_message():
        creds = load_credentials()
        service = build("calendar", "v3", credentials=creds)

        now_utc = datetime.now(timezone.utc)
        end_utc = now_utc + timedelta(days=DAYS_AHEAD)

        busy_blocks = get_busy_blocks(service, now_utc, end_utc)

        suggestions = []

        for day_offset in range(DAYS_AHEAD):
            day = (now_utc + timedelta(days=day_offset)).date()
            day_busy = [(s, e) for s, e in busy_blocks if s.date() == day]
            slots = find_slots_for_day(day, day_busy)

            for start, end in slots:
                suggestions.append(
                    f"{start.strftime('%A')} {start.strftime('%H')}–{end.strftime('%H')}"
                )
                if len(suggestions) == 3:
                    break

            if len(suggestions) == 3:
                break

        if not suggestions:
            return "Jag ser inga lediga tider denna vecka."

        lines = ["Jag ser tre möjliga tider denna vecka:"]
        for s in suggestions:
            lines.append(f"• {s}")

        return "\n".join(lines)

    @app.get("/suggest-times", response_class=PlainTextResponse)
    def suggest_times():
        return generate_agent_message()

    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8001)
