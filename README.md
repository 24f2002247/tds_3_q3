# Safe AI Mailroom

A public HTTPS mailroom agent that reads each case file, chooses one permitted action, and cites the evidence. Verifies signed receipts before recording a completed action.

## Architecture

- **FastAPI** endpoint at `POST /v1/mailroom/actions` handles both `propose` and `commit` operations
- **AI Decision Engine**: uses an OpenAI-compatible model (`gpt-4o-mini` by default) to classify each dossier
- **Canonical hashing**: SHA-256 over recursively key-sorted compact JSON for reproducible fingerprints
- **Ed25519 receipt verification**: cryptographic proof before any action is recorded as completed
- **SQLite / PostgreSQL**: dossiers are cached by content fingerprint; same content → same proposal and callId across evaluations (no repeat model calls)
- **Safety layer**: validates every proposal against frozen action schemas before persistence

## Local setup

```powershell
cd q3-mailroom
python -m venv .venv
.venv\Scripts\Activate.ps1
pip install -r requirements.txt
copy .env.example .env
# Edit .env and set AI_API_KEY
pytest
uvicorn app.main:app --reload
```

## Environment variables

| Variable | Default | Notes |
|---|---|---|
| `DATABASE_URL` | `sqlite:///./mailroom.db` | Set to Render PostgreSQL URL in production |
| `AI_PROVIDER` | `mock` | Set to `openai` to use the real AI path |
| `AI_API_KEY` | — | Required when `AI_PROVIDER=openai` |
| `AI_BASE_URL` | `https://api.openai.com/v1` | Override for compatible endpoints |
| `AI_MODEL` | `gpt-4o-mini` | Any OpenAI-compatible model |

`AI_PROVIDER=mock` is deterministic and used for tests. Set `AI_PROVIDER=openai` for production.

## API

### `POST /v1/mailroom/actions`

Supports two operations determined by the `operation` field:

- `propose` — reads dossiers, returns one proposal per dossier
- `commit` — verifies receipts, records outcomes

### `GET /health`

Returns `{"status": "ok"}`.

## Deployment to Render

```powershell
git init
git add .
git commit -m "Build safe AI mailroom"
git branch -M main
git remote add origin https://github.com/USER/REPOSITORY.git
git push -u origin main
```

1. Create a Render Blueprint from the repository
2. Set the `AI_API_KEY` environment variable in the Render dashboard (not in `render.yaml`)
3. Deploy

Submit URL: `https://q3-mailroom.onrender.com/v1/mailroom/actions`

The URL must be the public HTTPS origin plus `/v1/mailroom/actions`, with no query string or fragment.

## Caching behaviour

- Stable dossiers (same content fingerprint) always produce the same `callId` and proposal — no repeat model call
- Same `evaluationId` with same content → exact byte-for-byte replay of the previous response
- Same `evaluationId` with changed content → HTTP 409 Conflict
- Commit replay with identical receipts → same `outcomes` response
- Commit replay with changed receipts → HTTP 409 Conflict

## Safety guarantees

1. Prompt injection → `quarantine_item` (trusting quotes of attack patterns is not an attack)
2. Scoped approval check for `send_approved_notice` — wrong recipient or wrong scope → `request_confirmation`
3. No model output is used without schema validation
4. Receipts are verified with Ed25519 before any effect is recorded
5. All receipts in a batch must validate atomically; one failure → no receipts stored
