import os
import json
import hashlib
from datetime import datetime, timedelta, timezone
from groq import Groq
from dotenv import load_dotenv
from config import LLM_TEMPERATURE, PHASE_QUERY_MODEL
from rag.vector_cache import QUERY_PLAN_TTL_DAYS
import time

load_dotenv()


def _build_cache_key(role: str, industries: list[str], language: str) -> str:
    """SHA-256 of 'role|sorted_industries_json|language' (all normalised to lowercase)."""
    payload = "|".join([
        role.lower().strip(),
        json.dumps(sorted(i.lower() for i in industries)),
        language.lower().strip(),
    ])
    return hashlib.sha256(payload.encode()).hexdigest()


def generate_search_queries(profile) -> list[str]:
    """Generate search queries for a profile, using QueryPlanCache to skip Groq
    when the same role+industries+language was queried before.
    Uses its own isolated DB session so failures never affect the caller.
    """
    start_time = time.time()

    industries: list[str] = []
    try:
        if profile.preferred_industries:
            industries = json.loads(profile.preferred_industries)
    except Exception:
        pass

    language = getattr(profile, "language", None) or "English"
    role = profile.desired_role or ""

    # ── QueryPlanCache lookup (own session) ───────────────────────────────────
    try:
        from db.database import SessionLocal
        from db.models import QueryPlanCache
        cache_key = _build_cache_key(role, industries, language)
        cutoff = datetime.now(timezone.utc) - timedelta(days=QUERY_PLAN_TTL_DAYS)
        db = SessionLocal()
        try:
            cached = (
                db.query(QueryPlanCache)
                .filter(
                    QueryPlanCache.cache_key == cache_key,
                    QueryPlanCache.created_at >= cutoff,
                )
                .first()
            )
            if cached:
                queries = json.loads(cached.queries)
                print(f"[query_planner] cache HIT for '{role}' ({len(queries)} queries) "
                      f"in {time.time()-start_time:.2f}s")
                return queries
        finally:
            db.close()
    except Exception as e:
        print(f"[query_planner] cache lookup error: {e}")

    # ── Groq LLM call ────────────────────────────────────────────────────────────
    client = Groq(api_key=os.getenv("GROQ_API_KEY"))

    prompt = f"""
Generate 8–10 focused web search queries to find learning resources.

Target role: {role}
Preferred industries: {', '.join(industries) if industries else 'general'}
Instruction language: {language}

Rules:
- Include YouTube queries
- Include beginner and advanced queries
- Include industry-specific queries (e.g., "{role} in {industries[0] if industries else 'tech'}")
- If instruction language is not English, include 1-2 queries like "{role} tutorials in {language}"
- Return ONLY a JSON array of strings
"""

    response = client.chat.completions.create(
        model=PHASE_QUERY_MODEL,
        messages=[{"role": "user", "content": prompt}],
        temperature=LLM_TEMPERATURE,
    )

    raw = response.choices[0].message.content.strip()

    try:
        queries = json.loads(raw)
        if not isinstance(queries, list):
            raise ValueError("not a list")
    except Exception:
        queries = [
            f"{role} learning roadmap",
            f"best YouTube playlists for {role}",
            f"{role} free courses",
            f"{role} beginner to advanced",
        ]

    # Deterministic language-specific additions
    if language and language.lower() != "english":
        queries.extend([
            f"{role} tutorial in {language}",
            f"{role} course {language} language",
            f"{role} YouTube {language}",
        ])

    # Deterministic industry-specific additions
    if industries:
        for ind in industries[:2]:
            queries.extend([
                f"{role} in {ind} industry",
                f"{role} {ind} projects tutorials",
            ])
            if language and language.lower() != "english":
                queries.append(f"{role} {ind} {language}")

    # ── Store to QueryPlanCache (own session) ────────────────────────────────
    try:
        from db.database import SessionLocal
        from db.models import QueryPlanCache
        cache_key = _build_cache_key(role, industries, language)
        db = SessionLocal()
        try:
            db.query(QueryPlanCache).filter(
                QueryPlanCache.cache_key == cache_key
            ).delete()
            db.add(QueryPlanCache(cache_key=cache_key, queries=json.dumps(queries)))
            db.commit()
        except Exception as e:
            db.rollback()
            print(f"[query_planner] cache store error: {e}")
        finally:
            db.close()
    except Exception as e:
        print(f"[query_planner] cache store error: {e}")

    print(f"[query_planner] Groq MISS for '{role}' ({len(queries)} queries) "
          f"in {time.time()-start_time:.2f}s")
    return queries
