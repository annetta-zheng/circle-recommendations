"""Exploration: one deterministic epsilon-greedy slot for Circles the ranker under-serves."""

import pytest

from app import config, db, features, scoring
from tests.conftest import recommendations

WEEK = "2026-W39"  # ISO week of AS_OF 2026-09-21


@pytest.fixture
def exploring(monkeypatch):
    """Cold-start exploration as it was tried: every cold-start member, every week."""
    monkeypatch.setattr(config, "EXPLORE_COLD_EPSILON", 1.0)


def test_exploration_ships_off(client):
    assert config.EXPLORE_EPSILON == 0 and config.EXPLORE_COLD_EPSILON == 0
    for uid in range(1, 101):
        assert _explored(recommendations(client, uid)) == [], uid


def _explored(payload):
    return [r for r in payload["recommendations"] if r["reasons"][0]["type"] == "explore"]


def test_only_cold_start_members_explore_and_only_at_the_slot(client, conn, exploring):
    cold_with_slot = 0
    for uid in range(1, 101):
        payload = recommendations(client, uid)
        explored = _explored(payload)
        cold = not db.get_member(conn, uid)["stated_interests"]
        if cold:
            assert [r["rank"] for r in explored] == [config.EXPLORE_RANK], uid
            cold_with_slot += 1
        else:
            assert explored == [], uid
    assert cold_with_slot >= 25, "every cold-start member gets the slot"


def test_a_newcomers_exploration_pick_keeps_her_sampler_promises(client, exploring):
    recs = recommendations(client, 42)["recommendations"]
    explored = _explored({"recommendations": recs})
    assert explored and explored[0]["circle"]["join_policy"] == "open"
    topics = [r["circle"]["topic"] for r in recs if r["circle"]["topic"]]
    assert len(topics) == len(set(topics))


def test_her_top_four_never_move(client, monkeypatch, exploring):
    with_slot = {uid: recommendations(client, uid) for uid in range(1, 101)}
    monkeypatch.setattr(config, "EXPLORE_EPSILON", 0.0)
    monkeypatch.setattr(config, "EXPLORE_COLD_EPSILON", 0.0)
    for uid, payload in with_slot.items():
        without = recommendations(client, uid)
        top = lambda p: [r["circle"]["id"] for r in p["recommendations"][:config.EXPLORE_RANK - 1]]
        assert top(payload) == top(without), uid


def test_explored_circles_are_alive_and_under_served(client, conn, exploring):
    circles = {c["id"]: c for c in db.all_circles_with_stats(conn)}
    percentiles = features.volume_percentiles(list(circles.values()))
    for uid in range(1, 101):
        for r in _explored(recommendations(client, uid)):
            c = circles[r["circle"]["id"]]
            assert c["posts_30d"] >= config.EXPLORE_MIN_POSTS_30D
            assert features.explore_kind(c, percentiles[c["id"]]) is not None


def test_the_pick_rotates_with_the_week():
    pool = list(range(1, 41))
    pick = lambda uid, week: min(pool, key=lambda cid: scoring._seed(uid, week, cid))
    assert any(pick(uid, WEEK) != pick(uid, "2026-W40") for uid in range(1, 101))
