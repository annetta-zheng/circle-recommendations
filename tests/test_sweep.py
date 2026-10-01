"""List-level properties over every member: correctness, explanations, exposure, mission."""

import re
import statistics
from collections import Counter

import pytest

from app import db, features
from tests.conftest import ids, recommendations

# words a member must never read: internal factor names and machinery
BANNED = ["score", "percentile", "weight", "availability", "social", "topic_match",
          "implicit", "factor", "decay", "None", "null"]


@pytest.fixture(scope="module")
def all_payloads(client):
    return {uid: recommendations(client, uid, debug=True) for uid in range(1, 101)}


@pytest.fixture(scope="module")
def circles_by_id(conn):
    return {c["id"]: c for c in db.all_circles_with_stats(conn)}


def test_every_member_gets_a_full_clean_list(all_payloads, conn, circles_by_id):
    for uid, payload in all_payloads.items():
        member = db.get_member(conn, uid)
        joined = {m["circle_id"] for m in member["memberships"]}
        top = ids(payload)
        assert len(top) == 5, uid
        assert len(set(top)) == 5, uid
        assert not (set(top) & joined), f"user {uid} was recommended a Circle she is in"
        for cid in top:
            c = circles_by_id[cid]
            assert not c["is_full"], (uid, cid)
            assert c["access"] != "unlisted", (uid, cid)
            assert not features.is_dormant(c), (uid, cid)


def test_ranked_lists_are_prefix_stable(client):
    """Pagination is a slice of one list, so rank k must not depend on limit."""
    for uid in range(1, 101):
        short = ids(recommendations(client, uid, limit=3))
        long = ids(recommendations(client, uid, limit=12))
        assert long[:3] == short, f"user {uid}: top-3 changed when asking for 12"


def test_responses_are_deterministic(client):
    for uid in (1, 7, 15, 23, 42, 58, 99):
        a = ids(recommendations(client, uid))
        b = ids(recommendations(client, uid))
        assert a == b


def test_explanations_pass_lint(all_payloads):
    for uid, payload in all_payloads.items():
        sentences = [r["explanation"] for r in payload["recommendations"]]
        assert len(set(sentences)) == len(sentences), f"user {uid}: two identical sentences"
        for r in payload["recommendations"]:
            s = r["explanation"]
            assert r["reasons"], (uid, s)
            for word in BANNED:
                assert not re.search(rf"\b{word}\b", s), (uid, word, s)
            # every number in the sentence is a number from the data
            c = r["circle"]
            allowed = {c["member_count"], c["seats_left"] or 0}
            allowed |= {int(x) for x in re.findall(r"\d+", " ".join(z["detail"] for z in r["reasons"]))}
            for n in re.findall(r"\d+", s):
                assert int(n) in allowed, (uid, n, s)


def test_reasons_match_the_strategy(all_payloads):
    for uid, payload in all_payloads.items():
        first_types = {r["reasons"][0]["type"] for r in payload["recommendations"]}
        if payload["strategy"] == "newcomer":
            assert first_types <= {"newcomer", "explore"} and "newcomer" in first_types
            assert payload["hints"]
        elif payload["strategy"] == "implicit_interests":
            assert "topic_match" not in first_types
            assert payload["hints"]
        else:
            assert "newcomer" not in first_types


def test_exposure_is_spread_and_every_eligible_topic_circle_is_reachable(all_payloads, circles_by_id):
    exposure = Counter(cid for p in all_payloads.values() for cid in ids(p))
    n = len(all_payloads)
    # guardrail against a single Circle dominating every list
    worst_id, worst = exposure.most_common(1)[0]
    assert worst / n <= 0.60, f"Circle {worst_id} appears in {worst / n:.0%} of lists"
    # every eligible Circle with a topic is recommended to someone
    eligible = [c for c in circles_by_id.values()
                if not c["is_full"] and c["access"] != "unlisted" and not features.is_dormant(c)]
    unreachable = [c["id"] for c in eligible if c["topic"] and exposure[c["id"]] == 0]
    assert unreachable == [], unreachable
    # Circles without a topic can only surface as a fallback; report rather than assert
    no_topic = [(c["id"], exposure[c["id"]]) for c in eligible if not c["topic"]]
    print("no-topic Circle exposure:", no_topic)


def test_mission_metrics(all_payloads, circles_by_id):
    sizes = [circles_by_id[cid]["member_count"] for p in all_payloads.values() for cid in ids(p)]
    active = [circles_by_id[cid]["posts_30d"] >= 8 for p in all_payloads.values() for cid in ids(p)]
    median_size = statistics.median(sizes)
    active_share = sum(active) / len(active)
    print(f"median member_count {median_size}, active-tier share {active_share:.0%}")
    assert 6 <= median_size <= 15, "results should look like peer groups, not big communities"
    assert active_share >= 0.5, "most recommendations should be alive this month"
