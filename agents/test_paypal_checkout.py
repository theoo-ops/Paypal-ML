import pytest
import paypal_checkout as pc


def test_checkout_rejects_non_accepted_decision():
    with pytest.raises(pc.PayPalError, match="negotiation is accepted"):
        pc.create_checkout({"action": "offer", "price": 100, "scope": ["design"]}, currency="USD")


def test_checkout_rejects_invalid_scope():
    with pytest.raises(pc.PayPalError, match="scope"):
        pc.create_checkout({"action": "accept", "price": 100, "scope": []}, currency="USD")


@pytest.mark.parametrize("price", [0, -1, "not-money", float("inf")])
def test_checkout_rejects_invalid_amount(price):
    with pytest.raises(pc.PayPalError):
        pc.create_checkout({"action": "accept", "price": price, "scope": ["design"]}, currency="USD")


def test_checkout_creates_order_and_returns_approval_link(monkeypatch):
    monkeypatch.setenv("PAYPAL_CLIENT_ID", "sandbox-id")
    monkeypatch.setenv("PAYPAL_CLIENT_SECRET", "sandbox-secret")
    monkeypatch.setenv("PAYPAL_ENVIRONMENT", "SANDBOX")
    monkeypatch.setenv("PAYPAL_CURRENCY", "PHP")
    calls = []
    monkeypatch.setattr(pc, "_access_token", lambda: "fake-token")

    def fake_request(method, path, **kwargs):
        calls.append((method, path, kwargs))
        return {"id": "ORDER-12345", "status": "CREATED",
                "links": [{"rel": "payer-action", "href": "https://sandbox.paypal.com/checkout?token=ORDER-12345"}]}

    monkeypatch.setattr(pc, "_request_json", fake_request)
    result = pc.create_checkout({"action": "accept", "price": 1234.5, "scope": ["design", "build"]})
    assert result["order_id"] == "ORDER-12345"
    assert result["amount"] == "1234.50"
    assert result["currency"] == "PHP"
    assert result["approval_url"].startswith("https://sandbox.paypal.com/")
    assert calls[0][0:2] == ("POST", "/v2/checkout/orders")


def test_capture_refuses_unapproved_order(monkeypatch):
    monkeypatch.setattr(pc, "_access_token", lambda: "fake-token")
    calls = []

    def fake_request(method, path, **kwargs):
        calls.append((method, path))
        return {"id": "ORDER-12345", "status": "CREATED"}

    monkeypatch.setattr(pc, "_request_json", fake_request)
    with pytest.raises(pc.PayPalError, match="not approved"):
        pc.capture_checkout("ORDER-12345")
    assert calls == [("GET", "/v2/checkout/orders/ORDER-12345")]


def test_capture_requires_completed_status(monkeypatch):
    monkeypatch.setattr(pc, "_access_token", lambda: "fake-token")
    def fake_request(method, path, **kwargs):
        if method == "GET":
            return {"id": "ORDER-12345", "status": "APPROVED"}
        return {"id": "ORDER-12345", "status": "PENDING"}
    monkeypatch.setattr(pc, "_request_json", fake_request)
    with pytest.raises(pc.PayPalError, match="not confirmed"):
        pc.capture_checkout("ORDER-12345")


def test_capture_completed_order(monkeypatch):
    monkeypatch.setattr(pc, "_access_token", lambda: "fake-token")
    def fake_request(method, path, **kwargs):
        if method == "GET":
            return {"id": "ORDER-12345", "status": "APPROVED"}
        return {"id": "ORDER-12345", "status": "COMPLETED",
                "purchase_units": [{"payments": {"captures": [
                    {"id": "CAP-123", "status": "COMPLETED",
                     "amount": {"currency_code": "USD", "value": "20.00"}}
                ]}}]}
    monkeypatch.setattr(pc, "_request_json", fake_request)
    result = pc.capture_checkout("ORDER-12345")
    assert result["status"] == "COMPLETED"
    assert result["captures"][0]["capture_id"] == "CAP-123"
