from __future__ import annotations

import os
from fastapi import FastAPI, HTTPException
from pydantic import BaseModel
from typing import List, Optional, Dict, Any

from negotiation_agent import decide, Closed, PolicyViolation
from paypal_checkout import create_checkout, capture_checkout, PayPalError

app = FastAPI(
    title="AI Negotiation & PayPal Checkout System",
    description="Hackathon Prototype combining LLM/Algorithmic Negotiation with PayPal Sandbox Payments.",
    version="1.0.0"
)

class NegotiationRequest(BaseModel):
    role: str
    limits: dict
    scope: dict
    history: Optional[List[dict]] = []
    use_llm: Optional[bool] = False

class CheckoutRequest(BaseModel):
    decision: dict
    currency: Optional[str] = None

@app.post("/api/negotiate")
def api_negotiate(req: NegotiationRequest):
    try:
        payload = {
            "role": req.role,
            "limits": req.limits,
            "scope": req.scope,
            "history": req.history
        }
        result = decide(payload, use_llm=req.use_llm)
        return result
    except Closed as e:
        raise HTTPException(status_code=400, detail=str(e))
    except PolicyViolation as e:
        raise HTTPException(status_code=400, detail=str(e))
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@app.post("/api/checkout")
def api_create_checkout(req: CheckoutRequest):
    try:
        checkout_data = create_checkout(req.decision, currency=req.currency)
        return checkout_data
    except PayPalError as e:
        raise HTTPException(status_code=400, detail=str(e))

@app.get("/paypal/return")
def api_paypal_return(token: str):
    try:
        capture_result = capture_checkout(token)
        return {
            "status": "success",
            "message": "Payment captured successfully!",
            "capture_details": capture_result
        }
    except PayPalError as e:
        raise HTTPException(status_code=400, detail=str(e))

@app.get("/paypal/cancel")
def api_paypal_cancel():
    return {"status": "cancelled", "message": "PayPal checkout was cancelled by the user."}