from copy import deepcopy
from typing import Any

from app.schemas.approval_engine import RuleExtraction


_UNSUPPORTED_ANNOTATIONS = {
    "default", "title", "description", "examples", "minLength", "maxLength",
    "minimum", "maximum", "minItems", "maxItems",
}


def approval_extraction_schema() -> dict[str, Any]:
    """Return a Groq strict-mode schema derived from the runtime Pydantic model."""
    schema = deepcopy(RuleExtraction.model_json_schema())

    def normalize(node: Any) -> Any:
        if isinstance(node, list):
            return [normalize(item) for item in node]
        if not isinstance(node, dict):
            return node

        result = {key: normalize(value) for key, value in node.items() if key not in _UNSUPPORTED_ANNOTATIONS}
        if result.get("type") == "object":
            properties = result.get("properties", {})
            result["required"] = list(properties)
            result["additionalProperties"] = False
        return result

    strict_schema = normalize(schema)
    definitions = strict_schema["$defs"]
    # The runtime model permits arbitrary-depth condition trees. The extraction
    # response uses one logical group level with leaf conditions; it covers the
    # common AND/OR/NOT clauses while keeping constrained decoding tractable.
    # Pydantic still validates the resulting tree before it enters the engine.
    condition_value = {
        "anyOf": [
            {"type": "string"}, {"type": "number"}, {"type": "boolean"},
            {"type": "array", "items": {"anyOf": [{"type": "string"}, {"type": "number"}]}},
            {"type": "null"},
        ]
    }
    definitions["ConditionLeaf"] = {
        "type": "object",
        "properties": {
            "kind": {"type": "string", "enum": ["condition"]},
            "field": {"anyOf": [{"type": "string"}, {"type": "null"}]},
            "operator": {"anyOf": [
                {"type": "string", "enum": [
                    "equals", "not_equals", "contains", "not_contains", "in", "not_in",
                    "greater_than", "greater_than_or_equal", "less_than", "less_than_or_equal",
                    "exists", "not_exists",
                ]},
                {"type": "null"},
            ]},
            "value": condition_value,
            "evidence": {"anyOf": [{"type": "string"}, {"type": "null"}]},
        },
        "required": ["kind", "field", "operator", "value", "evidence"],
        "additionalProperties": False,
    }
    definitions["ConditionGroup"] = {
        "type": "object",
        "properties": {
            "kind": {"type": "string", "enum": ["and", "or", "not"]},
            "children": {"type": "array", "items": {"$ref": "#/$defs/ConditionLeaf"}},
        },
        "required": ["kind", "children"],
        "additionalProperties": False,
    }
    definitions["ConditionNode"] = {
        "anyOf": [
            {"$ref": "#/$defs/ConditionLeaf"},
            {"$ref": "#/$defs/ConditionGroup"},
        ]
    }
    return strict_schema
