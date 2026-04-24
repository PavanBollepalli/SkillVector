"""Shared learning-path storage helpers.

This module centralizes the canonical-path fingerprint, the user-to-path link,
and lazy migration from the legacy per-user LearningPath table.
"""

from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone

from sqlalchemy.orm import Session

from config import LEARNING_PATH_PROMPT_VERSION, LLM_MODEL
from db.models import CanonicalLearningPath, LearningPath, UserLearningPath


def _normalize_text(value: object) -> str:
    if value is None:
        return ""
    return str(value).strip()


def _normalize_skills(skills: object) -> list[dict[str, str]]:
    normalized: list[dict[str, str]] = []
    if not isinstance(skills, list):
        return normalized

    for item in skills:
        if isinstance(item, dict):
            name = _normalize_text(item.get("name", ""))
            if not name:
                continue
            proficiency = _normalize_text(item.get("proficiency", "beginner")).lower() or "beginner"
        else:
            name = _normalize_text(item)
            if not name:
                continue
            proficiency = "beginner"

        normalized.append({"name": name, "proficiency": proficiency})

    normalized.sort(key=lambda item: (item["name"].lower(), item["proficiency"]))
    return normalized


def build_path_fingerprint_payload(profile) -> dict:
    """Build a deterministic payload for canonical path fingerprinting."""
    try:
        raw_skills = json.loads(profile.skills) if profile.skills else []
    except Exception:
        raw_skills = []

    try:
        preferred_industries = json.loads(profile.preferred_industries) if profile.preferred_industries else []
    except Exception:
        preferred_industries = []

    payload = {
        "prompt_version": LEARNING_PATH_PROMPT_VERSION,
        "llm_model": LLM_MODEL,
        "desired_role": _normalize_text(profile.desired_role).lower(),
        "education_level": _normalize_text(profile.education_level).lower(),
        "current_status": _normalize_text(profile.current_status).lower(),
        "current_role": _normalize_text(profile.current_role).lower(),
        "current_industry": _normalize_text(profile.current_industry).lower(),
        "location": _normalize_text(profile.location).lower(),
        "language": _normalize_text(profile.language).lower() or "english",
        "learning_pace": _normalize_text(profile.learning_pace).lower(),
        "hours_per_week": _normalize_text(profile.hours_per_week).lower(),
        "budget_sensitivity": _normalize_text(profile.budget_sensitivity).lower(),
        "timeline": _normalize_text(profile.timeline).lower(),
        "preferred_industries": sorted(
            _normalize_text(ind).lower() for ind in preferred_industries if _normalize_text(ind)
        ),
        "skills": _normalize_skills(raw_skills),
    }
    return payload


def build_path_fingerprint(profile) -> str:
    payload = build_path_fingerprint_payload(profile)
    raw = json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=True)
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def fingerprint_to_lock_key(fingerprint: str) -> int:
    """Convert a fingerprint into a signed 64-bit advisory-lock key."""
    value = int(fingerprint[:16], 16)
    if value >= 2**63:
        value -= 2**64
    return value


def serialize_path_data(path_data: dict) -> str:
    return json.dumps(path_data, ensure_ascii=False)


def get_user_learning_path_record(db: Session, user_id: int) -> UserLearningPath | None:
    return db.query(UserLearningPath).filter(UserLearningPath.user_id == user_id).first()


def get_canonical_path_by_fingerprint(
    db: Session,
    fingerprint: str,
) -> CanonicalLearningPath | None:
    return db.query(CanonicalLearningPath).filter(
        CanonicalLearningPath.path_fingerprint == fingerprint
    ).first()


def _link_user_to_canonical(
    db: Session,
    user_id: int,
    canonical_path: CanonicalLearningPath,
) -> None:
    link = get_user_learning_path_record(db, user_id)
    if link:
        link.canonical_path_id = canonical_path.id
    else:
        db.add(UserLearningPath(user_id=user_id, canonical_path_id=canonical_path.id))


def resolve_user_learning_path(
    db: Session,
    user_id: int,
    profile=None,
    fingerprint: str | None = None,
) -> dict | None:
    """Return the current learning-path JSON for a user, if one exists.

    Resolution order:
      1. UserLearningPath -> CanonicalLearningPath
      2. Legacy LearningPath row
      3. Canonical path by fingerprint (if profile/fingerprint provided)
    """
    user_link = get_user_learning_path_record(db, user_id)
    if user_link and user_link.canonical_path:
        return json.loads(user_link.canonical_path.path_data)

    legacy = db.query(LearningPath).filter(LearningPath.user_id == user_id).first()
    if legacy:
        if profile is None:
            return json.loads(legacy.path_data)

        return migrate_legacy_learning_path(db, user_id, profile, legacy.path_data)

    if profile is not None:
        fingerprint = fingerprint or build_path_fingerprint(profile)
        canonical = get_canonical_path_by_fingerprint(db, fingerprint)
        if canonical:
            _link_user_to_canonical(db, user_id, canonical)
            db.commit()
            return json.loads(canonical.path_data)

    return None


def migrate_legacy_learning_path(
    db: Session,
    user_id: int,
    profile,
    legacy_path_data: str,
) -> dict:
    """Promote a legacy per-user path to a shared canonical path."""
    fingerprint = build_path_fingerprint(profile)
    canonical = get_canonical_path_by_fingerprint(db, fingerprint)

    if canonical is None:
        canonical = CanonicalLearningPath(
            path_fingerprint=fingerprint,
            path_data=legacy_path_data,
            prompt_version=LEARNING_PATH_PROMPT_VERSION,
            llm_model=LLM_MODEL,
            target_role=_normalize_text(getattr(profile, "desired_role", "")),
            language=_normalize_text(getattr(profile, "language", "")) or "English",
            timeline=_normalize_text(getattr(profile, "timeline", "")) or None,
            last_used_at=datetime.now(timezone.utc),
            usage_count=1,
        )
        db.add(canonical)
        db.flush()
    else:
        canonical.last_used_at = datetime.now(timezone.utc)
        canonical.usage_count = (canonical.usage_count or 0) + 1

    _link_user_to_canonical(db, user_id, canonical)

    try:
        legacy = db.query(LearningPath).filter(LearningPath.user_id == user_id).first()
        if legacy:
            db.delete(legacy)
    except Exception:
        pass

    db.commit()
    return json.loads(canonical.path_data)


def store_canonical_learning_path(
    db: Session,
    user_id: int,
    profile,
    path_data: dict,
    fingerprint: str | None = None,
) -> dict:
    """Persist a generated path once and link the user to the shared canonical row."""
    fingerprint = fingerprint or build_path_fingerprint(profile)
    serialized = serialize_path_data(path_data)

    canonical = get_canonical_path_by_fingerprint(db, fingerprint)
    if canonical is None:
        canonical = CanonicalLearningPath(
            path_fingerprint=fingerprint,
            path_data=serialized,
            prompt_version=LEARNING_PATH_PROMPT_VERSION,
            llm_model=LLM_MODEL,
            target_role=_normalize_text(getattr(profile, "desired_role", "")),
            language=_normalize_text(getattr(profile, "language", "")) or "English",
            timeline=_normalize_text(getattr(profile, "timeline", "")) or None,
            last_used_at=datetime.now(timezone.utc),
            usage_count=1,
        )
        db.add(canonical)
        db.flush()
    else:
        canonical.last_used_at = datetime.now(timezone.utc)
        canonical.usage_count = (canonical.usage_count or 0) + 1

    _link_user_to_canonical(db, user_id, canonical)

    legacy = db.query(LearningPath).filter(LearningPath.user_id == user_id).first()
    if legacy:
        db.delete(legacy)

    db.commit()
    return json.loads(canonical.path_data)


def clear_user_learning_path(db: Session, user_id: int) -> None:
    """Remove a user's active path link and legacy row without deleting shared canonical data."""
    link = get_user_learning_path_record(db, user_id)
    if link:
        db.delete(link)

    legacy = db.query(LearningPath).filter(LearningPath.user_id == user_id).first()
    if legacy:
        db.delete(legacy)