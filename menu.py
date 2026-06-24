"""
Interactive menu: pick a laundry + machine(s) + wash mode, then watch-and-reserve.

Run:  python main.py --menu [config.yaml]

Uses the account/coords from config.yaml for login + discovery, but lets you
choose the target live instead of pre-pinning it in YAML.
"""

from __future__ import annotations

import time

import requests

import auth
import poll
from client import ApiError, Client, SessionExpired

# Device status -> human label (modle/enum_/a.java).
STATUS_LABEL = {
    "1": "FREE",
    "2": "in use (reserved)",
    "3": "in use",
    "4": "out of service",
    "5": "bluetooth-only",
}
TYPE_LABEL = {"1": "washer", "2": "dryer", "3": "washer"}


def _ask(prompt: str, default: str | None = None) -> str:
    suffix = f" [{default}]" if default is not None else ""
    val = input(f"{prompt}{suffix}: ").strip()
    return val or (default or "")


def _pick(items: list, render, prompt: str):
    """Number a list, ask the user to choose one. Returns the chosen item."""
    for i, it in enumerate(items, 1):
        print(f"  {i}) {render(it)}")
    while True:
        raw = input(f"{prompt} (1-{len(items)}): ").strip()
        if raw.isdigit() and 1 <= int(raw) <= len(items):
            return items[int(raw) - 1]
        print("  invalid choice")


def choose_laundry(client: Client, cfg: dict) -> tuple[str, str]:
    """Return (laundry_id, dev_type).

    Machine type comes from config (dev_type) — no prompt. If coords are set we
    auto-list nearby laundries to pick from; otherwise we ask for an id directly.
    """
    dev_type = str(cfg.get("dev_type", "1"))
    have_coords = cfg.get("longitude") is not None and cfg.get("latitude") is not None
    if have_coords:
        laundries = poll.nearby_laundries(
            client, cfg["longitude"], cfg["latitude"], dev_type)
        if laundries:
            chosen = _pick(
                laundries,
                lambda it: (f"{it.get('laundry',{}).get('name','?')} "
                            f"(id {it.get('laundry',{}).get('laundryId')}, "
                            f"{it.get('distance','?')}m, "
                            f"spare={it.get('laundry',{}).get('spareDeviceNumber','?')})"),
                "pick a laundry")
            return str(chosen["laundry"]["laundryId"]), dev_type
        print("none found nearby; enter an id manually")

    return _ask("laundryId", str(cfg.get("laundry_id", ""))), dev_type


def show_machines(client: Client, laundry_id: str, dev_type: str) -> list[dict]:
    devices = poll.list_devices(client, laundry_id, dev_type)
    print(f"\n{TYPE_LABEL.get(dev_type, 'machine')}s at laundry {laundry_id}:")
    for d in devices:
        st = str(d.get("status"))
        rem = d.get("timeRemaining")
        tail = f", ~{rem}min left" if st == "3" and rem else ""
        print(f"  {d.get('deviceId')}  {d.get('name')}  "
              f"-> {STATUS_LABEL.get(st, st)}{tail}")
    return devices


def choose_targets(devices: list[dict]) -> set[str]:
    print("\nwhich machine(s) to watch?")
    print("  a) any machine that frees up")
    print("  or enter deviceIds comma-separated (e.g. 3746,3754)")
    raw = input("choice [a]: ").strip()
    if not raw or raw.lower() == "a":
        return set()  # empty = any
    return {x.strip() for x in raw.split(",") if x.strip()}


def choose_mode(client: Client, device_id: str) -> str:
    info = poll.device_info(client, device_id)
    modes = info.get("runMode") or []
    if not modes:
        return poll.pick_mode_id(client, device_id)
    chosen = _pick(
        modes,
        lambda m: f"{m.get('modeName')} (id {m.get('modeId')}, price {m.get('price')})",
        "pick a wash mode")
    return str(chosen["modeId"])


def run_menu(cfg: dict) -> None:
    client = Client.from_config(cfg)
    print("logging in...")
    auth.ensure_session(client, cfg["phone"], cfg["password"],
                        cfg.get("android_user_id", "null"), cfg["token_cache"])

    laundry_id, dev_type = choose_laundry(client, cfg)
    devices = show_machines(client, laundry_id, dev_type)
    if not devices:
        print("no machines listed; aborting.")
        return

    targets = choose_targets(devices)

    # If exactly one concrete target, let the user pick its mode now; otherwise
    # resolve mode per-device at reserve time.
    fixed_mode = None
    if len(targets) == 1:
        fixed_mode = choose_mode(client, next(iter(targets)))

    import main
    want = main.ask_reserve_count(cfg, interactive=True)

    # Poll interval comes from config (no prompt); 10s floor for courtesy.
    interval = max(10, int(cfg.get("poll_interval_sec", 30)))

    # Warm the mode cache for concrete targets -> first FREE event books at 0 extra RTT.
    poll.prefetch_modes(client, targets, fixed_mode or cfg.get("prefer_mode_id"))

    tnames = ", ".join(sorted(targets)) if targets else "any free machine"
    print(f"\nwatching {tnames} at laundry {laundry_id} every {interval}s, "
          f"reserving up to {want}.")
    if input("type 'go' to start (anything else cancels): ").strip().lower() != "go":
        print("cancelled.")
        return

    watch_and_reserve(client, cfg, laundry_id, dev_type, targets, fixed_mode,
                      interval, want)


def watch_and_reserve(client, cfg, laundry_id, dev_type, targets, fixed_mode, base,
                      want=1):
    """Interactive watch loop. Delegates each pass to main.find_and_reserve so the
    booking logic (status 1 vs 2 routing, mode resolution, notify) lives in ONE
    place. Imported lazily to avoid a circular import (main imports menu).
    Stops once `want` machines are reserved, then plays a done sound."""
    import main
    import reserve
    reserved: set[str] = set()
    while True:
        print(time.strftime("[%H:%M:%S] polling"))
        interval = base
        try:
            made, devices = main.find_and_reserve(
                cfg, client, laundry_id, dev_type, reserved,
                target_ids=targets or None, fixed_mode=fixed_mode)
            if made and len(reserved) >= want:
                print(f"reserved {len(reserved)}/{want} — done.")
                reserve.chime()
                return
            # tighten as a machine nears finishing (same ladder as run_loop)
            soonest = poll.soonest_free(devices)
            interval = poll.pace(base, soonest)
            if soonest is not None:
                print(f"  soonest free in ~{soonest}min -> next poll in {interval}s")
        except SessionExpired:
            print("  session expired, re-logging in")
            data = auth.login(client, cfg["phone"], cfg["password"],
                              cfg.get("android_user_id", "null"))
            auth.save_token(cfg["token_cache"], data)
        except ApiError as e:
            print(f"  api error: {e}")
        except requests.exceptions.RequestException as e:
            # transient network blip (timeout/conn reset) — never kill the loop
            print(f"  network error ({type(e).__name__}); retrying")
        time.sleep(interval)
