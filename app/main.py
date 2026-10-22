import json
from contextlib import asynccontextmanager
from typing import Any

from fastapi import FastAPI, Request
from fastapi.openapi.utils import get_openapi
from fastapi.responses import JSONResponse
from pydantic import ValidationError

from .config import get_settings
from .database import init_db
from .decision_engine import DecisionEngine
from .schemas import CommitRequest, ProposeRequest
from .service import ConflictError, MailroomService, NotFoundError

# ---------------------------------------------------------------------------
# OpenAPI schema helpers
# ---------------------------------------------------------------------------

_REF_TEMPLATE = "#/components/schemas/{model}"


def _build_openapi_schema(app: "FastAPI") -> dict[str, Any]:
    """
    Custom OpenAPI generator that adds a valid requestBody for the actions
    endpoint with correctly-resolved $ref paths.

    Strategy
    --------
    Pydantic's model_json_schema() places sub-models under a top-level
    "$defs" key and emits $ref values like "#/$defs/Foo".  When those schemas
    are embedded inside openapi_extra, the refs resolve relative to the
    embedding point — not the document root — so Swagger UI cannot find them.

    The fix: generate both schemas with ref_template="#/components/schemas/{model}"
    so every $ref already points to the OpenAPI components section, then hoist
    the collected $defs into components/schemas where they belong.  The
    requestBody oneOf entries contain no inline $defs at all; resolution
    happens at document root level as the OpenAPI 3.x spec requires.
    """
    # Generate base spec without any requestBody on the actions route
    # (the handler takes a raw Request so FastAPI produces nothing by default).
    openapi_schema = get_openapi(
        title=app.title,
        version=app.version,
        description=app.description,
        routes=app.routes,
    )

    # --- collect sub-model definitions from both request types ---------------
    propose_schema = ProposeRequest.model_json_schema(ref_template=_REF_TEMPLATE)
    commit_schema = CommitRequest.model_json_schema(ref_template=_REF_TEMPLATE)

    # Hoist $defs into components/schemas (create section if absent)
    components = openapi_schema.setdefault("components", {})
    schemas = components.setdefault("schemas", {})
    schemas.update(propose_schema.pop("$defs", {}))
    schemas.update(commit_schema.pop("$defs", {}))

    # Register the two top-level request models themselves in components/schemas
    # so the oneOf $refs resolve correctly.
    schemas["ProposeRequest"] = propose_schema
    schemas["CommitRequest"] = commit_schema

    # --- inject requestBody into the actions path ----------------------------
    actions_post = (
        openapi_schema
        .get("paths", {})
        .get("/v1/mailroom/actions", {})
        .get("post")
    )
    if actions_post is not None:
        actions_post["requestBody"] = {
            "required": True,
            "content": {
                "application/json": {
                    "schema": {
                        "oneOf": [
                            {"$ref": "#/components/schemas/ProposeRequest"},
                            {"$ref": "#/components/schemas/CommitRequest"},
                        ],
                        "discriminator": {"propertyName": "operation"},
                    }
                }
            },
        }

    return openapi_schema


# ---------------------------------------------------------------------------
# Application
# ---------------------------------------------------------------------------

@asynccontextmanager
async def lifespan(application: FastAPI):
    init_db()
    yield


app = FastAPI(title="Safe AI Mailroom", lifespan=lifespan)

settings = get_settings()
service = MailroomService(DecisionEngine(settings.ai_provider))

# Cache the custom schema (FastAPI's default caching pattern)
_openapi_cache: dict[str, Any] | None = None


def _openapi() -> dict[str, Any]:
    global _openapi_cache
    if _openapi_cache is None:
        _openapi_cache = _build_openapi_schema(app)
    return _openapi_cache


app.openapi = _openapi  # type: ignore[method-assign]


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------

@app.get("/health")
def health():
    return {"status": "ok"}


@app.post("/v1/mailroom/actions")
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
