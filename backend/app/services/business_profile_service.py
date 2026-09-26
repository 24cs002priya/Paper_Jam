from app.db.mongodb import get_database


REQUIRED_PROFILE_FIELDS = (
    "legal_name", "business_type", "entity_type", "contact_information",
    "registered_address", "operating_address", "primary_business_activity",
)


def calculate_profile_completion(business: dict) -> dict:
    missing = []
    for field in REQUIRED_PROFILE_FIELDS:
        if field == "contact_information":
            valid = bool(business.get("email") and business.get("phone"))
        elif field in {"registered_address", "operating_address"}:
            address = business.get(field) or {}
            valid = all(address.get(key) for key in ("address_line_1", "city", "district", "state", "pincode", "country"))
        elif field == "primary_business_activity":
            valid = bool(business.get("business_activities"))
        else:
            valid = bool(business.get(field))
        if not valid:
            missing.append(field)
    completed = len(REQUIRED_PROFILE_FIELDS) - len(missing)
    return {"percentage": round(completed * 100 / len(REQUIRED_PROFILE_FIELDS)), "completed_fields": completed,
            "required_fields": len(REQUIRED_PROFILE_FIELDS), "missing_fields": missing}


async def load_business(user: dict) -> dict:
    return await get_database().businesses.find_one({"_id": user["business_id"]})
