"""Weighted score and diversification. Weights come from config; nothing is learned."""

from __future__ import annotations

import hashlib

from app import config


def weighted_score(feats: dict[str, float]) -> float:
    return sum(config.WEIGHTS[k] * feats[k] for k in config.WEIGHTS)


def backfill(picked: list[dict], scored: list[dict], limit: int) -> list[dict]:
    """Refill the fallback slots of `picked` in industry order; every other slot stays.

    A fallback slot is one that went to a Circle with no topic signal for her.
    Fallbacks are re-ranked on score + INDUSTRY_BACKFILL_WEIGHT * industry with
    the same diversifier, and the k-th fallback slot takes the k-th of them. Both
    lists are prefix-stable, so the result is too.
    """
    weight = config.INDUSTRY_BACKFILL_WEIGHT
    fallbacks = [i for i in scored if i["meta"]["topic_kind"] is None]
    if not weight or not any(i["features"].get("industry") for i in fallbacks):
        return picked
    boosted = [dict(i, score=round(i["score"] + weight * i["features"]["industry"], 4)) for i in fallbacks]
    refill = iter(diversify(boosted, limit))
    return [next(refill) if item["meta"]["topic_kind"] is None else item for item in picked]


def _seed(*parts) -> int:
    """A stable hash (Python's hash() is salted per process, so it would break determinism)."""
    return int(hashlib.sha256(":".join(map(str, parts)).encode()).hexdigest()[:16], 16)


def explores(member_id: int, week: str, cold: bool) -> bool:
    """Whether this member-week gets an exploration slot: EXPLORE_COLD_EPSILON at cold start, else EXPLORE_EPSILON."""
    epsilon = config.EXPLORE_COLD_EPSILON if cold else config.EXPLORE_EPSILON
    return _seed(member_id, week) % 10_000 < epsilon * 10_000


def explore(picked: list[dict], scored: list[dict], member_id: int, week: str, cold: bool,
            newcomer: bool = False) -> list[dict]:
    """Put one exploration pick at EXPLORE_RANK when explores() says so.

    The pick is the pool Circle (meta["explore"]) with the lowest seeded hash that
    is not already above the slot. A newcomer's pick also keeps her sampler's
    promises: open to join, and a topic not already above it. Ranks above the slot
    are `picked` unchanged and the pick depends only on them, so the list stays
    prefix-stable; below it the ranked list continues without the explored Circle.
    Callers pass one extra pick so the list keeps its length.
    """
    k = config.EXPLORE_RANK
    if len(picked) < k or not explores(member_id, week, cold):
        return picked
    above = {i["circle"]["id"] for i in picked[:k - 1]}
    above_topics = {i["circle"]["topic"] for i in picked[:k - 1] if i["circle"]["topic"]}
    pool = [i for i in scored if i["meta"].get("explore") and i["circle"]["id"] not in above
            and (not newcomer or (i["circle"]["join_policy"] == "open" and i["circle"]["topic"] not in above_topics))]
    if not pool:
        return picked
    choice = min(pool, key=lambda i: (_seed(member_id, week, i["circle"]["id"]), i["circle"]["id"]))
    choice = dict(choice, explored=True, diversity_multiplier=1.0, final_score=choice["score"])
    rest = [i for i in picked[k - 1:] if i["circle"]["id"] != choice["circle"]["id"]]
    return picked[:k - 1] + [choice] + rest


def diversify(scored: list[dict], limit: int) -> list[dict]:
    """Greedy MMR with a binary topic similarity.

    After each pick, every remaining Circle on the same topic is multiplied by
    SAME_TOPIC_DECAY[k], k = how many Circles on that topic are already picked.
    Near-duplicates therefore cannot fill the list, but a second Circle on a
    stated interest stays competitive with off-topic ones. Circles without a
    topic never decay each other. Ties break on circle id for determinism.
    """
    remaining = list(scored)
    picked: list[dict] = []
    per_topic: dict[str, int] = {}
    decay = config.SAME_TOPIC_DECAY

    while remaining and len(picked) < limit:
        def effective(item: dict) -> float:
            t = item["circle"]["topic"]
            k = per_topic.get(t, 0) if t else 0
            return item["score"] * decay[min(k, len(decay) - 1)]

        best = max(remaining, key=lambda item: (effective(item), -item["circle"]["id"]))
        t = best["circle"]["topic"]
        k = per_topic.get(t, 0) if t else 0
        best = dict(best)
        best["diversity_multiplier"] = decay[min(k, len(decay) - 1)]
        best["final_score"] = round(best["score"] * best["diversity_multiplier"], 4)
        picked.append(best)
        remaining.remove(next(r for r in remaining if r["circle"]["id"] == best["circle"]["id"]))
        if t:
            per_topic[t] = k + 1
    return picked
