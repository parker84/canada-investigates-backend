import os
import tempfile
os.environ["DATABASE_URL"] = f"sqlite:///{tempfile.mkdtemp()}/test.db"
os.environ["SEED_DEMO"] = "true"
from fastapi.testclient import TestClient
from app.main import app

def test_search_and_filters():
    with TestClient(app) as client:
        assert client.get('/health').json() == {"status": "ok"}
        assert len(client.get('/api/v1/cases').json()['items']) == 4
        assert len(client.get('/api/v1/cases?q=museum&province=Ontario').json()['items']) == 1
        assert client.get('/api/v1/cases?q=%25').json()['items'] == []
        assert client.get('/api/v1/cases?status=resolved').json()['items'][0]['slug'] == 'tracks-beyond-the-town'
        assert client.get('/api/v1/cases/missing').status_code == 404

def test_submissions_are_private(monkeypatch):
    payload = {"title": "A new archival question", "province": "Ontario", "location": "Ottawa", "category": "History", "summary": "A question about an archival record that needs a documented source and editorial review.", "source_url": "https://example.org/record"}
    with TestClient(app) as client:
        assert client.post('/api/v1/cases', json=payload).status_code == 503
        monkeypatch.setenv('ENABLE_SUBMISSIONS', 'true')
        response = client.post('/api/v1/cases', json=payload)
        assert response.status_code == 201
        identifier = response.json()['id']
        assert client.get(f'/api/v1/cases/submission-{identifier}').status_code == 404
        assert client.get('/api/v1/cases?q=archival').json()['items'] == []
        assert client.post('/api/v1/cases', json=payload | {"source_url": "javascript:alert(1)"}).status_code == 422
