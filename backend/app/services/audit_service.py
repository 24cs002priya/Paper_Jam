from datetime import datetime, timezone

from app.db.mongodb import get_database


async def record_audit(action: str, *, actor_id=None, business_id=None, target_type="user", target_id=None, details=None):
    now = datetime.now(timezone.utc)
    await get_database().audit_logs.insert_one({
        "action": action,
        "actor_id": actor_id,
        "business_id": business_id,
        "entity_type": target_type,
        "entity_id": target_id,
        "metadata": details or {},
        "created_at": now,
        "actor_user_id": actor_id,
        "target_type": target_type,
        "target_id": target_id,
        "details": details or {},
        "timestamp": now,
    })
