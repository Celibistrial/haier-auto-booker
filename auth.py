"""
Login + tokenId caching.

Login params reproduced from presenter/e.java d():
    phoneNumber, type, deviceType="2", channelId="", androidUserId,
    osVersion=<app versionCode>, password=<plaintext>, verifyCode=""

type "2" = phone + password (from LoginActivity, which calls d(..,"2",password,..)).
Success retData (LoginResultBeen): { tokenId, userId, userName, phoneNumber, avatarURL }.

The tokenId is persisted to a cache file and reused until the server returns
10017/10001, at which point we re-login.
"""

from __future__ import annotations

import json
import os
import tempfile
import time

import requests

from client import ApiError, Client, SessionExpired

# App versionCode (apk: com-haier-globalwasher-51-...). osVersion param = versionCode.
APP_VERSION_CODE = "51"


def login(
    client: Client,
    phone_number: str,
    password: str,
    android_user_id: str = "null",  # app sends literal "null" when none set
    login_type: str = "2",
) -> dict:
    """Perform login, set client.token_id, return retData."""
    biz = {
        "phoneNumber": phone_number,
        "type": login_type,
        "deviceType": "2",
        "channelId": "",
        "androidUserId": android_user_id,
        "osVersion": APP_VERSION_CODE,
        "password": password,
        "verifyCode": "",
    }
    data = client.call("login", biz)
    token = (data or {}).get("tokenId", "")
    if not token:
        raise RuntimeError(f"login returned no tokenId: {data!r}")
    client.token_id = token
    return data


def load_token(cache_path: str) -> str:
    if not os.path.exists(cache_path):
        return ""
    try:
        with open(cache_path) as f:
            return json.load(f).get("tokenId", "")
    except (OSError, ValueError):
        return ""


def save_token(cache_path: str, data: dict) -> None:
    rec = {
        "tokenId": data.get("tokenId", ""),
        "userId": data.get("userId", ""),
        "userName": data.get("userName", ""),
        "savedAt": int(time.time()),
    }
    # Write 0o600 from creation (no world-readable window) and atomically replace,
    # so a crash mid-write can't corrupt the cache. The file holds a live token.
    d = os.path.dirname(os.path.abspath(cache_path))
    fd, tmp = tempfile.mkstemp(dir=d, prefix=".token.", suffix=".tmp")
    try:
        os.fchmod(fd, 0o600)
        with os.fdopen(fd, "w") as f:
            json.dump(rec, f, indent=2)
        os.replace(tmp, cache_path)
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


def ensure_session(client: Client, phone: str, password: str,
                   android_user_id: str, cache_path: str) -> None:
    """Use cached token; if missing or rejected, log in and cache the new one."""
    cached = load_token(cache_path)
    if cached:
        client.token_id = cached
        try:
            client.call("getProfile", {})  # cheap validity probe
            return
        except SessionExpired:
            pass  # token rejected -> re-login below
        except (ApiError, requests.exceptions.RequestException) as e:
            # Probe failed for a non-session reason (network blip, 5xx, bad JSON).
            # Don't burn a re-login on a transient error — trust the cached token
            # and let the main loop's own error handling retry the real call.
            print(f"  token validity probe failed ({e}); keeping cached token")
            return
    data = login(client, phone, password, android_user_id)
    save_token(cache_path, data)
