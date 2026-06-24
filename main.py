#!/usr/bin/env python3
"""
washbot — auto-booker for the Haier GlobalWasher laundry API.

Polls a target laundry's machines and books one the moment it's actionable:
status 1 FREE -> order.orderDevice (book now); status 2 IN_USE_SUBSCRIBE ->
order.reserve (next-cycle appointment). Then notifies you (you pay + start
manually in the app). Single account, rate limited. See README for scope/ethics.

Usage:
    python main.py [config.yaml]            # run the poll/book loop (config-driven target)
    python main.py --menu [config.yaml]     # interactive: pick laundry/machine/mode, then watch
    python main.py --discover [config.yaml] # one-shot: list nearby laundries + ids
    python main.py --orders [config.yaml]   # list the account's orders (spot a stuck [10009])
    python main.py --once [config.yaml]     # single poll pass, no loop
"""

from __future__ import annotations

import sys
import time

import requests
import yaml

import auth
import poll
import reserve
from client import ApiError, Client, SessionExpired
from menu import choose_laundry

MIN_INTERVAL = 10  # hard floor on poll interval (be a good citizen)


def load_config(path: str) -> dict:
    with open(path) as f:
        return yaml.safe_load(f)


def cmd_discover(cfg: dict) -> None:
    if cfg.get("longitude") is None or cfg.get("latitude") is None:
        print("set longitude/latitude in config to use --discover")
        return
    client = Client.from_config(cfg)
    auth.ensure_session(client, cfg["phone"], cfg["password"],
                        cfg.get("android_user_id", "null"), cfg["token_cache"])
    laundries = poll.nearby_laundries(
        client, cfg["longitude"], cfg["latitude"], str(cfg["dev_type"]))
    if not laundries:
        print("no laundries found nearby")
        return
    for item in laundries:
        info = item.get("laundry", {})
        print(f"{info.get('laundryId'):>12}  {info.get('name','?')}  "
              f"({item.get('distance','?')}m)  spare={info.get('spareDeviceNumber','?')}")


def cmd_orders(cfg: dict) -> None:
    """List the account's orders — surfaces a stuck/unpaid order that would
    block new bookings (10009)."""
    client = Client.from_config(cfg)
    auth.ensure_session(client, cfg["phone"], cfg["password"],
                        cfg.get("android_user_id", "null"), cfg["token_cache"])
    orders = reserve.list_orders(client)
    if not orders:
        print("no orders on this account (nothing blocking new bookings).")
        return
    print(f"{len(orders)} order(s):")
    for o in orders:
        print(f"  orderId={o.get('orderId')} status={o.get('orderStatus')} "
              f"device={o.get('deviceName')} ({o.get('deviceId')}) "
              f"created={o.get('createTime')}")
    print("\nAn open/unpaid order can cause [10009] on new bookings. Cancel with:")
    print("  python -c \"import yaml,auth,reserve;from client import Client;"
          "c=Client.from_config(cfg:=yaml.safe_load(open('config.yaml')));"
          "auth.ensure_session(c,cfg['phone'],cfg['password'],cfg.get('android_user_id','null'),cfg['token_cache']);"
          "print(reserve.cancel_order(c,'<ORDER_ID>'))\"")


def find_and_reserve(cfg: dict, client: Client, laundry_id: str, dev_type: str,
                     reserved: set[str], quiet: bool = False,
                     target_ids: set[str] | None = None,
                     fixed_mode: str | None = None) -> tuple[bool, list[dict]]:
    """One poll pass. Returns (reservation_made, devices_seen).

    quiet=True (race mode) suppresses the per-poll "nothing bookable" line so a
    fast loop doesn't storm the log; run_loop prints status-changes instead.

    target_ids/fixed_mode let an interactive caller (menu) pass a live-chosen
    watchlist + mode; when None they fall back to the config values.
    """
    if target_ids is None:
        target_ids = set(map(str, cfg.get("target_device_ids") or [])) or None
    devices = poll.list_devices(client, laundry_id, dev_type,
                                page=1, floor=cfg.get("floor"))
    # Guard the silent-empty trap: a watchlist that names no device present in
    # this laundry/type would always yield "no free machines".
    if target_ids:
        present = {str(d.get("deviceId")) for d in devices} & target_ids
        if not present:
            print(f"  WARNING: target_device_ids {sorted(target_ids)} not present in "
                  f"laundry {laundry_id} type {dev_type} (seen "
                  f"{sorted(str(d.get('deviceId')) for d in devices)}) — watching nothing")
    bookable = [(d, a) for (d, a) in poll.bookable_devices(devices, target_ids)
                if str(d.get("deviceId")) not in reserved]
    if not bookable:
        # Per-device status dump. We act on status 1 (FREE -> orderDevice) and
        # status 2 (IN_USE_SUBSCRIBE -> reserve/appointment). status 3 is in-use
        # with the subscription slot already taken (nothing to do but wait).
        watch = [d for d in devices if not target_ids
                 or str(d.get("deviceId")) in target_ids]
        if not quiet:
            detail = " ".join(f"{d.get('deviceId')}:st{d.get('status')}"
                              f"/{poll.parse_remaining(d.get('timeRemaining'))}m"
                              for d in watch)
            print(f"  nothing bookable ({len(devices)} seen) [{detail}]")
        return False, devices

    dev, action = bookable[0]
    device_id = str(dev["deviceId"])
    mode_id = fixed_mode or poll.pick_mode_id(client, device_id, cfg.get("prefer_mode_id"))
    st = str(dev.get("status"))
    label = "FREE->orderDevice" if action == "order" else "SUBSCRIBABLE->reserve(appointment)"
    print(f"  {dev.get('name')} ({device_id}) st{st} -> {label} mode {mode_id}")
    try:
        order = reserve.book(client, device_id, mode_id, cfg, action)
    except ApiError as e:
        # 10009 "busy device" can come back despite status=1; keep polling.
        print(f"  reserve rejected ({e}); still watching")
        return False, devices
    reserved.add(device_id)
    msg = (f"Reserved {dev.get('name')} ({device_id}) at laundry "
           f"{laundry_id} — orderId {order.get('orderId', '?')}. Pay + start in the app.")
    print("  " + msg)
    reserve.notify(msg, cfg.get("ntfy_url"))
    return True, devices


def ask_reserve_count(cfg: dict, interactive: bool) -> int:
    """How many machines to reserve before stopping. Prompts when interactive,
    else falls back to config max_reserves (cron/--once can't block on input)."""
    default = max(1, int(cfg.get("max_reserves", 1)))
    if not interactive:
        return default
    while True:
        raw = input(f"how many to reserve? [{default}]: ").strip()
        if not raw:
            return default
        if raw.isdigit() and int(raw) >= 1:
            return int(raw)
        print("  enter a whole number >= 1")


def run_loop(cfg: dict, once: bool = False) -> None:
    client = Client.from_config(cfg)
    auth.ensure_session(client, cfg["phone"], cfg["password"],
                        cfg.get("android_user_id", "null"), cfg["token_cache"])

    # Prompt for laundry only in an interactive session; --once and non-TTY
    # (cron) fall back to the config values so they never block on input().
    if once or not sys.stdin.isatty():
        laundry_id = str(cfg["laundry_id"])
        dev_type = str(cfg["dev_type"])
    else:
        laundry_id, dev_type = choose_laundry(client, cfg)

    race = bool(cfg.get("race_mode", False))
    floor = float(cfg.get("poll_floor_sec", 0.1))
    heartbeat = float(cfg.get("race_heartbeat_sec", 5))
    # race mode polls at `floor` constantly; otherwise honor poll_interval (with a
    # courteous 10s default floor when not racing).
    base = floor if race else max(MIN_INTERVAL, int(cfg.get("poll_interval_sec", 30)))
    # Ask how many to reserve in an interactive session; cron/--once use config.
    max_reserves = ask_reserve_count(cfg, interactive=not once and sys.stdin.isatty())
    reserved: set[str] = set()

    # Warm the mode cache for a concrete watchlist so the first FREE event books
    # at 0 extra RTT (no getDeviceInfo on the hot path).
    poll.prefetch_modes(client, cfg.get("target_device_ids") or [],
                        cfg.get("prefer_mode_id"))

    if race:
        print(f"RACE MODE: polling {laundry_id} every {floor}s, firing on status 1/2. "
              f"heartbeat {heartbeat}s.")

    last_sig = None
    last_beat = time.monotonic()
    while True:
        if not race:
            print(time.strftime("[%H:%M:%S] polling"))
        interval = base
        try:
            made, devices = find_and_reserve(cfg, client, laundry_id, dev_type,
                                             reserved, quiet=race)
            if made and len(reserved) >= max_reserves:
                print(f"reserved {len(reserved)}/{max_reserves} — done.")
                reserve.chime()
                return
            soonest = poll.soonest_free(devices)
            if race:
                # constant fast poll; print only on status change or heartbeat
                target_ids = set(map(str, cfg.get("target_device_ids") or []))
                sig = tuple((str(d.get("deviceId")), str(d.get("status")))
                            for d in devices
                            if not target_ids or str(d.get("deviceId")) in target_ids)
                now = time.monotonic()
                if sig != last_sig or now - last_beat >= heartbeat:
                    detail = " ".join(f"{did}:st{st}" for did, st in sig)
                    print(time.strftime(f"[%H:%M:%S] watching [{detail}]"))
                    last_sig = sig
                    last_beat = now
            else:
                interval = poll.pace(base, soonest, floor)
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

        if once:
            return
        time.sleep(interval)


def main(argv: list[str]) -> int:
    args = [a for a in argv[1:] if not a.startswith("--")]
    flags = {a for a in argv[1:] if a.startswith("--")}
    cfg_path = args[0] if args else "config.yaml"
    cfg = load_config(cfg_path)

    if "--discover" in flags:
        cmd_discover(cfg)
    elif "--orders" in flags:
        cmd_orders(cfg)
    elif "--menu" in flags:
        import menu
        menu.run_menu(cfg)
    else:
        run_loop(cfg, once="--once" in flags)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
