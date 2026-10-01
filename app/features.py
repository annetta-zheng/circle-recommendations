"""Feature functions: each turns raw Circle/member facts into a 0-1 signal.

Nothing here knows about HTTP or SQL; inputs are the dicts the repository returns.
"""

from __future__ import annotations

import math
import re

from app import config


# ------------------------------------------------------------------ gates

def exclusion_reasons(circle: dict, joined_ids: set[int], coworkers: int = 0) -> list[str]:
    """Every reason a Circle must not be recommended (all that apply, not the first hit).

    `coworkers` is passed only when she asked to exclude Circles with coworkers.
    """
    reasons = []
    if circle["id"] in joined_ids:
        reasons.append("already_member")
    if coworkers:
        reasons.append("coworkers")
    if circle["is_full"]:
        reasons.append("full")
    if circle["access"] == "unlisted":
        reasons.append("unlisted")
    if is_dormant(circle):
        reasons.append("dormant")
    return reasons


def is_dormant(circle: dict) -> bool:
    return (
        circle["posts_30d"] <= config.DORMANT_MAX_POSTS_30D
        and circle["days_since_activity"] > config.DORMANT_MIN_DAYS_QUIET
    )


# ------------------------------------------------------------------ activity

def log_volume(circle: dict) -> float:
    """Log-scaled 30-day volume. Chat runs 10-20x posts, so every term is log1p'd."""
    w = config.VOLUME_WEIGHTS
    return (
        w["posts"] * math.log1p(circle["posts_30d"])
        + w["comments"] * math.log1p(circle["comments_30d"])
        + w["chat"] * math.log1p(circle["chat_30d"])
        + w["reactions"] * math.log1p(circle["reactions_30d"])
    )


def volume_percentiles(circles: list[dict]) -> dict[int, float]:
    """Global percentile of log-volume across all Circles (0 = quietest, 1 = busiest)."""
    vols = {c["id"]: log_volume(c) for c in circles}
    ordered = sorted(vols.values())
    n = len(ordered)
    out = {}
    for cid, v in vols.items():
        below = sum(1 for x in ordered if x < v)
        out[cid] = below / (n - 1) if n > 1 else 1.0
    return out


def recency(circle: dict) -> float:
    days = max(0.0, float(circle["days_since_activity"]))
    return math.exp(-math.log(2) * days / config.RECENCY_HALF_LIFE_DAYS)


def activity(circle: dict, percentile: float) -> float:
    """Volume percentile and recency, plus a small bonus when the host is still posting."""
    return min(1.0,
               config.ACTIVITY_PERCENTILE_WEIGHT * percentile
               + config.ACTIVITY_RECENCY_WEIGHT * recency(circle)
               + config.ACTIVITY_HOST_BONUS * host(circle))


# ------------------------------------------------------------------ topic

def _keyword_pattern(keyword: str) -> str:
    if keyword.endswith("*"):
        return r"\b" + re.escape(keyword[:-1]) + r"\w*"
    return r"\b" + re.escape(keyword) + r"\b"


_BIO_PATTERNS = {
    slug: re.compile("|".join(_keyword_pattern(k) for k in keywords), re.IGNORECASE)
    for slug, keywords in config.TOPIC_KEYWORDS.items()
}


def bio_topics(bio: str | None) -> set[str]:
    """Topics her bio points to, by config.TOPIC_KEYWORDS. Empty when nothing matches."""
    if not bio:
        return set()
    return {slug for slug, pattern in _BIO_PATTERNS.items() if pattern.search(bio)}


def topic(circle: dict, stated: set[str], implicit: set[str],
          profile: set[str] = frozenset(), led: set[str] = frozenset()) -> tuple[float, str | None]:
    """Returns (score, kind) where kind is 'stated', 'led', 'implicit', 'profile', 'tag' or None.

    `led` is the subset of `implicit` from Circles she leads.
    """
    t = circle["topic"]
    if t and t in stated:
        return config.TOPIC_STATED, "stated"
    if t and t in implicit:
        weight = config.TOPIC_IMPLICIT_WITH_STATED if stated else config.TOPIC_IMPLICIT_ALONE
        if t in led and config.TOPIC_LED_BONUS:
            return weight + config.TOPIC_LED_BONUS, "led"
        return weight, "implicit"
    if t and t in profile:
        return config.TOPIC_PROFILE, "profile"
    if stated and any(tag in stated for tag in circle["tags"]):
        return config.TOPIC_TAG, "tag"
    return 0.0, None


# ------------------------------------------------------------------ social

def social(circle: dict, overlap: dict, member_location: str | None) -> tuple[float, dict]:
    """Shared-interest share of current members, plus same-city members for Circles that meet."""
    shared = overlap.get("shared_interest", 0)
    share = shared / circle["member_count"] if circle["member_count"] else 0.0
    shared_score = min(1.0, share / config.SHARED_INTEREST_SATURATION)

    same_city = 0
    if (
        member_location not in config.NON_CITIES
        and circle["format"] in config.CITY_FORMATS
    ):
        same_city = overlap.get("same_city", 0)
    city_score = min(1.0, same_city / config.SAME_CITY_SATURATION)

    score = (
        config.SOCIAL_SHARED_INTEREST_WEIGHT * shared_score
        + config.SOCIAL_SAME_CITY_WEIGHT * city_score
    )
    return score, {"shared_interest": shared, "same_city": same_city}


# ------------------------------------------------------------------ co-membership

def co_membership_sources(memberships: list[dict], limit: int = config.CO_MEMBERSHIP_MAX_SOURCE_CIRCLES) -> list[int]:
    """Her Circles that seed co-membership: the `limit` most recently joined, ties on id."""
    ordered = sorted(memberships, key=lambda m: (m["joined_at"], m["circle_id"]), reverse=True)
    return sorted(m["circle_id"] for m in ordered[:limit])


def co_membership(degrees: list[int]) -> tuple[float, dict]:
    """People in her Circles who also belong to this one, Adamic-Adar weighted.

    `degrees` holds, per co-member, how many Circles that co-member is in. Each counts
    1/log(1+d), so a member who joins everything does not steer every list.
    Below CO_MEMBERSHIP_MIN_MEMBERS distinct co-members the signal is zero.
    """
    n = len(degrees)
    if n < config.CO_MEMBERSHIP_MIN_MEMBERS:
        return 0.0, {"co_members": 0}
    aa = sum(1.0 / math.log(1 + d) for d in degrees)
    return min(1.0, aa / config.CO_MEMBERSHIP_SATURATION), {"co_members": n}


# ------------------------------------------------------------------ host

def host(circle: dict) -> float:
    """1.0 if the Circle's leader posted in the last 30 days (part of activity): a quiet host's Circle tends to fold."""
    return 1.0 if circle["leader_posts_30d"] > 0 else 0.0


# ------------------------------------------------------------------ exploration pool

def explore_kind(circle: dict, percentile: float) -> str | None:
    """Why a Circle is in the exploration pool: 'new', 'no_topic', 'quiet', or None if it is not.

    The pool is Circles the ranker under-serves but that are alive: new ones have
    no history, ones without a topic match no one, and the quieter half by
    activity loses the ties. Hard filters have already run.
    """
    if circle["posts_30d"] < config.EXPLORE_MIN_POSTS_30D:
        return None
    if circle["age_days"] <= config.EXPLORE_NEW_DAYS:
        return "new"
    if not circle["topic"]:
        return "no_topic"
    if percentile < config.EXPLORE_MAX_PERCENTILE:
        return "quiet"
    return None


# ------------------------------------------------------------------ industry (backfill)

def industry(overlap: dict, topic_kind: str | None) -> tuple[float, dict]:
    """Members in her industry, counted only for Circles no topic signal claims.

    Zero for any Circle on her topics (stated, implicit, profile or tag), so it
    adds nothing there and only reorders the fallback slots. Below
    INDUSTRY_MIN_MEMBERS the signal is zero.
    """
    n = overlap.get("same_industry", 0)
    if topic_kind is not None or n < config.INDUSTRY_MIN_MEMBERS:
        return 0.0, {"same_industry": 0}
    return min(1.0, n / config.SAME_INDUSTRY_SATURATION), {"same_industry": n}


# ------------------------------------------------------------------ availability

def availability(circle: dict) -> float:
    base = config.AVAIL_OPEN if circle["join_policy"] == "open" else config.AVAIL_REQUEST
    n = circle["member_count"]
    lo, hi = config.SIZE_BAND_IDEAL
    if lo <= n <= hi:
        base += config.SIZE_BAND_BONUS
    elif n > config.SIZE_BAND_LARGE:
        base += config.SIZE_BAND_PENALTY
    return max(0.0, min(1.0, base))
