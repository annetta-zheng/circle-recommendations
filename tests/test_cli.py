"""The CLI is the API without a socket: same routes, same JSON, plus a readable rendering."""

import json

import pytest

from app import cli


def run(capsys, *argv) -> str:
    assert cli.main(list(argv)) == 0
    return capsys.readouterr().out


def test_health(capsys):
    out = run(capsys, "health")
    assert out.startswith("ok ") and "100 users, 40 circles" in out


def test_user(capsys):
    out = run(capsys, "user", "15")
    assert out.startswith("#15 ") and "stated interests:   confidence, negotiation" in out


def test_recommend_renders_every_rank(capsys):
    out = run(capsys, "recommend", "42", "--limit", "3")
    lines = out.splitlines()
    assert lines[0].endswith("strategy: newcomer, as_of 2026-09-21")
    assert [l[:2] for l in lines[1:4]] == ["1.", "2.", "3."]
    assert lines[-2].startswith("hints: ")
    assert lines[-1].startswith("next page: --cursor ")


def test_recommend_cursor_continues_the_ranks(capsys):
    first = run(capsys, "recommend", "15", "-n", "3")
    cursor = first.splitlines()[-1].split()[-1]
    second = run(capsys, "recommend", "15", "-n", "3", "--cursor", cursor)
    assert [l[:2] for l in second.splitlines()[1:4]] == ["4.", "5.", "6."]


def test_recommend_debug_shows_breakdown_and_exclusions(capsys):
    out = run(capsys, "recommend", "23", "--debug", "-n", "1")
    assert "   score 0." in out and "topic=1.00" in out
    assert "#20 The Promotion Path: full" in out
    assert "#21 Career Climbers: unlisted, dormant" in out


def test_recommend_location_flag_reaches_the_api(capsys):
    out = run(capsys, "recommend", "15", "-n", "1", "--location=-122.33,47.61")  # "=" form: argparse on Python 3.10 reads "-122.33,..." as a flag
    assert out.splitlines()[0].endswith(", in Seattle, WA")


def test_json_matches_api(capsys, client):
    out = run(capsys, "--json", "recommend", "15", "--limit", "2")
    assert json.loads(out) == client.get("/users/15/recommendations", params={"limit": 2}).json()


@pytest.mark.parametrize("argv, message", [
    (["recommend", "999"], "error 404: user 999 not found"),
    (["recommend", "1", "-n", "50"], "error 422: limit: Input should be less than or equal to 20"),
    (["recommend", "1", "--cursor", "garbage"], "error 400: invalid cursor"),
])
def test_errors_exit_nonzero(capsys, argv, message):
    with pytest.raises(SystemExit) as e:
        cli.main(argv)
    assert e.value.code == message
