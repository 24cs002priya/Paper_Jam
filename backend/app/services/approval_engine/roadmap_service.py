from app.schemas.approval_engine import ApprovalRoadmap

ACTIVE_STATUSES = {"mandatory", "conditional", "needs_information"}


def build_roadmap(approvals: list[dict]) -> dict:
    grouped = {status: [] for status in ("mandatory", "conditional", "needs_information")}
    edges = []
    for item in approvals:
        if item["status"] in grouped:
            grouped[item["status"]].append(item["approval_id"])
    for item in approvals:
        if item["status"] not in ACTIVE_STATUSES:
            continue
        for dependency_id in item.get("depends_on", []):
            dependency = next((row for row in approvals if row["approval_id"] == dependency_id), None)
            if dependency and dependency["status"] in ACTIVE_STATUSES:
                edges.append({"approval_id": dependency_id, "must_precede": item["approval_id"]})
    return ApprovalRoadmap.model_validate({
        "status": "generated" if any(item["status"] in ACTIVE_STATUSES for item in approvals) else "empty",
        "ordering_basis": "explicit_regulatory_dependencies" if edges else "status_groups_only",
        "groups": grouped,
        "dependency_edges": edges,
        "approval_names": {item["approval_id"]: item["approval_name"] for item in approvals if item["status"] in ACTIVE_STATUSES},
    }).model_dump(mode="json")
