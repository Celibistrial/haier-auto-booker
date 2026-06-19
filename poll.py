"""
Polling helpers: find bookable (FREE) machines in a target laundry.

Device status enum (modle/enum_/a.java):
    1 = FREE                  <- bookable
    2 = IN_USE_SUBSCRIBE
    3 = IN_USE_NOT_SUBSCRIBE
    4 = CAN_NOT_RUN
    5 = BLUETOOTH appointment

List endpoints return retData as a JSON array (BaseListDate.retData = ArrayList).
Single-object endpoints return retData as an object (BaseData.retData).
"""

from __future__ import annotations

from client import Client

STATUS_FREE = "1"
# IN_USE_SUBSCRIBE: machine is running but its next-cycle subscription slot is
# open. The app fires order.reserve here -> a YUYUE (101) appointment that
# auto-books you for when it frees. This is the REMOTE autobook path at busy
# laundries (IITGN) where machines almost never hit FREE; they cycle 2<->3.
# Once you (or a competing bot) reserve a status-2 machine it flips to status 3
# (IN_USE_NOT_SUBSCRIBE) — so the status-2 window is the race.
STATUS_SUBSCRIBABLE = "2"

# Distance constant the app hardcodes for nearby search (i2.a.f34114l).
NEARBY_DISTANCE = 50000


def nearby_laundries(client: Client, longitude: float, latitude: float,
                     dev_type: str, page: int = 1) -> list[dict]:
    """laundry.getNearbyLaundryList -> list of LaundryRealted ({laundry, distance})."""
    data = client.call("nearbyLaundry", {
        "longitude": str(longitude),
        "latitude": str(latitude),
        "distance": str(NEARBY_DISTANCE),
        "type": dev_type,
        "pageNumber": str(page),
    })
    return data or []


def list_devices(client: Client, laundry_id: str, dev_type: str,
                 page: int = 1, floor: str | None = None) -> list[dict]:
    """laundry.getLaundryDeviceList -> list of DeviceListBeen."""
    biz = {"laundryId": laundry_id, "type": dev_type, "pageNumber": str(page)}
    if floor:
        biz["floorNumber"] = floor
    return client.call("laundryDevices", biz) or []


def device_info(client: Client, device_id: str) -> dict:
    """laundry.getDeviceInfo -> DeviceDetailBeen (incl. runMode[])."""
    return client.call("deviceInfo", {"deviceId": device_id}) or {}


def free_devices(devices: list[dict], target_ids: set[str] | None = None) -> list[dict]:
    """Filter to status==FREE, optionally restricted to a watchlist of deviceIds."""
    out = []
    for d in devices:
        if str(d.get("status")) != STATUS_FREE:
            continue
        if target_ids and str(d.get("deviceId")) not in target_ids:
            continue
        out.append(d)
    return out


def bookable_devices(devices: list[dict],
                     target_ids: set[str] | None = None) -> list[tuple[dict, str]]:
    """Devices actionable right now, each tagged with the call to make:

        status 1 FREE              -> "order"   (order.orderDevice, book now)
        status 2 IN_USE_SUBSCRIBE  -> "reserve" (order.reserve, YUYUE appointment)

    FREE is preferred over SUBSCRIBE (booking now beats queuing). Mirrors the
    app's wash-button branch in DeviceBaseActivity.onClick.
    """
    free, sub = [], []
    for d in devices:
        if target_ids and str(d.get("deviceId")) not in target_ids:
            continue
        st = str(d.get("status"))
        if st == STATUS_FREE:
            free.append((d, "order"))
        elif st == STATUS_SUBSCRIBABLE:
            sub.append((d, "reserve"))
    return free + sub


def parse_remaining(val) -> int | None:
    """timeRemaining as int minutes, or None if missing/non-numeric."""
    try:
        return int(str(val).strip())
    except (TypeError, ValueError):
        return None


def soonest_free(devices: list[dict]) -> int | None:
    """Smallest timeRemaining among in-use machines (status 3). None if none running."""
    times = [r for d in devices if str(d.get("status")) == "3"
             and (r := parse_remaining(d.get("timeRemaining"))) is not None]
    return min(times) if times else None


def pace(base: float, soonest: int | None, floor: float = 1.0) -> float:
    """Tighten the poll interval as a machine nears finishing, down to `floor`.

    The reserve race is won by whoever sees the status flip first, so we poll
    fastest right as a machine is about to finish (its status-2 window is about
    to open). CPU cost is mitigated by the envelope cipher/sign cache
    (sign.build_envelope) — repeat polls are ~1 RSA op (response decrypt).

    `floor` (config poll_floor_sec) is the hard fastest interval. WARNING: a low
    floor sustained = many req/s — may trip server rate-limiting / anomaly bans.
    Race mode bypasses this ladder entirely and polls at `floor` constantly.
    """
    if soonest is None:
        return base
    if soonest <= 1:
        return floor
    if soonest <= 3:
        return max(floor, 2)
    if soonest <= 10:
        return max(floor, 10)
    return base


# Resolved modeId per deviceId. runMode is static per machine, so once resolved
# we never need the extra getDeviceInfo round-trip again — critical on the
# free->book hot path where every RTT is a chance for a competitor to grab it.
_MODE_CACHE: dict[str, str] = {}


def pick_mode_id(client: Client, device_id: str, prefer_mode_id: str | None = None) -> str:
    """Resolve a runMode id for a device. Prefer configured id, else first displayable.

    Two latency cuts on the booking hot path:
      * prefer_mode_id set -> trust it, skip getDeviceInfo entirely (0 extra RTT).
      * otherwise resolve once via getDeviceInfo, then cache (1 RTT, first time only).
    """
    if prefer_mode_id:
        return str(prefer_mode_id)
    cached = _MODE_CACHE.get(device_id)
    if cached is not None:
        return cached

    info = device_info(client, device_id)
    modes = info.get("runMode") or []
    # fall back: current run mode, then first available mode
    if info.get("currentRunModeId"):
        mode = str(info["currentRunModeId"])
    elif modes:
        mode = str(modes[0].get("modeId"))
    else:
        raise RuntimeError(f"no runMode found for device {device_id}")
    _MODE_CACHE[device_id] = mode
    return mode


def prefetch_modes(client: Client, device_ids, prefer_mode_id: str | None = None) -> None:
    """Warm _MODE_CACHE for a watchlist before the poll loop, so the first FREE
    event books at 0 extra RTT. No-op when prefer_mode_id is set (already 0 RTT)
    or the watchlist is empty (any-machine mode caches on first sight)."""
    if prefer_mode_id:
        return
    for did in device_ids or ():
        try:
            pick_mode_id(client, str(did))
        except RuntimeError:
            pass  # leave uncached; resolved live at book time
