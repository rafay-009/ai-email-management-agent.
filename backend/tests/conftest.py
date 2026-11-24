import os
import tempfile

import pytest
from alembic import command
from alembic.config import Config
from fastapi.testclient import TestClient

# Set configuration before importing the application.
database_folder = tempfile.TemporaryDirectory()
os.environ["DATABASE_URL"] = f"sqlite:///{database_folder.name}/test.db"
os.environ["LLM_PROVIDER"] = "mock"
os.environ["APP_ENV"] = "test"
os.environ["DEMO_MODE"] = "true"
for name in ("GOOGLE_CLIENT_ID", "GOOGLE_CLIENT_SECRET", "TOKEN_ENCRYPTION_KEY"):
    os.environ[name] = ""

from app.config import APP
from app.main import app


@pytest.fixture(scope="session", autouse=True)
def migrated():
    command.upgrade(Config("alembic.ini"), "head")


@pytest.fixture
def client():
    with TestClient(app, base_url=APP, headers={"Origin": APP}) as browser:
        yield browser


def demo(client):
    assert client.post("/api/v1/auth/demo").status_code == 201
    client.headers["X-CSRF-Token"] = client.get("/api/v1/me").json()["csrf"]
    return client
