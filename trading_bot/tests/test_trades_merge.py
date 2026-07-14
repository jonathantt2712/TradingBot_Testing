"""Atomic merge of concurrent writes to trades.json.

The background loops load trades.json, mutate a snapshot, then save. Running as
separate asyncio tasks against the same file, a blind whole-snapshot save
clobbers trades another task opened/closed in the meantime. `_merge_trade_changes`
+ `_save_trade_changes` fix that by overlaying only the records a task touched
onto the latest on-disk list, under the lock.
"""
import asyncio
import json

import pytest

pytest.importorskip("fastapi")

import api_server  # noqa: E402
from api_server import _merge_trade_changes, _trade_key  # noqa: E402


def _t(tid, status="open", **extra):
    return {"id": tid, "status": status, **extra}


# ── _trade_key ───────────────────────────────────────────────────────────────

def test_trade_key_prefers_id():
    assert _trade_key({"id": "X", "order_id": "Y"}) == "X"


def test_trade_key_falls_back_to_order_id():
    assert _trade_key({"order_id": "Y"}) == "Y"


def test_trade_key_none_when_unkeyed():
    assert _trade_key({"ticker": "NVDA"}) is None


# ── _merge_trade_changes ─────────────────────────────────────────────────────

def test_no_changes_returns_disk():
    disk = [_t("A"), _t("B")]
    assert _merge_trade_changes(disk, [_t("A")], set()) is disk


def test_changed_trade_overlaid_from_snapshot():
    disk = [_t("A", "open"), _t("B", "open")]
    snapshot = [_t("A", "closed", pnl=100), _t("B", "open")]
    out = _merge_trade_changes(disk, snapshot, {"A"})
    by_id = {t["id"]: t for t in out}
    assert by_id["A"]["status"] == "closed" and by_id["A"]["pnl"] == 100
    assert by_id["B"]["status"] == "open"


def test_unchanged_trades_keep_disk_version_not_stale_snapshot():
    # snapshot holds a STALE copy of B (still open), but we only changed A —
    # B must come from disk (where another task already closed it).
    disk = [_t("A", "open"), _t("B", "closed", pnl=50)]
    snapshot = [_t("A", "closed"), _t("B", "open")]
    out = _merge_trade_changes(disk, snapshot, {"A"})
    by_id = {t["id"]: t for t in out}
    assert by_id["B"]["status"] == "closed" and by_id["B"]["pnl"] == 50


def test_concurrently_added_trade_is_preserved():
    # THE RACE: a task closes A from a 2-trade snapshot while another task
    # appended C to disk. C must survive the merge.
    disk = [_t("A", "open"), _t("B", "open"), _t("C", "open")]   # C added concurrently
    snapshot = [_t("A", "closed"), _t("B", "open")]              # task never saw C
    out = _merge_trade_changes(disk, snapshot, {"A"})
    ids = {t["id"] for t in out}
    assert ids == {"A", "B", "C"}
    assert next(t for t in out if t["id"] == "A")["status"] == "closed"
    assert next(t for t in out if t["id"] == "C")["status"] == "open"


def test_changed_trade_missing_from_disk_is_appended():
    disk = [_t("A", "open")]
    snapshot = [_t("A", "open"), _t("B", "closed")]
    out = _merge_trade_changes(disk, snapshot, {"B"})
    assert {t["id"] for t in out} == {"A", "B"}


def test_disk_order_preserved():
    disk = [_t("A"), _t("B"), _t("C")]
    snapshot = [_t("B", "closed")]
    out = _merge_trade_changes(disk, snapshot, {"B"})
    assert [t["id"] for t in out] == ["A", "B", "C"]


# ── _save_trade_changes (end-to-end under the lock) ──────────────────────────

def test_save_trade_changes_merges_against_latest_disk(tmp_path, monkeypatch):
    f = tmp_path / "trades.json"
    monkeypatch.setattr(api_server, "TRADES_FILE", f)

    async def scenario():
        api_server._trades_lock = asyncio.Lock()
        # task loads a 2-trade snapshot
        f.write_text(json.dumps([_t("A", "open"), _t("B", "open")]))
        snapshot = json.loads(f.read_text())
        # it closes A in its snapshot
        snapshot[0]["status"] = "closed"
        snapshot[0]["pnl"] = 100
        # meanwhile another task appends C to disk
        f.write_text(json.dumps([_t("A", "open"), _t("B", "open"), _t("C", "open")]))
        # now persist the task's change
        return await api_server._save_trade_changes(snapshot, {"A"})

    merged = asyncio.run(scenario())
    on_disk = {t["id"]: t for t in json.loads(f.read_text())}
    assert set(on_disk) == {"A", "B", "C"}           # C not clobbered
    assert on_disk["A"]["status"] == "closed"        # A's close persisted
    assert {t["id"] for t in merged} == {"A", "B", "C"}


# ── compaction ───────────────────────────────────────────────────────────────

def test_compaction_keeps_open_and_newest_closed(tmp_path, monkeypatch):
    import asyncio, json
    import api_server

    monkeypatch.setattr(api_server, "TRADES_FILE", tmp_path / "trades.json")
    monkeypatch.setattr(api_server, "_TRADES_CAP", 10)
    monkeypatch.setattr(api_server, "_TRADES_KEEP", 5)
    api_server._trades_lock = asyncio.Lock()

    trades = (
        [{"id": f"old-{i}", "status": "closed"} for i in range(8)]
        + [{"id": "open-1", "status": "open"}]                 # open, mid-file
        + [{"id": f"new-{i}", "status": "closed"} for i in range(4)]
    )
    (tmp_path / "trades.json").write_text(json.dumps(trades))

    asyncio.run(api_server._compact_trades())
    kept = json.loads((tmp_path / "trades.json").read_text())
    ids = [t["id"] for t in kept]
    assert "open-1" in ids                                     # open never dropped
    assert ids[-1] == "new-3"                                  # newest closed kept
    assert len([t for t in kept if t["status"] != "open"]) == 5
    assert not any(i.startswith("old-") and i != "old-7" for i in ids[:2])


def test_compaction_noop_under_cap(tmp_path, monkeypatch):
    import asyncio, json
    import api_server

    monkeypatch.setattr(api_server, "TRADES_FILE", tmp_path / "trades.json")
    monkeypatch.setattr(api_server, "_TRADES_CAP", 100)
    api_server._trades_lock = asyncio.Lock()
    trades = [{"id": "a", "status": "closed"}]
    (tmp_path / "trades.json").write_text(json.dumps(trades))

    asyncio.run(api_server._compact_trades())
    assert json.loads((tmp_path / "trades.json").read_text()) == trades
