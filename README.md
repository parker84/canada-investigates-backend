# Canada Investigates — backend

FastAPI + SQLAlchemy API for a mobile-first, community research platform. Companion: [web](https://github.com/parker84/canada-investigates-web). Intended domain: CanadaInvestigates.dev.

## Local development

Requires Python 3.10+.

```sh
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.lock
pip install --no-deps -e .
SEED_CASES=true ENABLE_SUBMISSIONS=true uvicorn app.main:app --reload
```

API documentation: http://localhost:8000/docs. SQLite persists to `investigations.db`. Environment variables must be exported in your shell; `.env.example` documents them, but `.env` is not automatically loaded.

```sh
pytest -q
```

## API

- `GET /health`
- `GET /api/v1/cases?q=&status=&province=&limit=50`
- `GET /api/v1/cases/{slug}`
- `POST /api/v1/cases` — creates a private submission for editorial review. Requires `ENABLE_SUBMISSIONS=true`; disabled by default until abuse controls and editorial tools are ready.

Only published cases appear in public queries or detail routes. Source URLs must use HTTP(S). Curated seeding is opt-in and idempotent. Three real cases from Keep Canada Weird episodes 231–232 include dated reporting, attributed claims, sources, and official police contact channels. The active filter means unresolved files on our research desk, not confirmation of current police activity. Seeding retires the four known fictional demo rows and preserves existing editorial changes.

## Deployment foundation

Dockerfile and Railway Procfile are included. Set `DATABASE_URL` to a PostgreSQL connection URL and `CORS_ORIGINS` to the web origin(s), comma-separated. Set `SEED_CASES=true` to publish the curated starter cases and leave `ENABLE_SUBMISSIONS=false` for a public preview. No credentials are required or included in source control.

The initial schema is created on startup for this first slice. Before evolving a production database, add Alembic migrations. There is no editorial UI or authentication yet; submissions must remain disabled on a public deployment until authenticated moderation, rate limits, abuse prevention, and audit logging are implemented. This repo has not been deployed.

## Next slices

1. Alembic migrations and normalized sources, claims, evidence, and timeline tables.
2. Authentication and reviewer roles; audited publish/reject/redact workflow.
3. Evidence provenance and uploads, with object storage.
4. Discussions, follows, and usefulness voting (never a truth score).
5. Geographic discovery, notifications, and production observability.

The product model is **Case → Claims → Evidence → Sources → Timeline → Discussion**. The current JSON detail field is an explicit prototype boundary before normalization.
