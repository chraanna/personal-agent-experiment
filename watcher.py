from datetime import datetime
from typing import Dict, Tuple, Optional

from calendar_google_adapter import GoogleCalendarAdapter

# --------------------------------------------------
# Types
# --------------------------------------------------

BusyBlock = Tuple[datetime, datetime]

# --------------------------------------------------
# In-memory watch store
# --------------------------------------------------
# watch_id -> (start_utc, end_utc)
WATCHES: Dict[str, Tuple[datetime, datetime]] = {}

# --------------------------------------------------
# Watch API
# --------------------------------------------------

def start_watch(watch_id: str, start_utc: datetime, end_utc: datetime):
    WATCHES[watch_id] = (start_utc, end_utc)


def stop_watch(watch_id: str):
    if watch_id in WATCHES:
        del WATCHES[watch_id]


def check_watch(
    adapter: GoogleCalendarAdapter,
    watch_id: str,
) -> Optional[str]:
    if watch_id not in WATCHES:
        return None

    start_utc, end_utc = WATCHES[watch_id]
    busy_blocks = adapter.get_busy_blocks(start_utc, end_utc)

    if busy_blocks:
        stop_watch(watch_id)
        return "Något i din kalender har ändrats."

    return None
