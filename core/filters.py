import uuid

from django.utils.dateparse import parse_date
from rest_framework.exceptions import ValidationError


def _convert(kind, raw, param):
    try:
        if kind == "uuid":
            return uuid.UUID(raw)
        if kind == "date":
            value = parse_date(raw)
            if value is None:
                raise ValueError
            return value
        if kind == "bool":
            if raw.lower() not in ("true", "false"):
                raise ValueError
            return raw.lower() == "true"
        if kind == "list":
            return [v.strip() for v in raw.split(",") if v.strip()]
        if kind == "uuidlist":
            return [uuid.UUID(v.strip()) for v in raw.split(",") if v.strip()]
        if kind == "int":
            return int(raw)
    except ValueError:
        raise ValidationError({"error": f"Invalid value for query parameter '{param}': {raw!r} (expected {kind})."})
    return raw


def apply_filters(qs, params, spec):
    """spec: {query_param: (orm_lookup, kind)} -> chained filter() calls."""
    for param, (lookup, kind) in spec.items():
        raw = params.get(param)
        if raw not in (None, ""):
            qs = qs.filter(**{lookup: _convert(kind, raw, param)})
    return qs
