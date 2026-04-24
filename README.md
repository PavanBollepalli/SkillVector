<div align="center">

# SkillVector

### AI-Powered Career Intelligence Platform

*Personalized learning paths through a two-stage RAG pipeline, hybrid vector search, real-time market analytics (O\*NET + Exa), multi-model LLM orchestration, and cross-user semantic caching.*

<br/>

<img src="https://img.shields.io/badge/Next.js-16.1-black?logo=next.js" />
<img src="https://img.shields.io/badge/React-19-61DAFB?logo=react" />
<img src="https://img.shields.io/badge/FastAPI-0.134-009688?logo=fastapi" />
<img src="https://img.shields.io/badge/PostgreSQL-pgvector-4169E1?logo=postgresql" />
<img src="https://img.shields.io/badge/Llama%203.3-70B-purple" />
<img src="https://img.shields.io/badge/Mistral-Embed-FF7000" />
<img src="https://img.shields.io/badge/O*NET-29.0-orange" />
<img src="https://img.shields.io/badge/Exa-Answer%20API-blue" />
<img src="https://img.shields.io/badge/YouTube-Data%20API%20v3-FF0000?logo=youtube" />
<img src="https://img.shields.io/badge/Three.js-0.182-black?logo=three.js" />

</div>

---

## What is SkillVector?

SkillVector is not another course recommendation engine. It is a **full-stack AI career intelligence system** that analyzes a user's current skill set, maps it against real labor market data from the U.S. Department of Labor **and real-time web intelligence via Exa**, and generates a deeply personalized, multi-phase learning roadmap — grounded in real web sources, not hallucinated content.

**The core differentiator:** Every learning path is built through a **two-stage production RAG pipeline** that retrieves, caches, and ranks live web resources using **hybrid vector search** (pgvector HNSW + metadata filtering), orchestrates multiple LLM providers to produce week-by-week plans calibrated to the user's proficiency levels, and **shares cached results across users** — so the second user targeting the same role gets results in milliseconds, not minutes.

---

## System Architecture

![System Architecture](assets/system_architecture_diagram.png)

| Layer | Stack | Role |
|-------|-------|------|
| **Frontend** | Next.js 16, React 19, TypeScript, Tailwind v4, React Three Fiber | SPA with 3D landing, profile dashboard, learning viewer, market insights, admin panel |
| **Backend** | FastAPI, Python 3.13, SQLAlchemy 2.0, Pydantic v2 | REST API with JWT auth, two-stage RAG pipeline, LLM orchestration, O\*NET + Exa integration |
| **Data** | PostgreSQL + pgvector (Supabase), HNSW indexes | 18 tables, hybrid vector search, multi-layer TTL caching, advisory locks |
| **AI / LLM** | Groq (Llama 3.3 70B, Llama 3.1 8B, Compound), Mistral (embeddings) | Multi-model orchestration with intelligent fallbacks and model-specific routing |
| **Web Search** | Tavily (articles/docs), YouTube Data API v3 (videos/playlists) | Dual-channel retrieval with language-aware routing |
| **Market Data** | O\*NET 29.0 (5 datasets, 1,000+ occupations) + Exa Answer API | SOC code matching, Hot Technology extraction, real-time salary/demand/growth data |

---

## Core Features

### 1. Two-Stage RAG Learning Path Generation

![RAG Pipeline](assets/rag_pipeline.png)

The path generation pipeline runs in **four sequential stages**, each with its own caching layer:

```
STAGE 1 → LLM generates path structure (phases, topics, skills, weekly breakdown, projects)
STAGE 2 → Phase Query Generator creates targeted search queries per phase (cross-user cached)
STAGE 3 → Batch Retriever fetches resources via 3-layer cache (L0 → L1 → L2)
STAGE 4 → Resources attached to phases with type-aware deduplication
```

#### Stage 1: Path Structure Generation (Llama 3.3 70B)

The LLM receives a rich context prompt built from:
- **O\*NET occupational data** — knowledge domains, work activities, required technologies (Hot Tech prioritized)
- **Exa real-time market skills** — training skills, growth rates, salary data from BLS/Indeed/Glassdoor
- **User profile** — skills with proficiency levels, education, career status, timeline, language preference
- **Mandatory skills distribution** — combined O\*NET + Exa skills are enforced across all phases

The output is a structured JSON learning path with empty resource arrays (populated in Stage 3).

#### Stage 2: Phase Query Generator (Llama 3.1 8B Instant)

A fast, lightweight LLM generates two query arrays per phase:

| Array | Target | Language |
|-------|--------|----------|
| `web_queries` (4–6) | Tavily API — articles, tutorials, docs, books | Always English |
| `youtube_queries` (3–5) | YouTube Data API — videos, playlists | English + user's language |

**Cross-user caching:** Queries are cached by `SHA-256(role + sorted_skills + language)` in the `QueryPlanCache` table. If another user targets the same role with the same skills, the cached queries are returned instantly — no LLM call, and downstream ResourceCache also hits.

#### Stage 3: Three-Layer Batch Retriever

```
L0  In-Memory Cache       →  ~0ms    (dict keyed by role + language + sorted queries)
L1  ResourceCache (pgvector)  →  1 DB UNION ALL  (semantic similarity ≥ 0.86 + metadata filter)
L2  Tavily + YouTube Live    →  only for L1 misses  (parallelized via asyncio.gather)
```

| Component | What it does |
|-----------|-------------|
| `batch_retriever.py` | Orchestrates L0/L1/L2 for both context retrieval and per-phase resource retrieval |
| `resource_cache_service.py` | Batch pgvector semantic search with `UNION ALL` — one DB round-trip for N queries |
| `retriever.py` | Tavily web search + YouTube Data API v3 with language-aware routing |
| `vector_cache.py` | Mistral embeddings (1024-dim) + pgvector HNSW hybrid search |

**YouTube Data API Integration:**
- Fetches both **individual videos** (excluding Shorts via `videoDuration=medium|long`) and **curated playlists**
- Supports **40+ languages** via `relevanceLanguage` parameter (Indian, European, Asian, Middle Eastern, African)
- Concurrent fetching of medium (4–20 min) and long (>20 min) videos to maximize quality

#### Stage 4: Resource Attachment

Resources are split by type (articles/books vs. videos/playlists), deduplicated by URL across all phases, and interleaved — up to 5 articles + 5 videos per phase.

**Concurrency Safety:** `pg_try_advisory_xact_lock()` prevents duplicate path generation when React StrictMode double-fires requests. If a lock is held, the second request polls every 500ms for up to 30 seconds until the first request commits.

---

### 2. O\*NET + Exa Real-Time Market Intelligence

![Market Insights](assets/market_insights.png)

A **dual-source market analysis engine** combining government labor data with real-time web intelligence:

#### O\*NET 29.0 (U.S. Department of Labor)

5 datasets loaded at startup into memory:

| Dataset | Usage |
|---------|-------|
| Occupation Data (1,000+ occupations) | SOC code matching via fuzzy search + known tech role mappings |
| Technology Skills | Hot Technology & In Demand skill extraction, autocomplete suggestions |
| Core Skills | Skill gap computation |
| Knowledge Domains | Knowledge area requirements for LLM prompt enrichment |
| Work Activities | Activity-level matching for LLM prompt enrichment |

**Pipeline:** User's desired role → fuzzy match to SOC code (confidence thresholds: 0.7 high, 0.55 medium + cross-domain validation) → extract required tech skills (Hot Technology prioritized) → compute skill gap → LLM generates salary/demand/growth analysis → results cached with TTL.

For roles not in O\*NET, the system falls back to LLM-based skill extraction.

#### Exa Answer API (Real-Time Market Data)

Live job market intelligence from BLS, Indeed, Glassdoor, and Payscale:

| Data Point | Source |
|------------|--------|
| Training skills (trending) | Exa web analysis |
| Growth rate | BLS projections |
| Total jobs | Job board aggregation |
| Salary range (starting/avg/max) | Payscale + Glassdoor |

**Three-layer caching for Exa data:**

```
L0  In-Memory (5 min TTL, max 128 entries)   →  ~0ms
L1  ExaMarketCache DB table (15-day TTL)      →  1 DB query
L2  Live Exa API call                          →  only for cache misses
```

**Skills Merging:** Exa real-time skills are merged with O\*NET required skills (Exa first, deduplicated) to ensure the learning path covers both established and trending technologies.

#### Role Context Cache

O\*NET lookup + Exa data + extracted skills are combined into a single `role_context` string and cached in the `RoleContextCache` table (15-day TTL). This means the expensive O\*NET + Exa pipeline runs once per role, not once per user.

---

### 3. Adaptive Test & Progression System

- LLM generates **15 MCQs per phase** (5 Easy / 5 Medium / 5 Hard)
- **Server-side answer storage** — correct answers never sent to the frontend; scoring happens on the backend
- **70% passing threshold** — passing unlocks the next phase
- **Auto-skill integration** — on pass, phase skills are automatically added to the user's profile
- **Cache invalidation cascade** — market insights are recalculated after new skills are added
- Multiple attempts allowed; full test history tracked per user

---

### 4. Weekly Task Progress Tracking

- **Granular week-by-week** task completion within each phase
- Each week in a phase's `weekly_breakdown` maps to a `WeeklyTaskProgress` record
- Users can mark individual weeks as completed
- Progress is tracked per user/phase/week with timestamps
- Phase progress and weekly task progress are initialized atomically on path generation

---

### 5. Anti-Cheat Video Assignment System

- **Heartbeat verification** — frontend sends heartbeat every 5 seconds during playback
- **Seek-skip detection** — any jump >15 seconds beyond `max_position` triggers a cheat flag
- **Legitimate time tracking** — caps at 8s credit per 5s heartbeat interval
- Auto-completes at 90% legitimate watch time
- Admin dashboard shows per-user cheat flags and completion stats

---

### 6. AI Career Assistant

- Powered by **Groq Compound** model (built-in web search)
- **Context-aware** — injected with learning path phases, current skills, desired role, and active progress
- Maintains **conversation history** (last 10 messages)
- Returns answers with **web source citations**
- **Graceful fallback** — auto-switches to Llama 3.3 70B if Compound model fails

---

### 7. 3D Interactive Landing (SkillUniverse)

- Built with **React Three Fiber** + Drei + Postprocessing (WebGL)
- **Framer Motion** animated storytelling overlay (5-scene auto-advancing sequence)
- **Zustand** state management for scene transitions
- Responsive design with progressive enhancement

---

## Authentication & Security

![Authentication Flow](assets/auth_flow.png)

| Method | Description |
|--------|-------------|
| **Google OAuth 2.0** | Primary auth — server-side token verification via Google userinfo API, auto-creates account on first login |
| **Email/Password** | Traditional registration with bcrypt hashing |
| **JWT Sessions** | 7-day token expiry, `HS256` algorithm |
| **Password Reset** | 6-digit OTP via Gmail SMTP, 10-minute expiry, secure email delivery with TLS/SSL fallback |

---

## Profile System

![Profile Setup Wizard](assets/profile_wizard.png)

**3-Step Streamlined Wizard:**

| Step | Name | Fields |
|------|------|--------|
| 1 | **Career Profile** | Desired role (with O\*NET autocomplete), target industries, education level, current status, current role/industry, location |
| 2 | **Competence Matrix** | Skills with proficiency levels (beginner/intermediate/advanced), real-time autocomplete from O\*NET Technology Skills |
| 3 | **Learning Preferences** | Timeline, learning velocity, hours per week, instruction language |

**Profile Dashboard ("Mission Control"):**

| Widget | Purpose |
|--------|---------|
| Career North Star | Overall career readiness score |
| Role Radar | 4-axis chart: salary, demand, skill match, growth potential |
| Skill DNA Matrix | Visual grid of current skills with proficiency |
| Reality Gap Bridge | Missing skills visualization with priority ranking |
| Action Command | Floating command bar for quick actions |

---

## Database Schema

![Database Schema](assets/database_schema.png)

**18 tables** with production-grade indexing:

| Table | Purpose | Key Indexes |
|-------|---------|-------------|
| `users` | Accounts (email, hashed password, admin flag) | B-tree on email, username |
| `password_resets` | OTP codes for password reset (6-digit, 10-min expiry) | B-tree on email |
| `user_profiles` | Skills (JSON), career goals, learning preferences | B-tree on user_id |
| `learning_paths` | AI-generated roadmaps (structured JSON) | B-tree on user_id |
| `phase_progress` | Per-phase unlock/completion/test tracking | Composite on (user_id, phase_index) |
| `weekly_task_progress` | Week-by-week learning task completion | Unique on (user_id, phase_index, week_number) |
| `test_attempts` | Full test history (score, answers, pass/fail) | Composite on (user_id, phase_index) |
| `active_tests` | Server-side question storage (anti-cheat) | Composite on (user_id, phase_index) |
| `video_assignments` | Admin-created video content | B-tree on id |
| `user_video_assignments` | Per-user video assignment links with due dates | FK on video_id, user_id |
| `video_progress` | Anti-cheat tracking (heartbeats, cheat flags) | Composite on (user_id, video_id) |
| `admin_activity_log` | Full audit trail of admin actions | B-tree on admin_id, created_at |
| `market_insights_cache` | TTL-based market analysis cache | Composite on (user_id, role) |
| `query_plan_cache` | Cross-user search query plan cache (SHA-256 keyed) | Unique B-tree on cache_key |
| `rag_source_cache` | Vector cache of web sources per query | **HNSW on query_embedding**, B-tree on target_role |
| `resource_cache` | Per-phase resource cache with pgvector | **HNSW on query_embedding**, B-tree on target_role, cache_key |
| `exa_market_cache` | Exa API real-time market data cache (15-day TTL) | Unique B-tree on role |
| `role_context_cache` | O\*NET + Exa role context cache (15-day TTL) | Unique B-tree on role |

---

## Multi-Layer Caching Architecture

SkillVector uses **five independent caching subsystems**, each with in-memory (L0) and database (L1) layers:

| Cache | L0 TTL | L1 TTL | L1 Store | Key Strategy |
|-------|--------|--------|----------|--------------|
| **RAG Source Cache** | 1 hour (process) | 30 days | pgvector HNSW (cosine ≥ 0.90) | role + query embedding |
| **Resource Cache** | 1 hour (256 max) | 30 days | pgvector HNSW (cosine ≥ 0.86) | SHA-256(type + query + lang + role) |
| **Query Plan Cache** | — | 30 days | PostgreSQL B-tree | SHA-256(role + skills + language) |
| **Exa Market Cache** | 5 min (128 max) | 15 days | PostgreSQL text | role name |
| **Role Context Cache** | 5 min (64 max) | 15 days | PostgreSQL text | role name |
| **Market Insights Cache** | — | 24 hours | PostgreSQL JSON | (user_id, role) |

All in-memory caches use bounded dicts with LRU eviction. All database caches use TTL-based expiry via `WHERE created_at >= cutoff` filters.

---

## Tech Stack

| Layer | Technology | Version |
|-------|------------|---------|
| **Frontend** | Next.js, React, TypeScript | 16.1, 19, 5.x |
| **Styling** | Tailwind CSS | 4.x |
| **3D / Animation** | Three.js, React Three Fiber, Framer Motion | 0.182, 9.5, 12.x |
| **State** | Zustand | 5.x |
| **Backend** | FastAPI, Python | 0.134, 3.13 |
| **ORM** | SQLAlchemy, Pydantic v2 | 2.0, 2.12 |
| **Database** | PostgreSQL + pgvector (Supabase) | 16 |
| **Vector Search** | pgvector with HNSW indexes | 0.3.6 |
| **Embeddings** | Mistral Embed (1024-dim) | 1.12 |
| **LLM** | Groq — Llama 3.3 70B, Llama 3.1 8B Instant, Groq Compound | 1.0 |
| **Web Search** | Tavily Search API | — |
| **Video Search** | YouTube Data API v3 | — |
| **Real-Time Market** | Exa Answer API (exa-py) | — |
| **Auth** | Google OAuth 2.0, JWT (python-jose), bcrypt | — |
| **Email** | Gmail SMTP (TLS/SSL) | — |
| **Market Data** | O\*NET (U.S. Dept. of Labor) | 29.0 |
| **PDF Export** | jsPDF + jspdf-autotable | 4.2, 5.0 |
| **Observability** | OpenTelemetry | 1.39 |

---

## API Reference

### Authentication
| Method | Endpoint | Description | Auth |
|--------|----------|-------------|------|
| POST | `/register` | Create account (email/password) | No |
| POST | `/login` | Login, returns JWT | No |
| POST | `/auth/google` | Google OAuth token exchange | No |
| POST | `/forgot-password` | Send 6-digit OTP to email | No |
| POST | `/verify-otp` | Verify password reset OTP | No |
| POST | `/reset-password` | Reset password after OTP verification | No |

### Profile & Market Analysis
| Method | Endpoint | Description | Auth |
|--------|----------|-------------|------|
| POST | `/userdetails` | Create/update user profile | JWT |
| GET | `/user-profile` | Get current user's profile | JWT |
| GET | `/profile/analysis` | O\*NET matching + skill gap + LLM market outlook | JWT |
| POST | `/profile-insights` | LLM-powered trending skills, salary, growth (TTL-cached) | JWT |
| GET | `/market-insights-test` | O\*NET + Exa real-time skill gap analysis | JWT |
| GET | `/suggestions/skills` | O\*NET Technology Skills autocomplete | JWT |
| GET | `/suggestions/roles` | O\*NET occupation title autocomplete | JWT |
| POST | `/add-skill` | Add skill to profile + invalidate caches | JWT |

### Learning Path
| Method | Endpoint | Description | Auth |
|--------|----------|-------------|------|
| GET | `/generate-path` | Two-stage RAG path generation (advisory-locked) | JWT |
| GET | `/phase-progress` | Get unlock/completion status for all phases | JWT |
| GET | `/phase-test/{idx}` | Generate 15 MCQs (answers stored server-side) | JWT |
| POST | `/submit-test` | Submit answers, score, unlock next phase on pass (≥70%) | JWT |
| POST | `/add-skill-and-regenerate-path` | Add skill + force path regeneration | JWT |
| GET | `/weekly-task-progress` | Get weekly task completion for a phase | JWT |
| GET | `/all-weekly-progress` | Get all weekly progress across all phases | JWT |
| PUT | `/weekly-task/{phase}/{week}` | Mark a specific week as completed | JWT |

### AI Assistant & Assignments
| Method | Endpoint | Description | Auth |
|--------|----------|-------------|------|
| POST | `/ai-assistant` | Context-aware AI chat with web search citations | JWT |
| GET | `/my-assignments` | Get assigned videos with progress | JWT |
| POST | `/video-progress/heartbeat` | Anti-cheat heartbeat (5s interval) | JWT |

### Admin (13 endpoints)
| Method | Endpoint | Description | Auth |
|--------|----------|-------------|------|
| POST | `/admin/login` | Admin login | No |
| GET | `/admin/analytics` | Full analytics dashboard data | Admin |
| GET | `/admin/users` | Paginated user list with search | Admin |
| GET | `/admin/users/{id}` | User deep-dive (profile, path, tests, videos) | Admin |
| PATCH | `/admin/users/{id}/toggle-active` | Activate/deactivate user | Admin |
| PATCH | `/admin/users/{id}/toggle-admin` | Grant/revoke admin role | Admin |
| POST | `/admin/videos` | Create video assignment | Admin |
| GET | `/admin/videos` | List all videos with stats | Admin |
| POST | `/admin/videos/{id}/assign` | Assign video to users | Admin |
| DELETE | `/admin/videos/{id}` | Delete video (cascade) | Admin |
| GET | `/admin/activity-log` | Paginated admin audit log | Admin |

---

## Project Structure

```
SkillVector/
├── frontend/                         # Next.js 16 + React 19
│   ├── app/
│   │   ├── (landing)/               # 3D landing page (React Three Fiber)
│   │   │   ├── _canvas/             # WebGL canvas components
│   │   │   ├── _components/         # Landing page sections
│   │   │   └── _hooks/              # Animation & scroll hooks
│   │   ├── (main)/
│   │   │   ├── login/               # Google OAuth + email login
│   │   │   ├── signup/              # Registration with onboarding
│   │   │   ├── forgot-password/     # OTP-based password reset
│   │   │   ├── profile/
│   │   │   │   ├── page.tsx         # Mission Control dashboard
│   │   │   │   └── setup/           # 3-step profile wizard
│   │   │   ├── learning-path/       # AI learning path viewer + phase tests
│   │   │   ├── market-insights/     # O*NET + Exa market intelligence + PDF export
│   │   │   ├── assignments/         # Video assignments with anti-cheat player
│   │   │   ├── documentation/       # Project documentation viewer
│   │   │   └── admin/               # Admin panel (analytics, users, videos, logs)
│   ├── components/
│   │   ├── SkillUniverse/           # Three.js 3D experience
│   │   ├── profile/                 # Dashboard widgets (North Star, Radar, DNA, Gap)
│   │   ├── profile-setup/           # Wizard steps (Step1Basic, Step2Skills, Step3Learning)
│   │   ├── market/                  # Skill gap charts, insight cards
│   │   ├── AIAssistant.tsx          # Floating AI chatbot
│   │   ├── TestModal.tsx            # MCQ test-taking modal
│   │   ├── TestResultModal.tsx      # Detailed test results with explanations
│   │   ├── AddSkillModal.tsx        # Add skill + regenerate path modal
│   │   ├── RoadmapSnapshot.tsx      # Visual roadmap overview
│   │   └── VideoPlayer.tsx          # YouTube player with heartbeat
│   └── lib/
│       ├── auth.ts                  # Auth utilities + token management
│       ├── types.ts                 # TypeScript interfaces
│       └── exportReport.ts          # PDF report generation (jsPDF)
│
├── server/                            # FastAPI backend
│   ├── main.py                       # App entry point + lifespan (O*NET loading)
│   ├── config.py                     # Centralized constants & TTL configuration
│   ├── auth.py                       # Password hashing + JWT
│   ├── routes/
│   │   ├── auth.py                   # Registration, login, Google OAuth, password reset
│   │   ├── profile.py                # Profile CRUD, O*NET analysis, skill management
│   │   ├── learning_path.py          # Two-stage path generation, phase tests, weekly progress
│   │   ├── market_insights.py        # O*NET + Exa skill gap analysis
│   │   ├── ai_assistant.py           # Context-aware AI chatbot
│   │   ├── assignments.py            # Video assignments + anti-cheat
│   │   └── admin.py                  # Admin panel (analytics, users, videos, audit)
│   ├── rag/
│   │   ├── query_planner.py          # LLM-generated search query planning
│   │   ├── phase_query_generator.py  # Stage 2: per-phase search query generation (cross-user cached)
│   │   ├── retriever.py              # Tavily + YouTube Data API v3 retrieval
│   │   ├── batch_retriever.py        # Stage 3: 3-layer retrieval (L0/L1/L2) + dedup
│   │   └── vector_cache.py           # pgvector hybrid search + Mistral embeddings
│   ├── market/
│   │   ├── load_onet.py              # O*NET dataset loader (5 TSV → pandas)
│   │   ├── role_matcher.py           # Fuzzy role → SOC code matching
│   │   ├── skill_extractor.py        # O*NET skill extraction (Hot Tech priority)
│   │   └── insights_engine.py        # Skill gap computation + LLM market outlook
│   ├── services/
│   │   ├── exa_market_service.py     # Exa Answer API integration (3-layer cache)
│   │   ├── resource_cache_service.py # ResourceCache batch operations (pgvector)
│   │   ├── role_context_cache.py     # O*NET + Exa role context cache
│   │   ├── cache_service.py          # TTL-based cache invalidation
│   │   └── admin_service.py          # Admin business logic
│   ├── db/
│   │   ├── database.py               # SQLAlchemy engine + session factory
│   │   └── models.py                 # 18 ORM models (including pgvector)
│   ├── schemas/                      # Pydantic v2 request/response schemas
│   ├── utils/
│   │   └── test_generator.py         # LLM-powered MCQ generation
│   └── data/                         # O*NET dataset files (5 TSV files)
│
└── assets/                            # Architecture & flow diagrams
```

---

## Getting Started

### Prerequisites
- Python 3.11+
- Node.js 18+
- PostgreSQL 16 with pgvector extension (or Supabase)
- API keys: [Groq](https://console.groq.com/), [Mistral](https://console.mistral.ai/), [Tavily](https://tavily.com/), [Exa](https://exa.ai/), [YouTube Data API](https://console.cloud.google.com/), [Google OAuth](https://console.cloud.google.com/)

### Backend Setup
```bash
cd server
python -m venv venv
.\venv\Scripts\activate          # Windows
source venv/bin/activate         # macOS/Linux
pip install -r requirements.txt

# Configure environment
cp .env.example .env
# Edit .env with your DATABASE_URL, SECRET_KEY, GROQ_API_KEY, MISTRAL_API_KEY,
# TAVILY_API_KEY, EXA_API_KEY, YOUTUBE_API_KEY, SMTP_EMAIL, SMTP_PASSWORD

# Run
uvicorn main:app --reload
```

### Frontend Setup
```bash
cd frontend
npm install

# Configure environment
cp .env.example .env.local
# Edit .env.local with NEXT_PUBLIC_API_URL and NEXT_PUBLIC_GOOGLE_CLIENT_ID

# Run
npm run dev
```

---

## Environment Variables

### Backend (`server/.env`)
| Variable | Description |
|----------|-------------|
| `DATABASE_URL` | PostgreSQL connection string (with pgvector) |
| `SECRET_KEY` | JWT signing secret |
| `GROQ_API_KEY` | Groq API key for Llama 3.3 70B + Compound |
| `MISTRAL_API_KEY` | Mistral API key for embeddings |
| `TAVILY_API_KEY` | Tavily Search API key |
| `EXA_API_KEY` | Exa API key for real-time market data |
| `YOUTUBE_API_KEY` | YouTube Data API v3 key |
| `SMTP_EMAIL` | Gmail address for OTP emails |
| `SMTP_PASSWORD` | Gmail App Password for SMTP |
| `FRONTEND_URL` | Frontend origin for CORS |

### Frontend (`frontend/.env.local`)
| Variable | Description |
|----------|-------------|
| `NEXT_PUBLIC_API_URL` | Backend API base URL |
| `NEXT_PUBLIC_GOOGLE_CLIENT_ID` | Google OAuth 2.0 client ID |

---

## Key Technical Decisions

| Decision | Rationale |
|----------|-----------|
| **Two-stage generation** | Stage 1 (path structure) uses large 70B model for quality; Stage 2 (search queries) uses fast 8B model for speed. Total LLM latency split across specialized models |
| **RAG over fine-tuning** | Learning resources change daily; RAG ensures freshness without retraining costs |
| **pgvector over Pinecone/Weaviate** | Cost efficiency, ACID compliance, relational + vector data colocated — no network egress to external vector DB |
| **Hybrid search (HNSW + B-tree)** | Metadata filtering (target_role, language) partitions the vector search space; standard production pattern for role-scoped retrieval |
| **Five-layer caching** | RAG sources, resources, query plans, Exa data, and role contexts each have independent L0/L1 caches with appropriate TTLs |
| **Cross-user caching** | Query plans and resources are keyed by role+skills, not user_id. Second user with same role pays ~0ms for queries that took seconds to generate |
| **Exa + O\*NET dual-source** | O\*NET provides stable occupational structure; Exa provides real-time salary/demand data. Both are merged for comprehensive skill requirements |
| **YouTube Data API v3** | Direct API integration for video/playlist retrieval with language routing, Shorts exclusion, and concurrent medium/long duration fetching |
| **Advisory locks** | `pg_try_advisory_xact_lock()` prevents duplicate path generation from concurrent requests (React StrictMode double-fires) |
| **Language-aware cache bypass** | Non-English queries always hit live APIs for YouTube; Tavily results cached in English only |
| **O\*NET over custom datasets** | Government-maintained, 1,000+ occupations, free, updated quarterly |
| **Server-side test answers** | Anti-cheat: answers revealed only after submission |
| **OpenTelemetry tracing** | Production observability for LLM latency and RAG pipeline performance |
| **Multi-model orchestration** | Groq Compound for chat (web search), Llama 3.3 70B for generation, Llama 3.1 8B for queries, Mistral for embeddings — each model chosen for its strength |

---

<p align="center">
  <strong>Built for career transformation — not course recommendations.</strong>
</p>
