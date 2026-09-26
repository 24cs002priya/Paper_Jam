from dataclasses import dataclass
from typing import Any

from app.schemas.approval_engine import ConditionNode


@dataclass
class Evaluation:
    result: bool | None
    leaves: list[dict[str, Any]]


def _lookup(context: dict[str, Any], path: str) -> tuple[bool, Any]:
    value: Any = context
    for part in path.split("."):
        if not isinstance(value, dict) or part not in value:
            return False, None
        value = value[part]
    return value is not None, value


def _contains(actual: Any, expected: Any) -> bool:
    if isinstance(actual, (list, tuple, set)):
        return any(_contains(item, expected) for item in actual)
    if isinstance(actual, str) and isinstance(expected, str):
        return expected.casefold() in actual.casefold()
    if isinstance(actual, dict):
        return expected in actual
    return actual == expected


def _compare(actual: Any, operator: str, expected: Any, exists: bool) -> bool | None:
    if operator == "exists": return exists
    if operator == "not_exists": return not exists
    if not exists: return None
    if operator == "equals": return actual == expected
    if operator == "not_equals": return actual != expected
    if operator == "contains": return _contains(actual, expected)
    if operator == "not_contains": return not _contains(actual, expected)
    if operator in {"in", "not_in"}:
        if not isinstance(expected, (list, tuple, set, str)): return None
        found = actual in expected
        return found if operator == "in" else not found
    if operator in {"greater_than", "greater_than_or_equal", "less_than", "less_than_or_equal"}:
        try:
            left, right = float(actual), float(expected)
        except (TypeError, ValueError):
            return None
        return {"greater_than": left > right, "greater_than_or_equal": left >= right,
                "less_than": left < right, "less_than_or_equal": left <= right}[operator]
    return None


def describe_condition(node: ConditionNode) -> str:
    if node.kind == "condition":
        return f"{node.field or 'unknown field'} {node.operator or 'is'} {node.value!r}"
    if node.kind == "not":
        return f"NOT ({describe_condition(node.children[0])})" if node.children else "NOT (incomplete condition)"
    return f" {node.kind.upper()} ".join(f"({describe_condition(child)})" for child in node.children)


def evaluate_condition(node: ConditionNode, context: dict[str, Any]) -> Evaluation:
    if node.kind == "condition":
        if not node.field or not node.operator:
            return Evaluation(None, [{"condition": describe_condition(node), "result": None, "field": node.field, "evidence": None}])
        exists, actual = _lookup(context, node.field)
        result = _compare(actual, node.operator, node.value, exists)
        evidence = f"{node.field} = {actual!r}" if exists else None
        return Evaluation(result, [{"condition": describe_condition(node), "result": result, "field": node.field, "evidence": evidence}])
    children = [evaluate_condition(child, context) for child in node.children]
    values = [child.result for child in children]
    if node.kind == "not":
        result = None if not values or values[0] is None else not values[0]
    elif node.kind == "and":
        result = False if False in values else True if all(value is True for value in values) and values else None
    elif node.kind == "or":
        result = True if True in values else False if all(value is False for value in values) and values else None
    else:
        result = None
    return Evaluation(result, [leaf for child in children for leaf in child.leaves])
