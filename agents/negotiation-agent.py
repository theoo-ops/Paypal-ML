from __future__ import annotations

import itertools
import json
import math
import os
import re
from dataclasses import dataclass

BUYER_OPEN_RATIO = 0.70
BUYER_DECAY = 0.70

SELLER_OPEN_RATIO = 0.98
SELLER_PASS_THROUGH = 0.70

ACCEPT_TOL = 0.01
MAX_DROP_CANDIDATES = 12

ROLES = ("buyer", "seller")
ACTIONS = ("offer", "accept", "reject")

class Closed(Exception):
    """The negotiation already ended"""

class PolicyViolation(Exception):
    """A decision broke a hard rule"""


@dataclass(frozen = True)
class Item:
    name: str
    value: float
    cost: float = 0.0

@dataclass(frozen = True)
class Limits:
    list_price: float
    target_price: float
    reservation_price: float
    max_rounds: int = 3

@dataclass(frozen = True)
class Offer:
    by: str
    action: str
    price: float
    scope: frozenset

@dataclass
class Decision:
    action: str
    price: float
    scope: list
    code: str = ""
    reason: str = ""

    def to_json(self) -> dict:
        return {"action": self.action, "price": round(self.price, 2), 
                "scope": self.scope, "reason": self.reason}

@dataclass
class Context:
    role: str
    limits: Limits
    required: frozenset
    optional: dict
    history: list

    @classmethod
    def from_json(cls, data: dict) -> "Context":
        role = data["role"]
        if role not in ROLES:
            raise ValueError("role must be 'buyer' or 'seller'")
        raw = data["limits"]
        lim = Limits(float(raw["list_price"]), float(raw["target_price"]), 
                     float(raw["reservation_price"]), int(raw.get("max_rounds", 3)))
        prices = (lim.list_price, lim.target_price, lim.reservation_price)

        if not all(math.isfinite(x) and x > 0 for x in prices) or lim.max_rounds < 1:
            raise ValueError("limits must be positive and finite, max_rounds >= 1")
        if role == "buyer" and lim.target_price > lim.reservation_price:
            raise ValueError("buyer target must be <= reservation")
        if role == "seller" and not lim.reservation_price <= lim.target_price <= lim.list_price:
            raise ValueError("seller needs reservation <= target <= list")

        scope = data["scope"]
        optional = {o["item"]: Item(o["item"], float(o["value"]), float(o.get("const", 0)))
                    for o in scope.get("optional", [])}
        history = []
        for h in data.get("history", []):
            price = float(h.get("price", 0))
            if h["by"] not in ROLES or h["action"] not in ACTIONS:
                raise ValueError("bad history entry")
            if not math.isfinite(price) or price < 0:
                raise ValueError("bad price in history")
            history.append(Offer(h["by"], h["action"], price, frozenset(h.get("scope", []))))

            return cls(role, lim, frozenset(scope.get("required", [])), optional, history)

    @property
    def full_scope(self) -> frozenset:
        return self.required | frozenset(self.optional)

    @property
    def mine(self) -> list:
        return [o for o in self.history if o.by == self.role]

    @property
    def theirs(self) -> list:
        return [o for o in self.history if o.by != self.role]

    @property
    def round(self) -> int:
        return len(self.mine) + 1

    @property
    def is_last(self) -> bool:
        return self.round >= self.limits.max_rounds

    @property
    def counterparty_stalled(self) -> bool:
        t = self.theirs
        return len(t) >= 2 and t[-1].price == t[-2].price and t[-1].scope == t[-2].scope

    def _dropped(self, scope: frozenset):
        return [it for name, it in self.optional.items() if name not in scope]

    def floor(self, scope: frozenset) -> float:
        return self.limits.reservation_price - sum(i.cost for i in self.dropped(scope))

    def ask(self, scope: frozenset, n: int | None = None) -> float:
        n = n or self.round
        lim = self.limits
        anchor = lim.list_price * SELLER_OPEN_RATIO
        steps = max(lim.max_rounds - 1, 1)
        share = max(0.0, 1 - lim.target_price / anchor) * min(n - 1, steps) / steps
        base = anchor - SELLER_PASS_THROUGH * sum(i.value for i in self.dropped(scope))

        return max(self.floor(scope), base * (1 - share))

def buyer_decide(ctx: Context) -> Decision:
    lim, n, total = ctx.limits, ctx.round, ctx.limits.max_rounds
    wanted = ctx.full_scope
    open_price = lim.target_price * BUYER_OPEN_RATIO

    def planned(k: int) -> float:
        if total <= 1:
            return lim.target_price
        k = min(k, total)