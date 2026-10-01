"""Profile fields: bio fills in only when nothing is stated; industry only backfills; company is unused."""

import re

import pytest

from app import config, db, features
from tests.conftest import recommendations

ON_TOPIC = {"topic_match", "implicit_interest", "profile_interest", "tag"}
INDUSTRY_PRIMARY = re.compile(r"^Not on your topics, but (\d+) members also work in [A-Z][A-Za-z]+$")


@pytest.fixture(scope="module")
def payloads(client):
    return {uid: recommendations(client, uid) for uid in range(1, 101)}


# ------------------------------------------------------------------ bio

def test_bio_keywords_match_words_not_fragments():
    assert features.bio_topics("Returning to work after a career break.") == {"career-breaks"}
    assert features.bio_topics("Prepping to negotiate my salary.") == {"negotiation"}
    assert features.bio_topics("A moment of momentum, really.") == set()
    assert features.bio_topics("Career changer, ex-consultant, now in tech.") == set(), "no interest is invented"
    assert features.bio_topics(None) == set()


def test_stated_interests_switch_the_bio_off(payloads, conn):
    for uid, payload in payloads.items():
        if db.get_member(conn, uid)["stated_interests"]:
            assert all(r["reasons"][0]["type"] != "profile_interest" for r in payload["recommendations"]), uid


def test_bio_fills_in_when_nothing_is_stated(payloads, conn):
    shown = 0
    for uid, payload in payloads.items():
        member = db.get_member(conn, uid)
        for r in payload["recommendations"]:
            if r["reasons"][0]["type"] == "profile_interest":
                shown += 1
                assert not member["stated_interests"]
                assert r["circle"]["topic"] in features.bio_topics(member["bio"])
    assert shown > 0


# ------------------------------------------------------------------ industry

def test_industry_never_moves_a_circle_on_her_topics(client, payloads, monkeypatch):
    monkeypatch.setattr(config, "INDUSTRY_BACKFILL_WEIGHT", 0.0)
    for uid, payload in payloads.items():
        without = recommendations(client, uid)
        on_topic = lambda p: [(r["rank"], r["circle"]["id"]) for r in p["recommendations"] if r["reasons"][0]["type"] in ON_TOPIC]
        assert on_topic(payload) == on_topic(without), uid


def test_industry_reasons_give_a_count_on_fallback_slots_only(payloads):
    shown = 0
    for uid, payload in payloads.items():
        for r in payload["recommendations"]:
            if r["reasons"][0]["type"] == "same_industry":
                shown += 1
                m = INDUSTRY_PRIMARY.match(r["reasons"][0]["detail"])
                assert m and int(m.group(1)) >= config.INDUSTRY_MIN_MEMBERS, r["reasons"][0]["detail"]
    assert shown > 0


# ------------------------------------------------------------------ company

def _coworker_circles(conn, member) -> set[int]:
    if not member["company"]:
        return set()
    return {r[0] for r in conn.execute(
        """SELECT DISTINCT m.circle_id FROM memberships m JOIN users u ON u.id = m.user_id
           WHERE u.company = ? AND m.user_id != ?""", (member["company"], member["id"]))}


def test_excluding_coworkers_filters_every_circle_with_one(client, conn):
    filtered = 0
    for uid in range(1, 101):
        member = db.get_member(conn, uid)
        payload = client.get(f"/users/{uid}/recommendations",
                             params={"limit": 20, "exclude_coworkers": "true", "debug": "true"}).json()
        assert payload["exclude_coworkers"] is True
        shown = {r["circle"]["id"] for r in payload["recommendations"]}
        assert not shown & _coworker_circles(conn, member), uid
        filtered += sum("coworkers" in e["reasons"] for e in payload["excluded"])
    assert filtered > 0


def test_by_default_company_plays_no_part(client, conn):
    assert "company" not in config.WEIGHTS
    assert recommendations(client, 15)["exclude_coworkers"] is False
    from app import recommender
    for uid in (7, 15, 23, 31):
        member = db.get_member(conn, uid)
        ids = lambda m: [r["circle"]["id"] for r in recommender.recommend(conn, m, 5)["recommendations"]]
        assert ids(member) == ids({**member, "company": "Some Other Co"}), uid


def test_the_flag_changes_nothing_for_a_member_without_a_company(client, conn):
    uid = next(u for u in range(1, 101) if not db.get_member(conn, u)["company"])
    ids = lambda params: [r["circle"]["id"] for r in client.get(f"/users/{uid}/recommendations", params=params).json()["recommendations"]]
    assert ids({"limit": 10}) == ids({"limit": 10, "exclude_coworkers": "true"})


def test_coworkers_are_never_shown(client, conn, payloads):
    companies = {r[0] for r in conn.execute("SELECT DISTINCT company FROM users WHERE company IS NOT NULL")}
    filtered = {uid: client.get(f"/users/{uid}/recommendations", params={"exclude_coworkers": "true"}).json()
                for uid in range(1, 101)}
    for payload in [*payloads.values(), *filtered.values()]:
        for r in payload["recommendations"]:
            text = " ".join([r["explanation"], *(x["detail"] for x in r["reasons"])])
            assert not any(c in text for c in companies), text
            assert not re.search(r"cowork|colleague|your company", text, re.IGNORECASE), text
