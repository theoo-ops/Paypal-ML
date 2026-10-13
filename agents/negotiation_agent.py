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
    """The negotiation already ended (someone accepted or rejected)."""


class PolicyViolation(Exception):
    """A decision broke a hard rule (limit or scope). Should never reach the caller."""


@dataclass(frozen=True)
class Item:
    name: str
    value: float 
    cost: float = 0.0


@dataclass(frozen=True)
class Limits:
    list_price: float
    target_price: float
    reservation_price: float
    max_rounds: int = 3


@dataclass(frozen=True)
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
        optional = {o["item"]: Item(o["item"], float(o["value"]), float(o.get("cost", 0)))
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
        return self.limits.reservation_price - sum(i.cost for i in self._dropped(scope))

    def ask(self, scope: frozenset, n: int | None = None) -> float:
        n = n or self.round
        lim = self.limits
        anchor = lim.list_price * SELLER_OPEN_RATIO
        steps = max(lim.max_rounds - 1, 1)
        share = max(0.0, 1 - lim.target_price / anchor) * min(n - 1, steps) / steps
        base = anchor - SELLER_PASS_THROUGH * sum(i.value for i in self._dropped(scope))
        return max(self.floor(scope), base * (1 - share))


def buyer_decide(ctx: Context) -> Decision:
    lim, n, total = ctx.limits, ctx.round, ctx.limits.max_rounds
    wanted = ctx.full_scope
    open_price = lim.target_price * BUYER_OPEN_RATIO

    def planned(k: int) -> float:
        if total <= 1:
            return lim.target_price
        k = min(k, total)
        frac = (1 - BUYER_DECAY ** (k - 1)) / (1 - BUYER_DECAY ** (total - 1))
        return open_price + (lim.target_price - open_price) * frac

    last = ctx.theirs[-1] if ctx.theirs else None
    prev = min(ctx.mine[-1].price, lim.reservation_price) if ctx.mine else None

    q = planned(n)
    if prev is not None:
        q = max(q, prev) 
    q = min(q, lim.reservation_price) 
    stalled = prev is not None and ctx.counterparty_stalled and n < total
    if stalled:
        q = prev

    def offer(price: float, code: str) -> Decision:
        return Decision("offer", price, sorted(wanted), code)

    if last is None:
        return offer(q, "open")
    if not wanted <= last.scope:
        if ctx.is_last:
            return Decision("reject", last.price, sorted(last.scope), "reject_scope")
        return offer(prev if prev is not None else q, "hold_scope")
    if last.price <= q:
        return Decision("accept", last.price, sorted(last.scope), "accept")
    if ctx.is_last:
        if last.price <= lim.reservation_price:
            return Decision("accept", last.price, sorted(last.scope), "accept")
        return Decision("reject", last.price, sorted(last.scope), "reject_price")
    return offer(q, "hold_stalled" if stalled else "counter")

def _closes(ctx: Context, scope: frozenset, price: float) -> bool:
    return price >= max(ctx.floor(scope), ctx.ask(scope) * (1 - ACCEPT_TOL))


def _leaner_scope(ctx: Context, scope: frozenset, price: float):
    droppable = sorted((i for i in scope if i in ctx.optional),
                       key=lambda i: -ctx.optional[i].value)[:MAX_DROP_CANDIDATES]
    best = None
    for k in range(1, len(droppable) + 1):
        for drop in itertools.combinations(droppable, k):
            s = scope - frozenset(drop)
            if _closes(ctx, s, price) and (best is None or ctx.ask(s) > ctx.ask(best)):
                best = s
    return best


def seller_decide(ctx: Context) -> Decision:
    full = ctx.full_scope
    last = ctx.theirs[-1] if ctx.theirs else None
    if last is None:
        return Decision("offer", ctx.ask(full), sorted(full), "open")

    scope = (last.scope & full) | ctx.required 
    has_required = ctx.required <= last.scope
    p = last.price

    if has_required and _closes(ctx, scope, p):
        return Decision("accept", p, sorted(scope), "accept")
    if ctx.is_last:
        if has_required and p >= ctx.floor(scope): 
            return Decision("accept", p, sorted(scope), "accept")
        return Decision("reject", p, sorted(scope), "reject_floor" if has_required else "reject_scope")

    lean = _leaner_scope(ctx, scope, p)
    if lean is not None:
        return Decision("offer", ctx.ask(lean), sorted(lean), "trade_scope")
    return Decision("offer", ctx.ask(scope), sorted(scope), "counter" if has_required else "counter_required")


def validate(ctx: Context, d: Decision) -> None:
    if d.action == "reject":
        return
    s, lim = frozenset(d.scope), ctx.limits
    if ctx.role == "buyer":
        if d.price > lim.reservation_price + 1e-9:
            raise PolicyViolation("buyer price above reservation")
        if not ctx.full_scope <= s:
            raise PolicyViolation("buyer scope reduced")
    else:
        if not ctx.required <= s:
            raise PolicyViolation("seller dropped required scope")
        if d.price < ctx.floor(s) - 1e-9:
            raise PolicyViolation("seller price below floor")


_TEMPLATES = {
    "buyer": {
        "open": "Opening offer of {price:,.0f} for the full scope.",
        "counter": "Moving up a step to {price:,.0f}; the scope stays the same.",
        "hold_stalled": "No movement on your side, so we're holding at {price:,.0f}.",
        "hold_scope": "The scope isn't something we trade; holding at {price:,.0f}.",
        "accept": "That price works for the full scope.",
        "reject_scope": "We need the full scope and can't proceed without it.",
        "reject_price": "We couldn't reach a workable price within the rounds available.",
    },
    "seller": {
        "open": "Opening at {price:,.0f} for the full scope.",
        "counter": "Adjusted to {price:,.0f}, which reflects this scope.",
        "counter_required": "Required deliverables stay in; {price:,.0f} covers them.",
        "trade_scope": "We can offer {price:,.0f} if the scope is reduced as proposed.",
        "accept": "Price and scope work for us.",
        "reject_floor": "We can't go that low at this scope.",
        "reject_scope": "The required deliverables must stay in scope.",
    },
}
_FORBIDDEN = re.compile(r"reservation|floor|target|walk[- ]?away|bottom line|maximum|budget", re.I)
_SYSTEM = ("You write the one-sentence 'reason' field of a negotiation message from the JSON facts. "
           "Max 20 words, calm and professional, no pressure tactics. Never mention budgets, limits, "
           "floors, targets or walk-away points. Use no number except the price in the facts. "
           "Output only the sentence.")


def _clean_reason(text: str, price: float):
    text = " ".join(text.split())
    if not text or len(text.split()) > 25 or _FORBIDDEN.search(text):
        return None
    for num in re.findall(r"\d[\d,]*\.?\d*", text):
        try:
            if abs(float(num.replace(",", "").rstrip(".")) - price) > 1:
                return None
        except ValueError:
            return None
    return text


def _llm_reason(ctx: Context, d: Decision):
    if not os.getenv("ANTHROPIC_API_KEY"):
        return None
    try:
        import anthropic  
        facts = {"role": ctx.role, "action": d.action, "price": round(d.price, 2),
                 "scope": d.scope, "why": d.code}
        msg = anthropic.Anthropic().messages.create(
            model=os.getenv("NEGOTIATION_MODEL", "claude-haiku-5-5"), max_tokens=80,
            system=_SYSTEM, messages=[{"role": "user", "content": json.dumps(facts)}])
        return msg.content[0].text
    except Exception:
        return None


def _reason(ctx: Context, d: Decision, use_llm: bool) -> str:
    text = _llm_reason(ctx, d) if use_llm else None
    text = _clean_reason(text, d.price) if text else None
    return text or _TEMPLATES[ctx.role][d.code].format(price=d.price)


def decide(payload: dict, use_llm: bool = False) -> dict:
    ctx = Context.from_json(payload)
    if any(o.action != "offer" for o in ctx.history):
        raise Closed("negotiation already ended")
    d = buyer_decide(ctx) if ctx.role == "buyer" else seller_decide(ctx)
    validate(ctx, d)
    d.reason = _reason(ctx, d, use_llm)
    return d.to_json()


def simulate(buyer_limits: dict, seller_limits: dict, scope: dict, use_llm: bool = False):
    history: list = []
    rounds = max(buyer_limits.get("max_rounds", 3), seller_limits.get("max_rounds", 3))
    for turn in range(2 * rounds + 2):
        role = "seller" if turn % 2 == 0 else "buyer"
        limits = seller_limits if role == "seller" else buyer_limits
        out = decide({"role": role, "limits": limits, "scope": scope, "history": history}, use_llm)
        history.append({"by": role, "action": out["action"], "price": out["price"], "scope": out["scope"]})
        yield role, out
        if out["action"] != "offer":
            return


if __name__ == "__main__":
    scope = {"required": ["design", "build", "deploy"],
             "optional": [{"item": "training", "value": 800, "cost": 200},
                          {"item": "support_3mo", "value": 1200, "cost": 400}]}
    buyer = {"list_price": 10000, "target_price": 7500, "reservation_price": 8500, "max_rounds": 3}
    seller = {"list_price": 10000, "target_price": 9000, "reservation_price": 7200, "max_rounds": 3}

    print("== agent vs agent ==")
    for who, out in simulate(buyer, seller, scope):
        print(f"{who:>6}: {json.dumps(out)}")

    print("\n== seller trades price for scope (buyer pushes 8,300 on the full scope) ==")
    history = [
        {"by": "seller", "action": "offer", "price": 9800,
         "scope": ["design", "build", "deploy", "training", "support_3mo"]},
        {"by": "buyer", "action": "offer", "price": 8300,
         "scope": ["design", "build", "deploy", "training", "support_3mo"]},
    ]
    print(json.dumps(decide({"role": "seller", "limits": seller, "scope": scope, "history": history})))