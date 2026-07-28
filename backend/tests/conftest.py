import os
from collections.abc import Generator
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

Path("data").mkdir(exist_ok=True)
os.environ.setdefault("DATABASE_URL", "sqlite:///./data/test_meet_tina.db")
os.environ.setdefault("COOKIE_SECURE", "false")
os.environ.setdefault("OPENWA_WEBHOOK_SECRET", "test-openwa")
os.environ.setdefault("N8N_CALLBACK_SECRET", "test-callback")
os.environ.setdefault("JWT_SECRET_KEY", "test-secret")

from app.core.database import Base, engine  # noqa: E402
from app.main import app  # noqa: E402


@pytest.fixture(autouse=True)
def reset_database() -> Generator[None, None, None]:
    Base.metadata.drop_all(bind=engine)
    Base.metadata.create_all(bind=engine)
    yield
    Base.metadata.drop_all(bind=engine)


@pytest.fixture
def client() -> TestClient:
    return TestClient(app)
