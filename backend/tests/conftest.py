import pytest

from app import main
from app.store import Store


@pytest.fixture(autouse=True)
def temp_store(tmp_path, monkeypatch):
    """Every test gets an empty database instead of the real data/reex.sqlite3."""
    store = Store(tmp_path / "reex.sqlite3")
    monkeypatch.setattr(main, "store", store)
    return store
