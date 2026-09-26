from pymongo import ASCENDING, DESCENDING

from app.core.config import get_settings
from app.db.mongodb import get_database


async def ensure_indexes() -> None:
    db = get_database()
    async for user in db.users.find({"email": {"$type": "string"}}, {"email": 1, "email_normalized": 1}):
        normalized = user["email"].strip().lower()
        if user.get("email_normalized") != normalized or user["email"] != normalized:
            await db.users.update_one({"_id": user["_id"]}, {"$set": {"email": normalized, "email_normalized": normalized}})
    await db.admins.create_index([("email", ASCENDING)], unique=True)
    await db.regulations.create_index([("slug", ASCENDING)], unique=True)
    await db.regulations.create_index([("status", ASCENDING), ("updated_at", DESCENDING)])
    await db.regulation_versions.create_index([("regulation_id", ASCENDING), ("version", ASCENDING)], unique=True)
    await db.regulation_versions.create_index([("status", ASCENDING)])
    await db.regulation_chunks.create_index([("regulation_id", ASCENDING), ("version_id", ASCENDING)])
    await db.regulation_chunks.create_index([("metadata.department", ASCENDING), ("metadata.jurisdiction", ASCENDING)])
    await db.rag_queries.create_index([("created_at", DESCENDING)])
    await db.rag_queries.create_index([("answer_generated", ASCENDING), ("created_at", DESCENDING)])
    await db.users.create_index(
        [("email_normalized", ASCENDING)], unique=True, name="users_email_normalized_unique",
        partialFilterExpression={"email_normalized": {"$type": "string"}},
    )
    await db.users.create_index([("role", ASCENDING), ("status", ASCENDING)], name="users_role_status")
    await db.businesses.create_index([("owner_user_id", ASCENDING), ("updated_at", DESCENDING)], name="businesses_owner_updated")
    await db.businesses.create_index([("business_type", ASCENDING)], name="businesses_type")
    await db.businesses.create_index([("status", ASCENDING)], name="businesses_status")
    await db.audit_logs.create_index([("timestamp", DESCENDING)], name="audit_timestamp")
    await db.business_users.create_index([("email_normalized", ASCENDING)], unique=True, name="business_users_email_unique")
    await db.business_users.create_index([("business_id", ASCENDING), ("role", ASCENDING)], name="business_users_business_role")
    await db.businesses.create_index([("user_id", ASCENDING)], unique=True, sparse=True, name="businesses_user_unique")
    await db.business_documents.create_index([("business_id", ASCENDING), ("uploaded_at", DESCENDING)], name="business_documents_business_uploaded")
    await db.business_documents.create_index([("business_id", ASCENDING), ("document_type_key", ASCENDING), ("version", DESCENDING)], name="business_documents_business_type_version")
    await db.business_document_counters.create_index([("business_id", ASCENDING), ("document_type_key", ASCENDING)], unique=True, name="business_document_counter_unique")
    await db.approval_passports.create_index([("business_id", ASCENDING)], unique=True, name="approval_passports_business_unique")
    await db.applications.create_index([("business_id", ASCENDING), ("updated_at", DESCENDING)], name="applications_business_updated")
    await db.applications.create_index([("business_id", ASCENDING), ("status", ASCENDING)], name="applications_business_status")
    await db.application_actions.create_index([("business_id", ASCENDING), ("status", ASCENDING)], name="actions_business_status")
    await db.compliance_items.create_index([("business_id", ASCENDING), ("status", ASCENDING)], name="compliance_business_status")
    await db.regulation_changes.create_index([("business_id", ASCENDING), ("created_at", DESCENDING)], name="changes_business_created")
    await db.audit_logs.create_index([("business_id", ASCENDING), ("created_at", DESCENDING)], name="audit_business_created")
    await db.revoked_tokens.create_index([("expires_at", ASCENDING)], expireAfterSeconds=0, name="revoked_tokens_ttl")
    await db.approval_results.create_index([("business_id", ASCENDING), ("generation_id", ASCENDING)], name="approval_results_business_generation")
    await db.approval_rules.create_index([("business_id", ASCENDING), ("generation_id", ASCENDING)], name="approval_rules_business_generation")
    await db.approval_engine_runs.create_index([("business_id", ASCENDING), ("created_at", DESCENDING)], name="approval_runs_business_created")
    existing_roadmap_indexes = await db.approval_roadmaps.index_information()
    if "approval_roadmaps_business_unique" in existing_roadmap_indexes:
        await db.approval_roadmaps.drop_index("approval_roadmaps_business_unique")
    await db.approval_roadmaps.create_index([("business_id", ASCENDING), ("generation_id", ASCENDING)], unique=True, name="approval_roadmaps_business_generation_unique")
    # Atlas Search indexes are managed via Atlas APIs or the Atlas UI. An exact
    # dimensioned JSON definition is emitted at startup once the model loads.
    _ = get_settings().vector_index_name
