"""
Run the broadcast-campaign dispatcher as its own process.

Optional: by default the web app already runs the dispatcher in a background
thread. Use this when you'd rather keep dialing off the web service (e.g. a
Render "Background Worker"): set CAMPAIGN_DISPATCHER_ENABLED=false on the web
service and run `python campaign_worker.py` here with the same environment.
"""
import time

from config import Config

Config.CAMPAIGN_DISPATCHER_ENABLED = True

from app import create_app  # noqa: E402  (must load after the flag is forced on)
from utils.campaign_dispatcher import ensure_dispatcher_running  # noqa: E402


if __name__ == "__main__":
    app = create_app()
    while True:
        ensure_dispatcher_running(app)
        time.sleep(30)
