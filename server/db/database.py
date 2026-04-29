from sqlalchemy.orm import sessionmaker, Session, declarative_base
from sqlalchemy import create_engine, text
from typing import Generator
import os
from dotenv import load_dotenv
load_dotenv()
db_uri = os.getenv("DATABASE_URL")
if not db_uri:
    raise RuntimeError("DATABASE_URL environment variable is required")

# Supabase (and some platforms) may give 'postgres://' — SQLAlchemy requires 'postgresql://'
if db_uri.startswith("postgres://"):
    db_uri = db_uri.replace("postgres://", "postgresql://", 1)

engine = create_engine(
    db_uri,
    echo=False,
    pool_size=15,       # enough for 13 parallel vector-cache sessions + route sessions
    max_overflow=5,     # allow a small burst above pool_size
    pool_pre_ping=True, # discard stale connections (important for Supabase idle timeout)
)
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
Base = declarative_base()


def get_db() -> Generator[Session, None, None]:
    """Dependency for getting database session in FastAPI routes."""
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


def init_db():
    """Initialize database tables. Ensures pgvector extension exists first."""
    with engine.connect() as conn:
        conn.execute(text("CREATE EXTENSION IF NOT EXISTS vector"))
        conn.commit()
    Base.metadata.create_all(bind=engine)