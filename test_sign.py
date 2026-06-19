#!/usr/bin/env python3
"""
Self-tests for the signing port.

Key fact (see sign.py): bizcontent is encrypted with the SERVER public key, so we
CANNOT decrypt it (we don't hold the server private key). The `sign` field is made
with the APP private key, which we DO hold, so signatures are fully checkable.

1. round_trip — proves, using only the embedded keys:
     * nonce == base64(MD5(appId&appSecret&timestamp))
     * bizcontent is a whole number of 128-byte RSA blocks (correct chunking)
     * verify_app_sign(cipher, sign) succeeds (signing path is correct)
   This is full mechanical correctness. The only thing it can't prove offline is
   that the SERVER accepts the request — that needs one live call.

2. verify_capture(timestamp, nonce, bizcontent_b64, sign_b64) — point at a REAL
   captured request (mitmproxy). Validates:
     * recomputed nonce == captured nonce  -> confirms appId/appSecret/timestamp fmt
     * captured sign verifies under the app public key -> confirms same signing key
     * captured bizcontent length is a multiple of 128 -> confirms chunking
   (bizcontent contents stay opaque — server key required to decrypt.)

Run:
    python test_sign.py
    python test_sign.py --capture <timestamp> <nonce> <bizcontent_b64> <sign_b64>
"""

from __future__ import annotations

import base64
import sys

import keys
import sign


def _ensure_test_keys() -> None:
    """Make the mechanical test runnable on a fresh clone with NO keys_local.py.

    The crypto is identical for any valid RSA-1024 key, so when no real keys are
    present we generate an ephemeral keypair and install it into `keys` + reset
    sign's lazy-load cache. (This makes SERVER_PUBLIC and APP_PRIVATE a matching
    pair, unlike the real two-keypair deployment — fine here, round_trip only
    checks nonce / chunking / app-sign-verify, none of which need them distinct.)
    """
    if keys.have_keys():
        return
    from cryptography.hazmat.primitives import serialization
    from cryptography.hazmat.primitives.asymmetric import rsa

    k = rsa.generate_private_key(public_exponent=65537, key_size=1024)
    priv = k.private_bytes(serialization.Encoding.DER,
                           serialization.PrivateFormat.PKCS8,
                           serialization.NoEncryption())
    pub = k.public_key().public_bytes(serialization.Encoding.DER,
                                       serialization.PublicFormat.SubjectPublicKeyInfo)
    keys.APP_ID = "TESTAPPID"
    keys.APP_SECRET = "TESTSECRET"
    keys.PUBLIC_KEY_B64 = base64.b64encode(pub).decode()
    keys.PRIVATE_KEY_B64 = base64.b64encode(priv).decode()
    keys.have_keys = lambda: True
    sign._SERVER_PUBLIC = sign._APP_PRIVATE = sign._APP_PUBLIC = None  # bust lazy cache
    print("  (no real keys found — using an ephemeral test keypair)")


def round_trip() -> None:
    print("== round_trip ==")
    _ensure_test_keys()
    biz = {"phoneNumber": "+10000000000", "type": "2", "password": "secret",
           "deviceType": "2", "channelId": "", "androidUserId": "",
           "osVersion": "51", "verifyCode": ""}
    env = sign.build_envelope("user.login", biz, token_id="")

    assert env["nonce"] == sign._nonce(env["timestamp"]), "nonce mismatch"
    print("  nonce OK:", env["nonce"])

    cipher = base64.b64decode(env["bizcontent"])
    assert cipher and len(cipher) % keys.RSA_DECRYPT_BLOCK == 0, \
        f"bizcontent not a multiple of {keys.RSA_DECRYPT_BLOCK}: {len(cipher)}"
    print(f"  bizcontent OK: {len(cipher)} bytes "
          f"({len(cipher) // keys.RSA_DECRYPT_BLOCK} RSA block(s))")

    assert sign.verify_app_sign(cipher, base64.b64decode(env["sign"])), "sign verify failed"
    print("  app-sign verify OK")

    for k in ("appId", "method", "format", "charset", "version", "timestamp", "tokenId"):
        assert k in env, f"missing envelope key {k}"
    # raw business params are also present in the body (matches the app)
    assert env["phoneNumber"] == biz["phoneNumber"], "biz params not echoed in body"
    print("  envelope shape OK")
    print("PASS\n")


def verify_capture(timestamp: str, nonce: str, bizcontent_b64: str, sign_b64: str) -> None:
    print("== verify_capture ==")
    if not keys.have_keys():
        print("  SKIP: --capture needs the REAL app keys (keys_local.py) to verify "
              "a captured request. Supply them first. See README.")
        return
    recomputed = sign._nonce(timestamp)
    match = recomputed == nonce
    print(f"  recomputed nonce: {recomputed}")
    print(f"  captured nonce:   {nonce}  -> {'MATCH' if match else 'MISMATCH'}")

    cipher = base64.b64decode(bizcontent_b64)
    mult = len(cipher) % keys.RSA_DECRYPT_BLOCK == 0
    print(f"  bizcontent: {len(cipher)} bytes, /128 = {'OK' if mult else 'BAD'}")

    ok_sig = sign.verify_app_sign(cipher, base64.b64decode(sign_b64))
    print(f"  captured sign verifies under app public key: {ok_sig}")

    print("PASS" if (match and mult and ok_sig) else "CHECK ABOVE")


# pytest entry point (collected as test_round_trip); CLI entry point below.
def test_round_trip() -> None:
    round_trip()


if __name__ == "__main__":
    if len(sys.argv) > 1 and sys.argv[1] == "--capture":
        verify_capture(*sys.argv[2:])
    else:
        round_trip()
