import json
from contextlib import asynccontextmanager
from typing import Union

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from pydantic import ValidationError

from .config import get_settings
from .database import init_db
from .decision_engine import DecisionEngine
from .schemas import CommitRequest, ProposeRequest
from .service import ConflictError, MailroomService, NotFoundError


def _actions_openapi_extra() -> dict:
    """Build the openapi_extra requestBody fragment for the actions endpoint.

    We inline both schemas under a oneOf discriminated on the 'operation' field
    so that Swagger UI shows a request body editor with both variants.
    """
    propose_schema = ProposeRequest.model_json_schema()
    commit_schema = CommitRequest.model_json_schema()
    # Collect $defs from both schemas and merge them; the oneOf entries are
    # inlined so any shared sub-schemas live in a single top-level $defs map.
    defs: dict = {}
    defs.update(propose_schema.pop("$defs", {}))
    defs.update(commit_schema.pop("$defs", {}))
    combined: dict = {
        "oneOf": [propose_schema, commit_schema],
        "discriminator": {"propertyName": "operation"},
    }
    if defs:
        combined["$defs"] = defs
    return {
        "requestBody": {
            "required": True,
            "content": {
                "application/json": {"schema": combined}
            },
        }
    }


@asynccontextmanager
async def lifespan(application: FastAPI):
    init_db()
    yield


app = FastAPI(title="Safe AI Mailroom", lifespan=lifespan)

settings = get_settings()
service = MailroomService(DecisionEngine(settings.ai_provider))


@app.get("/health")
def health():
    return {"status": "ok"}


@app.post("/v1/mailroom/actions", openapi_extra=_actions_openapi_extra())
async def actions(request: Request):
    try:
        body = await request.body()
        if len(body) > settings.max_body_bytes:
            return JSONResponse({"error": "request too large"}, status_code=413)
        raw = json.loads(body)
        operation = raw.get("operation")
        if operation == "propose":
            parsed = ProposeRequest.model_validate(raw)
            result = service.propose(parsed)
        elif operation == "commit":
            parsed = CommitRequest.model_validate(raw)
            result = service.commit(parsed)
        else:
            return JSONResponse({"error": "invalid operation"}, status_code=400)
        encoded = json.dumps(result, separators=(",", ":")).encode()
        if len(encoded) > 512 * 1024:
            return JSONResponse({"error": "response too large"}, status_code=413)
        return JSONResponse(result)
    except (json.JSONDecodeError, ValidationError, ValueError) as exc:
        return JSONResponse({"error": str(exc)}, status_code=422)
    except ConflictError as exc:
        return JSONResponse({"error": str(exc)}, status_code=409)
    except NotFoundError as exc:
        return JSONResponse({"error": str(exc)}, status_code=404)
