"""Turn whatever the caller has into the JSON Schema the API expects.

Pydantic is not a dependency and is never imported. A model is recognised by the method it carries,
so any object that can describe itself as JSON Schema works, and someone who has no use for
pydantic still installs this package with one dependency.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from .errors import HydrafetchError

MAX_REF_DEPTH = 12


def to_json_schema(schema: Any) -> dict[str, Any]:
    if isinstance(schema, Mapping):
        return inline_refs(dict(schema))

    for method in ("model_json_schema", "schema"):
        describe = getattr(schema, method, None)
        if callable(describe):
            return inline_refs(describe())

    raise HydrafetchError(
        "schema must be a dict of JSON Schema or a pydantic model.",
        code="SCHEMA_CONVERSION_FAILED",
        status=0,
    )


def inline_refs(schema: dict[str, Any]) -> dict[str, Any]:
    """Replace every local $ref with the thing it points at, and drop the definitions block.

    A nested model is the ordinary case, not an exotic one, and pydantic always describes it as a
    $ref into $defs. That indirection does not survive the trip to a model's tool schema, so
    forwarding it turns a normal two-model extraction into a confusing failure from somewhere the
    caller cannot see. Resolving it here costs nothing and keeps the surface honest.
    """
    defs = {**schema.get("$defs", {}), **schema.get("definitions", {})}
    if not defs:
        return schema

    def resolve(node: Any, depth: int) -> Any:
        if isinstance(node, list):
            return [resolve(item, depth) for item in node]
        if not isinstance(node, dict):
            return node

        target = node.get("$ref")
        if isinstance(target, str) and target.startswith("#/"):
            name = target.rsplit("/", 1)[-1]
            # A self-referential model has no finite inlining. Leaving the $ref in place is worse
            # than saying so, because the failure would surface as an opaque error from the API.
            if depth >= MAX_REF_DEPTH or name not in defs:
                raise HydrafetchError(
                    f"schema reference {target} is recursive or missing, so it cannot be inlined. "
                    "Flatten the model or pass JSON Schema directly.",
                    code="SCHEMA_CONVERSION_FAILED",
                    status=0,
                )
            merged = {**defs[name], **{k: v for k, v in node.items() if k != "$ref"}}
            return resolve(merged, depth + 1)

        return {k: resolve(v, depth) for k, v in node.items() if k not in ("$defs", "definitions")}

    return resolve(schema, 0)
