from app.schemas.approval_engine import ExtractedApprovalRule
from app.services.approval_engine.condition_evaluator import describe_condition, evaluate_condition


def decide_rule(rule: ExtractedApprovalRule, context: dict) -> dict:
    evaluations = [evaluate_condition(condition, context) for condition in rule.conditions]
    leaves = [leaf for evaluation in evaluations for leaf in evaluation.leaves]
    if any(evaluation.result is False for evaluation in evaluations):
        status = "not_applicable"
    elif any(evaluation.result is None for evaluation in evaluations):
        status = "needs_information"
    elif rule.rule_type == "conditional":
        status = "conditional"
    else:
        status = "mandatory"
    if not rule.conditions:
        status = "mandatory" if rule.rule_type == "mandatory" else "conditional"
    return {
        "status": status,
        "conditions": leaves,
        "missing_information": list(dict.fromkeys(
            item["field"] for item in leaves if item["result"] is None and item.get("field")
        )),
        "condition_descriptions": [describe_condition(condition) for condition in rule.conditions],
    }
