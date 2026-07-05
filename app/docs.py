from __future__ import annotations

from typing import Any

DOCS_PUBLIC_FLAG = "x-docs-public"


def docs_metadata(
    *,
    public: bool = True,
    summary: str | None = None,
    description: str | None = None,
    openapi_extra: dict[str, Any] | None = None,
    **kwargs: Any,
) -> dict[str, Any]:
    extra = dict(openapi_extra or {})
    extra[DOCS_PUBLIC_FLAG] = public

    route_kwargs = dict(kwargs)
    route_kwargs["openapi_extra"] = extra

    if summary is not None:
        route_kwargs["summary"] = summary
    if description is not None:
        route_kwargs["description"] = description

    return route_kwargs
