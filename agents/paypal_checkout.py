from __future__ import annotations

import base64
import json
import os
import re
import urllib.error
import urllib.request
from decimal import Decimal, InvalidOperation
from typing import Any

from dotenv import load_dotenv

load_dotenv()

SANDBOX_API = "https://api-m.sandbox.paypal.com"
PRODUCTION_API = "https://api-m.paypal.com"


class PayPalError(RuntimeError):
    """A safe, user-facing PayPal integration error."""


def _api_base() -> str:
    environment = os.getenv("PAYPAL_ENVIRONMENT", "SANDBOX").strip().upper()
    if environment == "SANDBOX":
        return SANDBOX_API
    if environment == "PRODUCTION":
        return PRODUCTION_API
    raise PayPalError("PAYPAL_ENVIRONMENT must be SANDBOX or PRODUCTION.")


def _credentials() -> tuple[str, str]:
    client_id = os.getenv("PAYPAL_CLIENT_ID", "BAAj16DlFUvO9zn_bxnUlxBMp9OqYpvXRbJ1GCOmRnOOC6nIKPMoRCwPCPI6RIKkgnMk2F4iXNW6TcGHcQ").strip()
    secret = os.getenv("PAYPAL_CLIENT_SECRET", "ECJOGYyZSQ_0sStHmrASEdfyGRAEWPS0bW9ysDjiIMvUTFpPv3mj34NLuLlyqGTbTsz_tkW0Yz9Mv3MC").strip()
    if not client_id or not secret:
        raise PayPalError("Set PAYPAL_CLIENT_ID and PAYPAL_CLIENT_SECRET in your environment.")
    return client_id, secret


def _request_json(method: str, path: str, *, token: str | None = None,
                  body: dict[str, Any] | None = None,
                  basic_auth: tuple[str, str] | None = None) -> dict[str, Any]:
    headers = {"Accept": "application/json", "Content-Type": "application/json"}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    if basic_auth:
        raw = f"{basic_auth[0]}:{basic_auth[1]}".encode("utf-8")
        headers["Authorization"] = "Basic " + base64.b64encode(raw).decode("ascii")
    data = None if body is None else json.dumps(body).encode("utf-8")
    request = urllib.request.Request(_api_base() + path, data=data, headers=headers, method=method)
    try:
        with urllib.request.urlopen(request, timeout=20) as response:
            raw_response = response.read().decode("utf-8")
            return json.loads(raw_response) if raw_response else {}
    except urllib.error.HTTPError as exc:
        try:
            detail = json.loads(exc.read().decode("utf-8"))
            message = detail.get("message") or detail.get("name") or "request rejected"
            issue = detail.get("details", [{}])[0].get("issue") if detail.get("details") else None
            suffix = f" ({issue})" if issue else ""
        except Exception:
            message, suffix = "request rejected", ""
        raise PayPalError(f"PayPal API error: {message}{suffix}.") from None
    except (urllib.error.URLError, TimeoutError, json.JSONDecodeError):
        raise PayPalError("Could not reach PayPal. Check your connection and try again.") from None


def _access_token() -> str:
    client_id, secret = _credentials()
    response = _request_json(
        "POST", "/v1/oauth2/token", basic_auth=(client_id, secret),
        body=None,
    ) if False else None
    import urllib.parse
    headers = {
        "Accept": "application/json",
        "Accept-Language": "en_US",
        "Content-Type": "application/x-www-form-urlencoded",
        "Authorization": "Basic " + base64.b64encode(f"{client_id}:{secret}".encode()).decode(),
    }
    request = urllib.request.Request(
        _api_base() + "/v1/oauth2/token",
        data=urllib.parse.urlencode({"grant_type": "client_credentials"}).encode(),
        headers=headers, method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=20) as response:
            payload = json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError:
        raise PayPalError("PayPal authentication failed. Check your sandbox app credentials.") from None
    except (urllib.error.URLError, TimeoutError, json.JSONDecodeError):
        raise PayPalError("Could not obtain a PayPal access token. Check your connection.") from None
    token = payload.get("access_token")
    if not token:
        raise PayPalError("PayPal did not return an access token.")
    return token


def _money(value: Any) -> str:
    try:
        amount = Decimal(str(value)).quantize(Decimal("0.01"))
    except (InvalidOperation, ValueError):
        raise PayPalError("The accepted negotiation price must be a valid amount.") from None
    if not amount.is_finite() or amount <= 0:
        raise PayPalError("The accepted negotiation price must be greater than zero.")
    return format(amount, ".2f")


def create_checkout(decision: dict[str, Any], *, currency: str | None = None,
                    return_url: str | None = None, cancel_url: str | None = None) -> dict[str, Any]:
    if not isinstance(decision, dict) or decision.get("action") != "accept":
        raise PayPalError("Checkout can only be created after a negotiation is accepted.")
    scope = decision.get("scope")
    if not isinstance(scope, list) or not scope or any(not isinstance(item, str) for item in scope):
        raise PayPalError("Accepted negotiation must contain a non-empty, valid scope.")
    amount = _money(decision.get("price"))
    currency_code = (currency or os.getenv("PAYPAL_CURRENCY", "USD")).strip().upper()
    if not re.fullmatch(r"[A-Z]{3}", currency_code):
        raise PayPalError("Set PAYPAL_CURRENCY to a three-letter currency code, such as USD or PHP.")
    return_url = return_url or os.getenv("PAYPAL_RETURN_URL", "http://localhost:8000/paypal/return")
    cancel_url = cancel_url or os.getenv("PAYPAL_CANCEL_URL", "http://localhost:8000/paypal/cancel")

    token = _access_token()
    order = _request_json(
        "POST", "/v2/checkout/orders", token=token,
        body={
            "intent": "CAPTURE",
            "purchase_units": [{
                "description": "Negotiated agreement: " + ", ".join(scope)[:120],
                "amount": {"currency_code": currency_code, "value": amount},
            }],
            "application_context": {
                "return_url": return_url,
                "cancel_url": cancel_url,
                "user_action": "PAY_NOW",
            },
        },
    )
    links = order.get("links", [])
    approval = next((link.get("href") for link in links
                     if link.get("rel") in ("payer-action", "approve") and link.get("href")), None)
    order_id = order.get("id")
    if not order_id or not approval:
        raise PayPalError("PayPal created no usable approval link. Check the order response in sandbox logs.")
    return {
        "order_id": order_id,
        "status": order.get("status", "CREATED"),
        "approval_url": approval,
        "amount": amount,
        "currency": currency_code,
    }


def capture_checkout(order_id: str) -> dict[str, Any]:
    if not isinstance(order_id, str) or not re.fullmatch(r"[A-Z0-9-]{5,64}", order_id):
        raise PayPalError("A valid PayPal order ID is required.")
    token = _access_token()
    order = _request_json("GET", f"/v2/checkout/orders/{order_id}", token=token)
    if order.get("status") != "APPROVED":
        raise PayPalError("This PayPal order is not approved yet; no capture was attempted.")
    result = _request_json(
        "POST", f"/v2/checkout/orders/{order_id}/capture", token=token, body={}
    )
    status = result.get("status")
    if status != "COMPLETED":
        raise PayPalError("PayPal has not confirmed a completed capture.")
    return {
        "order_id": result.get("id", order_id),
        "status": status,
        "captures": [
            {
                "capture_id": capture.get("id"),
                "status": capture.get("status"),
                "amount": capture.get("amount"),
            }
            for unit in result.get("purchase_units", [])
            for capture in unit.get("payments", {}).get("captures", [])
        ],
    }
