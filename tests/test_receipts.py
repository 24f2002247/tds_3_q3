import base64
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from cryptography.hazmat.primitives.serialization import Encoding, PublicFormat
from app.canonical import canonical_bytes
from app.receipt_verifier import verify_receipt


def test_ed25519_receipt_signature():
    private = Ed25519PrivateKey.generate()
    public = private.public_key().public_bytes(Encoding.Raw, PublicFormat.Raw)
    verifier = {"algorithm": "Ed25519", "publicKeyJwk": {"kty": "OKP", "crv": "Ed25519",
                                                         "x": base64.urlsafe_b64encode(public).rstrip(b"=").decode()}}
    receipt = {"dossierId": "d", "callId": "call-1234567890", "action": "no_action",
               "accepted": True, "proposalDigest": "a" * 64, "receiptId": "r"}
    message = {"profile": "ga5-mailroom-action-gate/v2", "evaluationId": "e", "inputDigest": "b" * 64,
               "receipt": receipt}
    signed = {**receipt, "receiptSignature": base64.b64encode(private.sign(canonical_bytes(message))).decode()}
    assert verify_receipt(verifier, "e", "b" * 64, signed)
