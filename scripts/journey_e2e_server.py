"""Isolated browser-test application with offline cloud dependencies."""
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import uvicorn
from backend.app import create_app
from backend.store import Store
from tests.journey_support import make_journey

if __name__ == "__main__":
    with tempfile.TemporaryDirectory(prefix="gab-journey-e2e-") as temporary:
        store = Store(str(Path(temporary) / "state.sqlite"))
        journey, _ = make_journey(store)
        app = create_app(repository=store, demo_mode=True, journey=journey)
        uvicorn.run(app, host="127.0.0.1", port=5189, access_log=False)
