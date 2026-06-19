"""
App credentials + RSA keys for the request-signing envelope.

NO KEYS ARE SHIPPED. The values below are empty placeholders. To run washbot you
must supply the appId, appSecret, and the two RSA keys yourself, extracted from
your OWN copy of the app's APK (com.haier.globalwasher), e.g. with jadx:

    jadx_out/sources/i2/a.java
        f34099g  appId            -> APP_ID
        f34102h  appSecret        -> APP_SECRET
        f34108j  RSA public key   -> PUBLIC_KEY_B64  (X.509 SubjectPublicKeyInfo, base64 DER)
        f34111k  RSA private key  -> PRIVATE_KEY_B64 (PKCS#8 PrivateKeyInfo, base64 DER)
        f34096f  version          -> VERSION

These are CLIENT-SIDE constants baked into the public APK — the "signature" they
produce provides no real authenticity (anyone with the APK has them). washbot
reproduces the scheme only so the server accepts requests from your own account.

How to supply them: copy keys_local.example.py -> keys_local.py and fill it in.
keys_local.py is gitignored and overrides the placeholders below at import time.
"""

# --- placeholders (overridden by keys_local.py if present) ---
APP_ID = ""
APP_SECRET = ""
VERSION = "1.6"
PUBLIC_KEY_B64 = ""
PRIVATE_KEY_B64 = ""

# RSA block sizes for a 1024-bit key (m0.java): 117-byte plaintext / 128-byte cipher.
RSA_ENCRYPT_BLOCK = 117
RSA_DECRYPT_BLOCK = 128

# Load local secrets if present (gitignored). Overrides the empty placeholders.
try:
    from keys_local import *  # noqa: F401,F403
except ImportError:
    pass


def have_keys() -> bool:
    """True only when real key material has been supplied (via keys_local.py)."""
    return bool(APP_ID and APP_SECRET and PUBLIC_KEY_B64 and PRIVATE_KEY_B64)
