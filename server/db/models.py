from sqlalchemy import Column, Integer, String, Boolean, DateTime, Float, Text
import sqlalchemy.orm
import sqlalchemy
from sqlalchemy.sql import func
from db.database import Base
from pgvector.sqlalchemy import Vector


class UserDB(Base):
    __tablename__ = "users"

    id = Column(Integer, primary_key=True, index=True)
    username = Column(String, unique=True, index=True, nullable=False)
    email = Column(String, unique=True, index=True, nullable=False)
    hashed_password = Column(String, nullable=True)  # NULL for Google OAuth users
    is_active = Column(Boolean, default=True)
    is_admin = Column(Boolean, default=False)
    created_at = Column(DateTime(timezone=True), server_default=func.now())


class PasswordReset(Base):
    """Stores OTP codes for password reset"""
    __tablename__ = "password_resets"

    id = Column(Integer, primary_key=True, index=True)
    email = Column(String, unique=True, index=True, nullable=False)
    otp = Column(String, nullable=False)
    expires_at = Column(DateTime(timezone=True), nullable=False)
    created_at = Column(DateTime(timezone=True), server_default=func.now())


class UserProfile(Base):
    __tablename__ = "user_profiles"

    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(Integer, unique=True, index=True, nullable=False)
    education_level = Column(String, nullable=True)
    current_status = Column(String, nullable=True)
    current_role = Column(String, nullable=True)
    current_industry = Column(String, nullable=True)
    location = Column(String, nullable=True)
    skills = Column(String, nullable=True) # Stored as JSON string
    desired_role = Column(String, nullable=True)
    preferred_industries = Column(String, nullable=True) # Stored as JSON string
    language = Column(String, nullable=True)
    learning_pace = Column(String, nullable=True)
    hours_per_week = Column(String, nullable=True)
    budget_sensitivity = Column(String, nullable=True)
    timeline = Column(String, nullable=True)


class LearningPath(Base):
    __tablename__ = "learning_paths"

    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(Integer, unique=True, index=True, nullable=False)
    path_data = Column(String, nullable=False) # Stored as JSON string
    created_at = Column(DateTime(timezone=True), server_default=func.now())

class PhaseProgress(Base):
    __tablename__ = "phase_progress"
    
    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(Integer, index=True, nullable=False)
    phase_index = Column(Integer, nullable=False)  # 0-based index
    is_unlocked = Column(Boolean, default=False)
    is_completed = Column(Boolean, default=False)
    test_passed = Column(Boolean, default=False)
    best_score = Column(Integer, default=0)  # Best test score percentage
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())


class WeeklyTaskProgress(Base):
    """Tracks weekly learning task completion within each phase"""
    __tablename__ = "weekly_task_progress"
    
    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(Integer, index=True, nullable=False)
    phase_index = Column(Integer, nullable=False)  # 0-based index of the phase
    week_number = Column(Integer, nullable=False)  # Week number within the phase (1, 2, 3, etc.)
    is_completed = Column(Boolean, default=False)
    completed_at = Column(DateTime(timezone=True), nullable=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())
    
    # Unique constraint: one record per user per phase per week
    __table_args__ = (
        sqlalchemy.UniqueConstraint('user_id', 'phase_index', 'week_number', name='uq_user_phase_week'),
    )

class TestAttempt(Base):
    __tablename__ = "test_attempts"
    
    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(Integer, index=True, nullable=False)
    phase_index = Column(Integer, nullable=False)
    score = Column(Integer, nullable=False)  # Score percentage (0-100)
    answers = Column(String, nullable=False)  # JSON string of user answers
    passed = Column(Boolean, default=False)
    created_at = Column(DateTime(timezone=True), server_default=func.now())

class ActiveTest(Base):
    __tablename__ = "active_tests"
    
    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(Integer, index=True, nullable=False)
    phase_index = Column(Integer, nullable=False)
    questions_data = Column(String, nullable=False)  # Full JSON with correct answers
    created_at = Column(DateTime(timezone=True), server_default=func.now())


# ============================================================
# ADMIN PANEL MODELS
# ============================================================

class VideoAssignment(Base):
    __tablename__ = "video_assignments"

    id = Column(Integer, primary_key=True, index=True)
    title = Column(String, nullable=False)
    youtube_url = Column(String, nullable=False)
    youtube_video_id = Column(String, nullable=False)
    description = Column(Text, nullable=True)
    duration_seconds = Column(Integer, nullable=False)  # Expected video duration
    category = Column(String, nullable=True)  # e.g., "AI/ML", "Web Dev", etc.
    assigned_by = Column(Integer, sqlalchemy.ForeignKey("users.id"), nullable=False)
    is_active = Column(Boolean, default=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now())

    # Relationships
    progress_records = sqlalchemy.orm.relationship("VideoProgress", back_populates="assignment", cascade="all, delete-orphan")
    user_assignments = sqlalchemy.orm.relationship("UserVideoAssignment", back_populates="assignment", cascade="all, delete-orphan")


class UserVideoAssignment(Base):
    """Links a video assignment to specific users (or all users if user_id is NULL)"""
    __tablename__ = "user_video_assignments"

    id = Column(Integer, primary_key=True, index=True)
    video_id = Column(Integer, sqlalchemy.ForeignKey("video_assignments.id"), nullable=False)
    user_id = Column(Integer, sqlalchemy.ForeignKey("users.id"), nullable=True)  # NULL = assigned to all
    assigned_at = Column(DateTime(timezone=True), server_default=func.now())
    due_date = Column(DateTime(timezone=True), nullable=True)
    is_mandatory = Column(Boolean, default=True)

    assignment = sqlalchemy.orm.relationship("VideoAssignment", back_populates="user_assignments")


class VideoProgress(Base):
    """Tracks legitimate video watching progress with anti-cheat"""
    __tablename__ = "video_progress"

    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(Integer, sqlalchemy.ForeignKey("users.id"), nullable=False)
    video_id = Column(Integer, sqlalchemy.ForeignKey("video_assignments.id"), nullable=False)
    watched_seconds = Column(Integer, default=0)  # Total verified watch time
    max_position = Column(Integer, default=0)  # Furthest legitimate position reached
    completion_percent = Column(Float, default=0.0)
    is_completed = Column(Boolean, default=False)
    watch_sessions = Column(Text, nullable=True)  # JSON: list of {start, end, duration, timestamp}
    last_heartbeat = Column(DateTime(timezone=True), nullable=True)
    total_pauses = Column(Integer, default=0)
    cheat_flags = Column(Integer, default=0)  # Number of suspicious events detected
    started_at = Column(DateTime(timezone=True), server_default=func.now())
    completed_at = Column(DateTime(timezone=True), nullable=True)
    updated_at = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())

    assignment = sqlalchemy.orm.relationship("VideoAssignment", back_populates="progress_records")


class AdminActivityLog(Base):
    """Audit log for admin actions"""
    __tablename__ = "admin_activity_log"

    id = Column(Integer, primary_key=True, index=True)
    admin_id = Column(Integer, sqlalchemy.ForeignKey("users.id"), nullable=False)
    action = Column(String, nullable=False)  # e.g., "assign_video", "toggle_user", etc.
    target_type = Column(String, nullable=True)  # "user", "video", etc.
    target_id = Column(Integer, nullable=True)
    details = Column(Text, nullable=True)  # JSON with extra context
    created_at = Column(DateTime(timezone=True), server_default=func.now())


class MarketInsightsCache(Base):
    """Caches market insights per user+role to avoid repeated LLM calls. TTL-based."""
    __tablename__ = "market_insights_cache"

    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(Integer, index=True, nullable=False)
    role = Column(String, nullable=False)
    gap_analysis = Column(Text, nullable=False)      # JSON: {role, soc_code, insights: {...}}
    profile_insights = Column(Text, nullable=False)   # JSON: {trending_skills, role_growth, ...}
    created_at = Column(DateTime(timezone=True), server_default=func.now())


class QueryPlanCache(Base):
    """Caches Groq-generated search query plans keyed by role+industries+language.
    Avoids repeated LLM calls for users with the same target role configuration.
    TTL: 30 days.
    """
    __tablename__ = "query_plan_cache"

    id = Column(Integer, primary_key=True, index=True)
    # SHA-256 of '{role}|{sorted_industries_json}|{language}' (all lowercased)
    cache_key = Column(String(64), unique=True, index=True, nullable=False)
    queries = Column(Text, nullable=False)  # Full JSON array of search query strings
    created_at = Column(DateTime(timezone=True), server_default=func.now())


class RagSourceCache(Base):
    """Vector cache of web sources fetched per search query.
    Uses pgvector cosine similarity to find semantically equivalent past queries.
    Partitioned by target_role and language so role+lang never share cached sources.
    TTL: 30 days.
    """
    __tablename__ = "rag_source_cache"

    id = Column(Integer, primary_key=True, index=True)
    query_text = Column(Text, nullable=False)
    query_embedding = Column(Vector(1024), nullable=False)  # mistral-embed dimension
    sources = Column(Text, nullable=False)  # JSON array of {title, url, content}
    target_role = Column(String, nullable=False, index=True, server_default="")
    language = Column(String, nullable=False, server_default="english")
    created_at = Column(DateTime(timezone=True), server_default=func.now())


class ResourceCache(Base):
    """Simple key-value cache for fetched learning resources (articles/videos).
    Keyed by SHA-256 of '{source_type}:{query}:{language}'.
    Enables cross-user caching: same queries share results.
    TTL: 30 days.
    """
    __tablename__ = "resource_cache"

    id = Column(Integer, primary_key=True, index=True)
    cache_key = Column(String(64), index=True, nullable=False) # No longer strictly unique across the whole table
    query_text = Column(Text, nullable=False)
    source_type = Column(String, nullable=False)   # "tavily" or "youtube"
    language = Column(String, nullable=False, server_default="english")
    resources = Column(Text, nullable=False)        # JSON array of {type, title, platform, link}

    target_role = Column(String, nullable=False, index=True, server_default="")
    query_embedding = Column(Vector(1024), nullable=True)  # null permitted for legacy rows

    created_at = Column(DateTime(timezone=True), server_default=func.now())


class ExaMarketCache(Base):
    """Caches Exa API market data per role.
    Exa data changes slowly (job market data is stable), so we cache for 15 days.
    Keyed by role name.
    """
    __tablename__ = "exa_market_cache"

    id = Column(Integer, primary_key=True, index=True)
    role = Column(String(255), unique=True, index=True, nullable=False)
    data = Column(Text, nullable=False)  # Full JSON: {training_skills, growth_rate, etc.}
    created_at = Column(DateTime(timezone=True), server_default=func.now())


class RoleContextCache(Base):
    """Caches O*NET + Exa role context for faster path generation.
    Pre-computes O*NET lookup + Exa data + extracted skills.
    TTL: 15 days via database query filter.
    """
    __tablename__ = "role_context_cache"

    id = Column(Integer, primary_key=True, index=True)
    role = Column(String(255), unique=True, index=True, nullable=False)
    role_context = Column(Text, nullable=False)  # Full context string for LLM
    required_skills = Column(Text, nullable=False)  # JSON array of skills
    created_at = Column(DateTime(timezone=True), server_default=func.now())

