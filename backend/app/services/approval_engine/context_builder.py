import json
from typing import Any


EXCLUDED_PROFILE_FIELDS = {"_id", "id", "user_id", "owner_user_id", "created_at", "updated_at", "profile_completion", "approval_generation_id", "approval_engine_request_id", "approval_engine_status", "approval_context_hash", "email", "phone", "pan", "gstin", "contact", "contact_person", "registered_address", "operating_address", "address", "business_name", "display_name", "legal_name"}


def _clean(value: Any) -> Any:
    if isinstance(value, dict):
        return {str(k): _clean(v) for k, v in value.items() if v not in (None, "", [], {})}
    if isinstance(value, list):
        return [_clean(v) for v in value if v not in (None, "", [], {})]
    if hasattr(value, "isoformat"):
        return value.isoformat()
    return value


def build_business_context(business: dict) -> dict[str, Any]:
    context = {key: _clean(value) for key, value in business.items() if key not in EXCLUDED_PROFILE_FIELDS and value not in (None, "", [], {})}
    operating = _clean(business.get("operating_address") or business.get("address") or {})
    registered = _clean(business.get("registered_address") or {})
    site_text = " ".join(str((business.get("operating_address") or business.get("registered_address") or {}).get(key, ""))
                          for key in ("address_line_1", "address_line_2"))
    if "gidc" in site_text.casefold() or "industrial estate" in site_text.casefold():
        context["site_context"] = "industrial estate premises"
    activities = context.get("business_activities") or context.get("activities") or []
    context["business_location"] = {k: operating.get(k) for k in ("city", "district", "state", "country") if operating.get(k)}
    if not context["business_location"]:
        context["business_location"] = {k: context[k] for k in ("state", "district", "city", "country") if context.get(k)}
    context["state"] = context.get("state") or operating.get("state") or registered.get("state") or (context.get("location_details") or {}).get("state")
    context["district"] = context.get("district") or operating.get("district") or registered.get("district") or (context.get("location_details") or {}).get("district")
    if context.get("location_details"):
        details = context["location_details"]
        context["location_details"] = {key: details[key] for key in ("city", "district", "state", "country", "jurisdiction", "region") if details.get(key)}
    if activities:
        context["business_activity"] = activities
    return {key: value for key, value in context.items() if value not in (None, "", [], {})}


def build_regulatory_query(context: dict[str, Any]) -> str:
    usable = {key: value for key, value in context.items() if value not in (None, "", [], {})}
    if not usable:
        return "Regulatory approval requirements, applicability conditions, documents, and authorities for a business."
    return "Find regulatory provisions that establish permits, approvals, registrations, licenses, or document requirements for this business. " + json.dumps(usable, ensure_ascii=False, default=str)[:3500]


def build_regulatory_queries(context: dict[str, Any]) -> list[str]:
    """Create profile-led searches that cover distinct regulatory domains."""
    business_type = context.get("business_type") or context.get("industry") or "business"
    industry = context.get("industry")
    activities = context.get("business_activities") or context.get("business_activity") or []
    location = context.get("business_location") or context.get("state") or context.get("location_details") or {}
    site_context = context.get("site_context") or ""
    activity_text = "; ".join(str(item) for item in activities) if isinstance(activities, list) else str(activities)
    location_text = ", ".join(str(value) for value in location.values()) if isinstance(location, dict) else str(location)
    compact_context = {key: context.get(key) for key in ("business_type", "industry", "business_activities", "business_location", "site_context") if context.get(key)}
    queries = [
        build_regulatory_query(compact_context),
        f"{business_type} {industry or ''} {location_text}: laws requiring business approvals, operating licences, registrations, permits or prior consents; applicability thresholds and authority.",
    ]
    business_text = f"{business_type} {industry or ''} {activity_text}".casefold()
    if any(term in business_text for term in ("manufactur", "processing", "factory", "production")):
        queries.append(
            f"Manufacturing premises: factory registration and licence; building plan and occupancy approval; fire safety clearance; consent to establish or operate; Air Act and Water Act State Pollution Control Board consent. {location_text}. Find exact statutory approval clauses and applicability conditions."
        )
    if any(term in business_text for term in ("packag", "packer", "pre-pack", "label")):
        queries.append(
            "Legal Metrology Packaged Commodities Rules 2011 rule 27 every manufacturer packer shall make application for registration Director Controller"
        )
        queries.append(
            "Application for registration Rule 27 manufacturer or packer pre-packaged commodities Director Controller in prescribed manner"
        )
    if any(term in business_text for term in ("cold storage", "warehouse", "storage", "logistics")):
        queries.append(
            f"Cold storage and food warehouse business: required storage, warehouse or cold-storage operating licence, registration or permit; exact law and eligibility conditions. {business_type} {location_text}."
        )
    if "industrial estate" in str(site_context).casefold():
        queries.append(
            f"Gujarat industrial estate GIDC OCA letter factory building plan approval construction occupancy conditions. {business_type} {location_text}."
        )
    if "food" in business_text:
        queries.append(
            f"Food manufacturer and packaged food FSSAI licence or registration; installed capacity and turnover thresholds; Central or State licence eligibility. {business_type} {location_text}."
        )
    if any(term in business_text for term in ("manufactur", "processing", "factory", "production")):
        queries.append(
            "Water Act section 25 no person shall establish industry likely to discharge sewage or trade effluent without previous consent State Board"
        )
        queries.append(
            f"Air Prevention and Control of Pollution Act section 21 no person shall without the previous consent State Board establish operate industrial plant air pollution control area Gujarat."
        )
    return list(dict.fromkeys(query.strip() for query in queries if query.strip()))
