"""
3-layer resource cache service for learning path YouTube/Tavily results.

  L0 — In-memory dict (bounded, 1-hour TTL, ~0ms)
  L1 — ResourceCache DB table (30-day TTL, PGVector similarity search)
  L2 — Live API call (YouTube / Tavily, seconds)

Cross-user, cross-restart persistence via L1 semantic search. L0 avoids DB 
round-trips for repeat queries within the same server process.
"""

import json
import hashlib
import time
from datetime import datetime, timedelta, timezone
from sqlalchemy import text

from config import RESOURCE_CACHE_TTL_DAYS
from rag.vector_cache import get_embeddings_batch, _vec_literal

# ── L0: In-memory cache ─────────────────────────────────────────────────────
# Bounded dict: cache_key → (timestamp, resources)
_L0_CACHE: dict[str, tuple[float, list[dict]]] = {}
_L0_MAX_SIZE = 256
_L0_TTL_SECONDS = 3600  # 1 hour

def _l0_get(cache_key: str) -> list[dict] | None:
    entry = _L0_CACHE.get(cache_key)
    if entry is None:
        return None
    ts, resources = entry
    if time.time() - ts > _L0_TTL_SECONDS:
        del _L0_CACHE[cache_key]
        return None
    return resources


def _l0_put(cache_key: str, resources: list[dict]) -> None:
    if len(_L0_CACHE) >= _L0_MAX_SIZE:
        oldest_key = min(_L0_CACHE, key=lambda k: _L0_CACHE[k][0])
        del _L0_CACHE[oldest_key]
    _L0_CACHE[cache_key] = (time.time(), resources)


def _l0_put_batch(entries: dict[str, list[dict]]) -> None:
    for key, resources in entries.items():
        _l0_put(key, resources)

# ── Key builder ──────────────────────────────────────────────────────────────

def _build_cache_key(source_type: str, query: str, language: str, target_role: str) -> str:
    """SHA-256 for exact in-memory caching."""
    payload = f"{source_type.lower().strip()}:{query.lower().strip()}:{language.lower().strip()}:{target_role.lower().strip()}"
    return hashlib.sha256(payload.encode()).hexdigest()

# ── Batch operations ─────────────────────────────────────────────────────────

def batch_get_cached_resources(
    items: list[tuple[str, str, str, str]],
) -> dict[str, list[dict] | None]:
    """Look up many (source_type, query, language, role) tuples.

    L0 (in-memory) is checked first via exact hash.
    L1 (DB) is checked via PGVector Semantic similarity.
    Returns a dict keyed by cache_key → resource list (hit) or None (miss).
    """
    if not items:
        return {}

    # Map items by cache_key
    keys = {_build_cache_key(st, q, lang, role): (st, q, lang, role) for st, q, lang, role in items}
    result: dict[str, list[dict] | None] = {}
    l1_needed_keys: dict[str, tuple[str, str, str, str]] = {}

    # ── L0 pass (Exact string match memory)
    l0_hits = 0
    for cache_key, (st, q, lang, role) in keys.items():
        l0_result = _l0_get(cache_key)
        if l0_result is not None:
            result[cache_key] = l0_result
            l0_hits += 1
        else:
            result[cache_key] = None
            l1_needed_keys[cache_key] = (st, q, lang, role)

    if l0_hits:
        print(f"[ResourceCache] L0 in-memory: {l0_hits} hits")

    if not l1_needed_keys:
        return result

    # ── L1 pass (Vector DB)
    missing_queries = [q for _, q, _, _ in l1_needed_keys.values()]
    try:
        # 1. Fetch embeddings for all missing queries
        embeddings = get_embeddings_batch(missing_queries)
        
        from db.database import SessionLocal
        cutoff = datetime.now(timezone.utc) - timedelta(days=RESOURCE_CACHE_TTL_DAYS)
        db = SessionLocal()
        
        try:
            l0_new_entries: dict[str, list[dict]] = {}
            parts = []
            params = {"cutoff": cutoff}
            
            # Use UNION ALL to search all queries semantically in one DB trip
            for idx, (cache_key, (st, _, lang, role)) in enumerate(l1_needed_keys.items()):
                vec_str = _vec_literal(embeddings[idx])
                
                ck_k = f"ck_{idx}"
                q_k = f"q_{idx}"
                st_k = f"st_{idx}"
                lang_k = f"lang_{idx}"
                role_k = f"role_{idx}"
                
                params[ck_k] = cache_key
                params[q_k] = missing_queries[idx]
                params[st_k] = st.lower().strip()
                params[lang_k] = lang.lower().strip() or "english"
                params[role_k] = role
                
                parts.append(
                    f"(SELECT :{ck_k} AS c_key, :{q_k} as og_query, resources, "
                    f"1 - (query_embedding <=> '{vec_str}'::vector) AS sim "
                    f"FROM resource_cache "
                    f"WHERE target_role = :{role_k} AND source_type = :{st_k} "
                    f"AND language = :{lang_k} AND created_at >= :cutoff "
                    f"AND 1 - (query_embedding <=> '{vec_str}'::vector) >= 0.86 "
                    f"ORDER BY query_embedding <=> '{vec_str}'::vector LIMIT 1)"
                )
            
            if parts:
                rows = db.execute(text("\nUNION ALL\n".join(parts)), params).fetchall()
                
                for row_c_key, og_q, resources_json, sim in rows:
                    res_obj = json.loads(resources_json)
                    result[row_c_key] = res_obj
                    l0_new_entries[row_c_key] = res_obj
                    print(f"[ResourceCache] PgVector HIT sim={sim:.3f} '{og_q[:50]}' ({len(res_obj)} res)")

                # Promote L1 vector hits to exact L0 cache hits for faster lookup next time
                if l0_new_entries:
                    _l0_put_batch(l0_new_entries)

            miss_count = sum(1 for v in result.values() if v is None)
            total_hits = len(result) - miss_count
            db_hits = total_hits - l0_hits
            
            if miss_count:
                print(f"[ResourceCache] Batch: {total_hits} hits ({l0_hits} L0 + {db_hits} DB), {miss_count} misses")
            elif l0_hits < len(keys):
                print(f"[ResourceCache] Batch: {total_hits} hits ({l0_hits} L0 + {db_hits} DB), 0 misses")

        finally:
            db.close()
    except Exception as e:
        print(f"[ResourceCache] batch lookup error: {e}")

    return result


def batch_store_cached_resources(
    items: list[tuple[str, str, str, str, list[dict]]],
) -> None:
    """Store many (source_type, query, language, role, resources) in ONE DB session."""
    storable = [(st, q, lang, role, res) for st, q, lang, role, res in items if res]
    if not storable:
        return

    # L0 put immediately
    for st, q, lang, role, resources in storable:
        cache_key = _build_cache_key(st, q, lang, role)
        _l0_put(cache_key, resources)

    try:
        from db.database import SessionLocal
        
        # Batch generate embeddings
        queries_to_embed = [q for _, q, _, _, _ in storable]
        embeddings = get_embeddings_batch(queries_to_embed)

        db = SessionLocal()
        try:
            for idx, (st, q, lang, role, resources) in enumerate(storable):
                cache_key = _build_cache_key(st, q, lang, role)
                vec_str = _vec_literal(embeddings[idx])
                
                # Delete exact old exact matches if overriding
                db.execute(text("DELETE FROM resource_cache WHERE cache_key = :ck"), {"ck": cache_key})
                
                db.execute(
                    text(f"""
                        INSERT INTO resource_cache 
                        (cache_key, query_text, source_type, language, target_role, resources, query_embedding, created_at)
                        VALUES (:ck, :qt, :st, :lang, :role, :res, '{vec_str}'::vector, NOW())
                    """),
                    {
                        "ck": cache_key,
                        "qt": q,
                        "st": st.lower().strip(),
                        "lang": lang.lower().strip() or "english",
                        "role": role,
                        "res": json.dumps(resources)
                    }
                )

            db.commit()
            print(f"[ResourceCache] BATCH STORE {len(storable)} entries (L0 + Vector DB)")
        except Exception as e:
            db.rollback()
            print(f"[ResourceCache] batch store error: {e}")
        finally:
            db.close()
    except Exception as e:
        print(f"[ResourceCache] batch embed/store error: {e}")
