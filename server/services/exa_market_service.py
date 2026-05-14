"""
Real-time market data via Exa API.
Uses Exa's answer API to fetch job market data from the web.

Caching:
- L0: In-memory cache (per-process, 5 min TTL)
- L1: Database cache (ExaMarketCache table, 15 days TTL)
- L2: Live Exa API call (only for cache misses)
"""

import os
import json
import time
from datetime import datetime, timedelta, timezone

from rag.retriever import clean_llm_json
from db.database import SessionLocal
from db.models import ExaMarketCache
from config import EXA_CACHE_TTL_DAYS


# ── L0: In-memory cache (per-process) ────────────────────────────────────────
# Key: role (lowercase), Value: (timestamp, data)
_exa_l0_cache: dict[str, tuple[float, dict]] = {}
_EXA_L0_MAX_SIZE = 128
_EXA_L0_TTL_SECONDS = 300  # 5 minutes


def _exa_l0_get(role: str) -> dict | None:
    """Get from L0 in-memory cache."""
    entry = _exa_l0_cache.get(role.lower())
    if entry is None:
        return None
    ts, data = entry
    if time.time() - ts > _EXA_L0_TTL_SECONDS:
        del _exa_l0_cache[role.lower()]
        return None
    return data


def _exa_l0_put(role: str, data: dict) -> None:
    """Put to L0 in-memory cache."""
    role_key = role.lower()
    if len(_exa_l0_cache) >= _EXA_L0_MAX_SIZE:
        # Evict oldest
        oldest_key = min(_exa_l0_cache, key=lambda k: _exa_l0_cache[k][0])
        del _exa_l0_cache[oldest_key]
    _exa_l0_cache[role_key] = (time.time(), data)


# ── L1: Database cache ───────────────────────────────────────────────────────


def _get_exa_db_cache(role: str) -> dict | None:
    """Get from L1 database cache (ExaMarketCache table)."""
    db = SessionLocal()
    try:
        cutoff = datetime.now(timezone.utc) - timedelta(days=EXA_CACHE_TTL_DAYS)
        entry = db.query(ExaMarketCache).filter(
            ExaMarketCache.role == role,
            ExaMarketCache.created_at >= cutoff
        ).first()
        if entry:
            return json.loads(entry.data)
        return None
    finally:
        db.close()


def _store_exa_db_cache(role: str, data: dict) -> None:
    """Store to L1 database cache (upsert: update if exists, insert if not)."""
    db = SessionLocal()
    try:
        serialized = json.dumps(data)
        now = datetime.now(timezone.utc)

        existing = db.query(ExaMarketCache).filter(ExaMarketCache.role == role).first()
        if existing:
            # Update in-place — avoids UniqueViolation from delete+insert race
            existing.data = serialized
            existing.created_at = now
        else:
            db.add(ExaMarketCache(role=role, data=serialized, created_at=now))

        db.commit()
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()


# ── Public API ───────────────────────────────────────────────────────────────


def fetch_realtime_market_data(role_name: str) -> dict:
    """
    Fetches real-time job market data for a role using Exa API.
    Uses 3-layer caching:
      L0 - In-memory cache (5 min TTL)
      L1 - Database cache (ExaMarketCache, 15 days TTL)
      L2 - Live Exa API call

    Returns:
        {
            "training_skills": [...],
            "growth_rate": str,
            "total_jobs": str,
            "starting_salary": str,
            "average_salary": str,
            "max_salary": str,
            "data_source": "cached" | "realtime",
        }
    """
    # L0: Check in-memory cache
    cached = _exa_l0_get(role_name)
    if cached:
        return cached

    # L1: Check database cache
    db_cached = _get_exa_db_cache(role_name)
    if db_cached:
        _exa_l0_put(role_name, db_cached)  # Populate L0
        return db_cached

    # L2: Call Exa API
    data = _fetch_exa_data_live(role_name)

    if data.get("data_source") == "realtime":
        # Only cache successful API responses
        _store_exa_db_cache(role_name, data)
        _exa_l0_put(role_name, data)

    return data


def _fetch_exa_data_live(role_name: str) -> dict:
    """Internal: Fetch directly from Exa API (no cache)."""
    exa_key = os.getenv("EXA_API_KEY")
    if not exa_key:
        return _fallback_realtime_data(role_name, "EXA_API_KEY not configured")

    try:
        from exa_py import Exa
        exa = Exa(api_key=exa_key)
    except ImportError:
        return _fallback_realtime_data(role_name, "exa-py not installed")
    except Exception as e:
        return _fallback_realtime_data(role_name, str(e))

    try:
        question = (
            f"For the job role '{role_name}', provide current US market data. "
            "Return ONLY valid JSON with these exact keys (use numbers from BLS, Indeed, Glassdoor, Payscale): "
            '{"training_skills": ["skill1", "skill2", ...], "growth_rate": "e.g. 8%", '
            '"total_jobs": "e.g. 500,000", "starting_salary": "e.g. $55,000", '
            '"average_salary": "e.g. $95,000", "max_salary": "e.g. $180,000"}'
        )
        results = exa.answer(question)
        answer_text = getattr(results, "answer", None) or str(results)
        if not answer_text:
            return _fallback_realtime_data(role_name, "Exa answer returned empty")

        data = json.loads(clean_llm_json(answer_text))
        data["data_source"] = "realtime"
        return data
    except json.JSONDecodeError as e:
        return _fallback_realtime_data(role_name, f"Invalid JSON from Exa: {e}")
    except Exception as e:
        return _fallback_realtime_data(role_name, str(e))


def _fallback_realtime_data(role_name: str, reason: str) -> dict:
    """Fallback when Exa/LLM fails - use reasonable defaults based on role."""
    return {
        "training_skills": [
            "Communication", "Problem Solving", "Technical Skills",
            "Data Analysis", "Project Management", "Continuous Learning"
        ],
        "growth_rate": "N/A",
        "total_jobs": "N/A",
        "starting_salary": "N/A",
        "average_salary": "N/A",
        "max_salary": "N/A",
        "data_source": "static",
        "fallback_reason": reason,
    }
