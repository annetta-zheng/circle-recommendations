"""Co-membership: people in her Circles who also belong to a candidate. A tie-breaker only."""

import re
import shutil

import pytest

from app import config, db, features, recommender
from tests.conftest import recommendations

COUNT_ONLY = re.compile(r"^\d+ members of your Circles? are here too$")


def test_fewer_than_two_co_members_count_for_nothing():
    assert features.co_membership([]) == (0.0, {"co_members": 0})
    assert features.co_membership([3]) == (0.0, {"co_members": 0})
    assert features.co_membership([3, 3])[0] > 0


def test_a_member_in_many_circles_counts_less():
    few, _ = features.co_membership([2, 2])
    many, _ = features.co_membership([9, 9])
    assert few > many > 0


def test_the_signal_is_capped_at_one():
    score, meta = features.co_membership([2] * 30)
    assert score == 1.0 and meta["co_members"] == 30


def test_weights_sum_to_one_and_co_membership_stays_minor():
    assert abs(sum(config.WEIGHTS.values()) - 1.0) < 1e-9
    assert 0 < config.WEIGHTS["co_membership"] <= config.WEIGHTS["social"], "below the attribute-based people signal"


def test_she_is_never_her_own_co_member(conn):
    # member 7 belongs only to Circle #4: everyone else in #4 is a co-member, she is not
    others = conn.execute("SELECT COUNT(*) FROM memberships WHERE circle_id = 4 AND user_id != 7").fetchone()[0]
    assert len(db.co_membership(conn, 7, [4])[4]) == others


def test_a_hidden_circle_does_not_leak_back(conn):
    # leave-one-out hides #35 from member 15 by passing only #9: votes for #35
    # may come from #9's members, never from #35's own
    peers = [r[0] for r in conn.execute("SELECT user_id FROM memberships WHERE circle_id = 9 AND user_id != 15")]
    marks = ",".join("?" * len(peers))
    expected = conn.execute(
        f"SELECT COUNT(*) FROM memberships WHERE circle_id = 35 AND user_id IN ({marks})", peers
    ).fetchone()[0]
    assert len(db.co_membership(conn, 15, [9]).get(35, [])) == expected


def test_reasons_give_a_count_never_a_name(client, conn):
    names = [f"{r['first_name']} {r['last_name']}" for r in conn.execute("SELECT first_name, last_name FROM users")]
    shown = 0
    for uid in range(1, 101):
        for rec in recommendations(client, uid)["recommendations"]:
            for reason in rec["reasons"]:
                if reason["type"] == "co_members":
                    shown += 1
                    assert COUNT_ONLY.match(reason["detail"]), reason["detail"]
                    assert int(reason["detail"].split()[0]) >= config.CO_MEMBERSHIP_MIN_MEMBERS
            text = " ".join([rec["explanation"], *(r["detail"] for r in rec["reasons"])])
            assert not any(name in text for name in names), (uid, text)
    assert shown > 0


# ------------------------------------------------------------------ cost bounds

@pytest.fixture
def crowded(seeded_db, tmp_path):
    """A copy of the seed with a hot Circle and a hub member."""
    path = tmp_path / "crowded.db"
    shutil.copy(seeded_db, path)
    conn = db.connect(path)
    first = conn.execute("SELECT MAX(id) FROM users").fetchone()[0] + 1
    circle_ids = [r[0] for r in conn.execute("SELECT id FROM circles ORDER BY id")]
    users, memberships = [], []
    for i in range(500):  # 500 members pile into #4 (member 7's only Circle) and #5
        uid = first + i
        users.append((uid, "Test", f"Member{i}", "2026-09-01T00:00:00.000Z", 0.0))
        memberships += [(uid, 4, "member", "2026-09-01T00:00:00.000Z"), (uid, 5, "member", "2026-09-01T00:00:00.000Z")]
    hub = first + 500  # the newest joiner of #4, and a member of every Circle
    users.append((hub, "Hub", "Member", "2026-09-02T00:00:00.000Z", 0.0))
    memberships += [(hub, cid, "member", "2026-09-02T00:00:00.000Z") for cid in circle_ids]
    conn.executemany("INSERT INTO users (id, first_name, last_name, joined_at, profile_completeness) VALUES (?, ?, ?, ?, ?)", users)
    conn.executemany("INSERT INTO memberships (user_id, circle_id, role, joined_at) VALUES (?, ?, ?, ?)", memberships)
    conn.commit()
    yield conn
    conn.close()


def test_a_hot_circle_seeds_at_most_the_cap(crowded):
    degrees = db.co_membership(crowded, 7, [4])
    assert len(degrees[5]) <= config.CO_MEMBERSHIP_MAX_PEERS_PER_CIRCLE < 500
    assert all(len(d) <= config.CO_MEMBERSHIP_MAX_PEERS_PER_CIRCLE for d in degrees.values())


def test_a_member_in_too_many_circles_is_dropped(crowded):
    n_circles = crowded.execute("SELECT COUNT(*) FROM circles").fetchone()[0]
    uncapped = db.co_membership(crowded, 7, [4], max_peer_degree=10_000)
    assert n_circles in {d for ds in uncapped.values() for d in ds}, "without the cap the hub is counted"
    capped = db.co_membership(crowded, 7, [4])
    assert max(d for ds in capped.values() for d in ds) <= config.CO_MEMBERSHIP_MAX_PEER_DEGREE < n_circles


def test_she_seeds_from_her_most_recent_circles_only():
    memberships = [{"circle_id": i, "joined_at": f"2026-01-{i:02d}"} for i in range(1, 26)]
    sources = features.co_membership_sources(memberships)
    assert sources == list(range(26 - config.CO_MEMBERSHIP_MAX_SOURCE_CIRCLES, 26))


def test_a_crowded_network_still_gets_a_full_list(crowded):
    payload = recommender.recommend(crowded, db.get_member(crowded, 7), limit=5)
    assert len(payload["recommendations"]) == 5
