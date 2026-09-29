# Canada Investigates — backend

FastAPI + SQLAlchemy API for a mobile-first, community research platform. Companion: [web](https://github.com/parker84/canada-investigates-web). Intended domain: CanadaInvestigates.dev.

## Local development

Requires Python 3.10+.

```sh
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.lock
pip install --no-deps -e .
SEED_CASES=true ENABLE_SUBMISSIONS=true AUTH_DEV_MODE=true uvicorn app.main:app --reload
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
- `POST /api/v1/auth/request-link`, `POST /api/v1/auth/verify`, `GET/PATCH /api/v1/auth/me`, `POST /api/v1/auth/logout` — email-link sign-in and server-side sessions.
- `GET/POST /api/v1/cases/{slug}/discussion` — approved posts and private contributions.
- `GET /api/v1/discussion/mine`, `PATCH /api/v1/discussion/{id}` — contribution status and revisions.
- `GET /api/v1/editor/queue`, `POST /api/v1/editor/contributions/{id}/review` — editor review.
- `POST /api/v1/discussion/{id}/report`, `GET /api/v1/editor/reports`, `POST /api/v1/editor/reports/{id}/resolve` — reports and removals.

Only published cases appear in public queries or detail routes. Source URLs must use HTTP(S). Curated seeding is opt-in and idempotent. Three real cases from Keep Canada Weird episodes 231–232 include dated reporting, attributed claims, sources, and official police contact channels. The active filter means unresolved files on our research desk, not confirmation of current police activity. Seeding retires the four known fictional demo rows and preserves existing editorial changes.

## Deployment foundation

Dockerfile and Railway Procfile are included. Set `DATABASE_URL` to a PostgreSQL connection URL and `CORS_ORIGINS` to the web origin(s), comma-separated. Set `SEED_CASES=true` to publish the curated starter cases and leave `ENABLE_SUBMISSIONS=false` for a public preview. No credentials are required or included in source control.

The original cases table predates schema versioning. Startup applies additive schema version 1 for accounts, sessions, contributions, revisions, review decisions, and reports. No existing case rows are changed. For a public deployment, set `APP_ENV=production`, `WEB_ORIGIN` to the web URL, `CORS_ORIGINS` to the same origin, and configure SMTP. Leave `AUTH_DEV_MODE=false`; the development link is returned by the API only when explicitly enabled in development. Set `EDITOR_EMAILS` before the first editor signs in. Only verified addresses listed there receive editor access. Enable case submissions only with an active editorial workflow. This repo has not been deployed.

## Next slices

1. Migrate legacy case JSON to normalized sources, claims, and timeline tables.
2. Add SMTP delivery monitoring, IP-based rate limits, and a staffed moderation policy before public launch.
3. Evidence provenance and uploads, with object storage.
4. Follows and usefulness voting (never a truth score).
5. Geographic discovery, notifications, and production observability.

The product model is **Case → Claims → Evidence → Sources → Timeline → Discussion**. The current JSON detail field is an explicit prototype boundary before normalization.
