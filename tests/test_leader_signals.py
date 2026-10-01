"""Leader signals: a Circle she hosts says more than one she joined; an active host keeps a Circle alive."""

import re

import pytest

from app import config, db, features
from tests.conftest import recommendations


@pytest.fixture
def leader_on(monkeypatch):
    """The values tried before they were switched off."""
    monkeypatch.setattr(config, "TOPIC_LED_BONUS", 0.2)
    monkeypatch.setattr(config, "ACTIVITY_HOST_BONUS", 0.02)


def test_leader_signals_ship_off(client):
    assert config.TOPIC_LED_BONUS == 0 and config.ACTIVITY_HOST_BONUS == 0
    for uid in range(1, 101):
        types = {x["type"] for r in recommendations(client, uid)["recommendations"] for x in r["reasons"]}
        assert not types & {"led_interest", "host_active"}, uid

HOST = re.compile(r"^the host posted (\d+) times? this month$")


def test_hosting_outweighs_joining_but_not_stating(leader_on):
    circle = {"topic": "negotiation", "tags": []}
    joined, _ = features.topic(circle, set(), {"negotiation"})
    hosted, kind = features.topic(circle, set(), {"negotiation"}, led={"negotiation"})
    assert kind == "led" and hosted == joined + config.TOPIC_LED_BONUS
    beside_stated, _ = features.topic(circle, {"confidence"}, {"negotiation"}, led={"negotiation"})
    assert beside_stated < config.TOPIC_STATED, "stated interests still lead"


def test_host_posts_count_only_the_leader(conn):
    for c in db.all_circles_with_stats(conn):
        expected = conn.execute(
            """SELECT COUNT(*) FROM activity a JOIN memberships l
                 ON l.circle_id = a.circle_id AND l.user_id = a.author_user_id AND l.role = 'leader'
               WHERE a.circle_id = ?
                 AND julianday((SELECT value FROM settings WHERE key = 'as_of')) - julianday(a.created_at) BETWEEN 0 AND 30""",
            (c["id"],)).fetchone()[0]
        assert c["leader_posts_30d"] == expected, c["id"]


def test_reasons_name_the_circle_she_leads_and_count_the_hosts_posts(client, conn, leader_on):
    circles = {c["id"]: c for c in db.all_circles_with_stats(conn)}
    led_shown = host_shown = 0
    for uid in range(1, 101):
        member = db.get_member(conn, uid)
        led_names = {m["name"] for m in member["memberships"] if m["role"] == "leader"}
        for r in recommendations(client, uid)["recommendations"]:
            for reason in r["reasons"]:
                if reason["type"] == "led_interest":
                    led_shown += 1
                    assert any(f"You lead {name}," in reason["detail"] for name in led_names), reason["detail"]
                if reason["type"] == "host_active":
                    host_shown += 1
                    m = HOST.match(reason["detail"])
                    assert m and int(m.group(1)) == circles[r["circle"]["id"]]["leader_posts_30d"] > 0
    assert led_shown > 0 and host_shown > 0
