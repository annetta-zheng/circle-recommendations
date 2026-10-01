"""Leave-one-out sanity check: hide one existing membership, ask for recommendations,
see whether the hidden Circle comes back in the top 5.

Memberships were generated with a 3x bias toward members interested in the
Circle's topic, so they are weak, topic-flavoured positives: this harness can
compare variants and catch regressions, but it is not a target to tune toward,
and it cannot see health or availability, which the ranker also optimizes for.

    python scripts/eval_loo.py
"""

from __future__ import annotations

import random
import sys
from collections import Counter
from contextlib import contextmanager
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app import config, db, features, recommender  # noqa: E402

LIMIT = 5


def loo(conn, variant: str) -> tuple[float, float, int, dict]:
    """hit@LIMIT, MRR@LIMIT, trials, and per-trial hits keyed by (member, held-out Circle)."""
    hits = rr = 0.0
    n = 0
    per_trial: dict[tuple[int, int], bool] = {}
    circles = {c["id"]: c for c in db.all_circles_with_stats(conn)}
    rng = random.Random(0)
    for uid in range(1, db.count_members(conn) + 1):
        member = db.get_member(conn, uid)
        for held in list(member["memberships"]):
            c = circles[held["circle_id"]]
            if c["access"] == "unlisted" or c["is_full"] or features.is_dormant(c):
                continue  # could never be recommended
            trial = dict(member)
            trial["memberships"] = [m for m in member["memberships"] if m["circle_id"] != held["circle_id"]]
            if variant == "random":
                eligible = [cid for cid, x in circles.items()
                            if cid not in {m["circle_id"] for m in trial["memberships"]}
                            and x["access"] != "unlisted" and not x["is_full"] and not features.is_dormant(x)]
                ids = rng.sample(eligible, LIMIT)
            else:
                ids = [r["circle"]["id"] for r in recommender.recommend(conn, trial, LIMIT)["recommendations"]]
            n += 1
            per_trial[(uid, held["circle_id"])] = held["circle_id"] in ids
            if held["circle_id"] in ids:
                hits += 1
                rr += 1.0 / (ids.index(held["circle_id"]) + 1)
    return hits / n, rr / n, n, per_trial


def lists(conn) -> dict[int, list[int]]:
    """Every member's top-LIMIT list under the current settings."""
    return {uid: [r["circle"]["id"] for r in recommender.recommend(conn, db.get_member(conn, uid), LIMIT)["recommendations"]]
            for uid in range(1, db.count_members(conn) + 1)}


BASE = {k: config.WEIGHTS[k] for k in ("topic", "activity", "social", "availability")}
W = dict(config.WEIGHTS)
OFF = {  # every piece beyond the base four factors, switched off
    "INDUSTRY_BACKFILL_WEIGHT": 0.0, "EXPLORE_EPSILON": 0.0, "TOPIC_LED_BONUS": 0.0, "ACTIVITY_HOST_BONUS": 0.0,
    "EXPLORE_COLD_EPSILON": 0.0,
}


def ladder(weights: dict, bio: bool = False, **on) -> dict:
    """A cumulative row: the given weights, bio on or off, and only the named extras back on."""
    cfg = {k: v for k, v in OFF.items() if k not in on}
    return {"weights": weights, "config": cfg, "bio": bio}


# Rows switch pieces off: "weights" replaces config.WEIGHTS, "config" sets other module constants,
# and "bio": False stops the bio matcher. The ladder adds one piece per row; the "no ..." rows
# take one piece out of shipped. Every number in the docs comes from one of these rows.
TRIED = {"TOPIC_LED_BONUS": 0.2, "ACTIVITY_HOST_BONUS": 0.02}  # leader signals as they were tried

VARIANTS = {
    "random": None,
    "topic only": ladder({"topic": 1.0, "activity": 0.0, "social": 0.0, "availability": 0.0}),
    "activity only": ladder({"topic": 0.0, "activity": 1.0, "social": 0.0, "availability": 0.0}),
    "base four, avail 0.10": ladder({**BASE, "availability": 0.10}),
    "base four factors": ladder(BASE),
    "+ co-membership 0.02, avail 0.10": ladder({**BASE, "availability": 0.10, "co_membership": 0.02}),
    "+ co-membership 0.02": ladder({**BASE, "co_membership": 0.02}),
    "+ co-membership at 0.10": ladder({**BASE, "co_membership": 0.10}),
    "+ co-membership": ladder({**W}),
    "+ bio, industry (shipped)": {},
    "no co-membership": {"weights": {**W, "co_membership": 0.0}},
    "no bio": {"bio": False},
    "no industry backfill": {"config": {"INDUSTRY_BACKFILL_WEIGHT": 0.0}},
    # built, measured, shipped off
    "off: + leader signals": {"config": TRIED},
    "off: + leader, cold explore": {"config": {**TRIED, "EXPLORE_COLD_EPSILON": 1.0}},
    "off: + cold-start explore": {"config": {"EXPLORE_COLD_EPSILON": 1.0}},
    "off: + explore 1 in 5": {"config": {"EXPLORE_EPSILON": 0.2, "EXPLORE_COLD_EPSILON": 0.2}},
    # weightings tried and not shipped
    "alt: shipped, avail 0.10": {"weights": {**W, "availability": 0.10}},
    "alt: co-membership 0.02": {"weights": {**W, "co_membership": 0.02}},
    "alt: social 0.15, co 0.05": {"weights": {**W, "social": 0.15}},
}
SHIPPED = "+ bio, industry (shipped)"


@contextmanager
def applied(variant: dict | None):
    """Switch a variant on for the duration of the block, then restore everything."""
    variant = variant or {}
    weights = dict(config.WEIGHTS)
    saved = {k: getattr(config, k) for k in variant.get("config", {})}
    bio = features.bio_topics
    try:
        if "weights" in variant:
            config.WEIGHTS.clear()
            config.WEIGHTS.update(variant["weights"])
        for k, v in variant.get("config", {}).items():
            setattr(config, k, v)
        if variant.get("bio") is False:
            features.bio_topics = lambda bio: set()
        yield
    finally:
        config.WEIGHTS.clear()
        config.WEIGHTS.update(weights)
        for k, v in saved.items():
            setattr(config, k, v)
        features.bio_topics = bio


def main(names: list[str]) -> None:
    """`python scripts/eval_loo.py` runs every row; pass row names to run only those."""
    unknown = [n for n in names if n not in VARIANTS]
    if unknown:
        sys.exit(f"unknown variant(s): {unknown}; choose from {list(VARIANTS)}")
    with db.session() as conn:
        with applied(VARIANTS[SHIPPED]):
            _, _, n, shipped_trials = loo(conn, SHIPPED)
            shipped_lists = lists(conn)
        se = (0.26 * 0.74 / n) ** 0.5
        print(f"leave-one-out over {n} eligible memberships, top-{LIMIT} (weak topic-flavoured labels; "
              f"one standard error ~ {se:.3f})")
        print(f"  {'variant':28s} hit@{LIMIT}  MRR@{LIMIT}  hits   vs shipped   top Circle  shown  lists changed")
        for name in names or list(VARIANTS):
            with applied(VARIANTS[name]):
                hit, mrr, n, trials = loo(conn, name)
                gained = sum(trials[k] and not shipped_trials[k] for k in trials)
                lost = sum(shipped_trials[k] and not trials[k] for k in trials)
                if name == "random":
                    tail = "        —      —              —"
                else:
                    shown = lists(conn)
                    exposure = Counter(cid for ids in shown.values() for cid in ids)
                    top_id, top = exposure.most_common(1)[0]
                    changed = sum(shown[u] != shipped_lists[u] for u in shown)
                    tail = f"  #{top_id:<2d} {top / len(shown):4.0%}    {len(exposure):3d}  {changed:4d} / {len(shown)}"
            print(f"  {name:28s} {hit:.3f}   {mrr:.3f}   {round(hit * n):3d}   +{gained:<2d} -{lost:<2d}   {tail}")


if __name__ == "__main__":
    main(sys.argv[1:])
