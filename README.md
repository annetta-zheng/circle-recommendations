# Circle Recommendations

An API that returns a ranked list of Circles a member should join, with a member-facing reason for each. Built for the Lean In take-home on the provided seed dataset (100 members, 40 Circles).

- **Design doc (one page):** [`DESIGN.md`](DESIGN.md)
- **Schema and rationale:** [`schema.sql`](schema.sql), [`docs/schema.md`](docs/schema.md)
- **Requirements and API contract:** [`docs/system-design.md`](docs/system-design.md)
- **How I work with AI:** [below](#how-i-work-with-ai-on-this-repo)

## Contents

1. [Quick Start](#quick-start)
2. [What it does](#what-it-does)
3. [Response shape](#response-shape)
4. [Output Example: Four scenarios](#output-example-four-scenarios)
5. [Tests](#tests)
6. [Known limits](#known-limits)
7. [Layout](#layout)
8. [How I work with AI on this repo](#how-i-work-with-ai-on-this-repo)

## Quick Start

Python 3.10+ and nothing else. No external services.

```bash
make venv && source .venv/bin/activate   # or: python3 -m venv .venv && .venv/bin/pip install -r requirements.txt
make seed                                # python -m app.seed  -> builds circles.db from data/*.json
```

Then ask for recommendations straight from the terminal. The CLI runs in-process by default, so no server is needed:

```bash
python -m app.cli health                      # row counts and the anchor date
python -m app.cli user 15                     # the member as the ranker sees her
python -m app.cli recommend 15                # ranked Circles, one line each
python -m app.cli recommend 15 --debug -n 3   # + score breakdown, excluded Circles
python -m app.cli recommend 15 --cursor <c>   # next page; <c> is printed at the end of the previous one
python -m app.cli recommend 15 --location "Seattle, WA"   # rank as if she were there; also --location=-122.33,47.61 (lon,lat)
python -m app.cli recommend 15 --exclude-coworkers        # no Circle with a coworker in it
python -m app.cli --json recommend 15         # the raw API response
python -m pytest -q                           # or: make test
python scripts/eval_loo.py                    # or: make eval  -> leave-one-out sanity check
```

The same three commands exist as `make health`, `make user ID=15` and `make recommend ID=15 LIMIT=3 DEBUG=1`.

To serve it over HTTP, run `make serve` (uvicorn on port 8000) and point the CLI at it with `--url http://localhost:8000`, or open the interactive docs at `localhost:8000/docs`. The endpoints are plain GETs, so `curl -s "localhost:8000/users/15/recommendations?limit=5&debug=true"` works too.

The database is `circles.db` next to `schema.sql`; set `CIRCLES_DB=/some/path.db` to put it elsewhere (for example on a network or read-only mount).

## What it does

Six steps per request — **candidates → hard filters → features → score → diversify → explain**:

1. Candidates: every Circle she is not already in.
2. Hard filters, every reason recorded: full, unlisted, dormant (no posts in 30 days *and* quiet for 90+ days), and — only when she asks with `exclude_coworkers=true` — any Circle with a coworker in it.
3. Features: topic match (her stated interests; with none stated, topics of her Circles and, at a lower weight, topics her bio points to), activity (global percentile of log-volume blended with recency), social proof (members who share her interests; same-city members for Circles that meet in person), availability (open vs. request-to-join, size band), co-membership (people in her Circles who also belong to it, two or more needed, Adamic-Adar weighted so members who join everything count less).
4. Score = 0.45·topic + 0.25·activity + 0.20·social + 0.05·availability + 0.05·co-membership; the weights sum to 1.0 and every feature is 0–1. Industry is backfill only: slots that went to Circles off her topics are refilled in score + 0.10·same-industry order, so it never moves a Circle on her topics. Company is a filter only, when she asks (step 2). All numbers live in [`app/config.py`](app/config.py); which ones are weights, which are feature values and which only reorder is laid out in [`docs/system-design.md`](docs/system-design.md#where-each-number-acts).
5. Diversify: same-topic decay 0.7, then 0.5, so three near-identical Circles never fill the list. Leader signals and an exploration slot are built, tested and measured, and ship off: no version separates from another on this data ([version comparison](docs/system-design.md#version-comparison)).
6. Explain: one template per reason type; a member never sees a factor name or a score.

Cold start is a ladder, reported in the response as `strategy`: `stated_interests` → `implicit_interests` (topics of Circles she already belongs to, plus topics her bio points to — read only when she stated nothing, so stated interests always lead) → `newcomer` (healthy, open, mid-size Circles, one per topic, plus a hint to add interests).

Recency is measured against the dataset's anchor date (`AS_OF = 2026-09-21`), never the wall clock, so the same request returns the same answer next month.


## Response shape

`GET /users/{id}/recommendations?limit=5&debug=false&cursor=` — `limit` 1–20 (422 outside), 404 for an unknown member. `cursor` is the `next_cursor` of the previous page (400 if malformed or another member's); ranks continue across pages and the last page carries `null`. `location` overrides her profile city for the same-city signal, as `City, ST` or `lon,lat` snapped to the nearest of the ten member cities within 50 km; the response echoes the city used. `exclude_coworkers=true` drops every Circle with a coworker in it; the response echoes the flag, and the cursor does not carry it.

```json
{
  "user_id": 23, "strategy": "stated_interests", "as_of": "2026-09-21", "location": "Atlanta, GA", "exclude_coworkers": false,
  "recommendations": [{
    "rank": 1,
    "circle": {"id": 22, "name": "Level Up Collective", "topic": "getting-promoted", "format": "hybrid",
               "join_policy": "request", "member_count": 14, "max_members": 20, "seats_left": 6},
    "score": 0.7603,
    "reasons": [{"type": "topic_match", "detail": "You're interested in Getting promoted"},
                {"type": "active", "detail": "8 posts this month"},
                {"type": "shared_interests", "detail": "2 members share your interests"},
                {"type": "request_to_join", "detail": "You'll need to request to join"}],
    "explanation": "You're interested in Getting promoted — 8 posts this month and 2 members share your interests. You'll need to request to join.",
    "score_breakdown": {"topic": 1.0, "activity": 0.7612, "social": 0.4, "availability": 0.8, "co_membership": 0.0, "industry": 0.0, "diversity_multiplier": 1.0}
  }],
  "hints": [],
  "next_cursor": "MjM6MQ",
  "excluded": [{"circle_id": 21, "name": "Career Climbers", "reasons": ["unlisted", "dormant"]}, "..."]
}
```

`score_breakdown` and `excluded` appear only with `debug=true`. Other endpoints: `GET /health`, `GET /users/{id}` (the member as the ranker sees her).

## Output Example: Four scenarios

Real output from `make seed && make recommend ID=<id>` on this repo.

```
$ python -m app.cli recommend 42    # cold start
user 42 — strategy: newcomer, as_of 2026-09-21
1. #11 The Negotiation Table (negotiation, open, 15/25) — One of the most active open Circles — 13 posts this month and 10 open spots.
2. #1 The Elevation Circle (leadership-skills, open, 24/∞) — One of the most active open Circles — 15 posts this month and no member cap.
3. #13 Confident Voices (confidence, open, 12/18) — One of the most active open Circles — 14 posts this month and 6 open spots.
4. #31 Team Leads Circle (managing-a-team, open, 15/∞) — One of the most active open Circles — 10 posts this month and no member cap.
5. #39 Mentorship Matters (sponsorship-mentorship, open, 11/17) — One of the most active open Circles — 11 posts this month and 6 open spots.
hints: ["Add a few interests to get personalized matches."]
next page: --cursor NDI6NQ

$ python -m app.cli recommend 7    # obvious matches
user 7 — strategy: stated_interests, as_of 2026-09-21, in Seattle, WA
1. #1 The Elevation Circle (leadership-skills, open, 24/∞) — You're interested in Leadership skills — 15 posts this month and 3 members share your interests.
2. #6 Speak Up Circle (public-speaking, open, 9/13) — You're interested in Public speaking — 8 posts this month and 4 open spots.
3. #5 Women Who Lead (leadership-skills, open, 24/31) — You're interested in Leadership skills — 11 posts this month and 2 members share your interests.
4. #38 Speak With Impact (public-speaking, open, 4/6) — You're interested in Public speaking — 1 member shares your interests and 3 posts this month.
5. #31 Team Leads Circle (managing-a-team, open, 15/∞) — Tagged Public speaking — 10 posts this month and 3 members are also in Seattle, WA.
next page: --cursor Nzo1

$ python -m app.cli recommend 15    # duplicate Circles
user 15 — strategy: stated_interests, as_of 2026-09-21, in Chicago, IL
1. #13 Confident Voices (confidence, open, 12/18) — You're interested in Confidence — 14 posts this month and 6 members share your interests.
2. #10 Negotiation Circle (negotiation, open, 9/∞) — You're interested in Negotiation — 11 posts this month and 2 members share your interests.
3. #1 The Elevation Circle (leadership-skills, open, 24/∞) — You're in Board-Ready Women, another Leadership skills Circle — 15 posts this month and 4 members share your interests.
4. #11 The Negotiation Table (negotiation, open, 15/25) — You're interested in Negotiation — 13 posts this month and 2 members share your interests.
5. #37 The Confidence Lab (confidence, request, 8/∞) — You're interested in Confidence — 4 posts this month and 2 members share your interests. You'll need to request to join.
next page: --cursor MTU6NQ

$ python -m app.cli recommend 23    # full / inactive
user 23 — strategy: stated_interests, as_of 2026-09-21, in Atlanta, GA
1. #22 Level Up Collective (getting-promoted, request, 14/20) — You're interested in Getting promoted — 8 posts this month and 2 members share your interests. You'll need to request to join.
2. #15 The Network Effect (networking, request, 26/35) — Not on your topics, but 3 members also work in Media — 16 posts this month and 9 open spots. You'll need to request to join.
3. #19 Working Mothers United (working-motherhood, request, 10/∞) — Tagged Getting promoted — 9 posts this month and 2 members are also in Atlanta, GA. You'll need to request to join.
4. #31 Team Leads Circle (managing-a-team, open, 15/∞) — Not on your topics, but 2 members also work in Media — 10 posts this month and no member cap.
5. #1 The Elevation Circle (leadership-skills, open, 24/∞) — Not on your topics, but one of the most active Circles with room — 15 posts this month and 1 member shares your interests.
next page: --cursor MjM6NQ
```

Reading them against the brief:

- **42, cold start:** five different topics, all open, all active; the reasons say only what is true (activity and room) and the response carries a hint to add interests.
- **7, obvious matches:** her two stated interests lead; the brief names #5 and #6, and both are on the list. #1 outranks #5 on a tie-break (15 vs. 11 posts, no member cap; #5 has more chat and more Seattle members). Either order is defensible; the formula was not tuned to put #5 first.
- **15, duplicates:** two of the three near-identical negotiation Circles, never all three; her other interest, confidence, is represented; #30 Early Career Collective is a confidence Circle but `unlisted`, so it is never shown.
- **23, full / inactive:** #22 first; #20 is excluded as `full`, #21 as `unlisted` *and* `dormant` (see `debug=true`). With one interest and one healthy Circle on it, ranks 2–5 are fallbacks; her industry (Media) picks two of them, and #19 keeps its slot on a tag match.

## Tests

`make test` runs 61 tests in about a second:

- the four planted scenarios, asserted as the properties the brief describes rather than named Circles at named ranks;
- a sweep over all 100 members: full lists, never a joined, full, unlisted or dormant Circle, deterministic and prefix-stable responses;
- an explanation lint: no internal vocabulary, no two identical sentences per member, every number traceable to the data;
- exposure and mission checks: no Circle dominates the lists, every eligible Circle with a topic is recommended to someone, the median recommended Circle has 12 members and 71% of recommendations are in the active tier;
- pagination: page two is what page one would have continued with, ranks continue, the walk ends with a null cursor, foreign or malformed cursors are 400;
- location override: city strings canonicalise, coordinates snap within 50 km or resolve to none, the override changes social proof and is named in the reason;
- co-membership: fewer than two co-members count for nothing, members in many Circles count less, she is never her own co-member, a Circle hidden by leave-one-out does not leak back through its own members, reasons give a count, never a name, and a hot Circle (500 members) or a member of every Circle cannot fan the query out;
- profile fields: the bio matches whole words and invents no interest, is off whenever interests are stated, and fills in when none are; industry never moves a Circle on her topics and shows only as a count; `exclude_coworkers` removes every Circle with a coworker, by default company changes nothing, and coworkers are never shown;
- exploration and leader signals ship off and are tested both ways: off, no member sees an explore, host or hosted-topic reason; on, the slot goes only to cold-start members at rank 5, her top four never move, a newcomer's pick stays open and on a new topic, every explored Circle is alive and in the pool, and the pick rotates with the week; hosting lifts a topic but never above a stated one, and the host count is the leader's own posts;
- the CLI: every rank rendered, `--debug` shows the breakdown and exclusions, `--json` is byte-for-byte the API response, bad input exits non-zero with the API's message.

`make eval` runs a leave-one-out check (hide one membership, see if it comes back in the top 5), one row per configuration, so every number here can be rerun: random 0.190, topic only 0.266, the four base factors 0.266, plus co-membership 0.263, plus bio and industry 0.273 (shipped, MRR 0.150). Every row past random sits inside one standard error (about 0.022), and a paired sign test separates none of them from shipped; the versions and the test are in [`docs/system-design.md`](docs/system-design.md#version-comparison). Profile fields, leaders and post authors are random against memberships in the seed (`generate.mjs:201-204`, `:272`, `:343`), so this harness cannot reward them. The memberships were generated with a topic bias, so this is a regression check, not a tuning target — and it cannot see health or availability, which the ranker also optimizes for.

## Known limits

- #1 The Elevation Circle appears in about half of all lists: it is the most active, uncapped, open Circle, and activity is the only signal left once topic and social proof run out. An exploration slot is built (for cold-start members it pulls #1 to 46% of lists) but ships off; exposure caps are the production fix, not a hand-set penalty.
- The bio matcher is a keyword list per topic (`config.TOPIC_KEYWORDS`), a stand-in for embeddings; it misses anything phrased another way.
- #28 The Latina Coalition has no topic, no tags and moderate activity, so nothing recommends it to anyone; Circles like it need a content signal the dataset does not carry. The cheap fix is product, not model: require a topic when a leader creates a Circle; embeddings over the name come after.
- Three quantities are estimates from the data's tiers, not learned: the 90-day dormant threshold, the 14-day recency half-life, and the four weights.

## Layout

```
app/config.py        every weight, threshold and the anchor date
app/db.py            repository: the only SQL
app/seed.py          JSON -> SQLite
app/features.py      gates, the feature functions and the bio matcher
app/scoring.py       weighted sum and same-topic decay
app/explain.py       reason templates
app/recommender.py   the pipeline
app/api.py           FastAPI routes and response models
app/cli.py           terminal client: in-process by default, --url for a running server
schema.sql           tables, indexes, circle_stats view
tests/               scenarios, sweep, lint, exposure, mission, cli
scripts/eval_loo.py  leave-one-out harness
data/                the five seed files, unchanged
```

## How I work with AI on this repo

I use AI as a fast, fallible collaborator, not an oracle, with a fixed division of labor: I define the problem and the method, the agent collects data and implements, and I revise, verify and decide. I turned the brief into a one-page digest and testable properties, and set the method: simulate before coding, and measure every idea as a `make eval` row (accuracy, exposure, coverage) against shipped with a paired sign test. The rules I set are enforced through `AGENTS.md`: every weight and gate needs a data-traceable reason, nothing is tuned toward a named Circle, and members never see factor names or scores. The agent profiled the data, gathered the research (Wheelan on group size, McPherson on homophily, Abdollahpouri on popularity bias, Liu on event-based social networks) and implemented the ranker, tests and eval. I checked every data claim against `generate.mjs` or a query and decided the tradeoffs: a deterministic, fully explained ranker with exploration built but off until impression and join logs can judge it, where the agent wanted cold-start exploration live; company as a filter the member chooses; popularity held by an exposure bound rather than a hand-set penalty. Before each commit, scenario transcripts come from real CLI runs and the 61 tests and eval table are rerun.
