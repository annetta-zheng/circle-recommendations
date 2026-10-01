"""The four planted scenarios from the dataset README, asserted as properties.

The assertions are the brief's own expectations, kept property-based so the
formula is never back-solved from a named Circle at a named rank.
"""

from app import config
from tests.conftest import ids, recommendations

NEGOTIATION_TRIPLET = {10, 11, 12}


def test_cold_start_user_42_gets_an_honest_open_list(client):
    payload = recommendations(client, 42, debug=True)
    recs = payload["recommendations"]

    assert payload["strategy"] == "newcomer"
    assert len(recs) == 5
    assert payload["hints"], "a newcomer is told how to get personal matches"
    # every pick is joinable today and alive
    assert all(r["circle"]["join_policy"] == "open" for r in recs)
    assert all(r["circle"]["seats_left"] is None or r["circle"]["seats_left"] > 0 for r in recs)
    assert all("active" in r["explanation"] or "posts this month" in r["explanation"] for r in recs)
    # a sampler, not five Circles on one topic
    topics = [r["circle"]["topic"] for r in recs if r["circle"]["topic"]]
    assert len(topics) == len(set(topics))
    # no invented personalization: the sampler's reasons, plus the one exploration slot at its rank
    assert all(r["reasons"][0]["type"] == "newcomer" for r in recs if r["rank"] != config.EXPLORE_RANK)
    assert recs[config.EXPLORE_RANK - 1]["reasons"][0]["type"] in {"newcomer", "explore"}


def test_obvious_matches_user_7(client):
    payload = recommendations(client, 7)
    recs = payload["recommendations"]
    top_ids = ids(payload)

    assert payload["strategy"] == "stated_interests"
    # her stated interests lead the list
    assert {recs[0]["circle"]["topic"], recs[1]["circle"]["topic"]} == {"leadership-skills", "public-speaking"}
    assert all(r["reasons"][0]["type"] == "topic_match" for r in recs[:2])
    # the Circles the brief names for her two interests are on the list
    assert 5 in top_ids and 6 in top_ids


def test_duplicate_circles_user_15(client):
    payload = recommendations(client, 15)
    top_ids = ids(payload)

    assert payload["strategy"] == "stated_interests"
    # three near-identical negotiation Circles never all make the list
    assert len(NEGOTIATION_TRIPLET & set(top_ids)) < 3
    # her other stated interest is represented
    topics = {r["circle"]["topic"] for r in payload["recommendations"]}
    assert "negotiation" in topics and "confidence" in topics
    # the unlisted confidence Circle never appears
    assert 30 not in top_ids


def test_full_and_inactive_user_23(client):
    payload = recommendations(client, 23, debug=True)
    top_ids = ids(payload)

    assert top_ids[0] == 22, "the healthy alternative on her topic ranks first"
    assert 20 not in top_ids and 21 not in top_ids
    excluded = {e["circle_id"]: e["reasons"] for e in payload["excluded"]}
    assert excluded[20] == ["full"]
    assert set(excluded[21]) == {"unlisted", "dormant"}, "every applicable reason is recorded"


def test_limit_is_respected_and_capped(client):
    assert len(ids(recommendations(client, 7, limit=3))) == 3
    assert len(ids(recommendations(client, 7, limit=20))) <= 20
    assert client.get("/users/7/recommendations", params={"limit": 0}).status_code == 422
    assert client.get("/users/7/recommendations", params={"limit": 21}).status_code == 422


def test_pagination_is_a_slice_of_one_ranked_list(client):
    whole = ids(recommendations(client, 15, limit=10))
    page1 = recommendations(client, 15, limit=4)
    page2 = client.get("/users/15/recommendations", params={"limit": 4, "cursor": page1["next_cursor"]}).json()
    assert ids(page1) + ids(page2) == whole[:8], "page two is what page one would have continued with"
    assert [r["rank"] for r in page2["recommendations"]] == [5, 6, 7, 8], "ranks continue across pages"
    # walk to the end: every eligible Circle exactly once, then an empty page with no cursor
    seen, cursor, pages = [], None, 0
    while True:
        params = {"limit": 20, **({"cursor": cursor} if cursor else {})}
        payload = client.get("/users/15/recommendations", params=params).json()
        seen += ids(payload); pages += 1; cursor = payload["next_cursor"]
        if cursor is None:
            break
    assert len(seen) == len(set(seen)) and pages <= 3
    past_end = client.get("/users/15/recommendations", params={"limit": 5, "cursor": "MTU6OTk"}).json()
    assert past_end["recommendations"] == [] and past_end["next_cursor"] is None


def test_cursor_is_validated_and_bound_to_the_user(client):
    assert client.get("/users/15/recommendations", params={"cursor": "not-base64!"}).status_code == 400
    cursor = recommendations(client, 15, limit=2)["next_cursor"]
    r = client.get("/users/7/recommendations", params={"cursor": cursor})
    assert r.status_code == 400 and "belong" in r.json()["detail"]


def test_unknown_user_is_404(client):
    r = client.get("/users/999/recommendations")
    assert r.status_code == 404
    assert "999" in r.json()["detail"]


def test_health_and_member_view(client):
    h = client.get("/health").json()
    assert h["status"] == "ok" and h["users"] == 100 and h["circles"] == 40
    m = client.get("/users/15").json()
    assert m["stated_interests"] == ["confidence", "negotiation"]
    assert {x["circle_id"] for x in m["memberships"]} == {9, 35}
