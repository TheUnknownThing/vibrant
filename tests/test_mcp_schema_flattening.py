from __future__ import annotations

from vibrant.orchestrator.interface.mcp.fastmcp_host import _flatten_local_ref_schema


def test_flatten_local_ref_schema_inlines_object_array_items() -> None:
    schema = {
        "$defs": {
            "SemanticExpression": {
                "type": "object",
                "required": ["table", "name"],
                "properties": {
                    "table": {"type": "string"},
                    "name": {"type": "string"},
                },
            }
        },
        "type": "object",
        "properties": {
            "dimensions": {
                "type": "array",
                "items": {"$ref": "#/$defs/SemanticExpression"},
            }
        },
    }

    flattened = _flatten_local_ref_schema(schema)

    assert "$defs" not in flattened
    assert flattened["properties"]["dimensions"]["items"] == {
        "type": "object",
        "required": ["table", "name"],
        "properties": {
            "table": {"type": "string"},
            "name": {"type": "string"},
        },
    }


def test_flatten_local_ref_schema_preserves_external_refs() -> None:
    schema = {
        "type": "object",
        "properties": {
            "payload": {
                "$ref": "https://example.com/schemas/payload.json",
            }
        },
    }

    flattened = _flatten_local_ref_schema(schema)

    assert flattened == schema
