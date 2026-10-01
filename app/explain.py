"""Member-facing reasons, built from the factors that actually produced the score.

One template per reason type. A member never sees a factor name, a weight or a
score; every number in a sentence is a number from the data.
"""

from __future__ import annotations

from app import config


def _plural(n: int, one: str, many: str) -> str:
    return one if n == 1 else many


def _host_reason(n: int) -> dict:
    return {"type": "host_active", "detail": f"the host posted {n} {_plural(n, 'time', 'times')} this month"}


def _co_members_reason(n: int, member: dict) -> dict:
    yours = _plural(len(member["memberships"]), "your Circle", "your Circles")
    return {"type": "co_members", "detail": f"{n} members of {yours} are here too"}


def build_reasons(item: dict, member: dict, labels: dict[str, str], strategy: str) -> tuple[list[dict], str]:
    circle = item["circle"]
    feats = item["features"]
    meta = item["meta"]

    # ---- primary reason: why this Circle is on her topic (or why it is here anyway)
    primary: dict | None = None
    kind = meta.get("topic_kind")
    label = labels.get(circle["topic"] or "", circle["topic"] or "")
    if item.get("explored"):
        # the honest reason: it is here to be tried, not because it matched
        detail = "A new Circle worth a look" if meta.get("explore") == "new" else "One you might not find on your own"
        primary = {"type": "explore", "detail": detail}
    elif kind == "stated":
        primary = {"type": "topic_match", "detail": f"You're interested in {label}"}
    elif kind == "led":
        src = meta.get("implicit_source") or "a Circle you lead"
        primary = {"type": "led_interest", "detail": f"You lead {src}, another {label} Circle"}
    elif kind == "implicit":
        src = meta.get("implicit_source") or "a Circle you belong to"
        primary = {"type": "implicit_interest", "detail": f"You're in {src}, another {label} Circle"}
    elif kind == "profile":
        primary = {"type": "profile_interest", "detail": f"Your profile suggests {label.lower()}"}
    elif kind == "tag":
        tagged = next((t for t in circle["tags"] if t in meta.get("stated", set())), None)
        primary = {"type": "tag", "detail": f"Tagged {labels.get(tagged, tagged)}"}
    elif strategy == "newcomer":
        detail = "One of the most active open Circles"
        n = meta.get("same_industry", 0)
        if n >= config.INDUSTRY_MIN_MEMBERS:
            detail += f", with {n} members in {member['industry']}"
        primary = {"type": "newcomer", "detail": detail}
    elif meta.get("same_industry", 0) >= config.INDUSTRY_MIN_MEMBERS:
        n = meta["same_industry"]
        primary = {"type": "same_industry",
                   "detail": f"Not on your topics, but {n} members also work in {member['industry']}"}
    else:
        primary = {"type": "fallback", "detail": "Not on your topics, but one of the most active Circles with room"}

    # ---- supporting details, ordered by how much each factor contributed
    supporting: list[dict] = []
    contributions = sorted(
        ((config.WEIGHTS.get(k, 0.0) * feats[k], k) for k in ("activity", "social", "availability", "co_membership")),
        reverse=True,
    )
    for _, factor in contributions:
        if factor == "activity":
            n = circle["posts_30d"]
            if n >= 1:
                supporting.append({"type": "active", "detail": f"{n} {_plural(n, 'post', 'posts')} this month"})
            elif circle["days_since_activity"] <= 7:
                supporting.append({"type": "active", "detail": "active this week"})
        elif factor == "social":
            shared = meta.get("shared_interest", 0)
            same_city = meta.get("same_city", 0)
            if shared >= 1:
                supporting.append({"type": "shared_interests",
                                   "detail": f"{shared} {_plural(shared, 'member shares', 'members share')} your interests"})
            elif same_city >= 1:
                supporting.append({"type": "same_city",
                                   "detail": f"{same_city} {_plural(same_city, 'member is', 'members are')} also in {member.get('location')}"})
        elif factor == "co_membership":
            n = meta.get("co_members", 0)
            if n >= config.CO_MEMBERSHIP_MIN_MEMBERS:
                supporting.append(_co_members_reason(n, member))
        elif factor == "availability":
            seats = circle["seats_left"]
            if seats is None:
                supporting.append({"type": "open_spots", "detail": "no member cap"})
            elif seats > 0:
                supporting.append({"type": "open_spots", "detail": f"{seats} open {_plural(seats, 'spot', 'spots')}"})
        if len(supporting) == 2:
            break

    reasons = [primary] + supporting
    # host (part of activity) and co-membership rarely make the sentence; the facts still go in
    # `reasons` for a UI to show
    if config.ACTIVITY_HOST_BONUS and circle["leader_posts_30d"] > 0:
        reasons.append(_host_reason(circle["leader_posts_30d"]))
    # co-membership: a count only, the ranker never holds names
    n = meta.get("co_members", 0)
    if n >= config.CO_MEMBERSHIP_MIN_MEMBERS and not any(s["type"] == "co_members" for s in supporting):
        reasons.append(_co_members_reason(n, member))
    if circle["join_policy"] == "request":
        reasons.append({"type": "request_to_join", "detail": "You'll need to request to join"})

    # ---- one sentence
    sentence = primary["detail"]
    if supporting:
        sentence += " — " + " and ".join(s["detail"] for s in supporting)
    sentence += "."
    if circle["join_policy"] == "request":
        sentence += " You'll need to request to join."
    return reasons, sentence


NEWCOMER_HINT = "Add a few interests to get personalized matches."
IMPLICIT_HINT = "Add a few interests and we'll match on them directly."
