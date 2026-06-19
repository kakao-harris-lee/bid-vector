"""Regression guards for OpenAPI tag hygiene.

These lock in the convention that each router is tagged once, at include time in
``app/api/routes.py``. A router that *also* self-declares a tag (as
``app/api/synthetic.py`` previously did with ``tags=["synthetic"]``) produces a
case-split in the spec — endpoints surface under both ``Synthetic`` and
``synthetic`` — which fans the API-doc pipeline into two clashing documents.
"""

from app.main import app

_HTTP_METHODS = {"get", "post", "put", "patch", "delete", "options", "head"}


def _operation_tags():
    """Yield the tag list of every operation in the generated OpenAPI spec."""
    spec = app.openapi()
    for path_item in spec.get("paths", {}).values():
        for method, operation in path_item.items():
            if method.lower() in _HTTP_METHODS and isinstance(operation, dict):
                yield operation.get("tags", [])


def test_no_case_variant_duplicate_tags():
    """No two API tags may differ only by case."""
    all_tags = {tag for tags in _operation_tags() for tag in tags}
    by_lower: dict[str, set[str]] = {}
    for tag in all_tags:
        by_lower.setdefault(tag.lower(), set()).add(tag)
    collisions = {lower: variants for lower, variants in by_lower.items() if len(variants) > 1}
    assert not collisions, f"case-variant duplicate tags in OpenAPI spec: {collisions}"


def test_synthetic_endpoints_tagged_titlecase_only():
    """Synthetic router endpoints expose only the Title-case 'Synthetic' tag."""
    spec = app.openapi()
    synthetic_tags: set[str] = set()
    for path, path_item in spec.get("paths", {}).items():
        if path.startswith("/api/v1/synthetic"):
            for method, operation in path_item.items():
                if method.lower() in _HTTP_METHODS and isinstance(operation, dict):
                    synthetic_tags.update(operation.get("tags", []))

    assert synthetic_tags, "expected at least one /api/v1/synthetic endpoint"
    assert "synthetic" not in synthetic_tags
    assert synthetic_tags == {"Synthetic"}
