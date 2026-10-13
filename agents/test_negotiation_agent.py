import random

import pytest

from negotiation_agent import (Closed, Context, PolicyViolation, _clean_reason,
                               decide, simulate)

KEYS = {"action", "price", "scope", "reason"}


def rand_case(rng):
    lst = rng.uniform(5000, 20000)
    names = ["design", "build", "deploy"][: rng.randint(1, 3)]
    optional = []
    for i in range(rng.randint(0, 4)):
        v = rng.uniform(100, 1500)
        optional.append({"item": f"opt{i}", "value": v, "cost": rng.uniform(0, v)})
    s_target = lst * rng.uniform(0.6, 0.97)
    b_target = lst * rng.uniform(0.4, 0.9)
    seller = {"list_price": lst, "target_price": s_target,
              "reservation_price": s_target * rng.uniform(0.5, 1.0),
              "max_rounds": rng.randint(1, 8)}
    buyer = {"list_price": lst, "target_price": b_target,
             "reservation_price": b_target * rng.uniform(1.0, 1.5),
             "max_rounds": rng.randint(1, 8)}
    return buyer, seller, {"required": names, "optional": optional}


def test_agent_vs_agent_never_breaks_limits_or_scope():
    rng = random.Random(7)
    for _ in range(3000):
        buyer, seller, scope = rand_case(rng)
        full = set(scope["required"]) | {o["item"] for o in scope["optional"]}
        sctx = Context.from_json({"role": "seller", "limits": seller, "scope": scope})
        last_buyer_price = 0.0
        for who, out in simulate(buyer, seller, scope):
            assert set(out) == KEYS and out["action"] in ("offer", "accept", "reject")
            s = set(out["scope"])
            if who == "buyer" and out["action"] == "offer":
                assert s == full
                assert out["price"] <= buyer["reservation_price"] + 0.01
                assert out["price"] >= last_buyer_price - 0.01
                last_buyer_price = out["price"]
            if who == "buyer" and out["action"] == "accept":
                assert out["price"] <= buyer["reservation_price"] + 0.01 and full <= s
            if who == "seller" and out["action"] != "reject":
                assert set(scope["required"]) <= s
                assert out["price"] >= sctx.floor(frozenset(s)) - 0.01


def test_garbage_counterparty_cannot_break_either_agent():
    rng = random.Random(11)
    for _ in range(3000):
        buyer, seller, scope = rand_case(rng)
        items = [o["item"] for o in scope["optional"]] + scope["required"] + ["junk", "x"]
        role, limits = rng.choice([("buyer", buyer), ("seller", seller)])
        other = "seller" if role == "buyer" else "buyer"
        history = [{"by": rng.choice([role, other]), "action": "offer",
                    "price": rng.choice([0, 1, rng.uniform(0, 50000), 1e9]),
                    "scope": rng.sample(items, rng.randint(0, len(items)))}
                   for _ in range(rng.randint(0, 6))]
        try:
            out = decide({"role": role, "limits": limits, "scope": scope, "history": history})
        except (Closed, ValueError):
            continue
        assert set(out) == KEYS


def test_closed_negotiation_raises():
    b, s, scope = rand_case(random.Random(1))
    h = [{"by": "seller", "action": "accept", "price": 1, "scope": []}]
    with pytest.raises(Closed):
        decide({"role": "buyer", "limits": b, "scope": scope, "history": h})


@pytest.mark.parametrize("bad", [float("nan"), float("inf"), -5])
def test_bad_prices_rejected(bad):
    b, s, scope = rand_case(random.Random(2))
    h = [{"by": "seller", "action": "offer", "price": bad, "scope": []}]
    with pytest.raises(ValueError):
        decide({"role": "buyer", "limits": b, "scope": scope, "history": h})


def test_reason_sanitizer_blocks_leaks():
    assert _clean_reason("Fair price at 8,000 for this scope.", 8000)
    assert _clean_reason("Our floor is firm.", 8000) is None
    assert _clean_reason("We could do 7200 maybe.", 8000) is None
    assert _clean_reason("word " * 40, 8000) is None


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-q"]))