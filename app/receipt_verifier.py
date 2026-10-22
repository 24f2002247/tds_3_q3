import base64
import json
from typing import Any
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey
from cryptography.hazmat.primitives.serialization import Encoding, PublicFormat
from cryptography.exceptions import InvalidSignature
from .canonical import canonical_bytes


def _b64url(value: str) -> bytes:
    return base64.urlsafe_b64decode(value + "=" * (-len(value) % 4))


def validate_verifier(verifier: dict[str, Any]) -> Ed25519PublicKey:
    jwk = verifier.get("publicKeyJwk", {})
    if verifier.get("algorithm") != "Ed25519" or jwk.get("kty") != "OKP" or jwk.get("crv") != "Ed25519":
        raise ValueError("invalid Ed25519 verifier")
    key = Ed25519PublicKey.from_public_bytes(_b64url(jwk["x"]))
    if len(key.public_bytes(Encoding.Raw, PublicFormat.Raw)) != 32:
        raise ValueError("invalid public key")
    return key


def verify_receipt(verifier: dict[str, Any], evaluation_id: str, input_digest: str, receipt: dict[str, Any]) -> bool:
    try:
        key = validate_verifier(verifier)
        signature = base64.b64decode(receipt["receiptSignature"], validate=True)
        inner = {k: receipt[k] for k in (
            "dossierId", "callId", "action", "accepted", "proposalDigest", "receiptId"
        )}
        message = {"profile": "ga5-mailroom-action-gate/v2", "evaluationId": evaluation_id,
                   "inputDigest": input_digest, "receipt": inner}
        key.verify(signature, canonical_bytes(message))
        return True
    except (KeyError, ValueError, TypeError, InvalidSignature):
        return False
