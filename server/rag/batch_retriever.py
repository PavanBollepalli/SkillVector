import asyncio
import time
from rag.retriever import retrieve_web_context, retrieve_articles, retrieve_videos
from rag.vector_cache import get_embeddings_batch, search_by_embeddings_batch, store_entry

# In-process cache for complete batch context strings.
# Key: (normalized_role, sorted_queries_tuple) — partitioned by role so
# different roles never share cached context even with identical queries.
# On a full cache-hit scenario (same role → same queries every time),
# this skips ALL embedding and DB work after the first call: ~0ms.
_context_cache: dict[tuple, str] = {}
_MAX_CONTEXT_CACHE = 128


async def batch_retrieve(queries: list[str], max_sources: int = 20, language: str = "English", target_role: str = "") -> str:
    """
    Three-layer retrieval:
      L0 - In-memory context cache  (~0ms)
      L1 - pgvector semantic cache  (filter by role + language, similarity >= 0.86)
      L2 - Tavily/YouTube live fetch (only for cache misses)

    Partitioned by (role, language); if >= 8 sources found in L1, return cached.
    """
    t0 = time.time()

    role_key = target_role.strip().lower()
    lang_key = language.strip().lower() or "english"

    # L0: in-memory hit
    cache_key = (role_key, lang_key, tuple(sorted(queries)))
    if cache_key in _context_cache:
        print(f"[batch_retrieve] L0 in-memory HIT in {time.time() - t0:.3f}s")
        return _context_cache[cache_key]

    # Step 1: embed all queries
    t1 = time.time()
    embeddings: list[list[float]] = await asyncio.to_thread(get_embeddings_batch, queries)
    print(f"[batch_retrieve] step1 embed  ({len(queries)} queries): {time.time() - t1:.3f}s")

    # Step 2: vector-DB search filtered by role + language
    t2 = time.time()
    results: list[list[dict] | None] = await asyncio.to_thread(
        search_by_embeddings_batch, embeddings, target_role, language
    )
    cache_hits = sum(1 for r in results if r is not None)
    miss_indices = [i for i, r in enumerate(results) if r is None]
    print(
        f"[batch_retrieve] step2 vector-DB ({cache_hits}/{len(queries)} hits, "
        f"{len(miss_indices)} misses): {time.time() - t2:.3f}s"
    )

    # Step 3: Tavily/YouTube for cache misses
    if miss_indices:
        t3 = time.time()
        fresh_results = await asyncio.gather(
            *[retrieve_web_context(queries[i], language=language) for i in miss_indices],
            return_exceptions=True,
        )
        print(f"[batch_retrieve] step3 Tavily/YT ({len(miss_indices)} calls): {time.time() - t3:.3f}s")

        # Step 4: store all fresh results (including language-specific) with role + language
        store_tasks = []
        for pos, i in enumerate(miss_indices):
            fresh = fresh_results[pos]
            if isinstance(fresh, Exception) or not fresh:
                results[i] = []
            else:
                results[i] = fresh
                store_tasks.append(
                    asyncio.to_thread(
                        store_entry, queries[i], embeddings[i], fresh, target_role, language
                    )
                )
        if store_tasks:
            t4 = time.time()
            await asyncio.gather(*store_tasks, return_exceptions=True)
            print(f"[batch_retrieve] step4 store   ({len(store_tasks)} entries): {time.time() - t4:.3f}s")

    # Step 5: deduplicate and build context string
    seen: set[str] = set()
    collected: list[dict] = []

    for result in results:
        for r in (result or []):
            url = r.get("url", "")
            if url and url not in seen:
                seen.add(url)
                collected.append(r)
            if len(collected) >= max_sources:
                break
        if len(collected) >= max_sources:
            break

    context = ""
    for r in collected:
        context += f"""
Title: {r['title']}
URL: {r['url']}
Content: {r['content']}
"""
    context = context.strip()

    # Store in in-memory cache (evict oldest if at capacity)
    if len(_context_cache) >= _MAX_CONTEXT_CACHE:
        del _context_cache[next(iter(_context_cache))]
    _context_cache[cache_key] = context

    elapsed = time.time() - t0
    print(
        f"[batch_retrieve] DONE {len(collected)} sources in {elapsed:.3f}s "
        f"({cache_hits}/{len(queries)} DB hits, {len(miss_indices)} live fetch)"
    )
    return context


# ── L0 cache for per-phase structured resources ─────────────────────────
_phase_resource_cache: dict[tuple, list[list[dict]]] = {}
_MAX_PHASE_CACHE = 64


async def batch_retrieve_phase_resources(
    phase_queries: dict[int, dict],
    language: str = "English",
    target_role: str = "",
    yt_concurrency: int = 5,
) -> list[list[dict]]:
    """Three-layer retrieval for per-phase learning resources.

      L0 — In-memory cache              (~0ms, keyed by role + language + all queries)
      L1 — ResourceCache DB (pgvector)   (semantic similarity >= 0.86, one UNION ALL)
      L2 — Tavily / YouTube live fetch   (only for L1 misses, parallelised)

    Returns a list of resource lists — one per phase index in phase_queries.
    Each resource is a dict: {type, title, platform, link}.
    """
    t0 = time.time()
    num_phases = max(phase_queries.keys(), default=-1) + 1
    if num_phases == 0:
        return []

    role_key = target_role.strip().lower()
    lang_key = language.strip().lower() or "english"

    # ── L0: full in-memory cache ─────────────────────────────────────────
    all_queries_flat: list[str] = []
    for pi in range(num_phases):
        pq = phase_queries.get(pi, {})
        all_queries_flat.extend(pq.get("web_queries", []))
        all_queries_flat.extend(pq.get("youtube_queries", []))

    l0_key = (role_key, lang_key, tuple(sorted(all_queries_flat)))
    if l0_key in _phase_resource_cache:
        print(f"[batch_retriever] L0 phase-resource HIT in {time.time() - t0:.3f}s")
        return _phase_resource_cache[l0_key]

    # ── Flatten all queries with metadata ────────────────────────────────
    from services.resource_cache_service import (
        _build_cache_key,
        batch_get_cached_resources,
        batch_store_cached_resources,
    )

    # (phase_idx, task_type, source_type, query, language, role)
    query_items: list[tuple[int, str, str, str, str, str]] = []
    for phase_idx in range(num_phases):
        queries = phase_queries.get(phase_idx, {})
        for wq in queries.get("web_queries", []):
            query_items.append((phase_idx, "web", "tavily", wq, "english", target_role))
        for yq in queries.get("youtube_queries", []):
            query_items.append((phase_idx, "yt", "youtube", yq, language, target_role))

    # ── L1: ResourceCache DB lookup (one batch UNION ALL query) ──────────
    t1 = time.time()
    cache_lookup_items = [(st, q, lang, role) for _, _, st, q, lang, role in query_items]
    cached_results = await asyncio.to_thread(batch_get_cached_resources, cache_lookup_items)
    l1_hits = sum(1 for v in cached_results.values() if v is not None)
    l1_misses = len(query_items) - l1_hits
    print(
        f"[batch_retriever] L1 DB: {l1_hits}/{len(query_items)} hits, "
        f"{l1_misses} misses in {time.time() - t1:.3f}s"
    )

    # ── L2: Live API calls for cache misses only ─────────────────────────
    yt_semaphore = asyncio.Semaphore(yt_concurrency)

    async def _fetch_articles(q: str) -> list[dict]:
        return await retrieve_articles(q, max_results=2)

    async def _fetch_videos(q: str, lang: str) -> list[dict]:
        async with yt_semaphore:
            return await retrieve_videos(q, language=lang, max_results=2)

    # Each entry is either a cached list (L1 hit) or an asyncio future (L2 dispatch)
    task_map: list[tuple[int, str, str, str, str, str, object]] = []

    for phase_idx, task_type, source_type, query, lang, role in query_items:
        cache_key = _build_cache_key(source_type, query, lang, role)
        hit = cached_results.get(cache_key)

        if hit is not None:
            task_map.append((phase_idx, task_type, source_type, query, lang, role, hit))
        else:
            if task_type == "web":
                future = asyncio.ensure_future(_fetch_articles(query))
            else:
                future = asyncio.ensure_future(_fetch_videos(query, lang))
            task_map.append((phase_idx, task_type, source_type, query, lang, role, future))

    # Await only live fetches (cached entries resolve instantly)
    live_tasks = [entry[6] for entry in task_map if asyncio.isfuture(entry[6])]
    if live_tasks:
        t2 = time.time()
        await asyncio.gather(*live_tasks, return_exceptions=True)
        print(f"[batch_retriever] L2 live fetch: {len(live_tasks)} calls in {time.time() - t2:.3f}s")

    # ── Collect per-phase results + store fresh fetches to L1 ────────────
    all_phase_resources: list[list[dict]] = [[] for _ in range(num_phases)]
    to_store: list[tuple[str, str, str, str, list[dict]]] = []

    for phase_idx, task_type, source_type, query, lang, role, result_or_future in task_map:
        try:
            if asyncio.isfuture(result_or_future):
                result = result_or_future.result()
                if result:
                    to_store.append((source_type, query, lang, role, result))
            else:
                result = result_or_future

            if result:
                all_phase_resources[phase_idx].extend(result)
        except Exception as e:
            print(f"[batch_retriever] Phase {phase_idx} {task_type} error: {e}")

    # ONE batch DB write for all fresh results
    if to_store:
        t3 = time.time()
        await asyncio.to_thread(batch_store_cached_resources, to_store)
        print(f"[batch_retriever] L1 store: {len(to_store)} entries in {time.time() - t3:.3f}s")

    # Per-phase summary
    for pi in range(num_phases):
        resources = all_phase_resources[pi]
        vid_count = len([r for r in resources if r.get("type") == "Video"])
        print(f"[batch_retriever] Phase {pi}: {len(resources)} resources ({vid_count} videos)")

    # ── Update L0 cache ──────────────────────────────────────────────────
    if len(_phase_resource_cache) >= _MAX_PHASE_CACHE:
        del _phase_resource_cache[next(iter(_phase_resource_cache))]
    _phase_resource_cache[l0_key] = all_phase_resources

    elapsed = time.time() - t0
    total = sum(len(r) for r in all_phase_resources)
    live_count = len(live_tasks) if live_tasks else 0
    print(
        f"[batch_retriever] DONE {total} resources across {num_phases} phases "
        f"in {elapsed:.3f}s ({l1_hits} L1 hits, {live_count} L2 fetches)"
    )
    return all_phase_resources


if __name__ == "__main__":
    async def _test():
        queries = [
            "data scientist roadmap",
            "best python libraries for data science",
            "how to learn machine learning",
        ]
        context = await batch_retrieve(queries, max_sources=10)
        print(context[:500])

    asyncio.run(_test())