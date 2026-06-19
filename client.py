"""
HTTP client for the GlobalWashCall API. Wraps sign.build_envelope, POSTs
form-urlencoded, parses the BaseData envelope:

    { "retCode":"00000", "retData":{...}, "retInfo":"", "retSign":"...", "sysTime":... }

retCode "00000" = success. "10017"/"10001" = session invalid -> re-login.
"""

from __future__ import annotations

import json

import requests

import sign

# Base hosts (i2/a.java). NOTE: base ends with "/" and paths begin with "/",
# producing a double slash "GlobalWashCallApi//..." — this exactly matches what
# the app builds (f34087c + "/common/..."), so we keep it.
BASE_PROD = "https://www.mrhiwash.com/GlobalWashCallApi/"
BASE_TEST = "https://test.mrhi.cn/GlobalWashCallApi/"

# logical name -> (method, path, http_verb).
# http_verb mirrors the volley method int in the decompiled calls: .g(1,..)=POST,
# .j(0,..)=GET. Login/order are POST; laundry reads are GET.
ENDPOINTS = {
    "login":            ("user.login",                  "/common/user/login.api",             "POST"),
    "getProfile":       ("user.getProfile",             "/api/user/getProfile.api",           "GET"),
    "nearbyLaundry":    ("laundry.getNearbyLaundryList", "/common/laundry/getNearbyLaundryList.api", "GET"),
    "laundryDetail":    ("laundry.getLaundryDetail",     "/common/laundry/getLaundryDetail.api", "GET"),
    "laundryDevices":   ("laundry.getLaundryDeviceList", "/common/laundry/getLaundryDeviceList.api", "GET"),
    "deviceInfo":       ("laundry.getDeviceInfo",        "/common/laundry/getDeviceInfo.api",  "GET"),
    "reserve":          ("order.reserve",                "/api/order/reserve.api",             "POST"),
    "orderDevice":      ("order.orderDevice",            "/api/order/orderDevice.api",         "POST"),
    "userOrders":       ("order.getUserOrderList",       "/api/order/getUserOrderList.api",    "GET"),
    "orderDetail":      ("order.getOrderDetail",         "/api/order/getOrderDetail.api",      "GET"),
    "cancelOrder":      ("order.cancelOrder",            "/api/order/cancelOrder.api",         "POST"),
}

SUCCESS = "00000"
SESSION_DEAD = {"10017", "10001"}


class ApiError(Exception):
    def __init__(self, ret_code, ret_info):
        super().__init__(f"[{ret_code}] {ret_info}")
        self.ret_code = ret_code
        self.ret_info = ret_info


class SessionExpired(ApiError):
    pass


class Client:
    def __init__(
        self,
        prod: bool = True,
        token_id: str = "",
        country: str = "US",
        language: str = "en",
        time_zone: str = "Greenwich Mean Time",
        timeout: int = 30,
        user_agent: str = "okhttp/3.12.1",
    ):
        self.base = BASE_PROD if prod else BASE_TEST
        self.token_id = token_id
        self.country = country
        self.language = language
        self.time_zone = time_zone
        self.timeout = timeout
        self.http = requests.Session()
        self.http.headers["User-Agent"] = user_agent

    @classmethod
    def from_config(cls, cfg: dict) -> "Client":
        """Single source of truth for client construction (used by every entrypoint)."""
        return cls(
            prod=cfg.get("prod", True),
            country=cfg.get("country", "US"),
            language=cfg.get("language", "en"),
            time_zone=cfg.get("time_zone", "GMT"),
            # fail fast: a hung request shouldn't stall the race for 30s
            timeout=int(cfg.get("request_timeout_sec", 10)),
        )

    def call(self, name: str, biz_params: dict | None = None) -> dict:
        """Fire a signed request. Returns retData (the inner payload).

        Raises SessionExpired on 10017/10001, ApiError on any other non-00000.
        """
        if name not in ENDPOINTS:
            raise KeyError(f"unknown endpoint {name!r}")
        method, path, verb = ENDPOINTS[name]
        url = self.base + path  # intentional double slash, see note above
        params = sign.build_envelope(
            method,
            biz_params or {},
            token_id=self.token_id,
            country=self.country,
            language=self.language,
            time_zone=self.time_zone,
        )
        if verb == "GET":
            resp = self.http.get(url, params=params, timeout=self.timeout)
        else:
            resp = self.http.post(url, data=params, timeout=self.timeout)
        resp.raise_for_status()
        try:
            body = resp.json()
        except ValueError:
            # gateway/HTML error page, empty body, etc. — surface as ApiError so
            # the poll loops (which catch ApiError) don't crash on a bad response.
            snippet = resp.text[:200].replace("\n", " ")
            raise ApiError("BADJSON", f"non-JSON response: {snippet!r}")

        ret_code = body.get("retCode")
        ret_info = body.get("retInfo", "")
        if ret_code == SUCCESS:
            raw = body.get("retData")
            if not raw:
                return None
            # retData is base64 RSA-ciphertext; decrypt with app private key -> JSON.
            return json.loads(sign.decrypt_response(raw))
        if ret_code in SESSION_DEAD:
            raise SessionExpired(ret_code, ret_info)
        raise ApiError(ret_code, ret_info)
