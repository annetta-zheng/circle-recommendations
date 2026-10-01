"""The pipeline: candidates -> hard filters -> features -> score -> diversify -> backfill -> explain
(plus an exploration slot after backfill, built and off by default).

Knows nothing about SQL (db.py) or HTTP (api.py).
"""

from __future__ import annotations

from datetime import date

from app import config, db, explain, features, scoring


def _strategy(stated: set[str], implicit: set[str]) -> str:
    if stated:
        return "stated_interests"
    if implicit:
        return "implicit_interests"
    return "newcomer"


def _circle_summary(c: dict) -> dict:
    return {
        "id": c["id"],
        "name": c["name"],
        "topic": c["topic"],
        "format": c["format"],
        "join_policy": c["join_policy"],
        "member_count": c["member_count"],
        "max_members": c["max_members"],
        "seats_left": c["seats_left"],
    }


def _newcomer_sample(scored: list[dict], limit: int) -> list[dict]:
    """Cold start: healthy, open, mid-size Circles, one per topic, ordered by activity.

    The score here is activity + availability only (nothing about her is known);
    same-city members act as a tie-break when she has a city. If distinct topics
    run out before the list is full, the next best Circles fill it.
    """
    def key(item: dict) -> tuple:
        city = min(item["meta"].get("same_city", 0), config.SAME_CITY_SATURATION)
        return (item["score"] + 0.001 * city, -item["circle"]["id"])

    # open Circles first: a newcomer should not start with a request she has to wait on
    ordered = sorted(scored, key=key, reverse=True)
    ordered = ([i for i in ordered if i["circle"]["join_policy"] == "open"]
               + [i for i in ordered if i["circle"]["join_policy"] != "open"])
    picked: list[dict] = []
    seen_topics: set[str] = set()
    for item in ordered:
        t = item["circle"]["topic"]
        if t and t in seen_topics:
            continue
        item = dict(item)
        item["diversity_multiplier"] = 1.0
        item["final_score"] = round(item["score"], 4)
        picked.append(item)
        if t:
            seen_topics.add(t)
        if len(picked) == limit:
            return picked
    picked_ids = {p["circle"]["id"] for p in picked}
    for item in ordered:
        if len(picked) == limit:
            break
        if item["circle"]["id"] in picked_ids:
            continue
        item = dict(item)
        item["diversity_multiplier"] = 1.0
        item["final_score"] = round(item["score"], 4)
        picked.append(item)
    return picked


def _week(as_of: str) -> str:
    """ISO week of the anchor date, the exploration seed's clock (never the wall clock)."""
    year, week, _ = date.fromisoformat(as_of[:10]).isocalendar()
    return f"{year}-W{week:02d}"


def recommend(conn, member: dict, limit: int = config.DEFAULT_LIMIT, debug: bool = False,
              offset: int = 0, exclude_coworkers: bool = False) -> dict:
    """Rank for `member` and return ranks offset+1 .. offset+limit.

    Pagination is a slice of one ranked list: both the diversifier and the newcomer
    sampler are prefix-stable (pick k depends only on picks 1..k-1), so page two is
    exactly what page one would have continued with. `next_offset` is None once the
    candidates are exhausted; the HTTP layer turns it into an opaque cursor.
    """
    circles = db.all_circles_with_stats(conn)
    labels = db.topic_labels(conn)
    as_of = db.as_of(conn)

    stated = set(member["stated_interests"])
    joined_ids = {m["circle_id"] for m in member["memberships"]}
    implicit: set[str] = set()
    implicit_source: dict[str, str] = {}
    for m in member["memberships"]:
        if m["topic"]:
            implicit.add(m["topic"])
            implicit_source.setdefault(m["topic"], m["name"])
    # topics of Circles she hosts; the reason then names the Circle she leads
    led: set[str] = set()
    for m in member["memberships"]:
        if m["topic"] and m["role"] == "leader":
            led.add(m["topic"])
            implicit_source[m["topic"]] = m["name"]
    # her bio fills in only when she stated nothing: stated interests always lead
    profile = set() if stated else features.bio_topics(member.get("bio"))
    strategy = _strategy(stated, implicit | profile)

    overlap = db.co_member_overlap(conn, member["id"], sorted(stated), member.get("location"),
                                   member.get("industry"), member.get("company"))

    # 1-2. candidates and hard filters (every reason recorded)
    excluded, candidates = [], []
    for c in circles:
        coworkers = overlap.get(c["id"], {}).get("coworkers", 0) if exclude_coworkers else 0
        reasons = features.exclusion_reasons(c, joined_ids, coworkers)
        if reasons:
            excluded.append({"circle_id": c["id"], "name": c["name"], "reasons": reasons})
        else:
            candidates.append(c)

    # 3. features
    percentiles = features.volume_percentiles(circles)  # global, over every Circle
    co = db.co_membership(conn, member["id"], features.co_membership_sources(member["memberships"]))  # degrees only
    scored = []
    for c in candidates:
        topic_score, topic_kind = features.topic(c, stated, implicit, profile, led)
        activity_score = features.activity(c, percentiles[c["id"]])
        social_score, social_meta = features.social(c, overlap.get(c["id"], {}), member.get("location"))
        availability_score = features.availability(c)
        co_score, co_meta = features.co_membership(co.get(c["id"], []))
        industry_score, industry_meta = features.industry(overlap.get(c["id"], {}), topic_kind)
        feats = {
            "topic": round(topic_score, 4),
            "activity": round(activity_score, 4),
            "social": round(social_score, 4),
            "availability": round(availability_score, 4),
            "co_membership": round(co_score, 4),
            "industry": round(industry_score, 4),
        }
        # 4. score
        if strategy == "newcomer":
            score = (config.NEWCOMER_ACTIVITY_WEIGHT * activity_score
                     + config.NEWCOMER_AVAILABILITY_WEIGHT * availability_score
                     + config.INDUSTRY_BACKFILL_WEIGHT * industry_score)  # every newcomer slot is backfill
        else:
            score = scoring.weighted_score(feats)
        scored.append({
            "circle": c,
            "features": feats,
            "score": round(score, 4),
            "meta": {
                "topic_kind": topic_kind,
                "implicit_source": implicit_source.get(c["topic"]) if c["topic"] else None,
                "stated": stated,
                **social_meta,
                **co_meta,
                **industry_meta,
                "explore": features.explore_kind(c, percentiles[c["id"]]),
            },
        })

    # 5. diversify, backfill, explore, then take this page of the ranked list
    want = offset + limit
    # one extra pick, so the list keeps its length when the explored Circle leaves the tail
    if strategy == "newcomer":
        picked = _newcomer_sample(scored, want + 1)
    else:
        picked = scoring.backfill(scoring.diversify(scored, want + 1), scored, want + 1)
    cold = not stated  # cold start: the implicit and newcomer rungs
    picked = scoring.explore(picked, scored, member["id"], _week(as_of), cold,
                             newcomer=strategy == "newcomer")[:want]
    page = picked[offset:want]
    next_offset = want if len(scored) > want else None

    # 6. explain
    recommendations = []
    for rank, item in enumerate(page, start=offset + 1):
        reasons, sentence = explain.build_reasons(item, member, labels, strategy)
        rec = {
            "rank": rank,
            "circle": _circle_summary(item["circle"]),
            "score": item["final_score"],
            "reasons": reasons,
            "explanation": sentence,
        }
        if debug:
            rec["score_breakdown"] = {**item["features"], "diversity_multiplier": item["diversity_multiplier"]}
            if item.get("explored"):
                rec["score_breakdown"]["explored"] = 1.0
        recommendations.append(rec)

    hints: list[str] = []
    if strategy == "newcomer":
        hints.append(explain.NEWCOMER_HINT)
    elif strategy == "implicit_interests":
        hints.append(explain.IMPLICIT_HINT)

    response = {
        "user_id": member["id"],
        "strategy": strategy,
        "as_of": as_of,
        "location": member.get("location") if member.get("location") not in config.NON_CITIES else None,
        "exclude_coworkers": exclude_coworkers,
        "recommendations": recommendations,
        "hints": hints,
        "next_offset": next_offset,
    }
    if debug:
        response["excluded"] = excluded
    return response
