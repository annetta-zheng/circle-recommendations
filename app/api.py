"""HTTP layer: routes, validation, status codes. Knows nothing about scoring.

    uvicorn app.api:app --reload
"""

from __future__ import annotations

import base64
from typing import Any, Optional

from fastapi import Depends, FastAPI, HTTPException, Query
from pydantic import BaseModel, Field

from app import config, db, geo, recommender

app = FastAPI(
    title="Circle Recommendations",
    version="0.1.0",
    description="Ranked Circles for a member, each with a member-facing reason.",
)


def get_conn():
    if not config.DB_PATH.exists():
        raise HTTPException(status_code=503, detail="database not seeded; run `make seed`")
    with db.session() as conn:
        yield conn


# ------------------------------------------------------------------ response models

class CircleSummary(BaseModel):
    id: int
    name: str
    topic: Optional[str]
    format: str
    join_policy: str
    member_count: int
    max_members: Optional[int]
    seats_left: Optional[int]


class Reason(BaseModel):
    type: str
    detail: str


class Recommendation(BaseModel):
    rank: int
    circle: CircleSummary
    score: float
    reasons: list[Reason]
    explanation: str
    score_breakdown: Optional[dict[str, float]] = None


class Exclusion(BaseModel):
    circle_id: int
    name: str
    reasons: list[str]


class RecommendationResponse(BaseModel):
    user_id: int
    strategy: str = Field(description="stated_interests | implicit_interests | newcomer")
    as_of: str
    location: Optional[str] = Field(None, description="the city the same-city signal used: the request override, else her profile")
    exclude_coworkers: bool = Field(False, description="whether Circles with a coworker in them were filtered out")
    recommendations: list[Recommendation]
    hints: list[str]
    next_cursor: Optional[str] = Field(None, description="pass back as ?cursor= for the next page; null when exhausted")
    excluded: Optional[list[Exclusion]] = None


class MemberView(BaseModel):
    id: int
    name: str
    job_title: Optional[str]
    industry: Optional[str]
    location: Optional[str]
    joined_at: str
    profile_completeness: float
    stated_interests: list[str]
    implicit_interests: list[str]
    memberships: list[dict[str, Any]]


# ------------------------------------------------------------------ cursor
# Opaque to clients, trivial to us: "<user_id>:<offset>" base64url-encoded. Bound to the
# user so a cursor from one member cannot page through another's list.

def encode_cursor(user_id: int, offset: int) -> str:
    return base64.urlsafe_b64encode(f"{user_id}:{offset}".encode()).decode().rstrip("=")


def decode_cursor(cursor: str, user_id: int) -> int:
    try:
        raw = base64.urlsafe_b64decode(cursor + "=" * (-len(cursor) % 4)).decode()
        uid, offset = (int(x) for x in raw.split(":"))
    except (ValueError, UnicodeDecodeError):
        raise HTTPException(status_code=400, detail="invalid cursor")
    if uid != user_id or offset < 0:
        raise HTTPException(status_code=400, detail="cursor does not belong to this user")
    return offset


# ------------------------------------------------------------------ routes

@app.get("/health")
def health(conn=Depends(get_conn)) -> dict:
    return {
        "status": "ok",
        "users": db.count_members(conn),
        "circles": db.count_circles(conn),
        "as_of": db.as_of(conn),
    }


@app.get("/users/{user_id}", response_model=MemberView)
def get_user(user_id: int, conn=Depends(get_conn)):
    member = db.get_member(conn, user_id)
    if member is None:
        raise HTTPException(status_code=404, detail=f"user {user_id} not found")
    return MemberView(
        id=member["id"],
        name=f"{member['first_name']} {member['last_name']}",
        job_title=member["job_title"],
        industry=member["industry"],
        location=member["location"],
        joined_at=member["joined_at"],
        profile_completeness=member["profile_completeness"],
        stated_interests=member["stated_interests"],
        implicit_interests=sorted({m["topic"] for m in member["memberships"] if m["topic"]}),
        memberships=member["memberships"],
    )


@app.get("/users/{user_id}/recommendations", response_model=RecommendationResponse,
         response_model_exclude_unset=True)
def get_recommendations(
    user_id: int,
    limit: int = Query(config.DEFAULT_LIMIT, ge=1, le=config.MAX_LIMIT),
    debug: bool = Query(False),
    cursor: Optional[str] = Query(None, description="next_cursor from the previous page"),
    location: Optional[str] = Query(None, description='override her profile city for this request: "Seattle, WA" or "lon,lat"'),
    exclude_coworkers: bool = Query(False, description="filter out every Circle with a coworker in it; otherwise coworkers are a small tie-breaker"),
    conn=Depends(get_conn),
):
    member = db.get_member(conn, user_id)
    if member is None:
        raise HTTPException(status_code=404, detail=f"user {user_id} not found")
    if location is not None:
        try:
            member = {**member, "location": geo.resolve(location)}
        except ValueError as e:
            raise HTTPException(status_code=422, detail=str(e))
    offset = decode_cursor(cursor, user_id) if cursor else 0
    payload = recommender.recommend(conn, member, limit=limit, debug=debug, offset=offset,
                                    exclude_coworkers=exclude_coworkers)
    nxt = payload.pop("next_offset")
    payload["next_cursor"] = encode_cursor(user_id, nxt) if nxt is not None else None
    return payload
