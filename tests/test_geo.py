"""?location= overrides the profile city; coordinates snap to the nearest known city."""

import pytest

from app import geo
from tests.conftest import recommendations


def test_city_string_is_canonicalised_case_insensitively():
    assert geo.resolve("seattle, wa") == "Seattle, WA"
    assert geo.resolve("  Chicago, IL ") == "Chicago, IL"
    assert geo.resolve("Nowhere, ZZ") == "Nowhere, ZZ", "unknown city passes through and matches nobody"
    assert geo.resolve(None) is None


def test_coordinates_snap_to_nearest_city_within_radius():
    assert geo.resolve("-122.33,47.61") == "Seattle, WA"
    assert geo.resolve("-122.20, 47.98") == "Seattle, WA", "Everett, 40 km out, still Seattle"
    assert geo.resolve("2.35,48.85") is None, "Paris is nobody's city in this data"
    assert geo.resolve("-0.1276,51.5074") == "London, UK"


def test_out_of_range_coordinates_are_rejected():
    with pytest.raises(ValueError):
        geo.resolve("500,0")


def test_location_override_changes_same_city_proof(client):
    default = recommendations(client, 15, debug=True)
    assert default["location"] == "Chicago, IL", "her profile city when no override is given"
    seattle = client.get("/users/15/recommendations", params={"location": "-122.33,47.61", "debug": "true"}).json()
    assert seattle["location"] == "Seattle, WA"
    # the override reaches the ranker: social scores differ for some Circle
    by_id = lambda p: {r["circle"]["id"]: r["score_breakdown"]["social"] for r in p["recommendations"]}
    assert by_id(default) != by_id(seattle)
    paris = client.get("/users/15/recommendations", params={"location": "2.35,48.85"}).json()
    assert paris["location"] is None
    assert client.get("/users/15/recommendations", params={"location": "999,999"}).status_code == 422


def test_location_reason_names_the_override(client):
    # member 4 has no shared-interest proof in #15, so same-city is the reason that surfaces,
    # and it names the override city, not her profile city
    for city, n in [("New York, NY", 4), ("Seattle, WA", 3)]:
        payload = client.get("/users/4/recommendations", params={"location": city, "limit": 20}).json()
        details = [x["detail"] for r in payload["recommendations"] if r["circle"]["id"] == 15
                   for x in r["reasons"] if x["type"] == "same_city"]
        assert details == [f"{n} members are also in {city}"]
