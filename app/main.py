import json
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from pydantic import ValidationError
from .database import init_db
from .config import get_settings
from .decision_engine import DecisionEngine
from .schemas import CommitRequest, ProposeRequest
from .service import ConflictError, MailroomService, NotFoundError

app = app = FastAPI(title="Safe AI Mailroom")
service = MailroomService(DecisionEngine(get_settings().ai_provider))


@app.on_event("startup")
def startup() -> None:
    init_db()


@app.get("/health")
def health():
    return {"status": "ok"}


@app.post("/v1/mailroom/actions")
async def actions(request: Request):
    try:
        body = await request.body()
        if len(body) > 2_000_000:
            return JSONResponse({"error": "request too large"}, status_code=413)
        raw = json.loads(body)
        if raw.get("operation") == "propose":
            parsed = ProposeRequest.model_validate(raw)
            result = service.propose(parsed)
        elif raw.get("operation") == "commit":
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
