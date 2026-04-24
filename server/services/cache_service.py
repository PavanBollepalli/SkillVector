from datetime import datetime, timedelta, timezone
from sqlalchemy.orm import Session
from db.models import MarketInsightsCache, PhaseProgress, WeeklyTaskProgress, ActiveTest
from config import MARKET_INSIGHTS_TTL_HOURS
from services.learning_path_store import clear_user_learning_path


def invalidate_learning_path(user_id: int, db: Session):
    """Removes a user's active path link and user-specific path state."""
    clear_user_learning_path(db, user_id)
    db.query(PhaseProgress).filter(PhaseProgress.user_id == user_id).delete()
    db.query(WeeklyTaskProgress).filter(WeeklyTaskProgress.user_id == user_id).delete()
    db.query(ActiveTest).filter(ActiveTest.user_id == user_id).delete()
    db.commit()


def invalidate_market_insights_cache(user_id: int, db: Session):
    """Deletes cached market insights for a user to force regeneration."""
    db.query(MarketInsightsCache).filter(MarketInsightsCache.user_id == user_id).delete()
    db.commit()


def get_valid_cache(user_id: int, role: str, db: Session):
    """Returns cached market insights if within TTL and role matches, else None."""
    cache = db.query(MarketInsightsCache).filter(
        MarketInsightsCache.user_id == user_id,
        MarketInsightsCache.role == role
    ).first()
    if not cache:
        return None
    age = datetime.now(timezone.utc) - cache.created_at.replace(tzinfo=timezone.utc)
    if age > timedelta(hours=MARKET_INSIGHTS_TTL_HOURS):
        # Expired — delete and return None
        db.delete(cache)
        db.commit()
        return None
    return cache
