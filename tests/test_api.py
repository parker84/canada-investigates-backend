import os
import tempfile
os.environ["DATABASE_URL"] = f"sqlite:///{tempfile.mkdtemp()}/test.db"
os.environ["SEED_CASES"] = "true"
from fastapi.testclient import TestClient
from app.main import app

def sign_in(client, monkeypatch, email="reader@example.ca", name="Reader"):
    monkeypatch.setenv("AUTH_DEV_MODE", "true")
    response = client.post('/api/v1/auth/request-link', json={"email": email})
    assert response.status_code == 200
    token = response.json()['dev_link'].split('token=')[1]
    verified = client.post('/api/v1/auth/verify', json={"token": token, "display_name": name})
    assert verified.status_code == 200
    return verified.json(), token

def test_search_and_filters():
    with TestClient(app) as client:
        assert client.get('/health').json() == {"status": "ok"}
        assert len(client.get('/api/v1/cases').json()['items']) == 3
        assert len(client.get('/api/v1/cases?q=Mulvey&province=Manitoba').json()['items']) == 1
        assert client.get('/api/v1/cases?q=%25').json()['items'] == []
        assert client.get('/api/v1/cases?status=resolved').json()['items'] == []
        assert len(client.get('/api/v1/cases?status=active').json()['items']) == 3
        assert len(client.get('/api/v1/cases?status=witness-appeal').json()['items']) == 1
        for case in client.get('/api/v1/cases').json()['items']:
            assert case['demo'] is False
            assert case['sources'] and case['status_as_of'] and case['tip']['url'].startswith('https://')
        assert client.get('/api/v1/cases/missing').status_code == 404

def test_submissions_are_private(monkeypatch):
    payload = {"title": "A new archival question", "province": "Ontario", "location": "Ottawa", "category": "History", "summary": "A question about an archival record that needs a documented source and editorial review.", "source_url": "https://example.org/record"}
    with TestClient(app) as client:
        assert client.post('/api/v1/cases', json=payload).status_code == 503
        monkeypatch.setenv('ENABLE_SUBMISSIONS', 'true')
        assert client.post('/api/v1/cases', json=payload).status_code == 401
        sign_in(client, monkeypatch)
        response = client.post('/api/v1/cases', json=payload)
        assert response.status_code == 201
        identifier = response.json()['id']
        assert client.get(f'/api/v1/cases/submission-{identifier}').status_code == 404
        assert client.get('/api/v1/cases?q=archival').json()['items'] == []
        assert client.post('/api/v1/cases', json=payload | {"source_url": "javascript:alert(1)"}).status_code == 422

def test_sign_in_and_moderated_discussion(monkeypatch):
    monkeypatch.setenv("EDITOR_EMAILS", "editor@example.ca")
    slug = "terris-pokemon-break-in"
    with TestClient(app) as reader:
        assert reader.get('/api/v1/auth/me').status_code == 401
        account, token = sign_in(reader, monkeypatch, "contributor@example.ca", "Contributor")
        assert account['role'] == 'contributor'
        assert reader.post('/api/v1/auth/verify', json={"token": token}).status_code == 400
        assert reader.get('/api/v1/auth/me').json()['display_name'] == 'Contributor'
        assert reader.post(f'/api/v1/cases/{slug}/discussion', json={"kind": "source", "body": "This is a potentially useful public source about the case."}).status_code == 422
        posted = reader.post(f'/api/v1/cases/{slug}/discussion', json={"kind": "source", "body": "This is a potentially useful public source about the case.", "source_url": "https://example.ca/report"})
        assert posted.status_code == 201
        identifier = posted.json()['id']
        assert reader.get(f'/api/v1/cases/{slug}/discussion').json()['items'] == []
        assert reader.get('/api/v1/discussion/mine').json()['items'][0]['status'] == 'pending'
        assert reader.get('/api/v1/editor/queue').status_code == 403
        assert reader.post(f'/api/v1/editor/contributions/{identifier}/review', json={"decision": "approved", "reason": "Source checked."}).status_code == 403
        assert reader.patch(f'/api/v1/discussion/{identifier}', json={"body": "This public source offers a more precise account of the report.", "source_url": "https://example.ca/report"}).status_code == 200
        assert reader.post(f'/api/v1/cases/{slug}/discussion', json={"kind": "question", "body": "Could someone clarify what this timeline entry means?"}, headers={"Origin": "https://evil.example"}).status_code == 403
        with TestClient(app) as editor:
            editor_account, _ = sign_in(editor, monkeypatch, "editor@example.ca", "Editor")
            assert editor_account['role'] == 'editor'
            assert len(editor.get('/api/v1/editor/queue').json()['items']) == 1
            approved = editor.post(f'/api/v1/editor/contributions/{identifier}/review', json={"decision": "approved", "reason": "Source checked against public report."})
            assert approved.status_code == 200
            assert reader.get(f'/api/v1/cases/{slug}/discussion').json()['items'][0]['id'] == identifier
            assert reader.patch(f'/api/v1/discussion/{identifier}', json={"body": "Trying to edit an approved item should not work."}).status_code == 409
            assert reader.post(f'/api/v1/discussion/{identifier}/report', json={"reason": "Please check whether this source is current."}).status_code == 201
            assert reader.post(f'/api/v1/discussion/{identifier}/report', json={"reason": "Please check whether this source is current."}).status_code == 409
            report = editor.get('/api/v1/editor/reports').json()['items'][0]
            assert report['contribution']['id'] == identifier
            assert editor.post(f'/api/v1/editor/contributions/{identifier}/review', json={"decision": "removed", "reason": "Public source no longer supports this claim."}).status_code == 200
            assert reader.get(f'/api/v1/cases/{slug}/discussion').json()['items'] == []
            assert editor.post(f"/api/v1/editor/reports/{report['id']}/resolve").status_code == 200
        assert reader.post('/api/v1/auth/logout').status_code == 204
        assert reader.get('/api/v1/auth/me').status_code == 401


def test_seed_retires_only_fictional_rows_and_preserves_edits():
    import json
    from sqlalchemy import select
    from app.main import Session, Case, seed
    with TestClient(app) as client:
        with Session() as db:
            db.add(Case(id="old-demo", slug="the-missing-museum-map", title="Old demo", province="Ontario", location="Ottawa", category="History", summary="Fictional", status="reported", created_at="2026-01-01", detail=json.dumps({"demo": True})))
            current = db.scalar(select(Case).where(Case.slug == "terris-pokemon-break-in"))
            current.title = "Editorial correction"
            db.commit()
        seed()
        seed()
        assert client.get('/api/v1/cases/the-missing-museum-map').status_code == 404
        assert client.get('/api/v1/cases?status=archived').json()['items'] == []
        assert len(client.get('/api/v1/cases').json()['items']) == 3
        assert client.get('/api/v1/cases/terris-pokemon-break-in').json()['title'] == "Editorial correction"
