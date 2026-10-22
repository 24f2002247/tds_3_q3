# Safe AI Mailroom

## Local setup

```powershell
cd q3-mailroom
python -m venv .venv
.venv\Scripts\Activate.ps1
pip install -r requirements.txt
copy .env.example .env
pytest
uvicorn app.main:app --reload
```

The application creates SQLite tables on startup. Set `DATABASE_URL` to the Render PostgreSQL connection string in production. `AI_PROVIDER=mock` is deterministic and safe-by-default. To use a compatible hosted OpenAI-style endpoint, set `AI_PROVIDER=openai`, `AI_API_KEY`, `AI_BASE_URL`, and `AI_MODEL`; the service validates the returned proposal before persistence.

The endpoint is `POST /v1/mailroom/actions`; diagnostics are at `GET /health`. Render uses `render.yaml`, binds to its supplied `PORT`, and provisions PostgreSQL. Do not commit `.env`, credentials, or key files.

## GitHub and Render

```powershell
git init
git add .
git commit -m "Build safe AI mailroom"
git branch -M main
git remote add origin https://github.com/USER/REPOSITORY.git
git push -u origin main
```

Create a Render Blueprint from the repository, review the generated database and environment variables, then deploy. The final course URL must be the public HTTPS origin plus `/v1/mailroom/actions`, with no query string or fragment, for example `https://q3-mailroom.onrender.com/v1/mailroom/actions`.
