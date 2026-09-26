from __future__ import annotations

import re
from datetime import date, datetime, timezone, timedelta

from bson import ObjectId

from app.db.mongodb import get_database
from app.services.document_validation_service import inspect_pdf, validate_document_type
from app.services.passport_service import normalize_document_type
from app.services.storage_service import storage


def _business_field(business: dict, name: str):
    if name in business:
        return business.get(name)
    if name.startswith("registered_address."):
        return (business.get("registered_address") or {}).get(name.split(".", 1)[1])
    if name.startswith("operating_address."):
        return (business.get("operating_address") or {}).get(name.split(".", 1)[1])
    if name.startswith("contact_person."):
        return (business.get("contact_person") or {}).get(name.split(".", 1)[1])
    return None


def _missing_information(approval: dict, business: dict) -> list[dict]:
    requirements = []
    seen = set()
    for condition in approval.get("conditions", []):
        if condition.get("result") is not None:
            continue
        field = condition.get("field")
        label = condition.get("condition", "Additional business information")
        value = _business_field(business, field) if field else None
        status = "complete" if value not in (None, "", [], {}) else "missing"
        identity = field or label
        if identity in seen:
            continue
        seen.add(identity)
        requirements.append({"field": field, "label": label, "status": status, "value": value if status == "complete" else None,
                             "source": condition.get("evidence")})
    for item in approval.get("required_documents", []):
        if item.get("requirement_type") == "information":
            label = item.get("document_name", "Additional information")
            if label not in seen:
                seen.add(label)
                requirements.append({"field": None, "label": label, "status": "missing", "source": item.get("evidence")})
    return requirements


def _applicable_documents(approval: dict) -> list[dict]:
    required = []
    conditions = approval.get("conditions", [])
    has_unresolved = any(item.get("result") is None for item in conditions)
    for item in approval.get("required_documents", []):
        requirement_type = item.get("requirement_type", "mandatory")
        if requirement_type == "information":
            continue
        if requirement_type == "conditional" and not has_unresolved and not any(x.get("result") is True for x in conditions):
            continue
        required.append(item)
    return required


async def _recheck_document_type(document: dict, business_id: ObjectId, db) -> None:
    """Re-evaluate older uploads once with the current type matcher."""
    if document.get("validation_status") != "needs_review" or not document.get("storage_key"):
        return
    checked_at = document.get("type_validation_checked_at")
    if isinstance(checked_at, datetime) and checked_at.tzinfo is None:
        checked_at = checked_at.replace(tzinfo=timezone.utc)
    if isinstance(checked_at, datetime) and datetime.now(timezone.utc) - checked_at < timedelta(hours=24):
        return
    try:
        content = await storage.read(document["storage_key"])
        pages, extracted, _ = await inspect_pdf(content)
    except (FileNotFoundError, ValueError, OSError):
        return
    source_text = "\n".join(page.text for page in pages)
    status, error = validate_document_type(document.get("document_type", ""),
                                           document.get("file_name", ""), source_text)
    update = {"validation_status": status, "validation_errors": [error] if error else [],
              "type_validation_checked_at": datetime.now(timezone.utc)}
    if status == "passed":
        update["extracted_data"] = extracted
    await db.business_documents.update_one({"_id": document["_id"], "business_id": business_id,
                                            "validation_status": "needs_review"}, {"$set": update})
    document.update(update)


def _compare_document_data(business: dict, document: dict) -> list[dict]:
    extracted = document.get("extracted_data") or {}
    candidates = {
        "business_name": [business.get("legal_name"), business.get("display_name")],
        "owner_name": [(business.get("contact_person") or {}).get("name")],
        "business_address": [" ".join(str(value) for address in (business.get("registered_address") or {}, business.get("operating_address") or {})
                                      for value in address.values() if value)],
        "registration_number": [business.get("registration_number")],
        "pan": [business.get("pan")],
        "gstin": [business.get("gstin")],
        "email": [business.get("email")],
        "phone": [business.get("phone")],
        "production_capacity_kg_per_day": [business.get("production_capacity_kg_per_day")],
        "annual_turnover": [business.get("annual_turnover")],
    }
    mismatches = []
    for field, expected_values in candidates.items():
        actual = extracted.get(field)
        expected_values = [str(value).strip() for value in expected_values if value not in (None, "")]
        if actual and expected_values:
            def clean(value):
                if field in {"production_capacity_kg_per_day", "annual_turnover"}:
                    match = re.search(r"[0-9][0-9,.]*", str(value))
                    if not match: return None
                    number = float(match.group(0).replace(",", ""))
                    lowered = str(value).casefold()
                    if field == "annual_turnover":
                        if "crore" in lowered: number *= 10_000_000
                        elif "lakh" in lowered or "lac" in lowered: number *= 100_000
                        elif "million" in lowered: number *= 1_000_000
                        elif "billion" in lowered: number *= 1_000_000_000
                    return round(number, 3)
                return re.sub(r"[^a-z0-9]", "", str(value).casefold())
            actual_value = clean(actual)
            expected_clean = [clean(expected) for expected in expected_values]
            if field == "business_address":
                matches = any(expected and actual_value and len(actual_value) >= 6
                              and (actual_value in expected or expected in actual_value) for expected in expected_clean)
            else:
                matches = actual_value is not None and any(expected is not None and actual_value == expected for expected in expected_clean)
            if actual_value is not None and not matches:
                mismatches.append({"field": field, "expected": expected_values[0], "actual": actual,
                                   "status": "mismatch", "document_id": str(document["_id"])})
    return mismatches


async def calculate_readiness(application: dict, *, persist_links: bool = True) -> dict:
    db = get_database()
    business_id = application["business_id"]
    business = await db.businesses.find_one({"_id": business_id}) or {}
    passport = await db.approval_passports.find_one({"business_id": business_id})
    if not passport:
        from app.services.passport_service import ensure_passport
        passport = await ensure_passport(business_id, db=db)

    # Draft applications follow the newest Phase 5 evaluation. Submitted records freeze the
    # requirements and document versions captured at submission.
    if application.get("status") != "draft" and application.get("readiness_snapshot"):
        return {**application["readiness_snapshot"], "application_id": str(application["_id"]),
                "readiness_status": application["readiness_status"], "can_submit": False}

    generation_id = business.get("approval_generation_id")
    approval = None
    if generation_id:
        candidates = await db.approval_results.find({"business_id": business_id, "generation_id": generation_id}).to_list(200)
        original_name = normalize_document_type(application.get("approval_name", ""))
        approval = next((item for item in candidates if normalize_document_type(item.get("approval_name", "")) == original_name), None)
    if not approval:
        return {"application_id": str(application["_id"]), "readiness_status": "blocked", "readiness_percentage": 0,
                "required_information": [], "required_documents": [], "validation": {"passed": False, "mismatches": [], "errors": ["Approval is no longer present in the current roadmap."]},
                "missing_documents": [], "mismatches": [], "can_submit": False, "passport_id": str(passport["_id"])}

    information = _missing_information(approval, business)
    raw_requirements = _applicable_documents(approval)
    all_documents = await db.business_documents.find({"business_id": business_id}).sort("version", -1).to_list(1000)
    today = datetime.now(timezone.utc).date()
    def expiry_state(item):
        expiry = item.get("expiry_date")
        if expiry is None: return "unknown"
        if isinstance(expiry, datetime): expiry = expiry.date()
        elif isinstance(expiry, str):
            try: expiry = date.fromisoformat(expiry[:10])
            except ValueError: return "unknown"
        if expiry < today: return "expired"
        if expiry <= today + timedelta(days=30): return "expiring_soon"
        return "valid"
    # Keep every current upload in the evaluation. An unreadable/rejected file is
    # different from no upload and should be shown as such in the readiness flow.
    available = [item for item in all_documents if item.get("lifecycle_status", "active") == "active"
                 and expiry_state(item) != "expired"]
    requirement_results = []
    missing_documents = []
    linked_ids = []
    mismatch_results = []
    for requirement in raw_requirements:
        name = requirement.get("document_name", "Required document")
        requirement_key = normalize_document_type(name)
        matching = next((doc for doc in available if normalize_document_type(doc.get("document_type", "")) == requirement_key), None)
        if matching:
            await _recheck_document_type(matching, business_id, db)
        if not matching:
            status = "missing"
        elif matching.get("verification_status") in {"rejected", "needs_update"}:
            status = "needs_update"
        elif matching.get("validation_status") == "failed":
            status = "invalid"
        elif matching.get("validation_status") != "passed":
            status = "needs_review"
        else:
            status = "present"
        requirement_results.append({"document_type": name, "requirement_type": requirement.get("requirement_type", "mandatory"),
                                    "status": status, "document_id": str(matching["_id"]) if matching else None,
                                    "version": matching.get("version") if matching else None,
                                    "verification_status": matching.get("verification_status") if matching else None,
                                    "validation_status": matching.get("validation_status") if matching else None,
                                    "validation_errors": matching.get("validation_errors", []) if matching else [],
                                    "uploaded": matching is not None,
                                    "expiry_status": expiry_state(matching) if matching else "unknown",
                                    "evidence": requirement.get("evidence")})
        if matching and status == "present":
            document_mismatches = _compare_document_data(business, matching)
            if document_mismatches:
                status = "mismatch"
                requirement_results[-1]["status"] = status
                mismatch_results.extend(document_mismatches)
            else:
                linked_ids.append(matching["_id"])
        if status != "present":
            missing_documents.append(name)

    if persist_links and linked_ids:
        for document_id in set(linked_ids):
            await db.business_documents.update_one({"_id": document_id, "business_id": business_id},
                                                   {"$addToSet": {"used_for_approvals": approval["_id"]}})
        existing_links = application.get("document_links", [])
        selected_links = [{"document_id": doc["_id"], "version": doc.get("version", 1)}
                          for doc in available if doc["_id"] in set(linked_ids)]
        merged_links = {str(link.get("document_id")): link for link in existing_links if link.get("document_id")}
        merged_links.update({str(link["document_id"]): link for link in selected_links})
        await db.applications.update_one({"_id": application["_id"], "business_id": business_id},
                                         {"$set": {"passport_id": passport["_id"],
                                                    "document_links": list(merged_links.values())}})

    base_requirement_count = len(information) + len(requirement_results)
    applicability_resolved = approval.get("status") in {"mandatory", "conditional"}
    required_count = base_requirement_count
    complete_count = (sum(item["status"] == "complete" for item in information) +
                      sum(item["status"] == "present" for item in requirement_results))
    # An approval with no checkable requirements is not falsely reported ready.
    percentage = round(100 * complete_count / required_count) if required_count else 0
    validation_errors = []
    for item in requirement_results:
        if item["status"] == "needs_review":
            validation_errors.append(f"{item['document_type']} needs a document type review.")
        elif item["status"] == "invalid":
            validation_errors.append(f"{item['document_type']} did not pass document validation.")
        elif item["status"] == "needs_update":
            validation_errors.append(f"{item['document_type']} was rejected or needs an updated version.")
    critical_mismatches = mismatch_results
    if critical_mismatches:
        readiness_status = "blocked"
    elif not required_count or information or missing_documents or validation_errors:
        readiness_status = "not_ready" if complete_count == 0 else "in_progress"
    else:
        readiness_status = "ready"
    if not applicability_resolved:
        readiness_status = "blocked"
        percentage = 0
        validation_errors.append("Approval applicability must be resolved before submission.")
    can_submit = readiness_status == "ready" and percentage == 100
    information_complete = all(item["status"] == "complete" for item in information)
    documents_complete = all(item["status"] == "present" for item in requirement_results)
    identity_fields = {"business_name", "owner_name", "registration_number", "pan", "gstin"}
    has_identity_data = any(item.get("document_id") and any(
        document.get("_id") and str(document["_id"]) == item["document_id"] and
        identity_fields.intersection((document.get("extracted_data") or {}).keys()) for document in all_documents
    ) for item in requirement_results)
    name_check = ("mismatch" if any(item["field"] in identity_fields for item in mismatch_results) else
                  "complete" if has_identity_data else "not_checked")
    profile_has_address = any(value for address in (business.get("registered_address") or {}, business.get("operating_address") or {})
                              for value in address.values())
    document_has_address = any((document.get("extracted_data") or {}).get("business_address") for document in all_documents
                               if document.get("lifecycle_status", "active") == "active")
    address_check = ("mismatch" if any(item["field"] == "business_address" for item in mismatch_results) else
                     "complete" if profile_has_address and document_has_address else "not_checked")
    checks = [
        {"key": "applicability", "label": "Approval applicability confirmed",
         "status": "complete" if applicability_resolved else "incomplete",
         "detail": "The roadmap identifies this approval as applicable." if applicability_resolved else "Resolve the approval conditions before preparing an application."},
        {"key": "business_information", "label": "Business information complete",
         "status": "complete" if information_complete else "incomplete",
         "detail": "All information requested for this approval is available." if information_complete else "Complete the business information requested for this approval."},
        {"key": "required_documents", "label": "Required documents uploaded",
         "status": "complete" if documents_complete else "incomplete",
         "detail": f"{sum(item['status'] == 'present' for item in requirement_results)} of {len(requirement_results)} required documents are usable."},
        {"key": "document_names", "label": "Document details match",
         "status": name_check,
         "detail": "Extracted business identity details match the profile." if name_check == "complete" else
                   "Resolve the business identity mismatch shown below." if name_check == "mismatch" else
                   "No readable identity details were available to compare."},
        {"key": "address", "label": "Address matches profile",
         "status": address_check,
         "detail": "An uploaded document contains an address that differs from the business profile." if address_check == "mismatch" else
                   "Readable document address details match the business profile." if address_check == "complete" else
                   "No readable address was available to compare with the profile."},
    ]
    readiness = {"application_id": str(application["_id"]), "passport_id": str(passport["_id"]),
            "approval_id": str(approval["_id"]), "approval_name": approval.get("approval_name"),
            "readiness_status": readiness_status, "readiness_percentage": percentage,
            "required_information": information, "required_documents": requirement_results,
            "checks": checks,
            "missing_documents": missing_documents, "mismatches": mismatch_results,
            "validation": {"passed": not validation_errors and not mismatch_results, "mismatches": mismatch_results,
                           "errors": validation_errors}, "can_submit": can_submit}
    await db.applications.update_one({"_id": application["_id"], "business_id": business_id},
                                     {"$set": {"readiness_status": readiness_status,
                                                "readiness_percentage": percentage,
                                                "readiness_checked_at": datetime.now(timezone.utc)}})
    return readiness
