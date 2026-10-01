# Circle Recommendations — Technical Design

**What it is.** `GET /users/{user_id}/recommendations?limit=5` returns ranked Circles, each with a member-facing reason. Python / FastAPI / SQLite, no external services. Circles are small peer groups, so the ranker looks for one she will *join and stay in*: her topic, alive this month, room for her. Schema rationale: `docs/schema.md`.

## Pipeline: filter → score → diversify → backfill → explain

1. **Candidates** — every Circle she is not already in.
2. **Hard filters**, every reason recorded — full; `unlisted`; dormant (no posts in 30 days *and* quiet 90+ days — between the data's tiers); and, only when she asks with `exclude_coworkers=true`, any Circle with a coworker in it. That filter is all company does: it is never scored and never shown (a count would tell her who from her company is in, say, a working-mothers Circle).
3. **Score** = 0.45·topic + 0.25·activity + 0.20·social + 0.05·availability + 0.05·co-membership (`config.py`), summing to 1.0. *Topic*: 1.0 stated interest, 0.6 from a Circle she belongs to, 0.3 from her bio (read only when she stated nothing, so stated interests always lead), 0.15 tag-only (most tags are random). *Activity*: global percentile of log-volume with recency. *Social*: members sharing a stated interest, plus same-city members where Circles meet in person. *Availability*: 1.0 open, 0.7 request-to-join, ±0.1 for the 6–15 size band; halved to 0.05 because full Circles are already filtered. *Co-membership*: people in her Circles who also belong to it, two or more needed, each weighted 1/log(1 + Circles they are in) (Adamic-Adar) so members who join everything do not steer every list; the member sees a count, never a name.
4. **Diversify** — MMR with a binary topic similarity: same-topic decay 0.7, then 0.5, so near-duplicates cannot fill the list.
5. **Backfill** — the slots diversify gave to Circles off her topics are refilled in score + 0.10 × same-industry order. It changes which fallbacks fill those slots, never whether a slot is a fallback, so a Circle on her topics never moves.
6. **Explain** — two factors with real numbers: *"You're interested in Negotiation — 13 posts this month and 10 open spots."* No factor names or scores; a test lints every sentence.

## Cold start is 30% of members

A ladder, reported as `strategy`: stated interests → implicit interests from memberships and bio → **newcomer sampler** (healthy, open, mid-size Circles, one per topic, plus a hint to add interests).

## Planted scenarios (real output in the README)

**42** gets five open, active Circles on five topics. **7**'s two interests lead, with #5 and #6 both listed. **15** gets two of the three negotiation Circles, never all three; #30 is `unlisted`. **23** gets #22 first; #20 is excluded as full, #21 as unlisted *and* dormant.

## Tradeoffs

- **Weighted heuristic over a learned ranker.** No join/decline labels exist; leave-one-out puts the shipped weights at parity with topic-only, as a topic-biased generator predicts.
- **Hard filters for full, unlisted, dormant; soft demotion otherwise.** She cannot join a full Circle; a quiet one is just weaker.
- **Bio and industry ship though leave-one-out cannot see them.** Seed profiles are random against memberships; the case for them is product, not this harness.
- **Leader signals and exploration are built, tested and shipped off.** Every version from the first commit on scores 0.266–0.276 hit@5 and a paired sign test separates none of them (`docs/system-design.md`, Version comparison), so shipped is the plain ranker. Cold-start exploration (a deterministic epsilon slot at rank 5) would pull #1 from 50% to 46% of lists; Thompson sampling replaces it once impressions and joins are logged.
- **Co-membership small (0.05).** On this data it leans toward popularity: at 0.10 it reshuffled 76 of 100 lists and lifted #1 from 49% to 52%; removing it moves #1 from 50% to 48%. Leave-one-out cannot tell 0, 0.02, 0.05 and 0.10 apart.

## Production scale (170k+ Circles)

Precomputed `circle_stats`; Thompson sampling over join rate in place of the epsilon slot, once impressions and joins are logged; then a gradient-boosted ranker (LambdaMART) over today's features plus a graph-embedding similarity, which keeps per-feature attributions for the reason templates; co-membership as precomputed Circle-to-Circle neighbours, with the request-time bounds already in place (her 20 most recent Circles, 50 co-members each, co-members in more than 30 Circles dropped) so a hot Circle or a heavy joiner cannot fan out; candidates from topic and geo indexes; join/decline/leave logs feeding offline evaluation, A/B tests and learning-to-rank; exposure caps and light exploration; embeddings for Circles without a topic.

**With more time.** A required topic when a leader creates a Circle (#27 and #28 have none, and 54 of 83 tags are random, so labels added later are not reliable); posts per member; career-stage affinity from job titles; same-company caution on negotiation and promotion topics.

**How I worked with AI.** Rules, what the agent produced, what I reversed, what I verified: the last section of `README.md`.
