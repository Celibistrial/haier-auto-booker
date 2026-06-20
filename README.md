# Haier Auto Booker

Auto-booker for the Haier **GlobalWasher** campus-laundry app
(`com.haier.globalwasher`, behind `mrhiwash.com`). It watches a laundromat and
books a washing machine the moment one is available, then notifies you. You pay
and start the wash manually in the real app — payment is never automated.

Built by reproducing the app's signed-request envelope from the decompiled APK.

## Disclaimer

This is an educational reverse-engineering project. Use it at your own risk.

- It drives a third-party app's private, undocumented API by reproducing its
  request-signing scheme. That likely violates the app's Terms of Service, and
  may run afoul of computer-misuse or unauthorized-access laws where you live.
  Whether running it is lawful is your call and your responsibility.
- No keys ship with this repo — not the app's `appId`, `appSecret`, or RSA keys.
  You supply your own, extracted from your own copy of the APK, for your own
  account (see *Supply your keys*).
- These are shared machines. Fast polling ("race mode") loads a server you do
  not own and can deny machines to your neighbours. Race mode is off by default;
  leave it off unless you understand and accept the trade-off.
- Intended for your own single account, occasional personal use, with courteous
  poll intervals. Nothing in the code enforces this. Do not run it as a service
  for other people or across multiple accounts.
- Provided as-is, with no warranty. If the vendor or your institution asks you
  to stop, stop. See `LICENSE` (non-commercial).

## Install

```sh
python3 -m venv .venv && ./.venv/bin/pip install -r requirements.txt
cp config.example.yaml config.yaml          # then edit: account + target
```

Requires Python 3.10+.

## Supply your keys

The repo ships `keys.py` with empty placeholders — no key material is included.
Extract the constants from your own APK (e.g. with
[jadx](https://github.com/skylot/jadx)), then:

```sh
cp keys_local.example.py keys_local.py       # then fill in
```

`keys_local.py` is gitignored and overrides the placeholders at import. The jadx
field → constant mapping is documented in the header of `keys.py`.

This is the hard part, and it is version-specific. The obfuscated field names in
`keys.py` (e.g. `f34108j`) come from one APK build and will differ in yours — you
must locate the appId, appSecret, and RSA-key constants in the network-signing
class yourself. If you cannot extract them, the tool cannot run. There is no
shipped key and no other source.

## How it works

Every request is wrapped in the app's envelope (`sign.py`, ported from
`modle/network/a.java` and `modle/utils/m0.java`):

```
timestamp  = now(GMT+8) yyyyMMddHHmmss
nonce      = base64(MD5("appId=..&appSecret=..&timestamp="+ts))
bizcontent = base64( RSA_encrypt(SERVER_PUBLIC_KEY, json(params)) )   # 117-byte chunks
sign       = base64( SHA1withRSA(APP_PRIVATE_KEY, raw_cipher_bytes) )
POST form: <raw params> + tokenId appId method format charset version
           country language timeZone nonce timestamp bizcontent sign
```

Two separate embedded keypairs (you supply both in `keys_local.py`):

- `SERVER_PUBLIC_KEY` — encrypts `bizcontent` (the server decrypts; we cannot).
- `APP_PRIVATE_KEY` — signs requests (the server verifies). Also decrypts the
  RSA-encrypted `retData` in responses.

Booking is status-aware, mirroring the app's wash-button branch:

| device status | meaning | action |
|---|---|---|
| `1` FREE | idle, bookable now | `order.orderDevice` (book now) |
| `2` IN_USE_SUBSCRIBE | running, next-cycle slot open | `order.reserve` → a YUYUE appointment (booked for the next cycle) |
| `3` IN_USE_NOT_SUBSCRIBE | running, slot already taken | wait |

At busy laundries machines rarely hit FREE — they cycle `2 ↔ 3`. The trick is
catching the brief status-2 window and firing `order.reserve` before anyone
else. That window is a race (see *Race mode*).

Flow: `login` → `tokenId` → poll `laundry.getLaundryDeviceList` → on a bookable
machine, resolve a `modeId` → book (`orderDevice` / `reserve`) → notify.

## Validate the crypto

Both checks are keyless — run them right after cloning:

```sh
./.venv/bin/python test_sign.py              # offline mechanical check
./.venv/bin/python test_units.py             # pure-logic unit tests
```

`test_sign.py` passes even before you supply keys (it falls back to an ephemeral
test keypair); `test_units.py` needs no keys. To validate against your real keys,
run again after filling in `keys_local.py`.

The offline test cannot prove the server accepts a request. To validate
end-to-end, capture one real request from the app with mitmproxy/Charles:

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
and poll at `poll_floor_sec` constantly to win the status-2 window. It is off by
default. It loads the server hard and can trip rate-limiting or bans — only
enable it if you accept that, and keep `poll_floor_sec` as high as you can
tolerate.

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
| `test_units.py` | unit tests for the pure decision logic |

## Confirmed against live traffic

- Signing port — offline self-test and captured-request validation pass.
- Responses are encrypted: `retData` is base64 RSA-ciphertext, decrypted with the
  app private key automatically (`sign.decrypt_response`).
- HTTP verb matters: laundry reads are GET, login/order are POST.
- `phoneNumber` format = `00<countrycode><number>` (no `+`). `type="2"` =
  phone+password. `androidUserId` may be the literal `"null"`.
- Login, discovery, device list, status detection, mode lookup — validated live.
- `order.reserve` end-to-end — validated live (status `101` YUYUE appointment).

## License

`LICENSE` — PolyForm Noncommercial 1.0.0. Personal, non-commercial use only; no
warranty. This does not cure the ToS/legal concerns in the disclaimer above.

Required Notice: Copyright 2026 Celibistrial.
