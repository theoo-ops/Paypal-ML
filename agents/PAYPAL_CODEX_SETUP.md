# PayPal AI Toolkit + negotiation agent setup

## 1. Install PayPal AI Toolkit in Codex

In a terminal where the Codex CLI is installed and authenticated, run:

```bash
codex plugin marketplace add paypal/AI-Toolkit
codex plugin add paypal@paypal-ai-toolkit
```

Restart Codex completely. Open the negotiation project folder and ask Codex:

> List the PayPal sandbox MCP tools available in this project, verify the PayPal best-practices skill is active, and review `paypal_checkout.py` without changing the negotiation rules.

If you want to develop against a local clone instead of the marketplace:

```bash
git clone https://github.com/paypal/AI-Toolkit.git
codex plugin marketplace add ./AI-Toolkit
codex plugin add paypal@paypal-ai-toolkit
```

## 2. Create sandbox credentials

1. Open https://developer.paypal.com/dashboard/applications/sandbox
2. Create or select a sandbox REST app.
3. Copy its sandbox Client ID and Secret.
4. Copy `.env.paypal.example` to `.env` and replace the placeholder credentials.
5. Keep `.env` out of Git. Do not paste secrets into prompts, commit them, or expose them in frontend code.

The AI Toolkit's MCP connection uses `PAYPAL_SANDBOX_ACCESS_TOKEN` in Codex's `~/.codex/config.toml`:

```toml
[shell_environment_policy.set]
PAYPAL_SANDBOX_ACCESS_TOKEN = "PASTE_A_FRESH_SANDBOX_ACCESS_TOKEN_HERE"
```

Generate a short-lived sandbox token using the Client ID and Secret from the PayPal Developer Dashboard. PowerShell example:

```powershell
$pair = "$env:PAYPAL_CLIENT_ID`:$env:PAYPAL_CLIENT_SECRET"
$basic = [Convert]::ToBase64String([Text.Encoding]::ASCII.GetBytes($pair))
Invoke-RestMethod -Method Post `
  -Uri "https://api-m.sandbox.paypal.com/v1/oauth2/token" `
  -Headers @{ Authorization = "Basic $basic" } `
  -ContentType "application/x-www-form-urlencoded" `
  -Body "grant_type=client_credentials"
```

Copy the returned `access_token` as one line into Codex config, then fully restart Codex. Treat it as a secret; refresh it when it expires. The Python checkout module obtains its own short-lived token from `PAYPAL_CLIENT_ID` and `PAYPAL_CLIENT_SECRET`; it does not depend on the Codex MCP token.

## 3. Use checkout in Python

`paypal_checkout.py` deliberately does not alter `negotiation_agent.py` or its pricing policy. After `decide(...)` returns an `accept` decision, call:

```python
from paypal_checkout import create_checkout, capture_checkout

decision = ...  # result from your existing decide(...) function
checkout = create_checkout(decision, currency="USD")
print(checkout["approval_url"])  # send this URL to the buyer
```

After the buyer approves and your application receives the PayPal order ID at a trusted return route:

```python
result = capture_checkout(order_id)
if result["status"] == "COMPLETED":
    # Mark the deal paid in your application/database here.
    ...
```

Do not capture based only on a browser redirect. The helper checks the order with PayPal first and requires `APPROVED`, then requires PayPal's capture result to be `COMPLETED`. For a real web application, bind the order ID to the accepted deal in server-side storage, validate the returned order ID belongs to that deal, and add verified webhooks/idempotency before production use.

## 4. Run tests

```bash
python -m pip install pytest
python -m pytest -q test_negotiation_agent_completed.py test_paypal_checkout.py
```

These tests mock PayPal responses and do not make real API calls. They check accepted-decision gating, amount validation, approval-link creation, and capture-status checks.
