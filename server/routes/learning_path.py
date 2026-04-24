from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session
from sqlalchemy import func, text
from db.database import get_db
from db.models import UserDB, UserProfile, PhaseProgress, TestAttempt, ActiveTest, MarketInsightsCache, WeeklyTaskProgress
from auth import get_current_user
from rag.retriever import clean_llm_json
from rag.phase_query_generator import generate_phase_queries
from market.role_matcher import match_role_to_soc
from market.skill_extractor import extract_top_skills, extract_top_knowledge, extract_top_activities
from services.cache_service import invalidate_market_insights_cache, invalidate_learning_path
from services.learning_path_store import (
    build_path_fingerprint,
    fingerprint_to_lock_key,
    resolve_user_learning_path,
    store_canonical_learning_path,
)
from services.exa_market_service import fetch_realtime_market_data
from services.role_context_cache import get_cached_role_context, store_role_context
from config import ONET_CACHE, LLM_MODEL, LLM_TEMPERATURE, TEST_PASSING_SCORE, EXA_CACHE_TTL_DAYS
from groq import Groq
import asyncio
import os
import json
import time

router = APIRouter()


def _ensure_phase_progress(db: Session, user_id: int, path_json: dict) -> None:
    existing_progress = db.query(PhaseProgress).filter(
        PhaseProgress.user_id == user_id
    ).count()

    if existing_progress == 0:
        from utils.test_generator import initialize_phase_progress
        num_phases = len(path_json.get("learning_path", []))
        if num_phases > 0:
            initialize_phase_progress(user_id, num_phases, db)


def _ensure_weekly_progress(db: Session, user_id: int, path_json: dict) -> None:
    # Fast path: if any weekly rows exist for this user, assume initialized.
    existing_any = db.query(WeeklyTaskProgress.id).filter(
        WeeklyTaskProgress.user_id == user_id
    ).first()
    if existing_any:
        return

    # Initialize all phases in one transaction to avoid per-phase commit overhead.
    rows_to_add: list[WeeklyTaskProgress] = []
    for phase_idx, phase in enumerate(path_json.get("learning_path", [])):
        num_weeks = phase.get("duration_weeks", len(phase.get("weekly_breakdown", [])))
        if num_weeks and num_weeks > 0:
            for week_num in range(1, int(num_weeks) + 1):
                rows_to_add.append(
                    WeeklyTaskProgress(
                        user_id=user_id,
                        phase_index=phase_idx,
                        week_number=week_num,
                        is_completed=False,
                    )
                )

    if rows_to_add:
        db.add_all(rows_to_add)
        db.commit()


@router.get("/generate-path")
async def generate_learning_path(
    db: Session = Depends(get_db),
    current_user: UserDB = Depends(get_current_user)
):
    profile = db.query(UserProfile).filter(UserProfile.user_id == current_user.id).first()

    if not profile:
        raise HTTPException(status_code=400, detail="User profile not found. Please complete your profile first.")

    fingerprint = build_path_fingerprint(profile)

    # Return cached path if exists
    t_cache = time.time()
    existing_path = resolve_user_learning_path(db, current_user.id, profile, fingerprint)
    if existing_path:
        print(f"[generate-path] DB cache HIT for user {current_user.id} in {time.time() - t_cache:.3f}s")
        _ensure_phase_progress(db, current_user.id, existing_path)
        _ensure_weekly_progress(db, current_user.id, existing_path)
        return existing_path

    t_total = time.time()
    print(f"[generate-path] no cached path for user {current_user.id} — starting full generation")
    # concurrent requests (e.g. React StrictMode double-fires useEffect).
    # pg_try_advisory_xact_lock is transaction-scoped — auto-released at
    # commit or rollback, so no explicit unlock is needed and it is safe
    # with connection pooling.
    lock_acquired = db.execute(
        text("SELECT pg_try_advisory_xact_lock(:lock_key)"),
        {"lock_key": fingerprint_to_lock_key(fingerprint)}
    ).scalar()

    if not lock_acquired:
        # Another request is already generating this path — wait for it then
        # return the result once it commits (poll with a short back-off).
        for _ in range(60):               # max ~30s wait
            await asyncio.sleep(0.5)
            db.expire_all()               # force re-read from DB
            ready = resolve_user_learning_path(db, current_user.id, profile, fingerprint)
            if ready:
                _ensure_phase_progress(db, current_user.id, ready)
                _ensure_weekly_progress(db, current_user.id, ready)
                return ready
        raise HTTPException(status_code=503, detail="Path generation in progress, please retry.")

    client = Groq(api_key=os.getenv("GROQ_API_KEY"))

    # Parse JSON fields safely
    raw_skills = json.loads(profile.skills) if profile.skills else []
    # Normalize skills: extract names for backward compatibility
    skills = [s["name"] if isinstance(s, dict) else s for s in raw_skills]
    # Build proficiency-aware skill descriptions for the LLM
    skills_with_proficiency = []
    for s in raw_skills:
        if isinstance(s, dict):
            skills_with_proficiency.append(f"{s['name']} ({s.get('proficiency', 'beginner')})")
        else:
            skills_with_proficiency.append(f"{s} (beginner)")
    industries = json.loads(profile.preferred_industries) if profile.preferred_industries else []
    instruction_language = profile.language or "English"

    # ==================================================
    # O*NET + EXA (REAL-TIME) ROLE CONTEXT - WITH CACHING
    # ==================================================
    t_onet = time.time()

    # Initialize variables
    role_context = ""
    onet_required_skills = []
    exa_skills = []

    # Check role context cache first (15 days TTL)
    cached_context = get_cached_role_context(profile.desired_role or "")
    if cached_context:
        role_context = cached_context["role_context"]
        combined_required_skills = cached_context["required_skills"]
        print(f"[generate-path] role context cache HIT for user {current_user.id} in {time.time() - t_onet:.3f}s")
    else:
        # Build role context from O*NET
        if "occupations" in ONET_CACHE:
            onet_match = match_role_to_soc(profile.desired_role, ONET_CACHE["occupations"])
            if onet_match:
                matched_soc, matched_title = onet_match
                knowledge_list = extract_top_knowledge(matched_soc, ONET_CACHE["knowledge"], top_n=8)
                activity_list = extract_top_activities(matched_soc, ONET_CACHE["activities"], top_n=8)
                onet_required_skills = extract_top_skills(matched_soc, ONET_CACHE["tech_skills"], top_n=15)
                parts = [f"O*NET Occupational Profile for \"{matched_title}\" (SOC {matched_soc}):"]
                if knowledge_list:
                    parts.append("Key Knowledge Domains: " + "; ".join(knowledge_list))
                if activity_list:
                    parts.append("Core Work Activities: " + "; ".join(activity_list))
                if onet_required_skills:
                    parts.append("Required Technologies (O*NET): " + ", ".join(onet_required_skills))
                role_context = "\n".join(parts)

        # Fetch Exa real-time training skills and merge with O*NET
        exa_data = fetch_realtime_market_data(profile.desired_role or "")
        exa_skills = exa_data.get("training_skills", []) or []
        combined_required_skills = list(dict.fromkeys(exa_skills + onet_required_skills))  # Exa first, dedupe

        if exa_skills:
            role_context = (role_context + "\n" if role_context else "") + "Real-time Market Skills (Exa): " + ", ".join(exa_skills)

        # Store in cache for next time (no expiry needed - role context is stable)
        store_role_context(profile.desired_role or "", role_context, combined_required_skills)

        print(f"[generate-path] role context cache MISS for user {current_user.id} - stored in cache")

    print(f"[generate-path] O*NET + Exa lookup:  {time.time() - t_onet:.3f}s")

    # ==================================================
    # STAGE 1: Generate Learning Path Structure (no resources)
    # ==================================================
    t_stage1 = time.time()

    prompt = f"""
You are an AI system that generates structured learning paths.

CRITICAL INSTRUCTIONS:
- TARGET ROLE FOCUS & EXISTING SKILLS: The learning path MUST be strictly focused on the skills required for the target role. The user's existing skills are provided ONLY to avoid teaching what they already know. If an existing skill is actually required for the target role, use its proficiency to calibrate the depth (skip fundamentals if advanced). However, if an existing skill is IRRELEVANT to the target role (e.g. React/Node for an AI Engineer), YOU MUST COMPLETELY IGNORE IT. Do NOT include irrelevant skills or topics in the path just because the user has them.
- The generated path MUST fit exactly within the 'Target Timeline' specified by the user (e.g., if target is 3 months, total duration must be approx 3 months). Adjust the scope and depth of modules to fit this constraint.
- EDUCATIONAL CALIBRATION: Use the user's education level to set the starting depth of the path.
- CAREER TRANSITION: If the user is currently employed or self-employed and their current role differs from the target role, this is a CAREER TRANSITION.
- INDUSTRY CONTEXT: The user is targeting the {', '.join(industries) if industries else 'general'} industry/industries. Tailor examples, projects, and use cases to these industries wherever relevant.
- RESOURCE LANGUAGE: The user prefers learning resources in '{instruction_language}'.

IMPORTANT: Do NOT include any resources in this pass. Leave the "resources" array EMPTY for every phase. Resources will be attached separately.

{f'''
ROLE REQUIREMENTS (from O*NET occupational data — use this to calibrate which topics, skills, and knowledge areas the path must cover):
{role_context}
''' if role_context else ""}
{f'''MANDATORY SKILLS DISTRIBUTION:
The following is the combined list of market-required technologies (Real-time Exa + O*NET). You MUST distribute ALL of these across the phases using their EXACT names in each phase's "skills" array. Every skill below must appear in at least one phase. You may also add other relevant skills, but these are required:
{json.dumps(combined_required_skills)}
''' if combined_required_skills else ""}
USER DETAILS:
- Target Role: {profile.desired_role}
- Current Education: {profile.education_level}
- Current Status: {profile.current_status}
{f'- Current Role: {profile.current_role}' if profile.current_role else ''}
{f'- Current Industry: {profile.current_industry}' if profile.current_industry else ''}
- Location: {profile.location}
- Existing Skills (with proficiency): {', '.join(skills_with_proficiency) if skills_with_proficiency else 'None'} Us
 - Preferred Industries: {', '.join(industries) if industries else 'Not specified'}
- Learning Pace: {profile.learning_pace}
- Hours per Week: {profile.hours_per_week}
- Instruction Language: {instruction_language}
- Budget Sensitivity: {profile.budget_sensitivity}
- Target Timeline: {profile.timeline}
- User Email: {current_user.email}

OUTPUT RULES (STRICT):
- Respond ONLY in valid JSON
- Do NOT include explanations or markdown
- Follow EXACTLY this JSON structure:

{{
  "meta": {{
    "goal": string,
    "duration_months": number,
    "weekly_time_hours": number,
    "level": string
  }},
  "learning_path": [
    {{
      "phase": string,
      "duration_weeks": number,
      "why_this_phase": string,
      "topics": [string],
      "skills": [string],
      "weekly_breakdown": [
        {{
          "week": number,
          "focus": string,
          "learning_objectives": [string],
          "practice_tasks": [string]
        }}
      ],
      "resources": [],
      "projects": [
        {{
          "title": string,
          "description": string,
          "difficulty": "Easy" | "Medium" | "Hard"
        }}
      ]
    }}
  ]
}}

CRITICAL CONTENT REQUIREMENTS:
1. "why_this_phase": Explain why this phase is important and correctly placed.
2. "topics": 5–8 detailed sub-topics per phase.
3. "skills": Technologies/tools the user will learn in this phase. If MANDATORY SKILLS DISTRIBUTION was provided above, use those EXACT names (copy-paste). Distribute ALL mandatory skills across phases so that completing the entire path covers every one. You may add extra skills too.
4. "weekly_breakdown": Break down each phase into weekly focused goals (duration_weeks number of weeks). Each week should have specific learning objectives and practice tasks.
5. "resources": MUST be an empty array []. Resources will be populated automatically.
6. "projects": MINIMUM 3-5 hands-on projects per phase with varying difficulty levels (Easy/Medium/Hard). Tie projects to the user's target industries: {', '.join(industries) if industries else 'general domain'}.
"""

    response = client.chat.completions.create(
        model=LLM_MODEL,
        messages=[{"role": "user", "content": prompt}],
        temperature=LLM_TEMPERATURE,
        response_format={"type": "json_object"}
    )
    print(f"[generate-path] Stage 1 (path structure): {time.time() - t_stage1:.3f}s")

    content = response.choices[0].message.content
    content = clean_llm_json(content)

    try:
        path_json = json.loads(content)
    except json.JSONDecodeError:
        return {
            "error": "Model returned invalid JSON",
            "raw_output": content
        }

    # Post-process: ensure required skills (O*NET + Exa) are distributed across phases
    if combined_required_skills and path_json.get("learning_path"):
        phases = path_json["learning_path"]
        all_phase_skills_lower = set()
        for phase in phases:
            for sk in phase.get("skills", []):
                all_phase_skills_lower.add(sk.lower().strip())

        missing = [sk for sk in combined_required_skills if sk.lower().strip() not in all_phase_skills_lower]
        if missing:
            for i, sk in enumerate(missing):
                target_phase = phases[i % len(phases)]
                if "skills" not in target_phase:
                    target_phase["skills"] = []
                target_phase["skills"].append(sk)

    phases = path_json.get("learning_path", [])

    # ==================================================
    # STAGE 2: Generate per-phase search queries (fast model)
    # ==================================================
    t_stage2 = time.time()
    phase_queries = await asyncio.to_thread(
        generate_phase_queries, phases, instruction_language, profile.desired_role or ""
    )
    print(f"[generate-path] Stage 2 (query generation): {time.time() - t_stage2:.3f}s")

    # ==================================================
    # STAGE 3: Fetch resources for all phases (3-layer batch retriever)
    # L0: in-memory cache  →  L1: ResourceCache DB (pgvector)  →  L2: Tavily/YouTube
    # ==================================================
    t_stage3 = time.time()
    from rag.batch_retriever import batch_retrieve_phase_resources
    all_phase_resources = await batch_retrieve_phase_resources(
        phase_queries, instruction_language, profile.desired_role or ""
    )
    print(f"[generate-path] Stage 3 (resource fetching): {time.time() - t_stage3:.3f}s")

    # ==================================================
    # STAGE 4: Attach resources to phases (deduplicated)
    # Interleave articles and videos to ensure both types appear
    # ==================================================
    t_stage4 = time.time()
    seen_urls: set[str] = set()

    for i, phase in enumerate(phases):
        raw_resources = all_phase_resources[i] if i < len(all_phase_resources) else []

        # Split by type and deduplicate separately
        articles: list[dict] = []
        videos: list[dict] = []

        for r in raw_resources:
            url = r.get("link", "")
            if not url or url in seen_urls:
                continue
            seen_urls.add(url)
            if r.get("type") in ("Video", "Playlist"):
                videos.append(r)
            else:
                articles.append(r)

        # Interleave: take up to 5 articles/books + up to 5 videos/playlists
        final: list[dict] = []
        final.extend(articles[:5])
        final.extend(videos[:5])
        phase["resources"] = final

        art_count = len([r for r in final if r.get("type") not in ("Video", "Playlist")])
        vid_count = len([r for r in final if r.get("type") == "Video"])
        pl_count = len([r for r in final if r.get("type") == "Playlist"])
        print(f"[Stage4] Phase {i}: {len(final)} resources ({art_count} articles, {vid_count} videos, {pl_count} playlists)")

    print(f"[generate-path] Stage 4 (resource attachment): {time.time() - t_stage4:.3f}s")

    # Persist once as a shared canonical path and link this user to it.
    path_json = store_canonical_learning_path(db, current_user.id, profile, path_json, fingerprint)

    # Initialize phase progress tracking
    _ensure_phase_progress(db, current_user.id, path_json)
    _ensure_weekly_progress(db, current_user.id, path_json)

    total_resources = sum(len(p.get("resources", [])) for p in path_json.get("learning_path", []))
    print(
        f"[generate-path] TOTAL time: {time.time() - t_total:.3f}s | "
        f"{len(phases)} phases, {total_resources} resources"
    )
    return path_json


@router.get("/phase-progress")
async def get_phase_progress(
    db: Session = Depends(get_db),
    current_user: UserDB = Depends(get_current_user)
):
    """Get progress tracking for all phases"""
    progress = db.query(PhaseProgress).filter(
        PhaseProgress.user_id == current_user.id
    ).order_by(PhaseProgress.phase_index).all()

    return {
        "progress": [
            {
                "phase_index": p.phase_index,
                "is_unlocked": p.is_unlocked,
                "is_completed": p.is_completed,
                "test_passed": p.test_passed,
                "best_score": p.best_score
            }
            for p in progress
        ]
    }


@router.get("/phase-test/{phase_index}")
async def get_phase_test(
    phase_index: int,
    db: Session = Depends(get_db),
    current_user: UserDB = Depends(get_current_user)
):
    """Generate MCQ test for a specific phase"""
    # Check if phase is unlocked
    progress = db.query(PhaseProgress).filter(
        PhaseProgress.user_id == current_user.id,
        PhaseProgress.phase_index == phase_index
    ).first()

    if not progress or not progress.is_unlocked:
        raise HTTPException(status_code=403, detail="Phase not unlocked yet")

    # Get learning path
    path_data = resolve_user_learning_path(db, current_user.id)
    if not path_data:
        raise HTTPException(status_code=404, detail="Learning path not found")

    learning_path = path_data.get("learning_path", [])

    if phase_index >= len(learning_path):
        raise HTTPException(status_code=404, detail="Phase not found")

    phase_data = learning_path[phase_index]

    # Generate MCQs
    from utils.test_generator import generate_phase_mcqs
    questions = generate_phase_mcqs(phase_data, phase_index)

    # Store questions with correct answers in DB for later validation
    existing = db.query(ActiveTest).filter(
        ActiveTest.user_id == current_user.id,
        ActiveTest.phase_index == phase_index
    ).first()

    if existing:
        existing.questions_data = json.dumps(questions)
        existing.created_at = func.now()
    else:
        active_test = ActiveTest(
            user_id=current_user.id,
            phase_index=phase_index,
            questions_data=json.dumps(questions)
        )
        db.add(active_test)
    db.commit()

    # Don't send correct answers to frontend
    questions_without_answers = [
        {
            "question": q["question"],
            "options": q["options"],
            "difficulty": q.get("difficulty", "Medium")
        }
        for q in questions
    ]

    return {
        "phase_index": phase_index,
        "phase_name": phase_data.get("phase", f"Phase {phase_index + 1}"),
        "questions": questions_without_answers,
        "total_questions": len(questions),
        "passing_score": TEST_PASSING_SCORE
    }


@router.post("/submit-test")
async def submit_test(
    test_data: dict,
    db: Session = Depends(get_db),
    current_user: UserDB = Depends(get_current_user)
):
    """Submit test answers and calculate score"""
    phase_index = test_data.get("phase_index")
    user_answers = test_data.get("answers", [])  # List of indices

    # Retrieve the SAME questions that were shown to the user
    active_test = db.query(ActiveTest).filter(
        ActiveTest.user_id == current_user.id,
        ActiveTest.phase_index == phase_index
    ).first()

    if not active_test:
        raise HTTPException(status_code=400, detail="No active test found. Please take the test first.")

    questions = json.loads(active_test.questions_data)

    if not questions or len(questions) == 0:
        raise HTTPException(status_code=500, detail="No test questions found")

    # Calculate score
    correct_count = 0
    for i, user_answer in enumerate(user_answers):
        if i < len(questions) and user_answer == questions[i]["correct_answer"]:
            correct_count += 1

    score = int((correct_count / len(questions)) * 100) if len(questions) > 0 else 0
    passed = score >= TEST_PASSING_SCORE

    # Save attempt
    attempt = TestAttempt(
        user_id=current_user.id,
        phase_index=phase_index,
        score=score,
        answers=json.dumps(user_answers),
        passed=passed
    )
    db.add(attempt)

    # Update progress
    progress = db.query(PhaseProgress).filter(
        PhaseProgress.user_id == current_user.id,
        PhaseProgress.phase_index == phase_index
    ).first()

    if progress:
        if score > progress.best_score:
            progress.best_score = score

        if passed and not progress.test_passed:
            progress.test_passed = True
            progress.is_completed = True
            progress.updated_at = func.now()

            # Unlock next phase
            next_progress = db.query(PhaseProgress).filter(
                PhaseProgress.user_id == current_user.id,
                PhaseProgress.phase_index == phase_index + 1
            ).first()

            if next_progress:
                next_progress.is_unlocked = True

            # Add phase skills to user profile
            try:
                path_data = resolve_user_learning_path(db, current_user.id)
                if path_data:
                    learning_path = path_data.get("learning_path", [])
                    if phase_index < len(learning_path):
                        phase_skills = learning_path[phase_index].get("skills", [])
                        if phase_skills:
                            profile = db.query(UserProfile).filter(UserProfile.user_id == current_user.id).first()
                            if profile:
                                current_skills = json.loads(profile.skills) if profile.skills else []
                                # Normalize to extract names for comparison
                                current_skills_lower = [(s["name"] if isinstance(s, dict) else s).lower() for s in current_skills]
                                # Ensure all existing skills are in object format
                                normalized = [{"name": s, "proficiency": "beginner"} if isinstance(s, str) else s for s in current_skills]
                                new_skills_added = False
                                for skill in phase_skills:
                                    if skill.strip() and skill.lower() not in current_skills_lower:
                                        normalized.append({"name": skill.strip(), "proficiency": "beginner"})
                                        current_skills_lower.append(skill.lower())
                                        new_skills_added = True
                                if new_skills_added:
                                    profile.skills = json.dumps(normalized)
                                    # Invalidate market insights cache since skills changed
                                    invalidate_market_insights_cache(current_user.id, db)
                                    print(f"[Phase {phase_index} passed] Added {len(phase_skills)} skills to user {current_user.id} profile")
            except Exception as skill_err:
                print(f"Warning: Failed to add phase skills to profile: {skill_err}")

    db.commit()

    # Return results with explanations
    results = []
    for i, q in enumerate(questions):
        user_ans = user_answers[i] if i < len(user_answers) else -1
        is_correct = user_ans == q["correct_answer"]
        results.append({
            "question": q["question"],
            "user_answer": user_ans,
            "correct_answer": q["correct_answer"],
            "is_correct": is_correct,
            "explanation": q.get("explanation", "")
        })

    return {
        "score": score,
        "passed": passed,
        "correct_count": correct_count,
        "total_questions": len(questions),
        "results": results,
        "next_phase_unlocked": passed
    }


@router.post("/add-skill-and-regenerate-path")
async def add_skill_and_regenerate_path(
    skill_data: dict,
    db: Session = Depends(get_db),
    current_user: UserDB = Depends(get_current_user)
):
    """Add a new skill to profile and regenerate learning path"""
    skill = skill_data.get("skill", "").strip()
    proficiency = skill_data.get("proficiency", "beginner").strip()

    if not skill:
        raise HTTPException(status_code=400, detail="Skill name required")

    if proficiency not in ("beginner", "intermediate", "advanced"):
        proficiency = "beginner"

    profile = db.query(UserProfile).filter(UserProfile.user_id == current_user.id).first()
    if not profile:
        raise HTTPException(status_code=400, detail="Profile not found")

    # Add skill to profile
    current_skills = json.loads(profile.skills) if profile.skills else []
    # Normalize to object format
    normalized = []
    for s in current_skills:
        if isinstance(s, str):
            normalized.append({"name": s, "proficiency": "beginner"})
        else:
            normalized.append(s)

    if skill.lower() not in [s["name"].lower() for s in normalized]:
        normalized.append({"name": skill, "proficiency": proficiency})
        profile.skills = json.dumps(normalized)
        db.commit()

    # Delete the user's active learning-path link to force regeneration.
    invalidate_learning_path(current_user.id, db)
    # Invalidate market insights cache (skills changed)
    db.query(MarketInsightsCache).filter(MarketInsightsCache.user_id == current_user.id).delete()
    db.commit()

    return {
        "message": "Skill added and path regeneration triggered",
        "skills": normalized,
        "regenerate_path": True
    }


@router.get("/weekly-task-progress")
async def get_weekly_task_progress(
    phase_index: int,
    db: Session = Depends(get_db),
    current_user: UserDB = Depends(get_current_user)
):
    """Get progress for all weekly tasks in a specific phase"""
    tasks = db.query(WeeklyTaskProgress).filter(
        WeeklyTaskProgress.user_id == current_user.id,
        WeeklyTaskProgress.phase_index == phase_index
    ).order_by(WeeklyTaskProgress.week_number).all()

    return {
        "phase_index": phase_index,
        "tasks": [
            {
                "week_number": t.week_number,
                "is_completed": t.is_completed,
                "completed_at": t.completed_at.isoformat() if t.completed_at else None
            }
            for t in tasks
        ]
    }


@router.get("/all-weekly-progress")
async def get_all_weekly_progress(
    db: Session = Depends(get_db),
    current_user: UserDB = Depends(get_current_user)
):
    """Get progress for all weekly tasks across all phases"""
    tasks = db.query(WeeklyTaskProgress).filter(
        WeeklyTaskProgress.user_id == current_user.id
    ).all()

    # Group by phase_index
    progress_by_phase = {}
    for t in tasks:
        if t.phase_index not in progress_by_phase:
            progress_by_phase[t.phase_index] = []
        progress_by_phase[t.phase_index].append({
            "week_number": t.week_number,
            "is_completed": t.is_completed,
            "completed_at": t.completed_at.isoformat() if t.completed_at else None
        })

    return {
        "progress_by_phase": progress_by_phase
    }


@router.put("/weekly-task/{phase_index}/{week_number}")
async def update_weekly_task_progress(
    phase_index: int,
    week_number: int,
    request: dict,
    db: Session = Depends(get_db),
    current_user: UserDB = Depends(get_current_user)
):
    """Update completion status of a specific weekly task"""
    from sqlalchemy.sql import func
    
    is_completed = request.get("is_completed", False)
    
    task = db.query(WeeklyTaskProgress).filter(
        WeeklyTaskProgress.user_id == current_user.id,
        WeeklyTaskProgress.phase_index == phase_index,
        WeeklyTaskProgress.week_number == week_number
    ).first()

    if not task:
        # Create new task progress record
        task = WeeklyTaskProgress(
            user_id=current_user.id,
            phase_index=phase_index,
            week_number=week_number,
            is_completed=is_completed,
            completed_at=func.now() if is_completed else None
        )
        db.add(task)
    else:
        task.is_completed = is_completed
        task.completed_at = func.now() if is_completed else None

    db.commit()

    return {
        "phase_index": phase_index,
        "week_number": week_number,
        "is_completed": is_completed
    }
