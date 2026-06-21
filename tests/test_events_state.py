from app.events import EventEmitter


def test_state_summary_defaults_and_sequence():
    emitter = EventEmitter(run_id="test-run")
    e1 = emitter.emit("planning", "Planning", "Create a search plan.")
    assert e1["sequence"] == 1
    assert e1["state_summary"] == e1["summary"]
    # provide explicit state_summary and next_action
    e2 = emitter.emit(
        "searching",
        "Searching",
        "Running searches.",
        state_summary="I’m searching Brave for sources.",
        next_action="Classify each source.",
    )
    assert e2["sequence"] == 2
    assert e2["state_summary"] == "I’m searching Brave for sources."
    assert e2["next_action"] == "Classify each source."
    assert e2["run_id"] == "test-run"
