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


def test_json_schema_has_strict_contract():
    from negotiation_agent import json_schema
    schema = json_schema()
    assert schema["additionalProperties"] is False
    assert set(schema["required"]) == KEYS
    assert schema["properties"]["action"]["enum"] == ["offer", "accept", "reject"]


def test_search_filters_before_ranking_and_returns_reasons():
    from negotiation_agent import rank_search_results
    listings = [
        {"title": "AI website build", "description": "website automation", "price": 9000, "available": True},
        {"title": "AI website design", "description": "website", "price": 15000, "available": True},
        {"title": "AI website deployment", "description": "deploy website", "price": 7000, "available": False},
    ]
    results = rank_search_results("AI website", listings, {"max_price": 10000, "available_only": True})
    assert len(results) == 1
    assert results[0]["title"] == "AI website build"
    assert results[0]["score"] > 0
    assert results[0]["reason"]


def test_rematch_returns_top_three_and_does_not_leak_budget():
    from negotiation_agent import match_rematches
    request = {"description": "AI website automation", "budget": 10000,
               "delivery_days": 14, "required_tags": ["website"]}
    listings = [
        {"title": "AI website automation", "description": "website workflow", "price": 9000,
         "available": True, "delivery_days": 7, "tags": ["website"]},
        {"title": "Website automation service", "description": "AI integration", "price": 9500,
         "available": True, "delivery_days": 10, "tags": ["automation"]},
        {"title": "AI website", "description": "automation", "price": 11000,
         "available": True, "delivery_days": 5, "tags": ["website"]},
        {"title": "AI website setup", "description": "automation", "price": 8000,
         "available": False, "delivery_days": 5, "tags": ["website"]},
    ]
    results = match_rematches(request, listings)
    assert len(results) == 2
    assert all("reason" in item for item in results)
    assert all("budget" not in item["reason"].lower() for item in results)
    assert all(item["price"] <= 10000 for item in results)


def test_search_rejects_empty_query():
    from negotiation_agent import rank_search_results
    with pytest.raises(ValueError):
        rank_search_results(" ", [])
