import pytest
from fastapi.testclient import TestClient
from figtrace.app import create_app

HEADERS = {"X-Figtrace-Request": "1"}


@pytest.fixture
def workspace(tmp_path):
    data = tmp_path / "data"
    sources = tmp_path / "sources"
    sources.mkdir()
    app = create_app(data, start_worker=False)
    with TestClient(app, headers=HEADERS) as client:
        yield client, app.state.library, sources
