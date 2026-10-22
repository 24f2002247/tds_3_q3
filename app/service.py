import json
import uuid
from typing import Any
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from .canonical import proposal_digest, sha256_hex
from .database import SessionLocal
from .decision_engine import DecisionEngine
from .models import DossierRecord, Evaluation, ReceiptRecord
from .receipt_verifier import validate_verifier, verify_receipt
from .schemas import CommitRequest, Dossier, ProposeRequest


class MailroomService:
    def __init__(self, engine: DecisionEngine | None = None):
        self.engine = engine or DecisionEngine()

    def propose(self, request: ProposeRequest) -> dict[str, Any]:
        raw = request.model_dump(mode="json")
        validate_verifier(raw["receiptVerifier"])
        digest = sha256_hex(raw["dossiers"])
        with SessionLocal() as db:
            existing = db.scalar(select(Evaluation).where(Evaluation.evaluation_id == request.evaluationId))
            if existing:
                if existing.input_digest != digest:
                    raise ConflictError("evaluationId reused with changed content")
                return json.loads(existing.response_json)
            proposals = []
            allowed = set(request.allowedActions)
            for dossier in request.dossiers:
                d = dossier.model_dump(mode="json")
                fingerprint = sha256_hex(d)
                record = db.scalar(select(DossierRecord).where(DossierRecord.fingerprint == fingerprint))
                if record:
                    proposal = json.loads(record.proposal_json)
                    if proposal["dossierId"] != d["dossierId"]:
                        proposal = {**proposal, "dossierId": d["dossierId"]}
                else:
                    call_id = "call-" + uuid.uuid5(uuid.NAMESPACE_URL, fingerprint).hex
                    proposal = self.engine.decide(d, allowed, call_id)
                    db.add(DossierRecord(dossier_id=d["dossierId"], fingerprint=fingerprint,
                                         proposal_json=json.dumps(proposal, separators=(",", ":"))))
                proposals.append(proposal)
            response = {"profile": request.profile, "evaluationId": request.evaluationId,
                        "status": "awaiting_receipts", "inputDigest": digest, "proposals": proposals}
            db.add(Evaluation(evaluation_id=request.evaluationId, input_digest=digest,
                              request_json=json.dumps(raw, separators=(",", ":")),
                              response_json=json.dumps(response, separators=(",", ":")),
                              verifier_json=json.dumps(raw["receiptVerifier"], separators=(",", ":"))))
            try:
                db.commit()
            except IntegrityError:
                db.rollback()
                existing = db.scalar(select(Evaluation).where(Evaluation.evaluation_id == request.evaluationId))
                if existing and existing.input_digest == digest:
                    return json.loads(existing.response_json)
                raise ConflictError("concurrent evaluation conflict")
            return response

    def commit(self, request: CommitRequest) -> dict[str, Any]:
        with SessionLocal() as db:
            evaluation = db.scalar(select(Evaluation).where(Evaluation.evaluation_id == request.evaluationId))
            if not evaluation:
                raise NotFoundError("unknown evaluation")
            if evaluation.input_digest != request.inputDigest:
                raise ConflictError("inputDigest mismatch")
            if evaluation.committed_json:
                return json.loads(evaluation.committed_json)
            proposed = {p["dossierId"]: p for p in json.loads(evaluation.response_json)["proposals"]}
            if set(proposed) != {r.dossierId for r in request.receipts}:
                raise ValueError("receipt set does not match proposals")
            outcomes = []
            for receipt in request.receipts:
                p = proposed[receipt.dossierId]
                r = receipt.model_dump()
                if not verify_receipt(json.loads(evaluation.verifier_json), request.evaluationId,
                                      request.inputDigest, r):
                    raise ValueError("invalid receipt signature")
                if (receipt.callId != p["callId"] or receipt.action != p["action"] or
                        receipt.proposalDigest != proposal_digest(p)):
                    raise ValueError("receipt does not match proposal")
                status = "executed" if receipt.accepted else "rejected"
                db.add(ReceiptRecord(evaluation_id=request.evaluationId, dossier_id=receipt.dossierId,
                                     call_id=receipt.callId, receipt_id=receipt.receiptId,
                                     accepted=receipt.accepted, outcome=status))
                outcomes.append({"dossierId": receipt.dossierId, "callId": receipt.callId,
                                 "action": receipt.action, "proposalDigest": receipt.proposalDigest,
                                 "receiptId": receipt.receiptId, "status": status})
            response = {"profile": request.profile, "evaluationId": request.evaluationId,
                        "status": "completed", "inputDigest": request.inputDigest, "outcomes": outcomes}
            evaluation.committed_json = json.dumps(response, separators=(",", ":"))
            db.commit()
            return response


class ConflictError(Exception):
    pass


class NotFoundError(Exception):
    pass
