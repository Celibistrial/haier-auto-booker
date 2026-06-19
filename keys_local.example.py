"""
Template for keys_local.py (which is gitignored). Copy this file:

    cp keys_local.example.py keys_local.py

then fill in the values extracted from your OWN copy of the app's APK. See the
header of keys.py for the exact jadx field -> constant mapping.
"""

APP_ID = "your-app-id"
APP_SECRET = "your-app-secret"
VERSION = "1.6"

# X.509 SubjectPublicKeyInfo (base64 DER) — RSA-encrypts bizcontent.
PUBLIC_KEY_B64 = "your-base64-der-public-key"

# PKCS#8 PrivateKeyInfo (base64 DER) — SHA1withRSA-signs the bizcontent bytes.
PRIVATE_KEY_B64 = "your-base64-der-private-key"
