from __future__ import annotations

import pytest

from app.database import Database


@pytest.fixture
def db(tmp_path):
    database = Database(tmp_path / "test.db")
    database.init_db()
    return database
