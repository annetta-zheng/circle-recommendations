"""Every number a reviewer might argue with lives here.

Weights and thresholds come from properties of the data (the generator's
health tiers, the tag noise, the chat-to-post ratio) and from a product stance
(small, active, joinable peer groups). They were checked against the four
planted members, not tuned until named Circles landed at named ranks.
"""

import os
from pathlib import Path

# ---------------------------------------------------------------- paths
ROOT = Path(__file__).resolve().parent.parent
DATA_DIR = ROOT / "data"
DB_PATH = Path(os.environ.get("CIRCLES_DB", ROOT / "circles.db"))  # override for read-only or network mounts
SCHEMA_PATH = ROOT / "schema.sql"

# ---------------------------------------------------------------- time
# Recency is measured against the dataset's anchor date, never the wall clock,
# so the same request returns the same answer next month.
AS_OF = "2026-09-21"

# ---------------------------------------------------------------- gates
DORMANT_MAX_POSTS_30D = 0      # no posts in the last 30 days ...
DORMANT_MIN_DAYS_QUIET = 90    # ... and quiet for 90+ days. Inactive tier: 150-250 d; moderate: <= 20 d.

# ---------------------------------------------------------------- score
# Five factors, summing to 1.0; every feature is 0-1. co_membership is a ranking factor at 0.05
# (it was a 0.02 tie-breaker): above the 0.030 median gap between neighbouring ranks, so it now
# reorders more than near-ties. At 0.10 it reshuffled 76 of 100 lists and lifted #1 from 49% to 52%
# of lists, so in this data it leans toward popularity; `make eval` (+ co-membership rows) tracks it.
# availability 0.10 -> 0.05: full Circles are already a hard filter and request-to-join is already in
# the reason, so what is left is open-over-request (now 0.015 of the score) and the size band (0.005).
# Every scenario holds at 0, 0.05 and 0.15. Leave-one-out cannot see joinability and its verdict flips
# with context (shipped 0.255 -> 0.266, base 0.271 -> 0.266; `make eval`, avail 0.10 rows), so it is
# not the reason. Newcomers keep their own 0.4.
WEIGHTS = {"topic": 0.45, "activity": 0.25, "social": 0.20, "availability": 0.05,
           "co_membership": 0.05}

# topic feature
TOPIC_STATED = 1.0               # circle.topic is one of her stated interests
TOPIC_IMPLICIT_ALONE = 0.6       # topic of a Circle she already belongs to, when she stated no interests
TOPIC_IMPLICIT_WITH_STATED = 0.4  # same, when she also has stated interests
TOPIC_LED_BONUS = 0.0             # on top of implicit when she leads a Circle on the topic. Tried at 0.2 (0.8 alone,
                                  # 0.6 beside stated: hosting says more than joining). Seed leaders are drawn at
                                  # random (generate.mjs:272), 1 of 40 interested in their topic, so it is noise here;
                                  # built, measured and shipped off: `make eval` puts it within noise of off (see docs/system-design.md, Version comparison)
TOPIC_TAG = 0.15                 # tag-only match: 54 of 83 tags are random -> tie-break only
TOPIC_PROFILE = 0.3              # topic her bio points to; read only when she stated no interests, so stated
                                 # interests always lead (obvious matches). Half of implicit-alone: a keyword in one
                                 # sentence is weaker evidence than a Circle she joined. Seed bios are random against
                                 # memberships (generate.mjs:201), so leave-one-out cannot reward it (`make eval`: no bio)

# Bio -> topics: words and phrases per topic, from each label plus plain synonyms. "*" marks a
# prefix ("negotiat*" = negotiate, negotiation); anything else is a whole word or phrase. A
# stand-in for embeddings. Of the six bios in the seed data, five point to a topic; the career
# changer's points to none, and none is invented for her.
TOPIC_KEYWORDS = {
    "leadership-skills": ("leader*", "leadership"),
    "managing-a-team": ("manager", "managers", "managing a team", "my team", "direct reports"),
    "giving-feedback": ("feedback",),
    "negotiation": ("negotiat*", "salary", "compensation", "comp conversation"),
    "getting-promoted": ("promot*", "next level"),
    "managing-up": ("managing up", "my boss"),
    "visibility-recognition": ("visibility", "visible", "recogni*"),
    "networking": ("network*",),
    "sponsorship-mentorship": ("mentor*", "sponsor*"),
    "confidence": ("confidence", "confident"),
    "imposter-syndrome": ("imposter", "impostor"),
    "stress-burnout": ("burnout", "burned out", "burnt out", "stress*"),
    "resilience": ("resilien*", "setback*"),
    "communication": ("communicat*",),
    "difficult-conversations": ("difficult conversation*", "hard conversation*", "tough conversation*"),
    "gender-bias": ("bias", "biases", "pay equity", "pay gap", "sexism"),
    "allyship": ("ally", "allies", "allyship"),
    "working-motherhood": ("mother*", "mom", "moms", "parent*"),
    "public-speaking": ("public speaking", "speaking", "speaker*", "presentation*"),
    "career-breaks": ("career break*", "returning to work", "return to work", "back to work"),
    "work-life-integration": ("work-life", "work life", "balance"),
    "being-the-only-one": ("only woman", "only one"),
}

# activity feature: global percentile of log-volume, blended with recency
VOLUME_WEIGHTS = {"posts": 1.0, "comments": 0.8, "chat": 0.7, "reactions": 0.3}
ACTIVITY_PERCENTILE_WEIGHT = 0.7
ACTIVITY_RECENCY_WEIGHT = 0.3
# host, inside activity: added to the activity feature (capped at 1.0) if the Circle's leader posted
# in the last 30 days. Tried at 0.02 (0.25 x 0.02 = 0.005 of the score): a Circle whose host went quiet
# tends to fold. Seed post authors are drawn uniformly from members (generate.mjs:343), so it splits
# the Circles 20/20 at random; built, measured and shipped off: `make eval` puts it within noise of off (see docs/system-design.md, Version comparison).
ACTIVITY_HOST_BONUS = 0.0
RECENCY_HALF_LIFE_DAYS = 14

# social feature
SOCIAL_SHARED_INTEREST_WEIGHT = 0.7
SOCIAL_SAME_CITY_WEIGHT = 0.3
SHARED_INTEREST_SATURATION = 0.5   # share of members sharing an interest at which the signal is 1.0
SAME_CITY_SATURATION = 3           # same-city members at which the signal is 1.0
NON_CITIES = {None, "", "Remote"}  # values that are not a place people can meet
CITY_FORMATS = {"in_person", "hybrid"}
# The ten cities members live in, as (lon, lat), so a ?location=lon,lat request can be
# snapped to one of them. The data has no coordinates of its own; this is the whole map.
CITY_COORDS = {
    "New York, NY": (-74.0060, 40.7128),
    "Toronto, ON": (-79.3832, 43.6532),
    "Seattle, WA": (-122.3321, 47.6062),
    "Chicago, IL": (-87.6298, 41.8781),
    "Atlanta, GA": (-84.3880, 33.7490),
    "Boston, MA": (-71.0589, 42.3601),
    "Austin, TX": (-97.7431, 30.2672),
    "Denver, CO": (-104.9903, 39.7392),
    "London, UK": (-0.1276, 51.5074),
}
CITY_MATCH_RADIUS_KM = 50  # roughly a metro area; farther than this and no city is "hers"

# co-membership feature: people in her Circles who also belong to the candidate
CO_MEMBERSHIP_MIN_MEMBERS = 2    # one overlap is noise at ~4 Circles per member, and a count of one points at a person
CO_MEMBERSHIP_SATURATION = 5.0   # Adamic-Adar sum at which the signal is 1.0: ~p90 of eligible pairs (p50 2.6), so it
                                 # still separates Circles for members in many Circles instead of pinning at 1.0
# Cost bounds, so a member in 100,000 Circles or a Circle with 100,000 members cannot fan
# the query out. None of them binds on the seed data (max 9 Circles per member, 28 members
# per Circle), so results there are unchanged. Worst case: 20 x 50 x 30 = 30,000 rows.
CO_MEMBERSHIP_MAX_SOURCE_CIRCLES = 20   # her most recently joined Circles: ~2x the most any member holds; recent says most about her now
CO_MEMBERSHIP_MAX_PEERS_PER_CIRCLE = 50  # most recent joiners per source Circle: above the largest Circle; peer groups are small by design
CO_MEMBERSHIP_MAX_PEER_DEGREE = 30      # co-members in more Circles are dropped, not just down-weighted: ~3x the data's max, a directory not a peer

# company: a filter only, and her choice. With ?exclude_coworkers=true every Circle with a coworker
# in it is a hard filter ("coworkers"): coworkers can dampen the candor a peer Circle needs. Without
# it, company plays no part in the ranking. Never shown: a coworker count would tell her who from her
# company is in, say, a working-mothers Circle. The filter changes 42 of 100 lists, none left short.

# industry feature (backfill): members in her industry, for Circles no topic signal claims.
INDUSTRY_MIN_MEMBERS = 2          # as co-membership: a count of one points at a person
SAME_INDUSTRY_SATURATION = 3      # same-industry members at which the signal is 1.0; a Circle averages about one
# Not in WEIGHTS: the list is ranked without it, then the slots that went to Circles off her
# topics are refilled in score + INDUSTRY_BACKFILL_WEIGHT * industry order (scoring.backfill).
# It decides which fallback fills a slot, never whether a slot is a fallback, so no Circle on
# her topics moves. Newcomer lists are all backfill, so it adds to the newcomer score directly.
INDUSTRY_BACKFILL_WEIGHT = 0.10   # a full signal outweighs the typical gap between fallbacks (0.01-0.05)

# availability feature
AVAIL_OPEN = 1.0
AVAIL_REQUEST = 0.7
SIZE_BAND_IDEAL = (6, 15)   # peer groups work at this size (Wheelan 2009: 3-8 beat 9+; the data's small and medium tiers run 3-15)
SIZE_BAND_BONUS = 0.10      # x the 0.05 availability weight = 0.005 of the score: a tie-break, never a topic override
SIZE_BAND_LARGE = 20        # 16-20 is neutral. No Circle has 16-23 members, so any cut there hits the same four (#1, #5, #15, #30)
SIZE_BAND_PENALTY = -0.10   # the same 0.005, against: big Circles already win on activity (more members, more posts)

# diversification: multiplier by number of same-topic Circles already picked (last value repeats)
SAME_TOPIC_DECAY = [1.0, 0.7, 0.5]

# newcomer sampler (cold start)
NEWCOMER_ACTIVITY_WEIGHT = 0.6
NEWCOMER_AVAILABILITY_WEIGHT = 0.4

# exploration: one deterministic epsilon-greedy slot, so Circles the ranker under-serves get seen.
# Seeded by sha256(member id, ISO week of as_of): the same request gets the same answer, and the
# pick rotates as as_of moves. Thompson sampling is the better bandit but needs impression and join
# logs to update its posterior; this data has neither. A newcomer's pick stays open and on a new topic.
# Both rates are built, measured and shipped off: `make eval` puts it within noise of off (see docs/system-design.md, Version comparison): cold start
# only (1.0) bought #1 50% -> 46% of lists and one more Circle shown for 2 fewer held-out hits (105 -> 103).
EXPLORE_EPSILON = 0.0          # members with stated interests (tried at 0.2 for everyone)
EXPLORE_COLD_EPSILON = 0.0     # cold start, nothing stated: the implicit and newcomer rungs, 30 members (tried at 1.0)
EXPLORE_RANK = 5               # last slot of the default page: her top four never move, and ranks above
                               # the slot are the same with or without it, so paging stays prefix-stable
EXPLORE_MIN_POSTS_30D = 2      # a quiet room, not an empty one
EXPLORE_MAX_PERCENTILE = 0.5   # the quieter half by activity: the ranker leans on activity, so they lose ties
EXPLORE_NEW_DAYS = 90          # created in the last quarter: a new Circle has no history to rank on

# ---------------------------------------------------------------- api
DEFAULT_LIMIT = 5
MAX_LIMIT = 20
