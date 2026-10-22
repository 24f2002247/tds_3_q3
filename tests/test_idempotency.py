from .test_api import request_body
from fastapi.testclient import TestClient
from app.main import app


def test_evaluation_content_conflict():
    body = request_body("conflict-test")
    with TestClient(app) as client:
        assert client.post("/v1/mailroom/actions", json=body).status_code == 200
        changed = request_body("conflict-test")
        changed["dossiers"][0]["objective"] = "changed"
        assert client.post("/v1/mailroom/actions", json=changed).status_code == 409


def test_unknown_commit_is_rejected():
    body = request_body("unknown-commit")
    body.update({"operation": "commit", "inputDigest": "0" * 64, "receipts": []})
    with TestClient(app) as client:
        assert client.post("/v1/mailroom/actions", json=body).status_code in (404, 422)
