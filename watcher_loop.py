import time

from calendar_google_adapter import GoogleCalendarAdapter
from watcher import WATCHES, check_watch

def run_watcher_loop(poll_interval_seconds: int = 60):
    adapter = GoogleCalendarAdapter()
    print("👀 Watcher running...")

    while True:
        for watch_id in list(WATCHES.keys()):
            message = check_watch(adapter, watch_id)
            if message:
                print(f"[WATCH {watch_id}] {message}")

        time.sleep(poll_interval_seconds)
