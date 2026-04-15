"""
Role Context Cache service.
Caches O*NET + Exa role context and required skills for faster path generation.

Caching strategy:
- L0: In-memory (5 min TTL)
- L1: Database (RoleContextCache table, 15 days TTL)

The cached data includes:
- role_context: Full LLM prompt context string
- required_skills: JSON array of required technologies
"""

import json
import time
from datetime import datetime, timedelta, timezone

from sqlalchemy import text
from db.database import SessionLocal
from db.models import RoleContextCache
from config import ROLE_CONTEXT_CACHE_TTL_DAYS


# ── L0: In-memory cache (per-process) ────────────────────────────────────────
# Key: role (lowercase), Value: (timestamp, {context, skills})
_role_l0_cache: dict[str, tuple[float, dict]] = {}
_ROLE_L0_MAX_SIZE = 64
_ROLE_L0_TTL_SECONDS = 300  # 5 minutes


def _role_l0_get(role: str) -> dict | None:
    """Get from L0 in-memory cache."""
    entry = _role_l0_cache.get(role.lower())
    if entry is None:
        return None
    ts, data = entry
    if time.time() - ts > _ROLE_L0_TTL_SECONDS:
        del _role_l0_cache[role.lower()]
        return None
    return data


def _role_l0_put(role: str, data: dict) -> None:
    """Put to L0 in-memory cache."""
    role_key = role.lower()
    if len(_role_l0_cache) >= _ROLE_L0_MAX_SIZE:
        # Evict oldest
        oldest_key = min(_role_l0_cache, key=lambda k: _role_l0_cache[k][0])
        del _role_l0_cache[oldest_key]
    _role_l0_cache[role_key] = (time.time(), data)


# ── L1: Database cache ───────────────────────────────────────────────────────


def get_cached_role_context(role: str) -> dict | None:
    """Get cached role context from L1 database cache."""
    db = SessionLocal()
    try:
        cutoff = datetime.now(timezone.utc) - timedelta(days=ROLE_CONTEXT_CACHE_TTL_DAYS)
        entry = db.query(RoleContextCache).filter(
            RoleContextCache.role == role,
            RoleContextCache.created_at >= cutoff
        ).first()
        if entry:
            return {
                "role_context": entry.role_context,
                "required_skills": json.loads(entry.required_skills)
            }
        return None
    finally:
        db.close()


def store_role_context(role: str, role_context: str, required_skills: list) -> None:
    """Store role context to L1 database cache."""
    db = SessionLocal()
    try:
        # Delete existing entry
        existing = db.query(RoleContextCache).filter(RoleContextCache.role == role).first()
        if existing:
            db.delete(existing)

        entry = RoleContextCache(
            role=role,
            role_context=role_context,
            required_skills=json.dumps(required_skills),
            created_at=datetime.now(timezone.utc)
        )
        db.add(entry)
        db.commit()
    finally:
        db.close()


# ── Cleanup utilities ────────────────────────────────────────────────────────


def clear_expired_caches() -> tuple[int, int]:
    """Remove expired entries from both L1 caches. Call on server startup."""
    db = SessionLocal()
    try:
        cutoff = datetime.now(timezone.utc) - timedelta(days=ROLE_CONTEXT_CACHE_TTL_DAYS)
        result = db.execute(
            text("DELETE FROM role_context_cache WHERE created_at < :cutoff"),
            {"cutoff": cutoff}
        )
        deleted = result.rowcount
        db.commit()
        return deleted, 0  # 0 for exa cache (handled separately)
    finally:
        db.close()
