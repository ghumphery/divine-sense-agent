# -*- coding: utf-8 -*-
"""v0.9.123.0 — the in-process per-turn execution event stream core."""
import sys

sys.path.insert(0, ".")

import src.agent.turn_events as te


def setup_function(_):
    te.reset_for_tests()


def teardown_function(_):
    te.reset_for_tests()


def test_record_and_snapshot_roundtrip():
    te.start("t1")
    te.record("t1", "llm_round", "LLM 第 1 輪生成中…")
    te.record("t1", "tool_call", "🔧 get_current_time({})")
    snap = te.snapshot("t1")
    assert snap["found"] is True and snap["status"] == "running"
    assert [e["kind"] for e in snap["events"]] == ["llm_round", "tool_call"]
    assert snap["next_cursor"] == 2


def test_snapshot_cursor_is_incremental():
    te.start("t1")
    te.record("t1", "llm_round", "r1")
    s1 = te.snapshot("t1", 0)
    assert len(s1["events"]) == 1 and s1["next_cursor"] == 1
    te.record("t1", "tool_call", "c1")
    te.record("t1", "tool_result", "ok …")
    s2 = te.snapshot("t1", s1["next_cursor"])
    assert [e["kind"] for e in s2["events"]] == ["tool_call", "tool_result"]
    assert s2["next_cursor"] == 3
    # re-polling the same cursor yields nothing new (no dupes)
    s3 = te.snapshot("t1", 3)
    assert s3["events"] == []


def test_text_capped_at_event_chars():
    te.start("t1")
    te.record("t1", "note", "x" * 1000)
    e = te.snapshot("t1")["events"][0]
    assert len(e["text"]) <= te._EVENT_CHARS + 1  # cap + ellipsis
    assert e["text"].endswith("…")


def test_events_cap_replaces_last_with_note():
    te.start("t1")
    for i in range(te._EVENTS_PER_TURN + 5):
        te.record("t1", "note", f"e{i}")
    evs = te.snapshot("t1")["events"]
    assert len(evs) == te._EVENTS_PER_TURN
    assert evs[-1]["text"].startswith("…(")


def test_finish_stamps_terminal_status_and_result():
    te.start("t1")
    te.record("t1", "llm_round", "r1")
    te.finish("t1", "done", {
        "answer": "A" * 20000,
        "context": "ctx",
        "sources": ["f.md"],
        "usage": {"total_tokens": 99, "calls": 2},
        "tools_used": ["search_documents"],
    })
    snap = te.snapshot("t1")
    assert snap["status"] == "done"
    r = snap["result"]
    assert len(r["answer"]) <= te._RESULT_STR_TRUNC + 1
    assert r["sources"] == ["f.md"]
    assert r["usage"]["total_tokens"] == 99
    assert r["tools_used"] == ["search_documents"]
    # terminal status is frozen: a further record is appened-able but
    # the status stays terminal
    te.record("t1", "note", "late")
    assert te.snapshot("t1")["status"] == "done"


def test_invalid_status_coerced_to_error():
    te.start("t1")
    te.finish("t1", "weird")
    assert te.snapshot("t1")["status"] == "error"


def test_live_turn_lru_cap():
    for i in range(te._LIVE_TURNS + 3):
        te.start(f"t{i}")
    assert te.snapshot("t0")["found"] is False
    assert te.snapshot(f"t{te._LIVE_TURNS + 2}")["found"] is True


def test_unknown_turn_snapshot_is_honest():
    snap = te.snapshot("nope")
    assert snap["found"] is False and snap["status"] == "unknown"
    assert snap["events"] == [] and snap["result"] is None


def test_never_raises_on_garbage():
    for fn in (
        lambda: te.start(None),
        lambda: te.record(None, None, None),
        lambda: te.finish(None, None, object()),
        lambda: te.snapshot(None, "bad"),
    ):
        fn()  # must be silent no-ops / degradations


def test_threaded_writers_do_not_lose_events():
    import threading
    te.start("t1")
    def _w(k):
        for i in range(50):
            te.record("t1", "note", f"{k}{i}")
    ts = [threading.Thread(target=_w, args=(k,)) for k in "ab"]
    for t in ts:
        t.start()
    for t in ts:
        t.join()
    snap = te.snapshot("t1")
    assert snap["next_cursor"] == 100