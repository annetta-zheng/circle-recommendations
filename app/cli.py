"""Command-line client for the recommendations API.

    python -m app.cli health
    python -m app.cli user 15
    python -m app.cli recommend 15 --limit 5 --debug
    python -m app.cli recommend 15 --cursor <next_cursor>   # the next page
    python -m app.cli recommend 15 --location "Seattle, WA"  # or --location=-122.33,47.61 (lon,lat)
    python -m app.cli recommend 15 --exclude-coworkers      # no Circle with a coworker in it
    python -m app.cli --url http://localhost:8000 recommend 15 --json

Without --url the commands run in-process against circles.db (no server needed),
through the same FastAPI app the server exposes, so validation, status codes and
the JSON shape are identical. With --url they go over HTTP to a running server.
"""

from __future__ import annotations

import argparse
import json
import sys
from typing import Any

import httpx

INF = "∞"  # no member cap


# ------------------------------------------------------------------ transport

def _client(url: str | None):
    if url:
        return httpx.Client(base_url=url.rstrip("/"), timeout=10.0)
    from fastapi.testclient import TestClient  # in-process ASGI client, same routes as the server
    from app.api import app
    return TestClient(app)


def _get(client, path: str, params: dict[str, Any] | None = None) -> dict:
    try:
        r = client.get(path, params=params)
    except httpx.ConnectError as e:
        sys.exit(f"error: cannot reach server ({e}); start it with `make serve` or drop --url")
    if r.status_code != 200:
        try:
            detail = r.json().get("detail", r.text)
        except ValueError:
            detail = r.text
        if isinstance(detail, list):  # 422 validation errors: "limit: Input should be ..."
            detail = "; ".join(f"{'.'.join(map(str, e['loc'][1:]))}: {e['msg']}" for e in detail)
        sys.exit(f"error {r.status_code}: {detail}")
    return r.json()


# ------------------------------------------------------------------ formatting

def _cap(circle: dict) -> str:
    cap = circle["max_members"]
    return f"{circle['member_count']}/{INF if cap is None else cap}"


def format_health(h: dict) -> str:
    return f"{h['status']} — {h['users']} users, {h['circles']} circles, as_of {h['as_of']}"


def format_user(u: dict) -> str:
    lines = [f"#{u['id']} {u['name']}"]
    bio = ", ".join(x for x in (u["job_title"], u["industry"], u["location"]) if x)
    if bio:
        lines.append(f"  {bio}")
    lines.append(f"  joined {u['joined_at']}, profile {u['profile_completeness']:.0%} complete")
    lines.append(f"  stated interests:   {', '.join(u['stated_interests']) or '(none)'}")
    lines.append(f"  implicit interests: {', '.join(u['implicit_interests']) or '(none)'}")
    if u["memberships"]:
        lines.append("  memberships:")
        for m in u["memberships"]:
            lines.append(f"    #{m['circle_id']} {m['name']} ({m['topic'] or 'no topic'})")
    else:
        lines.append("  memberships: (none)")
    return "\n".join(lines)


def format_recommendations(payload: dict) -> str:
    where = f", in {payload['location']}" if payload.get("location") else ""
    where += ", excluding coworkers" if payload.get("exclude_coworkers") else ""
    lines = [f"user {payload['user_id']} — strategy: {payload['strategy']}, as_of {payload['as_of']}{where}"]
    for rec in payload["recommendations"]:
        c = rec["circle"]
        lines.append(f"{rec['rank']}. #{c['id']} {c['name']} ({c['topic'] or 'no topic'}, "
                     f"{c['join_policy']}, {_cap(c)}) — {rec['explanation']}")
        if rec.get("score_breakdown"):
            b = rec["score_breakdown"]
            parts = [f"{k}={v:.2f}" for k, v in b.items() if k != "diversity_multiplier"]
            lines.append(f"   score {rec['score']:.4f}  [{' '.join(parts)}"
                         f" ×{b['diversity_multiplier']:.2f}]")
    if payload.get("hints"):
        lines.append(f"hints: {json.dumps(payload['hints'])}")
    if payload.get("excluded"):
        lines.append(f"excluded ({len(payload['excluded'])}):")
        for e in payload["excluded"]:
            lines.append(f"  #{e['circle_id']} {e['name']}: {', '.join(e['reasons'])}")
    if payload.get("next_cursor"):
        lines.append(f"next page: --cursor {payload['next_cursor']}")
    return "\n".join(lines)


# ------------------------------------------------------------------ commands

def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="python -m app.cli",
                                description="Query the Circle recommendations API.")
    p.add_argument("--url", metavar="URL",
                   help="base URL of a running server (default: run in-process against circles.db)")
    p.add_argument("--json", action="store_true", help="print the raw JSON response")
    sub = p.add_subparsers(dest="command", required=True)

    sub.add_parser("health", help="row counts and the anchor date")

    u = sub.add_parser("user", help="a member as the ranker sees her")
    u.add_argument("user_id", type=int)

    r = sub.add_parser("recommend", help="ranked Circles for a member")
    r.add_argument("user_id", type=int)
    r.add_argument("-n", "--limit", type=int, default=5, help="1-20 (default 5)")
    r.add_argument("--debug", action="store_true", help="add score breakdown and excluded Circles")
    r.add_argument("--cursor", metavar="CURSOR", help="next_cursor from the previous page")
    r.add_argument("--location", metavar="CITY|LON,LAT",
                   help='override her profile city for this request, e.g. "Seattle, WA" or -122.33,47.61')
    r.add_argument("--exclude-coworkers", action="store_true",
                   help="filter out every Circle with a coworker in it")
    return p


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    client = _client(args.url)
    if args.command == "health":
        payload, fmt = _get(client, "/health"), format_health
    elif args.command == "user":
        payload, fmt = _get(client, f"/users/{args.user_id}"), format_user
    else:
        params = {"limit": args.limit, "debug": str(args.debug).lower()}
        if args.cursor:
            params["cursor"] = args.cursor
        if args.location is not None:
            params["location"] = args.location
        if args.exclude_coworkers:
            params["exclude_coworkers"] = "true"
        payload, fmt = _get(client, f"/users/{args.user_id}/recommendations", params), format_recommendations
    print(json.dumps(payload, indent=2) if args.json else fmt(payload))
    return 0


if __name__ == "__main__":
    sys.exit(main())
