import pytest
from fastapi.testclient import TestClient

from app import config, db, seed
from app.api import app


@pytest.fixture(scope="session", autouse=True)
def seeded_db(tmp_path_factory):
    """Seed a throwaway database once per test session and point the app at it."""
    path = tmp_path_factory.mktemp("db") / "circles.db"
    seed.seed(db_path=path)
    original = config.DB_PATH
    config.DB_PATH = path
    yield path
    config.DB_PATH = original


@pytest.fixture(scope="session")
def client():
    return TestClient(app)


@pytest.fixture(scope="session")
def conn(seeded_db):
    c = db.connect(seeded_db)
    yield c
    c.close()


def recommendations(client, user_id: int, limit: int = 5, debug: bool = False) -> dict:
    r = client.get(f"/users/{user_id}/recommendations", params={"limit": limit, "debug": str(debug).lower()})
    assert r.status_code == 200, r.text
    return r.json()


def ids(payload: dict) -> list[int]:
    return [rec["circle"]["id"] for rec in payload["recommendations"]]
