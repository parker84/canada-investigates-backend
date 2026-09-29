from datetime import datetime, timezone, timedelta
from uuid import uuid4
import json
import os
import secrets
import hashlib
import smtplib
from email.message import EmailMessage
from urllib.parse import urlparse
from pathlib import Path
from contextlib import asynccontextmanager
from fastapi import FastAPI, HTTPException, Query, Request, Response, Cookie
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field, HttpUrl
from sqlalchemy import create_engine, String, Text, select, ForeignKey
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, sessionmaker

url = os.getenv("DATABASE_URL", "sqlite:///./investigations.db")
if url.startswith("postgres://"):
    url = url.replace("postgres://", "postgresql+psycopg://", 1)
elif url.startswith("postgresql://"):
    url = url.replace("postgresql://", "postgresql+psycopg://", 1)
engine = create_engine(url, connect_args={"check_same_thread": False} if url.startswith("sqlite") else {})
Session = sessionmaker(engine)

class Base(DeclarativeBase):
    pass

class Case(Base):
    __tablename__ = "cases"
    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    slug: Mapped[str] = mapped_column(String(160), unique=True)
    title: Mapped[str] = mapped_column(String(160))
    province: Mapped[str] = mapped_column(String(60))
    location: Mapped[str] = mapped_column(String(120))
    category: Mapped[str] = mapped_column(String(60))
    summary: Mapped[str] = mapped_column(Text)
    status: Mapped[str] = mapped_column(String(30), default="under-review")
    created_at: Mapped[str] = mapped_column(String(40))
    detail: Mapped[str] = mapped_column(Text, default="{}")

class User(Base):
    __tablename__ = "users"
    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    email: Mapped[str] = mapped_column(String(320), unique=True)
    display_name: Mapped[str] = mapped_column(String(60))
    role: Mapped[str] = mapped_column(String(20), default="contributor")
    created_at: Mapped[str] = mapped_column(String(40))

class LoginToken(Base):
    __tablename__ = "login_tokens"
    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    email: Mapped[str] = mapped_column(String(320))
    token_hash: Mapped[str] = mapped_column(String(64), unique=True)
    expires_at: Mapped[str] = mapped_column(String(40))
    used_at: Mapped[str | None] = mapped_column(String(40), nullable=True)
    created_at: Mapped[str] = mapped_column(String(40))

class AuthSession(Base):
    __tablename__ = "auth_sessions"
    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id"))
    token_hash: Mapped[str] = mapped_column(String(64), unique=True)
    expires_at: Mapped[str] = mapped_column(String(40))
    created_at: Mapped[str] = mapped_column(String(40))

class Contribution(Base):
    __tablename__ = "contributions"
    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    case_id: Mapped[str] = mapped_column(ForeignKey("cases.id"))
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id"))
    kind: Mapped[str] = mapped_column(String(20))
    body: Mapped[str] = mapped_column(Text)
    source_url: Mapped[str | None] = mapped_column(Text, nullable=True)
    status: Mapped[str] = mapped_column(String(30), default="pending")
    review_note: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[str] = mapped_column(String(40))
    reviewed_at: Mapped[str | None] = mapped_column(String(40), nullable=True)

class ReviewEvent(Base):
    __tablename__ = "review_events"
    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    contribution_id: Mapped[str] = mapped_column(ForeignKey("contributions.id"))
    reviewer_id: Mapped[str] = mapped_column(ForeignKey("users.id"))
    decision: Mapped[str] = mapped_column(String(30))
    reason: Mapped[str] = mapped_column(Text)
    created_at: Mapped[str] = mapped_column(String(40))

class ContributionRevision(Base):
    __tablename__ = "contribution_revisions"
    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    contribution_id: Mapped[str] = mapped_column(ForeignKey("contributions.id"))
    body: Mapped[str] = mapped_column(Text)
    source_url: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[str] = mapped_column(String(40))

class Report(Base):
    __tablename__ = "reports"
    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    contribution_id: Mapped[str] = mapped_column(ForeignKey("contributions.id"))
    reporter_id: Mapped[str] = mapped_column(ForeignKey("users.id"))
    reason: Mapped[str] = mapped_column(Text)
    created_at: Mapped[str] = mapped_column(String(40))
    resolved_at: Mapped[str | None] = mapped_column(String(40), nullable=True)

class SchemaVersion(Base):
    __tablename__ = "schema_versions"
    version: Mapped[int] = mapped_column(primary_key=True)
    applied_at: Mapped[str] = mapped_column(String(40))

def migrate():
    # The original cases table predates versioned schema changes.
    Base.metadata.create_all(engine, tables=[Case.__table__, SchemaVersion.__table__])
    with Session() as db:
        if not db.get(SchemaVersion, 1):
            Base.metadata.create_all(engine, tables=[User.__table__, LoginToken.__table__, AuthSession.__table__, Contribution.__table__, ReviewEvent.__table__, ContributionRevision.__table__, Report.__table__])
            db.add(SchemaVersion(version=1, applied_at=datetime.now(timezone.utc).isoformat()))
            db.commit()

class Submission(BaseModel):
    title: str = Field(min_length=8, max_length=160, pattern=r"\S")
    province: str = Field(min_length=2, max_length=60, pattern=r"\S")
    location: str = Field(min_length=2, max_length=120, pattern=r"\S")
    category: str = Field(min_length=2, max_length=60, pattern=r"\S")
    summary: str = Field(min_length=40, max_length=5000, pattern=r"\S")
    source_url: HttpUrl

PUBLIC_STATUSES = ("reported", "witness-appeal", "investigating", "resolved")
LEGACY_DEMOS = ("the-missing-museum-map", "a-signal-from-the-coast", "the-last-picture-house", "tracks-beyond-the-town")

def seed():
    records = json.loads(Path(__file__).with_name("seed_cases.json").read_text())
    with Session() as db:
        for old in db.scalars(select(Case).where(Case.slug.in_(LEGACY_DEMOS))):
            if json.loads(old.detail).get("demo") is True:
                old.status = "archived"
        for record in records:
            values = record | {"detail": json.dumps(record["detail"], ensure_ascii=False)}
            existing = db.scalar(select(Case).where(Case.slug == record["slug"]))
            # Existing editorial changes survive restarts; seeded files are inserted once.
            if not existing:
                db.add(Case(id=str(uuid4()), created_at=datetime.now(timezone.utc).isoformat(), **values))
        db.commit()

@asynccontextmanager
async def lifespan(app):
    migrate()
    if os.getenv("SEED_CASES", os.getenv("SEED_DEMO", "false")).lower() == "true":
        seed()
    yield

app = FastAPI(title="Canada Investigates API", version="0.1.0", lifespan=lifespan)
app.add_middleware(CORSMiddleware, allow_origins=os.getenv("CORS_ORIGINS", "http://localhost:5173,http://127.0.0.1:5173,https://canadainvestigates.dev").split(","), allow_credentials=True, allow_methods=["GET", "POST", "PATCH"], allow_headers=["Content-Type", "X-CSRF-Token"])

def serialize(case):
    return {key: getattr(case, key) for key in ("id", "slug", "title", "province", "location", "category", "summary", "status", "created_at")} | json.loads(case.detail)

@app.get("/health")
def health():
    return {"status": "ok"}

@app.get("/api/v1/cases")
def cases(q: str = Query("", max_length=200), status: str = "", province: str = "", limit: int = Query(50, ge=1, le=100)):
    with Session() as db:
        stmt = select(Case).where(Case.status.in_(PUBLIC_STATUSES))
        if q.strip():
            stmt = stmt.where(Case.title.icontains(q.strip(), autoescape=True) | Case.summary.icontains(q.strip(), autoescape=True))
        if status == "active":
            stmt = stmt.where(Case.status != "resolved")
        elif status:
            stmt = stmt.where(Case.status == status)
        if province:
            stmt = stmt.where(Case.province == province)
        return {"items": [serialize(c) for c in db.scalars(stmt.order_by(Case.created_at.desc()).limit(limit))]}

@app.get("/api/v1/cases/{slug}")
def case_detail(slug: str):
    with Session() as db:
        case = db.scalar(select(Case).where(Case.slug == slug, Case.status.in_(PUBLIC_STATUSES)))
        if not case:
            raise HTTPException(404, "Case not found")
        return serialize(case)

@app.post("/api/v1/cases", status_code=201)
def submit(data: Submission, request: Request):
    if os.getenv("ENABLE_SUBMISSIONS", "false").lower() != "true":
        raise HTTPException(503, "Submissions are not open yet. Please check back soon.")
    check_origin(request)
    with Session() as db:
        authenticated(request, db)
    identifier = str(uuid4())
    with Session() as db:
        case = Case(id=identifier, slug=f"submission-{identifier}", **data.model_dump(exclude={"source_url"}), status="under-review", created_at=datetime.now(timezone.utc).isoformat(), detail=json.dumps({"sources": [{"url": str(data.source_url), "label": "Submitted source"}], "demo": False}))
        db.add(case)
        db.commit()
    return {"id": identifier, "status": "under-review", "message": "Received for editorial review. Your submission is not public."}

def now():
    return datetime.now(timezone.utc).isoformat()

def digest(value: str):
    return hashlib.sha256(value.encode()).hexdigest()

def public_user(user: User):
    return {"id": user.id, "display_name": user.display_name, "role": user.role}

def authenticated(request: Request, db):
    raw = request.cookies.get("ci_session", "")
    if not raw:
        raise HTTPException(401, "Sign in to contribute.")
    session = db.scalar(select(AuthSession).where(AuthSession.token_hash == digest(raw), AuthSession.expires_at > now()))
    if not session:
        raise HTTPException(401, "Your session has expired. Please sign in again.")
    user = db.get(User, session.user_id)
    if not user:
        raise HTTPException(401, "Account unavailable.")
    return user

def check_origin(request: Request):
    origin = request.headers.get("origin")
    allowed = os.getenv("CORS_ORIGINS", "http://localhost:5173,http://127.0.0.1:5173,https://canadainvestigates.dev").split(",")
    if origin and origin not in allowed:
        raise HTTPException(403, "Origin not allowed.")
    # Browser form posts have an Origin; same-origin deployments may omit it in tests.
    if request.headers.get("sec-fetch-site") == "cross-site" and not origin:
        raise HTTPException(403, "Origin required.")

class LoginRequest(BaseModel):
    email: str = Field(min_length=5, max_length=320, pattern=r"^[^\s@]+@[^\s@]+\.[^\s@]+$")

class VerifyRequest(BaseModel):
    token: str = Field(min_length=32, max_length=200)
    display_name: str = Field(default="", max_length=60)

class ProfileUpdate(BaseModel):
    display_name: str = Field(min_length=2, max_length=60, pattern=r"\S")

class ContributionInput(BaseModel):
    kind: str = Field(pattern=r"^(question|source|correction)$")
    body: str = Field(min_length=20, max_length=5000, pattern=r"\S")
    source_url: HttpUrl | None = None

class ReviewInput(BaseModel):
    decision: str = Field(pattern=r"^(approved|changes-requested|rejected|removed)$")
    reason: str = Field(min_length=5, max_length=2000, pattern=r"\S")

class ReportInput(BaseModel):
    reason: str = Field(min_length=10, max_length=1000, pattern=r"\S")

class RevisionInput(BaseModel):
    body: str = Field(min_length=20, max_length=5000, pattern=r"\S")
    source_url: HttpUrl | None = None

def serialize_contribution(item: Contribution, author: User):
    return {"id": item.id, "kind": item.kind, "body": item.body, "source_url": item.source_url,
            "status": item.status, "created_at": item.created_at, "author": public_user(author)}

@app.post("/api/v1/auth/request-link")
def request_link(data: LoginRequest, request: Request):
    check_origin(request)
    dev_mode = os.getenv("AUTH_DEV_MODE", "false").lower() == "true"
    web_origin = os.getenv("WEB_ORIGIN", "http://localhost:5173").rstrip("/")
    if dev_mode and (os.getenv("APP_ENV", "development") != "development" or urlparse(web_origin).hostname not in ("localhost", "127.0.0.1")):
        raise HTTPException(503, "Development sign-in is unavailable in this environment.")
    if not dev_mode and (not os.getenv("SMTP_HOST") or not os.getenv("SMTP_FROM")):
        raise HTTPException(503, "Email sign-in is not configured.")
    email = data.email.strip().lower()
    with Session() as db:
        recent = db.scalars(select(LoginToken).where(LoginToken.email == email, LoginToken.created_at > (datetime.now(timezone.utc) - timedelta(hours=1)).isoformat())).all()
        if len(recent) >= 3:
            raise HTTPException(429, "Too many sign-in requests. Try again later.")
        raw = secrets.token_urlsafe(32)
        db.add(LoginToken(id=str(uuid4()), email=email, token_hash=digest(raw), created_at=now(), expires_at=(datetime.now(timezone.utc) + timedelta(minutes=15)).isoformat()))
        db.commit()
    link = f"{web_origin}/#/sign-in?token={raw}"
    if dev_mode:
        return {"message": "Sign-in link ready for local development.", "dev_link": link}
    host = os.getenv("SMTP_HOST")
    sender = os.getenv("SMTP_FROM")
    if not host or not sender:
        raise HTTPException(503, "Email sign-in is not configured.")
    message = EmailMessage()
    message["From"], message["To"], message["Subject"] = sender, email, "Sign in to Canada Investigates"
    message.set_content(f"Use this link to sign in. It expires in 15 minutes:\n\n{link}\n\nIf you didn't request it, ignore this email.")
    with smtplib.SMTP(host, int(os.getenv("SMTP_PORT", "587")), timeout=10) as smtp:
        smtp.starttls()
        if os.getenv("SMTP_USERNAME"):
            smtp.login(os.environ["SMTP_USERNAME"], os.environ["SMTP_PASSWORD"])
        smtp.send_message(message)
    return {"message": "Check your email for a sign-in link."}

@app.post("/api/v1/auth/verify")
def verify(data: VerifyRequest, response: Response, request: Request):
    check_origin(request)
    with Session() as db:
        record = db.scalar(select(LoginToken).where(LoginToken.token_hash == digest(data.token), LoginToken.used_at.is_(None), LoginToken.expires_at > now()))
        if not record:
            raise HTTPException(400, "This sign-in link has expired or was already used.")
        user = db.scalar(select(User).where(User.email == record.email))
        if not user:
            name = data.display_name.strip()
            if len(name) < 2:
                raise HTTPException(422, "Choose a display name with at least two characters.")
            editors = {email.strip().lower() for email in os.getenv("EDITOR_EMAILS", "").split(",") if email.strip()}
            user = User(id=str(uuid4()), email=record.email, display_name=name, role="editor" if record.email in editors else "contributor", created_at=now())
            db.add(user)
        record.used_at = now()
        raw = secrets.token_urlsafe(32)
        db.add(AuthSession(id=str(uuid4()), user_id=user.id, token_hash=digest(raw), created_at=now(), expires_at=(datetime.now(timezone.utc) + timedelta(days=14)).isoformat()))
        db.commit()
        response.set_cookie("ci_session", raw, max_age=14 * 86400, httponly=True, secure=os.getenv("APP_ENV") == "production", samesite="lax", path="/")
        return public_user(user)

@app.get("/api/v1/auth/me")
def me(request: Request):
    with Session() as db:
        return public_user(authenticated(request, db))

@app.patch("/api/v1/auth/me")
def update_me(data: ProfileUpdate, request: Request):
    check_origin(request)
    with Session() as db:
        user = authenticated(request, db)
        user.display_name = data.display_name.strip()
        db.commit()
        return public_user(user)

@app.post("/api/v1/auth/logout", status_code=204)
def logout(request: Request, response: Response):
    check_origin(request)
    raw = request.cookies.get("ci_session", "")
    if raw:
        with Session() as db:
            session = db.scalar(select(AuthSession).where(AuthSession.token_hash == digest(raw)))
            if session:
                db.delete(session)
                db.commit()
    response.delete_cookie("ci_session", path="/")

@app.get("/api/v1/cases/{slug}/discussion")
def discussion(slug: str):
    with Session() as db:
        case = db.scalar(select(Case).where(Case.slug == slug, Case.status.in_(PUBLIC_STATUSES)))
        if not case:
            raise HTTPException(404, "Case not found")
        items = db.scalars(select(Contribution).where(Contribution.case_id == case.id, Contribution.status == "approved").order_by(Contribution.created_at.desc())).all()
        return {"items": [serialize_contribution(item, db.get(User, item.user_id)) for item in items]}

@app.post("/api/v1/cases/{slug}/discussion", status_code=201)
def contribute(slug: str, data: ContributionInput, request: Request):
    check_origin(request)
    if data.kind == "source" and not data.source_url:
        raise HTTPException(422, "A public source URL is required.")
    with Session() as db:
        user = authenticated(request, db)
        case = db.scalar(select(Case).where(Case.slug == slug, Case.status.in_(PUBLIC_STATUSES)))
        if not case:
            raise HTTPException(404, "Case not found")
        recent = db.scalars(select(Contribution).where(Contribution.user_id == user.id, Contribution.created_at > (datetime.now(timezone.utc) - timedelta(hours=1)).isoformat())).all()
        if len(recent) >= 5:
            raise HTTPException(429, "Contribution limit reached. Please try again later.")
        item = Contribution(id=str(uuid4()), case_id=case.id, user_id=user.id, kind=data.kind, body=data.body.strip(), source_url=str(data.source_url) if data.source_url else None, status="pending", created_at=now())
        db.add(item)
        db.commit()
        return {"id": item.id, "status": "pending", "message": "Received for editorial review. Only you and editors can see it for now."}

@app.get("/api/v1/discussion/mine")
def my_contributions(request: Request):
    with Session() as db:
        user = authenticated(request, db)
        items = db.scalars(select(Contribution).where(Contribution.user_id == user.id).order_by(Contribution.created_at.desc())).all()
        return {"items": [serialize_contribution(item, user) | {"review_note": item.review_note, "case_slug": db.get(Case, item.case_id).slug} for item in items]}

@app.patch("/api/v1/discussion/{identifier}")
def revise_contribution(identifier: str, data: RevisionInput, request: Request):
    check_origin(request)
    with Session() as db:
        user = authenticated(request, db)
        item = db.get(Contribution, identifier)
        if not item or item.user_id != user.id:
            raise HTTPException(404, "Contribution not found")
        if item.status not in ("pending", "changes-requested"):
            raise HTTPException(409, "This contribution can no longer be edited.")
        if item.kind == "source" and not data.source_url:
            raise HTTPException(422, "A public source URL is required.")
        db.add(ContributionRevision(id=str(uuid4()), contribution_id=item.id, body=item.body, source_url=item.source_url, created_at=now()))
        item.body, item.source_url, item.status, item.review_note = data.body.strip(), str(data.source_url) if data.source_url else None, "pending", None
        db.commit()
        return {"id": item.id, "status": item.status}

@app.get("/api/v1/editor/queue")
def review_queue(request: Request):
    with Session() as db:
        user = authenticated(request, db)
        if user.role != "editor":
            raise HTTPException(403, "Editor access required.")
        items = db.scalars(select(Contribution).where(Contribution.status == "pending").order_by(Contribution.created_at.asc())).all()
        return {"items": [serialize_contribution(item, db.get(User, item.user_id)) | {"case_slug": db.get(Case, item.case_id).slug} for item in items]}

@app.post("/api/v1/editor/contributions/{identifier}/review")
def review(identifier: str, data: ReviewInput, request: Request):
    check_origin(request)
    with Session() as db:
        user = authenticated(request, db)
        if user.role != "editor":
            raise HTTPException(403, "Editor access required.")
        item = db.get(Contribution, identifier)
        if not item:
            raise HTTPException(404, "Contribution not found")
        if data.decision == "removed" and item.status != "approved":
            raise HTTPException(409, "Only published contributions can be removed.")
        if data.decision != "removed" and item.status != "pending":
            raise HTTPException(409, "This contribution has already been reviewed.")
        item.status, item.review_note, item.reviewed_at = data.decision, data.reason.strip(), now()
        db.add(ReviewEvent(id=str(uuid4()), contribution_id=item.id, reviewer_id=user.id, decision=data.decision, reason=data.reason.strip(), created_at=now()))
        db.commit()
        return {"id": item.id, "status": item.status}

@app.post("/api/v1/discussion/{identifier}/report", status_code=201)
def report_contribution(identifier: str, data: ReportInput, request: Request):
    check_origin(request)
    with Session() as db:
        user = authenticated(request, db)
        item = db.get(Contribution, identifier)
        if not item or item.status != "approved":
            raise HTTPException(404, "Contribution not found")
        previous = db.scalar(select(Report).where(Report.contribution_id == identifier, Report.reporter_id == user.id, Report.resolved_at.is_(None)))
        if previous:
            raise HTTPException(409, "You have already reported this post.")
        db.add(Report(id=str(uuid4()), contribution_id=identifier, reporter_id=user.id, reason=data.reason.strip(), created_at=now()))
        db.commit()
        return {"message": "Report received for editorial review."}

@app.get("/api/v1/editor/reports")
def report_queue(request: Request):
    with Session() as db:
        user = authenticated(request, db)
        if user.role != "editor":
            raise HTTPException(403, "Editor access required.")
        reports = db.scalars(select(Report).where(Report.resolved_at.is_(None)).order_by(Report.created_at.asc())).all()
        return {"items": [{"id": report.id, "reason": report.reason, "created_at": report.created_at,
                "contribution": serialize_contribution(item, db.get(User, item.user_id)),
                "case_slug": db.get(Case, item.case_id).slug}
                for report in reports if (item := db.get(Contribution, report.contribution_id))]}

@app.post("/api/v1/editor/reports/{identifier}/resolve")
def resolve_report(identifier: str, request: Request):
    check_origin(request)
    with Session() as db:
        user = authenticated(request, db)
        if user.role != "editor":
            raise HTTPException(403, "Editor access required.")
        report = db.get(Report, identifier)
        if not report or report.resolved_at:
            raise HTTPException(404, "Open report not found")
        report.resolved_at = now()
        db.commit()
        return {"id": report.id, "status": "resolved"}
