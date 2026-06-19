#!/usr/bin/env python3
"""
Unit tests for the pure decision logic (no network, no keys required).

Run:
    python test_units.py     # plain asserts, prints PASS
    pytest -q                # also collected by pytest
"""

from __future__ import annotations

import poll
import reserve
import sign

# --- poll.bookable_devices: status routing + ordering + watchlist ---

def test_bookable_status_routing():
    devs = [
        {"deviceId": "A", "status": "1"},   # FREE -> order
        {"deviceId": "B", "status": "2"},   # IN_USE_SUBSCRIBE -> reserve
        {"deviceId": "C", "status": "3"},   # taken -> omitted
        {"deviceId": "D", "status": "4"},   # out of service -> omitted
    ]
    out = poll.bookable_devices(devs)
    assert [(d["deviceId"], a) for d, a in out] == [("A", "order"), ("B", "reserve")]


def test_bookable_free_before_sub_regardless_of_input_order():
    devs = [{"deviceId": "B", "status": "2"}, {"deviceId": "A", "status": "1"}]
    out = poll.bookable_devices(devs)
    assert [a for _, a in out] == ["order", "reserve"]  # all order before all reserve


def test_bookable_watchlist_filter():
    devs = [{"deviceId": "A", "status": "1"}, {"deviceId": "B", "status": "1"}]
    out = poll.bookable_devices(devs, target_ids={"A"})
    assert [d["deviceId"] for d, _ in out] == ["A"]


def test_bookable_int_status_matched():
    # live JSON may send status as int, not str
    out = poll.bookable_devices([{"deviceId": "A", "status": 1}])
    assert [(d["deviceId"], a) for d, a in out] == [("A", "order")]


# --- poll.parse_remaining ---

def test_parse_remaining():
    assert poll.parse_remaining("5") == 5
    assert poll.parse_remaining(" 7 ") == 7
    assert poll.parse_remaining(5) == 5
    assert poll.parse_remaining(None) is None
    assert poll.parse_remaining("") is None
    assert poll.parse_remaining("abc") is None


# --- poll.soonest_free: only status 3 counts, min, skip non-numeric ---

def test_soonest_free():
    devs = [
        {"status": "3", "timeRemaining": "12"},
        {"status": "3", "timeRemaining": "4"},
        {"status": "1", "timeRemaining": "1"},    # FREE ignored
        {"status": "3", "timeRemaining": "junk"},  # skipped, not 0
    ]
    assert poll.soonest_free(devs) == 4
    assert poll.soonest_free([{"status": "1", "timeRemaining": "1"}]) is None
    assert poll.soonest_free([]) is None


# --- poll.pace: ladder + floor dominance ---

def test_pace_ladder():
    assert poll.pace(30, None, 0.1) == 30          # nothing running -> base
    assert poll.pace(30, 1, 0.1) == 0.1            # <=1 -> floor
    assert poll.pace(30, 3, 0.1) == 2              # <=3 -> max(floor,2)
    assert poll.pace(30, 10, 0.1) == 10           # <=10 -> max(floor,10)
    assert poll.pace(30, 50, 0.1) == 30           # >10 -> base


def test_pace_floor_dominates():
    # a high floor wins over the ladder rung (the ban-safety property)
    assert poll.pace(30, 2, 20) == 20


# --- reserve.book: action dispatch + param building ---

class _FakeClient:
    def __init__(self):
        self.calls = []

    def call(self, name, biz):
        self.calls.append((name, biz))
        return {"orderId": "X"}


def test_book_reserve_action():
    c = _FakeClient()
    reserve.book(c, "D1", "M1", {}, action="reserve")
    name, biz = c.calls[0]
    assert name == "reserve"
    assert biz == {"deviceId": "D1", "modeId": "M1", "orderesource": "1"}
    assert "isRq" not in biz and "runCount" not in biz


def test_book_order_action_param_building():
    c = _FakeClient()
    reserve.book(c, "D1", "M1", {"is_rq": 1, "run_count": 3}, action="order")
    name, biz = c.calls[0]
    assert name == "orderDevice"
    assert biz["isRq"] == "1" and biz["runCount"] == "3"  # int -> str


def test_book_method_override_forces_reserve():
    c = _FakeClient()
    reserve.book(c, "D1", "M1", {"book_method": "reserve"}, action="order")
    assert c.calls[0][0] == "reserve"  # override beats action


# --- sign.biz_json: reserved-key filter + stringify + compact ---

def test_biz_json():
    out = sign.biz_json({"deviceId": "5", "tokenId": "T", "sign": "S", "x": 5})
    # reserved keys stripped, values stringified, compact separators
    assert out == '{"deviceId":"5","x":"5"}'


def test_biz_json_non_ascii_raw():
    assert sign.biz_json({"n": "café"}) == '{"n":"café"}'  # ensure_ascii=False


def _run_all():
    fns = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    for fn in fns:
        fn()
        print(f"  ok: {fn.__name__}")
    print(f"PASS ({len(fns)} tests)")


if __name__ == "__main__":
    _run_all()
