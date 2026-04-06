"""
Vector cache for RAG web sources using pgvector + Mistral embeddings.

Flow per query in batch_retrieve:
  1. Embed the query via Mistral `mistral-embed` (1024-dim)
  2. Cosine-search rag_source_cache for similar past queries (threshold >= 0.86)
  3. Filter by target_role and language; order by similarity
  4. If >= 8 unique source URLs found → return cached sources (no Tavily/YouTube call)
  5. Else → caller fetches via Tavily/YouTube, then calls store_entry() to persist
"""

import os
import json
from datetime import datetime, timedelta, timezone
from mistralai.client import Mistral
from sqlalchemy import text
from dotenv import load_dotenv

load_dotenv()

# ── Config ────────────────────────────────────────────────────────────────────
SIMILARITY_THRESHOLD = 0.86         # cosine similarity floor (0.86 increases cache hit rate)
MIN_SOURCES_FOR_HIT = 8             # minimum unique URLs needed to skip live fetch
SOURCE_CACHE_TTL_DAYS = 30          # discard entries older than this
QUERY_PLAN_TTL_DAYS = 30            # same TTL for query-plan cache (query_planner.py uses this)

# ── Mistral client (lazy singleton) ──────────────────────────────────────────
_mistral_client: Mistral | None = None


def _client() -> Mistral:
    global _mistral_client
    if _mistral_client is None:
        api_key = os.getenv("MISTRAL_API_KEY")
        if not api_key:
            raise RuntimeError("MISTRAL_API_KEY is not set in environment")
        _mistral_client = Mistral(api_key=api_key)
    return _mistral_client

def _get_db():
    """Create a fresh isolated session owned by the cache module."""
    from db.database import SessionLocal
    return SessionLocal()

# ── Public helpers ────────────────────────────────────────────────────────────

# In-process embedding cache: query string → 1024-dim vector.
# Plain dict (not @lru_cache) so get_embeddings_batch can inspect membership
# before deciding which texts to batch-send to Mistral.
_embedding_cache: dict[str, list[float]] = {}


def get_embedding(text_str: str) -> list[float]:
    """Return the Mistral embedding for text_str, cached in _embedding_cache.
    Blocking/sync — wrap in asyncio.to_thread() from async code.
    """
    if text_str not in _embedding_cache:
        resp = _client().embeddings.create(model="mistral-embed", inputs=[text_str])
        _embedding_cache[text_str] = resp.data[0].embedding
    return _embedding_cache[text_str]


def get_embeddings_batch(texts: list[str]) -> list[list[float]]:
    """Embed *all* texts in ONE Mistral API call (only for cache misses).

    Texts already in _embedding_cache are returned instantly.
    New texts are sent to Mistral in a single batched request, then cached.
    Blocking/sync — call via asyncio.to_thread() from async code.
    """
    miss_indices = [i for i, t in enumerate(texts) if t not in _embedding_cache]
    if miss_indices:
        miss_texts = [texts[i] for i in miss_indices]
        resp = _client().embeddings.create(model="mistral-embed", inputs=miss_texts)
        for pos, i in enumerate(miss_indices):
            _embedding_cache[texts[i]] = resp.data[pos].embedding
    return [_embedding_cache[t] for t in texts]


def _vec_literal(embedding: list[float]) -> str:
    """Format a float list as a pgvector literal string: '[v1,v2,...]'"""
    return "[" + ",".join(f"{v:.8f}" for v in embedding) + "]"


def _normalize_lang(language: str) -> str:
    """Normalize language for DB storage/lookup. English variants -> 'english'."""
    if not language or not language.strip():
        return "english"
    low = language.strip().lower()
    return "english" if low in ("english", "en") else low


def search_by_embedding(
    embedding: list[float], target_role: str = "", language: str = "English"
) -> list[dict] | None:
    """Search rag_source_cache for sources under semantically similar queries.

    Filters by target_role and language; orders by similarity (best first).
    Returns merged, deduplicated sources if >= MIN_SOURCES_FOR_HIT unique URLs
    are found within TTL, else None (cache miss).
    """
    db = _get_db()
    try:
        cutoff = datetime.now(timezone.utc) - timedelta(days=SOURCE_CACHE_TTL_DAYS)
        vec_str = _vec_literal(embedding)
        lang_key = _normalize_lang(language)

        rows = db.execute(
            text(f"""
                SELECT sources
                FROM rag_source_cache
                WHERE target_role = :role
                  AND language = :lang
                  AND created_at >= :cutoff
                  AND 1 - (query_embedding <=> '{vec_str}'::vector) >= :threshold
                ORDER BY query_embedding <=> '{vec_str}'::vector
                LIMIT 10
            """),
            {
                "role": target_role,
                "lang": lang_key,
                "threshold": SIMILARITY_THRESHOLD,
                "cutoff": cutoff,
            },
        ).fetchall()

        if not rows:
            return None

        seen_urls: set[str] = set()
        merged: list[dict] = []
        for (sources_json,) in rows:
            for src in json.loads(sources_json):
                url = src.get("url", "")
                if url and url not in seen_urls:
                    seen_urls.add(url)
                    merged.append(src)

        return merged if len(merged) >= MIN_SOURCES_FOR_HIT else None

    except Exception as e:
        print(f"[vector_cache] search error: {e}")
        return None
    finally:
        db.close()


def search_by_embeddings_batch(
    embeddings: list[list[float]],
    target_role: str = "",
    language: str = "English",
) -> list[list[dict] | None]:
    """Search all N embeddings in ONE DB round-trip using UNION ALL.

    Filters by target_role and language; orders by similarity (best first).
    Returns list[N]: each element is a list of source dicts (hit) or None (miss).
    """
    if not embeddings:
        return []
    db = _get_db()
    try:
        cutoff = datetime.now(timezone.utc) - timedelta(days=SOURCE_CACHE_TTL_DAYS)
        lang_key = _normalize_lang(language)

        parts = []
        for idx, emb in enumerate(embeddings):
            vec = _vec_literal(emb)
            parts.append(
                f"(SELECT {idx} AS idx, sources "
                f"FROM rag_source_cache "
                f"WHERE target_role = :role "
                f"  AND language = :lang "
                f"  AND created_at >= :cutoff "
                f"  AND 1 - (query_embedding <=> '{vec}'::vector) >= :threshold "
                f"ORDER BY query_embedding <=> '{vec}'::vector "
                f"LIMIT 10)"
            )

        rows = db.execute(
            text("\nUNION ALL\n".join(parts)),
            {
                "role": target_role,
                "lang": lang_key,
                "cutoff": cutoff,
                "threshold": SIMILARITY_THRESHOLD,
            },
        ).fetchall()

        grouped: dict[int, list[dict]] = {i: [] for i in range(len(embeddings))}
        seen_per_idx: dict[int, set[str]] = {i: set() for i in range(len(embeddings))}
        for (idx, sources_json) in rows:
            for src in json.loads(sources_json):
                url = src.get("url", "")
                if url and url not in seen_per_idx[idx]:
                    seen_per_idx[idx].add(url)
                    grouped[idx].append(src)

        return [
            grouped[i] if len(grouped[i]) >= MIN_SOURCES_FOR_HIT else None
            for i in range(len(embeddings))
        ]

    except Exception as e:
        print(f"[vector_cache] batch search error: {e}")
        return [None] * len(embeddings)
    finally:
        db.close()


def store_entry(
    query: str,
    embedding: list[float],
    sources: list[dict],
    target_role: str = "",
    language: str = "English",
) -> None:
    """Persist query, embedding, role, language, and sources to rag_source_cache."""
    if not sources:
        return
    db = _get_db()
    try:
        vec_str = _vec_literal(embedding)
        lang_key = _normalize_lang(language)
        db.execute(
            text(f"""
                INSERT INTO rag_source_cache (query_text, query_embedding, sources, target_role, language, created_at)
                VALUES (:query, '{vec_str}'::vector, :sources, :role, :lang, NOW())
            """),
            {
                "query": query,
                "sources": json.dumps(sources),
                "role": target_role,
                "lang": lang_key,
            },
        )
        db.commit()
    except Exception as e:
        db.rollback()
        print(f"[vector_cache] store error: {e}")
    finally:
        db.close()
