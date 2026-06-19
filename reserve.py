"""
Fire order.reserve and push a notification.

reserve params (presenter/x.java i()): deviceId, modeId, orderesource="1".
Returns OrderDetailsBeen; a successful hold lands at order status 101 (YUYUE).
After this you still pay + start manually in the real app.
"""

from __future__ import annotations

import requests

from client import Client


def reserve(client: Client, device_id: str, mode_id: str) -> dict:
    """order.reserve -> OrderDetailsBeen (the appointment/YUYUE hold, no payment)."""
    return client.call("reserve", {
        "deviceId": device_id,
        "modeId": mode_id,
        "orderesource": "1",
    }) or {}


def order_device(client: Client, device_id: str, mode_id: str,
                 is_rq: int = 1, run_count: str = "1",
                 order_devi_type: str | None = None) -> dict:
    """order.orderDevice -> OrderDetailsBeen.

    The app's primary "book this machine" path (presenter/x.java h()). Sends the
    same deviceId/modeId/orderesource as reserve, plus isRq (appointment flag,
    int->str) and runCount (wash-cycle count). orderDeviType only when present.
    """
    biz = {
        "deviceId": device_id,
        "modeId": mode_id,
        "isRq": str(is_rq),
        "orderesource": "1",
        "runCount": str(run_count),
    }
    if order_devi_type:
        biz["orderDeviType"] = order_devi_type
    return client.call("orderDevice", biz) or {}


def list_orders(client: Client, page: int = 1, order_type: str = "1") -> list[dict]:
    """order.getUserOrderList -> list of OrderDetailsBeen (account's orders)."""
    return client.call("userOrders", {
        "pageNumber": str(page),
        "orderType": order_type,
    }) or []


def order_detail(client: Client, order_id: str) -> dict:
    """order.getOrderDetail -> OrderDetailsBeen for one order."""
    return client.call("orderDetail", {"orderId": str(order_id)}) or {}


def cancel_order(client: Client, order_id: str) -> dict:
    """order.cancelOrder -> cancels an open order (frees the account to book again)."""
    return client.call("cancelOrder", {"orderId": str(order_id)}) or {}


def book(client: Client, device_id: str, mode_id: str, cfg: dict,
         action: str = "order") -> dict:
    """Dispatch to the right booking call for the device's current status.

    action="reserve" (device status 2, IN_USE_SUBSCRIBE) -> order.reserve, the
        YUYUE appointment that auto-books you for the next cycle (remote autobook).
    action="order"   (device status 1, FREE)             -> order.orderDevice,
        booked now. isRq=1 = remote (no QR scan), matching the app.

    cfg.book_method can force "reserve" regardless (debug / appointment-only mode).
    """
    if action == "reserve" or str(cfg.get("book_method", "")) == "reserve":
        return reserve(client, device_id, mode_id)
    return order_device(
        client, device_id, mode_id,
        is_rq=int(cfg.get("is_rq", 1)),
        run_count=str(cfg.get("run_count", "1")),
    )


def notify(message: str, ntfy_url: str | None) -> None:
    """Best-effort push via an ntfy topic URL (e.g. https://ntfy.sh/your-topic)."""
    if not ntfy_url:
        return
    try:
        requests.post(ntfy_url, data=message.encode("utf-8"), timeout=10)
    except requests.RequestException:
        pass
