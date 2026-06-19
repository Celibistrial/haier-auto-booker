"""
Faithful port of the request-signing envelope built by the app's network layer
(jadx_out/sources/com/haier/globalwasher/modle/network/a.java -> static method m(),
plus the crypto helpers in .../modle/utils/m0.java).

For each request the app does:

    ts         = now(GMT+8) "yyyyMMddHHmmss"
    nonce      = base64( MD5("appId=<id>&appSecret=<secret>&timestamp=" + ts) )
    bizJSON    = JSONObject(biz_params).toString()            # only the business params
    cipher     = RSA_PKCS1v15_encrypt(PUBLIC_KEY, bizJSON)    # chunked at 117 bytes
    bizcontent = base64(cipher)
    sign       = base64( SHA1withRSA(PRIVATE_KEY, cipher) )   # signs the RAW cipher bytes

Then POSTs (form-urlencoded) the business params *plus* these envelope fields:
    tokenId appId method format=JSON charset=UTF-8 version country language
    timeZone nonce timestamp bizcontent sign

Note: the app leaves the raw business params in the body alongside bizcontent
(method m() never strips them). We replicate that for maximum compatibility; the
server treats the signed bizcontent as the source of truth.
"""

from __future__ import annotations

import base64
import hashlib
import json
from datetime import datetime, timedelta, timezone

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import padding

import keys

# Envelope keys that must NOT be folded into bizcontent (mirrors the reserved-key
# check in a.m(): the loop skips exactly these when building the JSON).
RESERVED = {
    "tokenId", "appId", "timestamp", "nonce", "method", "format", "charset",
    "version", "country", "language", "bizcontent", "outAccess", "sign", "timeZone",
}

# IMPORTANT: these are TWO SEPARATE keypairs, not a matching pair.
#   _SERVER_PUBLIC (f34108j): the SERVER's public key. We RSA-encrypt bizcontent
#       with it; only the server's private key (which we don't have) can decrypt.
#       It's also the key used to verify server response signatures (m0.e / retSign).
#   _APP_PRIVATE  (f34111k): the APP's signing private key. We sign the bizcontent
#       bytes with it; the server holds the matching app public key to verify.
# Consequence: we can PRODUCE valid requests but cannot decrypt our own (or a
# captured) bizcontent. The only end-to-end proof is the server accepting a call.
#
# Loaded lazily: keys.py ships with empty placeholders (no keys committed), so
# importing this module must not fail. The keys are parsed on first crypto use;
# a clear error fires then if you haven't supplied them via keys_local.py.
_SERVER_PUBLIC = None
_APP_PRIVATE = None
_APP_PUBLIC = None  # counterpart for verifying our own sign

_NO_KEYS_MSG = (
    "No RSA key material. washbot ships without keys. Copy "
    "keys_local.example.py -> keys_local.py and fill in the appId, appSecret, "
    "and RSA keys extracted from your OWN APK. See keys.py for the field mapping."
)


def _load_keys() -> None:
    """Parse the embedded keys on first use. Raises a clear error if absent."""
    global _SERVER_PUBLIC, _APP_PRIVATE, _APP_PUBLIC
    if _APP_PRIVATE is not None:
        return
    if not keys.have_keys():
        raise RuntimeError(_NO_KEYS_MSG)
    _SERVER_PUBLIC = serialization.load_der_public_key(
        base64.b64decode(keys.PUBLIC_KEY_B64))
    _APP_PRIVATE = serialization.load_der_private_key(
        base64.b64decode(keys.PRIVATE_KEY_B64), password=None)
    _APP_PUBLIC = _APP_PRIVATE.public_key()


def _timestamp(now: datetime | None = None) -> str:
    """yyyyMMddHHmmss in GMT+8 (SimpleDateFormat with TimeZone GMT+08)."""
    now = now or datetime.now(timezone(timedelta(hours=8)))
    return now.strftime("%Y%m%d%H%M%S")


def _nonce(ts: str) -> str:
    raw = f"appId={keys.APP_ID}&appSecret={keys.APP_SECRET}&timestamp={ts}".encode()
    return base64.b64encode(hashlib.md5(raw).digest()).decode()


def _rsa_encrypt(plaintext: bytes) -> bytes:
    """RSA/ECB/PKCS1Padding with the SERVER public key, chunked at 117-byte
    blocks, concatenated (m0.b)."""
    _load_keys()
    out = bytearray()
    for i in range(0, len(plaintext), keys.RSA_ENCRYPT_BLOCK):
        block = plaintext[i:i + keys.RSA_ENCRYPT_BLOCK]
        out += _SERVER_PUBLIC.encrypt(block, padding.PKCS1v15())
    return bytes(out)


def _rsa_sign(data: bytes) -> bytes:
    """SHA1withRSA over the raw ciphertext bytes, with the APP private key (m0.d)."""
    _load_keys()
    return _APP_PRIVATE.sign(data, padding.PKCS1v15(), hashes.SHA1())


def verify_app_sign(cipher: bytes, signature: bytes) -> bool:
    """Verify a signature made by the app private key, using its derived public
    counterpart. Confirms our signing path is mechanically correct and that a
    captured `sign` was produced by this same embedded key."""
    _load_keys()
    try:
        _APP_PUBLIC.verify(signature, cipher, padding.PKCS1v15(), hashes.SHA1())
        return True
    except InvalidSignature:
        return False


def decrypt_response(b64: str) -> str:
    """Decrypt a server response payload (BaseData.retData / BaseListDate.retData).

    The server encrypts the payload with the APP public key, so we decrypt with the
    APP private key (128-byte blocks). Confirmed against live login traffic.
    """
    _load_keys()
    ct = base64.b64decode(b64)
    out = bytearray()
    for i in range(0, len(ct), keys.RSA_DECRYPT_BLOCK):
        out += _APP_PRIVATE.decrypt(ct[i:i + keys.RSA_DECRYPT_BLOCK], padding.PKCS1v15())
    return out.decode("utf-8")


def verify_server_sign(payload: bytes, signature: bytes) -> bool:
    """Verify a SERVER response signature (BaseData.retSign) with the server
    public key (m0.e). For later use when validating responses."""
    _load_keys()
    try:
        _SERVER_PUBLIC.verify(signature, payload, padding.PKCS1v15(), hashes.SHA1())
        return True
    except InvalidSignature:
        return False


def biz_json(biz_params: dict) -> str:
    """Compact JSON matching org.json JSONObject.toString() (no spaces)."""
    # Only non-reserved keys go into bizcontent (a.m() filters reserved keys out).
    payload = {k: str(v) for k, v in biz_params.items() if k not in RESERVED}
    return json.dumps(payload, separators=(",", ":"), ensure_ascii=False)


# Cache (bizcontent, sign) keyed by the biz_json string. cipher+sign depend ONLY
# on the business params (timestamp/nonce live in the envelope, not the cipher),
# so identical polls reuse them and skip 2 of 3 RSA ops. A fresh random-padded
# cipher per call is not required — the server just decrypts whatever it gets and
# verifies sign against that cipher. Bounded to avoid unbounded growth.
_ENVELOPE_CACHE: dict[str, tuple[str, str]] = {}
_ENVELOPE_CACHE_MAX = 64


def build_envelope(
    method: str,
    biz_params: dict,
    token_id: str = "",
    country: str = "US",
    language: str = "en",
    time_zone: str = "Greenwich Mean Time",
    now: datetime | None = None,
) -> dict:
    """Return the full form-POST parameter dict for one signed request."""
    ts = _timestamp(now)
    nonce = _nonce(ts)

    bj = biz_json(biz_params)
    cached = _ENVELOPE_CACHE.get(bj)
    if cached is None:
        cipher = _rsa_encrypt(bj.encode("utf-8"))
        bizcontent = base64.b64encode(cipher).decode()
        sign = base64.b64encode(_rsa_sign(cipher)).decode()
        if len(_ENVELOPE_CACHE) >= _ENVELOPE_CACHE_MAX:
            _ENVELOPE_CACHE.clear()
        _ENVELOPE_CACHE[bj] = (bizcontent, sign)
    else:
        bizcontent, sign = cached

    # Raw business params first (replicates app: they stay in the body), then envelope.
    out = {k: str(v) for k, v in biz_params.items()}
    out.update({
        "bizcontent": bizcontent,
        "sign": sign,
        "nonce": nonce,
        "tokenId": token_id or "",
        "timestamp": ts,
        "appId": keys.APP_ID,
        "method": method,
        "format": "JSON",
        "charset": "UTF-8",
        "version": keys.VERSION,
        "country": country,
        "language": language,
        "timeZone": time_zone,
    })
    return out
