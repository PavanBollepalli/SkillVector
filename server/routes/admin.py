from fastapi import APIRouter, Depends, HTTPException, Query, Request
from sqlalchemy.orm import Session
from sqlalchemy import func, desc, and_, or_, case, cast, Date
from typing import Optional
from datetime import datetime, timedelta, timezone
from db.database import get_db
from db.models import (
    UserDB, UserProfile, PhaseProgress, TestAttempt,
    VideoAssignment, UserVideoAssignment, VideoProgress, AdminActivityLog, MarketInsightsCache
)
from auth import verify_password, create_access_token, get_current_user
from dependencies import require_admin
from services.admin_service import log_admin_action, extract_youtube_id
from services.learning_path_store import resolve_user_learning_path
import schemas.UserSchemas as schemas
import json
import os

router = APIRouter()


# ============================================================
# ADMIN AUTH
# ============================================================

@router.post("/admin/login")
async def admin_login(user: schemas.UserLogin, db: Session = Depends(get_db)):
    """Admin-specific login that verifies admin privileges."""
    db_user = db.query(UserDB).filter(UserDB.email == user.email).first()
    if not db_user:
        raise HTTPException(status_code=401, detail="Invalid credentials")
    if not verify_password(user.password, db_user.hashed_password):
        raise HTTPException(status_code=401, detail="Invalid credentials")
    if not db_user.is_active:
        raise HTTPException(status_code=403, detail="Your account has been suspended. Contact an administrator.")
    if not db_user.is_admin:
        raise HTTPException(status_code=403, detail="Admin access required")
    access_token = create_access_token(data={"sub": db_user.email, "is_admin": True})
    return {"access_token": access_token, "token_type": "bearer", "user_id": db_user.id, "is_admin": True}


@router.get("/admin/me")
async def admin_me(current_user: UserDB = Depends(require_admin)):
    """Get current admin info."""
    return {
        "id": current_user.id,
        "username": current_user.username,
        "email": current_user.email,
        "is_admin": current_user.is_admin
    }


# ============================================================
# ADMIN - ANALYTICS DASHBOARD
# ============================================================

@router.get("/admin/analytics")
async def admin_analytics(
    db: Session = Depends(get_db),
    current_user: UserDB = Depends(require_admin)
):
    """Comprehensive analytics for the admin dashboard."""
    # Total counts
    total_users = db.query(func.count(UserDB.id)).scalar()
    total_profiles = db.query(func.count(UserProfile.id)).scalar()
    from db.models import UserLearningPath, CanonicalLearningPath
    total_paths = db.query(func.count(UserLearningPath.id)).scalar()
    total_canonical_paths = db.query(func.count(CanonicalLearningPath.id)).scalar()
    total_tests = db.query(func.count(TestAttempt.id)).scalar()
    total_videos = db.query(func.count(VideoAssignment.id)).filter(VideoAssignment.is_active == True).scalar()

    # Users registered in last 7 days
    week_ago = datetime.now(timezone.utc) - timedelta(days=7)
    new_users_week = db.query(func.count(UserDB.id)).filter(UserDB.created_at >= week_ago).scalar()

    # Profile completion rate
    profile_completion_rate = round((total_profiles / total_users * 100), 1) if total_users > 0 else 0

    # Learning path generation rate
    path_generation_rate = round((total_paths / total_profiles * 100), 1) if total_profiles > 0 else 0

    # Test pass rate
    passed_tests = db.query(func.count(TestAttempt.id)).filter(TestAttempt.passed == True).scalar()
    test_pass_rate = round((passed_tests / total_tests * 100), 1) if total_tests > 0 else 0

    # Average test score
    avg_score = db.query(func.avg(TestAttempt.score)).scalar()
    avg_score = round(avg_score, 1) if avg_score else 0

    # Phase completion distribution
    phase_stats = db.query(
        PhaseProgress.phase_index,
        func.count(PhaseProgress.id).label("total"),
        func.sum(case((PhaseProgress.is_completed == True, 1), else_=0)).label("completed")
    ).group_by(PhaseProgress.phase_index).order_by(PhaseProgress.phase_index).all()

    phase_completion = [
        {"phase": p.phase_index + 1, "total": p.total, "completed": int(p.completed or 0)}
        for p in phase_stats
    ]

    # Top desired roles
    top_roles = db.query(
        UserProfile.desired_role,
        func.count(UserProfile.id).label("count")
    ).filter(UserProfile.desired_role != None).group_by(UserProfile.desired_role).order_by(desc("count")).limit(10).all()

    # Video assignment stats
    video_completions = db.query(func.count(VideoProgress.id)).filter(VideoProgress.is_completed == True).scalar()
    total_video_progress = db.query(func.count(VideoProgress.id)).scalar()
    video_completion_rate = round((video_completions / total_video_progress * 100), 1) if total_video_progress > 0 else 0

    # Users by registration date (last 30 days)
    thirty_days_ago = datetime.now(timezone.utc) - timedelta(days=30)
    date_col = cast(UserDB.created_at, Date)
    daily_registrations = db.query(
        date_col.label("date"),
        func.count(UserDB.id).label("count")
    ).filter(UserDB.created_at >= thirty_days_ago).group_by(date_col).order_by(date_col).all()

    # Skill frequency analysis
    all_profiles = db.query(UserProfile.skills).filter(UserProfile.skills != None).all()
    skill_counter = {}
    for p in all_profiles:
        try:
            skills = json.loads(p.skills)
            for s in skills:
                skill_name = s["name"] if isinstance(s, dict) else s
                skill_counter[skill_name] = skill_counter.get(skill_name, 0) + 1
        except:
            pass
    top_skills = sorted(skill_counter.items(), key=lambda x: x[1], reverse=True)[:15]

    # Recent activity (last 20 admin actions)
    recent_logs = db.query(AdminActivityLog).order_by(desc(AdminActivityLog.created_at)).limit(20).all()

    return {
        "overview": {
            "total_users": total_users,
            "total_profiles": total_profiles,
            "total_paths": total_paths,
            "total_canonical_paths": total_canonical_paths,
            "total_tests": total_tests,
            "total_videos": total_videos,
            "new_users_week": new_users_week,
            "profile_completion_rate": profile_completion_rate,
            "path_generation_rate": path_generation_rate,
            "test_pass_rate": test_pass_rate,
            "avg_test_score": avg_score,
            "video_completion_rate": video_completion_rate,
        },
        "phase_completion": phase_completion,
        "top_roles": [{"role": r.desired_role, "count": r.count} for r in top_roles],
        "top_skills": [{"skill": s[0], "count": s[1]} for s in top_skills],
        "daily_registrations": [{"date": str(d.date), "count": d.count} for d in daily_registrations],
        "recent_activity": [
            {
                "id": log.id,
                "action": log.action,
                "target_type": log.target_type,
                "target_id": log.target_id,
                "details": log.details,
                "created_at": log.created_at.isoformat() if log.created_at else None
            }
            for log in recent_logs
        ]
    }


# ============================================================
# ADMIN - USER MANAGEMENT
# ============================================================

@router.get("/admin/users")
async def admin_list_users(
    search: Optional[str] = None,
    page: int = Query(1, ge=1),
    per_page: int = Query(20, ge=1, le=100),
    db: Session = Depends(get_db),
    current_user: UserDB = Depends(require_admin)
):
    """List all users with detailed info including profiles, skills, goals, and progress."""
    query = db.query(UserDB)

    if search:
        query = query.filter(
            or_(
                UserDB.username.ilike(f"%{search}%"),
                UserDB.email.ilike(f"%{search}%")
            )
        )

    total = query.count()
    users = query.order_by(desc(UserDB.created_at)).offset((page - 1) * per_page).limit(per_page).all()

    result = []
    for u in users:
        profile = db.query(UserProfile).filter(UserProfile.user_id == u.id).first()
        path = resolve_user_learning_path(db, u.id)

        # Phase progress
        phases = db.query(PhaseProgress).filter(PhaseProgress.user_id == u.id).order_by(PhaseProgress.phase_index).all()
        completed_phases = sum(1 for p in phases if p.is_completed)
        total_phases = len(phases)

        # Test stats
        test_attempts = db.query(TestAttempt).filter(TestAttempt.user_id == u.id).all()
        avg_test_score = round(sum(t.score for t in test_attempts) / len(test_attempts), 1) if test_attempts else 0
        tests_passed = sum(1 for t in test_attempts if t.passed)

        # Video progress
        video_progress = db.query(VideoProgress).filter(VideoProgress.user_id == u.id).all()
        videos_completed = sum(1 for v in video_progress if v.is_completed)
        videos_assigned = len(video_progress)

        user_data = {
            "id": u.id,
            "username": u.username,
            "email": u.email,
            "is_active": u.is_active,
            "is_admin": u.is_admin,
            "created_at": u.created_at.isoformat() if u.created_at else None,
            "has_profile": profile is not None,
            "has_learning_path": path is not None,
            "profile": None,
            "progress": {
                "completed_phases": completed_phases,
                "total_phases": total_phases,
                "completion_rate": round((completed_phases / total_phases * 100), 1) if total_phases > 0 else 0,
                "avg_test_score": avg_test_score,
                "tests_passed": tests_passed,
                "total_tests": len(test_attempts),
                "videos_completed": videos_completed,
                "videos_assigned": videos_assigned,
            }
        }

        if profile:
            user_data["profile"] = {
                "desired_role": profile.desired_role,
                "skills": json.loads(profile.skills) if profile.skills else [],
                "education_level": profile.education_level,
                "location": profile.location,
                "current_status": profile.current_status,
                "current_role": profile.current_role,
                "current_industry": profile.current_industry,
                "learning_pace": profile.learning_pace,
                "hours_per_week": profile.hours_per_week,
                "timeline": profile.timeline,
            }

        result.append(user_data)

    return {
        "users": result,
        "total": total,
        "page": page,
        "per_page": per_page,
        "total_pages": (total + per_page - 1) // per_page
    }


@router.get("/admin/users/{user_id}")
async def admin_get_user_detail(
    user_id: int,
    db: Session = Depends(get_db),
    current_user: UserDB = Depends(require_admin)
):
    """Get detailed view of a single user including learning path, test history, and video progress."""
    user = db.query(UserDB).filter(UserDB.id == user_id).first()
    if not user:
        raise HTTPException(status_code=404, detail="User not found")

    profile = db.query(UserProfile).filter(UserProfile.user_id == user_id).first()
    phases = db.query(PhaseProgress).filter(PhaseProgress.user_id == user_id).order_by(PhaseProgress.phase_index).all()
    test_attempts = db.query(TestAttempt).filter(TestAttempt.user_id == user_id).order_by(desc(TestAttempt.created_at)).all()
    video_progress = db.query(VideoProgress).filter(VideoProgress.user_id == user_id).all()

    # Parse learning path
    learning_path_data = resolve_user_learning_path(db, user_id)

    return {
        "user": {
            "id": user.id,
            "username": user.username,
            "email": user.email,
            "is_active": user.is_active,
            "is_admin": user.is_admin,
            "created_at": user.created_at.isoformat() if user.created_at else None,
        },
        "profile": {
            "desired_role": profile.desired_role if profile else None,
            "skills": json.loads(profile.skills) if profile and profile.skills else [],
            "education_level": profile.education_level if profile else None,
            "location": profile.location if profile else None,
            "current_status": profile.current_status if profile else None,
            "current_role": profile.current_role if profile else None,
            "current_industry": profile.current_industry if profile else None,
            "learning_pace": profile.learning_pace if profile else None,
            "hours_per_week": profile.hours_per_week if profile else None,
            "timeline": profile.timeline if profile else None,
            "preferred_industries": json.loads(profile.preferred_industries) if profile and profile.preferred_industries else [],
        } if profile else None,
        "learning_path": learning_path_data,
        "phase_progress": [
            {
                "phase_index": p.phase_index,
                "is_unlocked": p.is_unlocked,
                "is_completed": p.is_completed,
                "test_passed": p.test_passed,
                "best_score": p.best_score,
            }
            for p in phases
        ],
        "test_history": [
            {
                "phase_index": t.phase_index,
                "score": t.score,
                "passed": t.passed,
                "created_at": t.created_at.isoformat() if t.created_at else None,
            }
            for t in test_attempts
        ],
        "video_progress": [
            {
                "video_id": v.video_id,
                "completion_percent": round(v.completion_percent, 1),
                "is_completed": v.is_completed,
                "watched_seconds": v.watched_seconds,
                "cheat_flags": v.cheat_flags,
                "started_at": v.started_at.isoformat() if v.started_at else None,
                "completed_at": v.completed_at.isoformat() if v.completed_at else None,
            }
            for v in video_progress
        ]
    }


@router.patch("/admin/users/{user_id}/toggle-active")
async def admin_toggle_user_active(
    user_id: int,
    db: Session = Depends(get_db),
    current_user: UserDB = Depends(require_admin)
):
    """Activate or deactivate a user account."""
    user = db.query(UserDB).filter(UserDB.id == user_id).first()
    if not user:
        raise HTTPException(status_code=404, detail="User not found")
    if user.id == current_user.id:
        raise HTTPException(status_code=400, detail="Cannot deactivate your own account")

    user.is_active = not user.is_active
    db.commit()
    log_admin_action(db, current_user.id, "toggle_user_active", "user", user_id,
                     json.dumps({"new_status": user.is_active}))
    return {"message": f"User {'activated' if user.is_active else 'deactivated'}", "is_active": user.is_active}


@router.patch("/admin/users/{user_id}/toggle-admin")
async def admin_toggle_admin(
    user_id: int,
    db: Session = Depends(get_db),
    current_user: UserDB = Depends(require_admin)
):
    """Grant or revoke admin privileges."""
    user = db.query(UserDB).filter(UserDB.id == user_id).first()
    if not user:
        raise HTTPException(status_code=404, detail="User not found")
    if user.id == current_user.id:
        raise HTTPException(status_code=400, detail="Cannot modify your own admin status")

    user.is_admin = not user.is_admin
    db.commit()
    log_admin_action(db, current_user.id, "toggle_admin", "user", user_id,
                     json.dumps({"new_admin_status": user.is_admin}))
    return {"message": f"Admin {'granted' if user.is_admin else 'revoked'}", "is_admin": user.is_admin}


# ============================================================
# ADMIN - VIDEO ASSIGNMENT MANAGEMENT
# ============================================================

@router.post("/admin/videos")
async def admin_create_video(
    video_data: dict,
    db: Session = Depends(get_db),
    current_user: UserDB = Depends(require_admin)
):
    """Create a new video assignment."""
    title = video_data.get("title", "").strip()
    youtube_url = video_data.get("youtube_url", "").strip()
    description = video_data.get("description", "").strip()
    duration_seconds = video_data.get("duration_seconds", 0)
    category = video_data.get("category", "").strip()

    if not title or not youtube_url:
        raise HTTPException(status_code=400, detail="Title and YouTube URL are required")

    try:
        video_id = extract_youtube_id(youtube_url)
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid YouTube URL")

    video = VideoAssignment(
        title=title,
        youtube_url=youtube_url,
        youtube_video_id=video_id,
        description=description,
        duration_seconds=duration_seconds,
        category=category or None,
        assigned_by=current_user.id,
    )
    db.add(video)
    db.commit()
    db.refresh(video)

    log_admin_action(db, current_user.id, "create_video", "video", video.id,
                     json.dumps({"title": title}))
    return {"message": "Video created", "video_id": video.id}


@router.get("/admin/videos")
async def admin_list_videos(
    db: Session = Depends(get_db),
    current_user: UserDB = Depends(require_admin)
):
    """List all video assignments with progress stats."""
    videos = db.query(VideoAssignment).order_by(desc(VideoAssignment.created_at)).all()

    result = []
    for v in videos:
        # Get progress stats
        progress_records = db.query(VideoProgress).filter(VideoProgress.video_id == v.id).all()
        total_assigned = len(progress_records)
        completed_count = sum(1 for p in progress_records if p.is_completed)
        avg_completion = round(sum(p.completion_percent for p in progress_records) / total_assigned, 1) if total_assigned > 0 else 0
        cheat_flags_total = sum(p.cheat_flags for p in progress_records)

        # Get assigned users count
        assignments = db.query(UserVideoAssignment).filter(UserVideoAssignment.video_id == v.id).count()

        result.append({
            "id": v.id,
            "title": v.title,
            "youtube_url": v.youtube_url,
            "youtube_video_id": v.youtube_video_id,
            "description": v.description,
            "duration_seconds": v.duration_seconds,
            "category": v.category,
            "is_active": v.is_active,
            "created_at": v.created_at.isoformat() if v.created_at else None,
            "stats": {
                "assigned_users": assignments,
                "started": total_assigned,
                "completed": completed_count,
                "avg_completion": avg_completion,
                "cheat_flags": cheat_flags_total,
            }
        })

    return {"videos": result}


@router.post("/admin/videos/{video_id}/assign")
async def admin_assign_video(
    video_id: int,
    assignment_data: dict,
    db: Session = Depends(get_db),
    current_user: UserDB = Depends(require_admin)
):
    """Assign a video to specific users or all users."""
    video = db.query(VideoAssignment).filter(VideoAssignment.id == video_id).first()
    if not video:
        raise HTTPException(status_code=404, detail="Video not found")

    user_ids = assignment_data.get("user_ids", [])  # Empty = assign to all
    due_date_str = assignment_data.get("due_date")
    is_mandatory = assignment_data.get("is_mandatory", True)

    due_date = None
    if due_date_str:
        try:
            due_date = datetime.fromisoformat(due_date_str)
        except:
            pass

    assigned_count = 0

    if not user_ids:
        # Assign to all non-admin users
        all_users = db.query(UserDB).filter(UserDB.is_admin == False).all()
        user_ids = [u.id for u in all_users]

    for uid in user_ids:
        # Check if already assigned
        existing = db.query(UserVideoAssignment).filter(
            UserVideoAssignment.video_id == video_id,
            UserVideoAssignment.user_id == uid
        ).first()
        if not existing:
            assignment = UserVideoAssignment(
                video_id=video_id,
                user_id=uid,
                due_date=due_date,
                is_mandatory=is_mandatory,
            )
            db.add(assignment)

            # Initialize progress record
            existing_progress = db.query(VideoProgress).filter(
                VideoProgress.video_id == video_id,
                VideoProgress.user_id == uid
            ).first()
            if not existing_progress:
                progress = VideoProgress(user_id=uid, video_id=video_id)
                db.add(progress)
            assigned_count += 1

    db.commit()
    log_admin_action(db, current_user.id, "assign_video", "video", video_id,
                     json.dumps({"user_ids": user_ids, "count": assigned_count}))
    return {"message": f"Video assigned to {assigned_count} users", "assigned_count": assigned_count}


@router.get("/admin/videos/{video_id}/progress")
async def admin_video_progress(
    video_id: int,
    db: Session = Depends(get_db),
    current_user: UserDB = Depends(require_admin)
):
    """Get detailed progress for a specific video across all users."""
    video = db.query(VideoAssignment).filter(VideoAssignment.id == video_id).first()
    if not video:
        raise HTTPException(status_code=404, detail="Video not found")

    progress_records = db.query(VideoProgress).filter(VideoProgress.video_id == video_id).all()

    user_progress = []
    for p in progress_records:
        user = db.query(UserDB).filter(UserDB.id == p.user_id).first()
        user_progress.append({
            "user_id": p.user_id,
            "username": user.username if user else "Unknown",
            "email": user.email if user else "",
            "watched_seconds": p.watched_seconds,
            "completion_percent": round(p.completion_percent, 1),
            "is_completed": p.is_completed,
            "cheat_flags": p.cheat_flags,
            "total_pauses": p.total_pauses,
            "started_at": p.started_at.isoformat() if p.started_at else None,
            "completed_at": p.completed_at.isoformat() if p.completed_at else None,
            "last_heartbeat": p.last_heartbeat.isoformat() if p.last_heartbeat else None,
        })

    return {
        "video": {
            "id": video.id,
            "title": video.title,
            "duration_seconds": video.duration_seconds,
        },
        "progress": user_progress
    }


@router.delete("/admin/videos/{video_id}")
async def admin_delete_video(
    video_id: int,
    db: Session = Depends(get_db),
    current_user: UserDB = Depends(require_admin)
):
    """Delete a video assignment."""
    video = db.query(VideoAssignment).filter(VideoAssignment.id == video_id).first()
    if not video:
        raise HTTPException(status_code=404, detail="Video not found")

    db.delete(video)
    db.commit()
    log_admin_action(db, current_user.id, "delete_video", "video", video_id,
                     json.dumps({"title": video.title}))
    return {"message": "Video deleted"}


# ============================================================
# ADMIN - ACTIVITY LOG
# ============================================================

@router.get("/admin/activity-log")
async def admin_activity_log(
    page: int = Query(1, ge=1),
    per_page: int = Query(50, ge=1, le=100),
    db: Session = Depends(get_db),
    current_user: UserDB = Depends(require_admin)
):
    """Get admin activity audit log."""
    total = db.query(func.count(AdminActivityLog.id)).scalar()
    logs = db.query(AdminActivityLog).order_by(desc(AdminActivityLog.created_at)).offset((page - 1) * per_page).limit(per_page).all()

    result = []
    for log in logs:
        admin = db.query(UserDB).filter(UserDB.id == log.admin_id).first()
        result.append({
            "id": log.id,
            "admin_name": admin.username if admin else "Unknown",
            "action": log.action,
            "target_type": log.target_type,
            "target_id": log.target_id,
            "details": log.details,
            "created_at": log.created_at.isoformat() if log.created_at else None,
        })

    return {"logs": result, "total": total, "page": page}


# ============================================================
# ADMIN - MAKE USER ADMIN (utility endpoints)
# ============================================================

@router.post("/make-admin")
async def make_first_admin(
    request: Request,
    db: Session = Depends(get_db)
):
    """One-time setup to make a user admin. Only works if no admins exist yet."""
    payload = await request.json()
    admin_count = db.query(func.count(UserDB.id)).filter(UserDB.is_admin == True).scalar()
    if admin_count > 0:
        raise HTTPException(status_code=403, detail="Admin already exists. Use admin panel to manage admins.")

    email = payload.get("email", "").strip()
    secret = payload.get("secret", "").strip()

    if secret != os.getenv("SECRET_KEY"):
        raise HTTPException(status_code=403, detail="Invalid secret")

    user = db.query(UserDB).filter(UserDB.email == email).first()
    if not user:
        raise HTTPException(status_code=404, detail="User not found")

    user.is_admin = True
    db.commit()
    return {"message": f"User {user.username} is now an admin"}


# ── DEV: one-time admin setup (remove in production) ──────────────────
@router.post("/dev/create-admin")
async def dev_create_admin(db: Session = Depends(get_db)):
    """Creates a default admin account. Only works if no admin exists."""
    from auth import hash_password
    from sqlalchemy import text, inspect as sa_inspect

    # Auto-add missing columns to existing tables
    inspector = sa_inspect(db.bind)
    user_cols = [c["name"] for c in inspector.get_columns("users")]
    if "is_admin" not in user_cols:
        db.execute(text("ALTER TABLE users ADD COLUMN is_admin BOOLEAN DEFAULT FALSE"))
        db.commit()

    # Create new tables (video_assignments, etc.)
    from db.database import Base, engine
    Base.metadata.create_all(bind=engine)

    admin_count = db.query(func.count(UserDB.id)).filter(UserDB.is_admin == True).scalar()
    if admin_count > 0:
        raise HTTPException(status_code=403, detail="Admin already exists")
    existing = db.query(UserDB).filter(UserDB.email == "admin@skillvector.com").first()
    if existing:
        existing.is_admin = True
        db.commit()
        return {"email": "admin@skillvector.com", "password": "(your existing password)", "message": "Existing user promoted to admin"}
    hashed = hash_password("admin123")
    admin = UserDB(username="admin", email="admin@skillvector.com", hashed_password=hashed, is_admin=True, is_active=True)
    db.add(admin)
    db.commit()
    return {"email": "admin@skillvector.com", "password": "admin123", "message": "Admin user created"}
