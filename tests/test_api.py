import base64
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from cryptography.hazmat.primitives.serialization import Encoding, PublicFormat
from fastapi.testclient import TestClient
from app.main import app


def request_body(evaluation_id="api-test"):
    private = Ed25519PrivateKey.generate()
    public = private.public_key().public_bytes(Encoding.Raw, PublicFormat.Raw)
    dossier = {
        "dossierId": f"d-{evaluation_id}", "partition": "fresh_audit",
        "receivedAt": "2026-10-22T10:00:00Z", "mailbox": "support@example.com",
        "objective": "Help with an order",
        "sources": [{"sourceId": "s1", "kind": "email", "provenance": "customer",
                     "title": "Mail", "lines": [{"lineId": "l1", "text": "Please help with order ABC"}]}],
    }
    body = {
        "profile": "ga5-mailroom-action-gate/v2", "operation": "propose",
        "evaluationId": evaluation_id,
        "receiptVerifier": {"algorithm": "Ed25519", "publicKeyJwk": {
            "kty": "OKP", "crv": "Ed25519",
            "x": base64.urlsafe_b64encode(public).rstrip(b"=").decode()}},
        "corpus": {"coreId": "c", "auditId": "a", "stableCount": 58, "freshCount": 6},
        "allowedActions": ["create_draft", "update_internal_record", "send_approved_notice",
                           "request_confirmation", "quarantine_item", "no_action"],
        "dossiers": [dossier],
    }
    return body


def test_propose_and_exact_replay():
    body = request_body()
    with TestClient(app) as client:
        first = client.post("/v1/mailroom/actions", json=body)
        second = client.post("/v1/mailroom/actions", json=body)
    assert first.status_code == second.status_code == 200
    assert first.json() == second.json()
    assert first.json()["status"] == "awaiting_receipts"


def test_unknown_operation_and_malformed_json():
    with TestClient(app) as client:
        assert client.post("/v1/mailroom/actions", json={"operation": "unknown"}).status_code == 400
        assert client.post("/v1/mailroom/actions", content=b"{not-json",
                          headers={"content-type": "application/json"}).status_code == 422
