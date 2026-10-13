from paypal_checkout import create_checkout, PayPalError

accepted_deal = {
    "action": "accept",
    "price": "25.00",
    "scope": [
        "Website setup",
        "Basic automation",
    ],
}

try:
    checkout = create_checkout(accepted_deal)

    print("Order ID:", checkout["order_id"])
    print("Status:", checkout["status"])
    print("Amount:", checkout["amount"], checkout["currency"])
    print("Pay here:", checkout["approval_url"])

except PayPalError as error:
    print("Checkout error:", error)