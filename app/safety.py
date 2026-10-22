import re
from typing import Any
from .schemas import Action

CALL_RE = re.compile(r"^[A-Za-z0-9._:-]{12,128}$")


def validate_proposal(proposal: dict[str, Any], dossier: dict[str, Any], allowed: set[str]) -> None:
    action = proposal.get("action")
    if action not in allowed or action not in Action.__args__:
        raise ValueError("action is not allowed")
    line_ids = {line["lineId"] for source in dossier["sources"] for line in source["lines"]}
    evidence = proposal.get("evidence")
    if not isinstance(evidence, list) or not evidence or len(evidence) != len(set(evidence)) or not set(evidence) <= line_ids:
        raise ValueError("invalid evidence")
    if not CALL_RE.fullmatch(proposal.get("callId", "")):
        raise ValueError("invalid callId")
    shapes = {
        "create_draft": ("draft_queue", {"recipient", "referenceId", "status", "template"}),
        "update_internal_record": ("case_record", {"field", "sourceEventId", "value"}),
        "send_approved_notice": ("email", {"referenceId", "status", "template"}),
        "request_confirmation": ("approval_queue", {"claimedSender", "questionCode", "referenceId"}),
        "quarantine_item": ("security_queue", {"artifactId", "reasonCode"}),
        "no_action": (None, {"reasonCode", "referenceId"}),
    }
    target_shape, payload_keys = shapes[action]
    if target_shape is None:
        if proposal.get("target") is not None:
            raise ValueError("no_action target must be null")
    elif set(proposal.get("target", {})) != {"kind", "id"} or proposal.get("target", {}).get("kind") != target_shape:
        raise ValueError("invalid target kind")
    if set(proposal.get("payload", {})) != payload_keys:
        raise ValueError("invalid payload fields")
