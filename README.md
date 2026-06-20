# Haier Auto Booker

Auto-booker for the Haier **GlobalWasher** campus-laundry app
(`com.haier.globalwasher`). Polls a target laundromat and books a washing machine
for you the moment one is available, then notifies you. **You pay + start
manually** in the real app (payment goes through the app and is not automated).

Built by reproducing the app's signed-request envelope from the decompiled APK.

## ⚠️ Disclaimer — read before using

**This is an educational reverse-engineering project. Use at your own risk.**

- It drives the **private, undocumented API** of a third-party app by reproducing
  its request-signing scheme. Doing so **likely violates that app's Terms of
  Service** and may run afoul of computer-misuse / unauthorized-access laws in
  your jurisdiction. You alone are responsible for whether running it is lawful.
- **No keys are shipped.** This repo does **not** include the app's `appId`,
  `appSecret`, or RSA keys. You must extract them from your own copy of the APK,
  for your own account (see *Supply your keys* below).
- **Shared resource.** This automates booking of *shared* laundry machines. Fast
  polling ("race mode") loads a server you don't own and can deny machines to
  your neighbours. Race mode is **off by default** — leave it that way unless you
  understand and accept the consequences.
- **Intended scope:** your own single account, occasional personal use, courteous
  poll intervals. Nothing in the code enforces this — it's on you. Do **not** run
  it as a service for other people or across multiple accounts.
- Provided **as-is, no warranty**. If the vendor or your institution asks you to
  stop, stop. See `LICENSE` (non-commercial).

## How it works

Every request is wrapped in the app's envelope (`sign.py`, ported from
`modle/network/a.java` + `modle/utils/m0.java`):

```
timestamp  = now(GMT+8) yyyyMMddHHmmss
nonce      = base64(MD5("appId=..&appSecret=..&timestamp="+ts))
bizcontent = base64( RSA_encrypt(SERVER_PUBLIC_KEY, json(params)) )   # 117-byte chunks
sign       = base64( SHA1withRSA(APP_PRIVATE_KEY, raw_cipher_bytes) )
POST form: <raw params> + tokenId appId method format charset version
           country language timeZone nonce timestamp bizcontent sign
```

Two **separate** embedded keypairs (you supply both in `keys_local.py`):
- `SERVER_PUBLIC_KEY` — encrypts `bizcontent` (server decrypts; we can't).
- `APP_PRIVATE_KEY` — signs requests (server verifies). Also decrypts the
  RSA-encrypted `retData` in responses.

**Booking is status-aware** (mirrors the app's wash-button branch):

| device status | meaning | washbot action |
|---|---|---|
| `1` FREE | idle, bookable now | `order.orderDevice` (book now) |
| `2` IN_USE_SUBSCRIBE | running, next-cycle subscription slot open | `order.reserve` → a YUYUE appointment (you're booked for the next cycle) |
| `3` IN_USE_NOT_SUBSCRIBE | running, slot already taken | wait |

At busy laundries machines rarely hit FREE — they cycle `2 ↔ 3`. The remote
autobook path is catching the brief **status-2 window** and firing `order.reserve`
before anyone else. That window is a race (see *Race mode*).

Flow: `login` → `tokenId` → poll `laundry.getLaundryDeviceList` → on a bookable
machine, resolve a `modeId` → book (`orderDevice`/`reserve`) → notify.

## Install

```sh
python3 -m venv .venv && ./.venv/bin/pip install -r requirements.txt
cp config.example.yaml config.yaml          # then edit (account + target)
```

Requires **Python 3.10+**.

## Supply your keys

The repo ships `keys.py` with empty placeholders — no key material is included.
Extract the constants from your own APK (e.g. with [jadx](https://github.com/skylot/jadx)),
then:

```sh
cp keys_local.example.py keys_local.py       # then fill in
```

`keys_local.py` is gitignored and overrides the placeholders at import. The jadx
field → constant mapping is documented in the header of `keys.py`.

> **This is the hard part, and it is version-specific.** The obfuscated field
> names in `keys.py` (e.g. `f34108j`) come from one APK build and will differ in
> yours — you must locate the appId/appSecret/RSA-key constants in the
> network-signing class yourself. **If you can't extract them, the tool cannot
> run.** There is no shipped key and no other source.

## Validate the crypto

```sh
./.venv/bin/python test_sign.py              # offline mechanical check
./.venv/bin/python test_units.py             # pure-logic unit tests
```

`test_sign.py` PASSES even before you supply keys (it falls back to an ephemeral
test keypair). `test_units.py` needs no keys. To validate against your *real*
keys, run after filling in `keys_local.py`.

The only thing the offline test can't prove is that the **server** accepts a
request. To validate end-to-end, capture one real request from the app with
mitmproxy/Charles and confirm:

```sh
./.venv/bin/python test_sign.py --capture <timestamp> <nonce> <bizcontent_b64> <sign_b64>
```

## Usage

```sh
./.venv/bin/python main.py --discover     # list nearby laundryIds (needs lon/lat in config)
./.venv/bin/python main.py --menu         # interactive: pick laundry/machine/mode, then watch
./.venv/bin/python main.py --orders       # list your account's orders (spot a stuck [10009])
./.venv/bin/python main.py --once         # single poll pass, no loop
./.venv/bin/python main.py                # config-driven loop until max_reserves reached
```

Easiest first run: `--discover` to get a `laundry_id`, then `--menu`.

### Race mode

`race_mode: true` makes the loop ignore `poll_interval_sec` (and the 10s floor)
and poll at `poll_floor_sec` constantly to win the status-2 window. **Off by
default.** It loads the server hard and can trip rate-limiting/bans — only enable
it if you accept that, and keep `poll_floor_sec` as high as you can tolerate.

## Files

| file | role |
|---|---|
| `keys.py` | key placeholders + loader (you supply real keys via `keys_local.py`) |
| `keys_local.example.py` | template for your extracted keys |
| `sign.py` | request envelope: nonce / chunked RSA encrypt / SHA1withRSA sign / response decrypt |
| `client.py` | base hosts, endpoint map, POST/GET + BaseData parsing |
| `auth.py` | login + tokenId cache/refresh |
| `poll.py` | nearby/device listing + status routing + modeId resolution + pacing |
| `reserve.py` | `orderDevice` / `reserve` / order list/cancel + ntfy push |
| `main.py` | config-driven loop / `--discover` / `--menu` / `--orders` / `--once` |
| `menu.py` | interactive laundry/machine/mode picker |
| `test_sign.py` | offline + capture-based signature validation |

## Confirmed against live traffic

- Request signing port — offline self-test + captured-request validation pass.
- Responses are encrypted: `retData` is base64 RSA-ciphertext, decrypted with the
  app private key automatically (`sign.decrypt_response`).
- HTTP verb matters: laundry reads are **GET**, login/order are **POST**.
- `phoneNumber` format = `00<countrycode><number>` (no `+`). `type="2"` =
  phone+password. `androidUserId` may be the literal `"null"`.
- Login, discovery, device list, status detection, mode lookup — validated live.
- `order.reserve` end-to-end — validated live (status `101` YUYUE appointment).

## License

`LICENSE` — **PolyForm Noncommercial 1.0.0**. Personal/non-commercial use only;
no warranty. This does not cure the ToS/legal concerns in the disclaimer above.

Required Notice: Copyright 2026 Celibistrial.
