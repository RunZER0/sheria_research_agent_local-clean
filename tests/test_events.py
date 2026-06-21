import json
import os
import sys

# Ensure project root is on path for test imports
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from app.events import EventEmitter


def test_event_sequence_and_serialization():
    emitter = EventEmitter(run_id="test-run")
    e1 = emitter.emit("run_started", "Run started", "Starting run.")
    e2 = emitter.emit("planning", "Planning", "Creating plan.")

    assert e1["run_id"] == "test-run"
    assert e1["sequence"] == 1
    assert e2["sequence"] == 2

    payload = [e1, e2]
    s = json.dumps(payload)
    assert "test-run" in s
    assert "run_started" in s
