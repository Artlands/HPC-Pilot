"""Typed tool framework. See spec 00 §3.1.

Every tool is a function decorated with @tool, declaring a Pydantic input model. The
decorator registers it and exposes JSON schema for LLM tool-calling.
"""

from __future__ import annotations

from collections.abc import Callable
from enum import StrEnum
from typing import Any, get_type_hints

from pydantic import BaseModel

from hpc_agent.tools.result import ToolResult


class Risk(StrEnum):
    READ = "read"  # no mutation, auto-run
    LOW = "low"  # reversible, in-policy auto-run allowed
    MEDIUM = "medium"  # mutation, approval unless within policy bounds
    HIGH = "high"  # destructive / wide blast radius, always approval


class ToolMeta(BaseModel):
    name: str
    domain: str
    risk: Risk
    input_model: type[BaseModel]
    description: str

    model_config = {"arbitrary_types_allowed": True}

    @property
    def capability(self) -> str:
        return self.name  # already "domain.tool"

    def json_schema(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "description": self.description,
            "input_schema": self.input_model.model_json_schema(),
        }


ToolFn = Callable[..., ToolResult]

_REGISTRY: dict[str, tuple[ToolMeta, ToolFn, Callable[[BaseModel], int]]] = {}


def tool(
    *,
    name: str,
    risk: Risk,
    domain: str,
    blast_radius: Callable[[BaseModel], int] = lambda _inp: 1,
) -> Callable[[ToolFn], ToolFn]:
    """Register a tool. The wrapped function takes (input_model_instance, *, actor, ...)
    and returns a ToolResult.

    `blast_radius` maps the validated input to the number of entities affected, for cap
    enforcement (spec 01 §6).
    """

    def decorator(fn: ToolFn) -> ToolFn:
        hints = get_type_hints(fn)
        # First positional parameter's annotation is the input model.
        params = list(fn.__code__.co_varnames[: fn.__code__.co_argcount])
        input_param = params[0]
        input_model = hints[input_param]
        if not (isinstance(input_model, type) and issubclass(input_model, BaseModel)):
            raise TypeError(f"{name}: first arg must be a Pydantic model, got {input_model!r}")

        meta = ToolMeta(
            name=name,
            domain=domain,
            risk=risk,
            input_model=input_model,
            description=(fn.__doc__ or "").strip().split("\n")[0],
        )
        _REGISTRY[name] = (meta, fn, blast_radius)
        fn.__tool_meta__ = meta  # type: ignore[attr-defined]
        return fn

    return decorator


def get_tool(name: str) -> tuple[ToolMeta, ToolFn, Callable[[BaseModel], int]]:
    if name not in _REGISTRY:
        raise KeyError(f"unknown tool: {name}")
    return _REGISTRY[name]


def all_tools() -> list[ToolMeta]:
    return [meta for meta, _fn, _br in _REGISTRY.values()]


def tool_schemas() -> list[dict[str, Any]]:
    return [meta.json_schema() for meta in all_tools()]
