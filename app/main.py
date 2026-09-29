from datetime import datetime, timezone
from uuid import uuid4
import json
import os
from contextlib import asynccontextmanager
from fastapi import FastAPI, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field, HttpUrl
from sqlalchemy import create_engine, String, Text, select
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

class Submission(BaseModel):
    title: str = Field(min_length=8, max_length=160, pattern=r"\S")
    province: str = Field(min_length=2, max_length=60, pattern=r"\S")
    location: str = Field(min_length=2, max_length=120, pattern=r"\S")
    category: str = Field(min_length=2, max_length=60, pattern=r"\S")
    summary: str = Field(min_length=40, max_length=5000, pattern=r"\S")
    source_url: HttpUrl

DEMO = [
    ("the-missing-museum-map", "The missing museum map", "Ontario", "Ottawa, ON", "History", "An old exhibition catalogue mentions a map that never appears in the digitized collection. Can the paper trail fill in the gap?", "open", "01", "map"),
    ("a-signal-from-the-coast", "A signal from the coast", "Nova Scotia", "Lunenburg, NS", "Maritime", "A community archive contains two different dates for the same lighthouse signal. An exercise in following the original record.", "investigating", "02", "coast"),
    ("the-last-picture-house", "The last picture house", "Manitoba", "Winnipeg, MB", "Local history", "Three photographs. Two street names. One neighbourhood cinema. Help piece together the story behind a changing city block.", "open", "03", "city"),
    ("tracks-beyond-the-town", "Tracks beyond the town", "British Columbia", "Revelstoke, BC", "Environment", "An abandoned route appears in an old survey but disappears from a later edition. Explore how to compare maps and document uncertainty.", "resolved", "04", "forest"),
]

def seed():
    with Session() as db:
        for slug, title, province, location, category, summary, status, number, art in DEMO:
            if db.scalar(select(Case).where(Case.slug == slug)):
                continue
            detail = {"demo": True, "number": number, "art": art, "sources": [], "claims": [{"text": "The records described in this sample require comparison with original documents.", "status": "Unverified"}], "timeline": [{"date": "2026-09-29", "text": "Illustrative case created to demonstrate the investigation workspace."}], "note": "This is a fictional demonstration case. No real incident or public allegation is represented."}
            db.add(Case(id=str(uuid4()), slug=slug, title=title, province=province, location=location, category=category, summary=summary, status=status, created_at=datetime.now(timezone.utc).isoformat(), detail=json.dumps(detail)))
        db.commit()

@asynccontextmanager
async def lifespan(app):
    Base.metadata.create_all(engine)
    if os.getenv("SEED_DEMO", "false").lower() == "true":
        seed()
    yield

app = FastAPI(title="Canada Investigates API", version="0.1.0", lifespan=lifespan)
app.add_middleware(CORSMiddleware, allow_origins=os.getenv("CORS_ORIGINS", "http://localhost:5173,http://127.0.0.1:5173,https://canadainvestigates.dev").split(","), allow_methods=["GET", "POST"], allow_headers=["Content-Type"])

def serialize(case):
    return {key: getattr(case, key) for key in ("id", "slug", "title", "province", "location", "category", "summary", "status", "created_at")} | json.loads(case.detail)

@app.get("/health")
def health():
    return {"status": "ok"}

@app.get("/api/v1/cases")
def cases(q: str = Query("", max_length=200), status: str = "", province: str = "", limit: int = Query(50, ge=1, le=100)):
    with Session() as db:
        stmt = select(Case).where(Case.status != "under-review")
        if q.strip():
            stmt = stmt.where(Case.title.icontains(q.strip(), autoescape=True) | Case.summary.icontains(q.strip(), autoescape=True))
        if status:
            stmt = stmt.where(Case.status == status)
        if province:
            stmt = stmt.where(Case.province == province)
        return {"items": [serialize(c) for c in db.scalars(stmt.order_by(Case.created_at.desc()).limit(limit))]}

@app.get("/api/v1/cases/{slug}")
def case_detail(slug: str):
    with Session() as db:
        case = db.scalar(select(Case).where(Case.slug == slug, Case.status != "under-review"))
        if not case:
            raise HTTPException(404, "Case not found")
        return serialize(case)

@app.post("/api/v1/cases", status_code=201)
def submit(data: Submission):
    if os.getenv("ENABLE_SUBMISSIONS", "false").lower() != "true":
        raise HTTPException(503, "Submissions are not open yet. Please check back soon.")
    identifier = str(uuid4())
    with Session() as db:
        case = Case(id=identifier, slug=f"submission-{identifier}", **data.model_dump(exclude={"source_url"}), status="under-review", created_at=datetime.now(timezone.utc).isoformat(), detail=json.dumps({"sources": [{"url": str(data.source_url), "label": "Submitted source"}], "demo": False}))
        db.add(case)
        db.commit()
    return {"id": identifier, "status": "under-review", "message": "Received for editorial review. Your submission is not public."}
