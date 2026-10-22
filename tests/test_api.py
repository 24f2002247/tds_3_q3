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


def test_openapi_actions_endpoint_has_request_body():
    with TestClient(app) as client:
        spec = client.get("/openapi.json").json()

    post = spec["paths"]["/v1/mailroom/actions"]["post"]
    assert "requestBody" in post, "POST /v1/mailroom/actions must declare a requestBody"

    content = post["requestBody"]["content"]
    assert "application/json" in content, "requestBody must include application/json content"

    schema = content["application/json"]["schema"]
    # Must be a oneOf covering both propose and commit shapes
    assert "oneOf" in schema, "schema must use oneOf to cover propose and commit"
    assert len(schema["oneOf"]) == 2, "oneOf must have exactly two variants"

    # Each variant must reference or inline an 'operation' property
    inline_schemas = schema["oneOf"]
    operations = set()
    for variant in inline_schemas:
        props = variant.get("properties", {})
        assert "operation" in props, f"variant is missing 'operation' property: {variant}"
        enum_vals = props["operation"].get("enum") or props["operation"].get("const")
        if isinstance(enum_vals, list):
            operations.update(enum_vals)
        elif enum_vals is not None:
            operations.add(enum_vals)
    assert "propose" in operations, "propose operation not found in oneOf variants"
    assert "commit" in operations, "commit operation not found in oneOf variants"
