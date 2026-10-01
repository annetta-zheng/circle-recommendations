# AGENTS.md

Instructions for coding agents working in this repo. The README is for humans and explains what the ranker does; this file is what a new teammate would need to change it safely. The "How I work with AI" section at the end of the README is a different thing: the narrative of how AI was used on this submission. Do not fold its content into this file.

## Project

A FastAPI + SQLite service that ranks Circles (small peer groups) for a member and explains each pick in plain language. Python 3.10+, no external services. Pipeline: candidates → hard filters → features → score → diversify → backfill → explain. Full description in `README.md`, design rationale in `DESIGN.md`, requirements and API contract in `docs/system-design.md`, schema rationale in `docs/schema.md`.

## Setup and commands

```bash
make venv && source .venv/bin/activate   # creates .venv and installs pinned requirements.txt
make seed                                # data/*.json -> circles.db (rerun after touching schema.sql or app/seed.py)
make test                                # pytest, ~1 s, 61 tests
make eval                                # leave-one-out + exposure per variant row (~2 s); rows by name: python scripts/eval_loo.py "no bio" shipped
make serve                               # uvicorn with --reload on :8000
make recommend ID=15 LIMIT=5 DEBUG=1     # CLI, in-process; add URL=http://localhost:8000 to go over HTTP
```

The Makefile defaults `PY` to `.venv/bin/python`. Without the venv, nothing is installed system-wide, so run `make venv` first. The variable for the member id is `ID`, not `USER` (make inherits `USER` from the shell).

## Layout and layering

```
app/config.py        every weight, threshold, path and the anchor date AS_OF
app/db.py            the only module that writes SQL
app/seed.py          JSON -> SQLite, applies schema.sql
app/features.py      hard-filter gates, the feature functions and the bio matcher
app/scoring.py       weighted sum and same-topic decay
app/explain.py       reason templates -> member-facing sentences
app/recommender.py   the pipeline; no SQL, no HTTP
app/api.py           FastAPI routes and Pydantic models; no scoring
app/geo.py           ?location= resolver: city string or lon,lat -> a known city, via config.CITY_COORDS
app/cli.py           terminal client; goes through the FastAPI app, never around it
schema.sql           tables, indexes, the circle_stats view
tests/               scenarios, sweep, lint, exposure, mission, cli
scripts/eval_loo.py  leave-one-out harness
data/                the five seed files; never edit them
```

Keep the layers: SQL stays in `db.py`, HTTP stays in `api.py`, numbers stay in `config.py`. If a change needs a new query, add a function to `db.py` and call it from the pipeline.

## Rules that the tests enforce

- **Never tune toward a named Circle at a named rank.** Tests assert the properties the brief describes (her interests lead; never all three duplicate Circles; the full and dormant ones are absent), not fixed orderings. Do not add a test that pins a Circle to a rank.
- **Every number in `config.py` needs a reason in a comment beside it,** traceable to the data (health tiers, tag noise, chat-to-post ratio) or to a stated product stance. Do not add a magic number elsewhere.
- **Members never see a factor name or a score.** Explanations come from templates in `explain.py`, and `test_explanations_pass_lint` rejects internal vocabulary, duplicate sentences and numbers that do not trace to the data. Add a template rather than formatting a string inline.
- **Recency is measured against `AS_OF`, never the wall clock.** Do not call `datetime.now()` or `date.today()` anywhere in `app/`.
- **Hard filters record every applicable reason,** so `debug=true` shows a Circle as both `unlisted` and `dormant` when both apply. Do not short-circuit the gate loop.
- **Responses are deterministic.** Ties break on circle id. Any new sort needs a stable secondary key.
- **Ranked lists are prefix-stable.** Pagination slices one list (rank `offset+limit`, then the tail), so pick *k* may depend only on picks 1..*k*−1. A re-ranking step that looks at the whole page (say, a global exposure cap applied after selection) would break `test_ranked_lists_are_prefix_stable`; put such logic before selection or make the cursor carry state.
- **The sweep must stay clean:** every one of the 100 members gets a full list with no joined, full, unlisted or dormant Circle.

## Testing

- Run `make test` before every commit. The suite seeds a throwaway database into a temp directory (see `tests/conftest.py`) and points `config.DB_PATH` at it, so tests never touch `circles.db`.
- Test through the HTTP client fixture, not by calling `recommender.recommend` directly, unless the thing under test is a pure function in `features.py` or `scoring.py`.
- When a weight or threshold changes, also run `make eval` and report the hit@5 and MRR numbers in the commit message alongside the previous ones (random 0.190, topic only 0.266, shipped 0.273). Judge regressions on the shipped row: parity with topic-only is expected, and a drop is a regression. Rows marked `off:` switch on pieces that are built but shipped off; report them when they change.
- The table under "What each piece costs and buys" in `docs/system-design.md` is `make eval` output. When a weight, threshold or variant changes, rerun it and update the table and its reading notes in the same commit.
- Exploration is seeded by `scoring._seed` (sha256), never Python's `hash()`, which is salted per process and would break determinism.
- If the four scenario transcripts in the README change, regenerate them from real CLI output (`make recommend ID=42`, then 7, 15, 23) and paste them verbatim. Do not hand-edit them.
- Update the test count in the README Tests section when tests are added or removed.

## Gotchas

- `circles.db`, `*.db-journal`, `.venv/` and `.pytest_cache/` are gitignored. Never commit a database file.
- `CIRCLES_DB=/path/to.db` overrides the database location for the API, the CLI and the eval script.
- `/health` returns 503 with a hint if the database has not been seeded.
- `limit` is validated 1–20 at the API layer (`config.MAX_LIMIT`); the CLI surfaces the 422 message and exits 1.
- `?location=` overrides the profile city for one request only and is echoed back as `location`. Coordinates snap to `config.CITY_COORDS` within 50 km; the dataset has no coordinates of its own, so adding a city to the data means adding it to that table too. The cursor does not carry the override, and it does not carry `exclude_coworkers` either.
- Cursors are base64url of `user_id:offset`, encoded and decoded in `api.py`. They are bound to the user (400 otherwise) and are the only HTTP concept the recommender does not see; it takes a plain `offset`.
- Dormant means no posts in 30 days **and** quiet for 90+ days. Both halves are needed: the data's inactive tier is 150–250 days quiet and the moderate tier is at most 20 days, so 90 sits between them.
- Circle #1 The Elevation Circle appears in about half of all lists by design (most active, uncapped, open). The exposure test bounds this; do not add a hand-set penalty for it.

## Commits and PRs

- One logical change per commit, imperative subject line, under 72 characters, in the style of `Circle recommendations: API, schema, tests, design doc`.
- Docs move with code: a change to the pipeline, a weight or the response shape updates `README.md`, `DESIGN.md` and `docs/system-design.md` in the same commit.
- Before opening a PR: `make test`, `make eval` if scoring changed, and a fresh `make seed` to confirm the schema still loads.
