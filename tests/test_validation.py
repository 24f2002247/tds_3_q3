import pytest
from pydantic import ValidationError
from app.schemas import ProposeRequest


def test_duplicate_dossier_ids_rejected():
    base = {"profile": "ga5-mailroom-action-gate/v2", "operation": "propose", "evaluationId": "e",
            "receiptVerifier": {"algorithm": "Ed25519", "publicKeyJwk": {"kty": "OKP", "crv": "Ed25519", "x": "AA"}},
            "corpus": {"coreId": "c", "auditId": "a", "stableCount": 58, "freshCount": 6},
            "allowedActions": ["create_draft", "update_internal_record", "send_approved_notice",
                               "request_confirmation", "quarantine_item", "no_action"],
            "dossiers": []}
    with pytest.raises(ValidationError):
        ProposeRequest.model_validate(base)
