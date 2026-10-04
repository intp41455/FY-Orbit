"""Function/Tool Calling 注册·发现·调用 HTTP surface (P1-05).

Wraps the dynamic :mod:`services.tool_registry` (distinct from the static
``skills.harness`` gateway):

* ``POST /api/tools/register``       — log a tool (name/description/JSON Schema/entry)
* ``GET  /api/tools/discover``       — list every registered tool (discovery)
* ``GET  /api/tools/calls``          — recent call receipts (evidence trail)
* ``POST /api/tools/{name}/invoke``  — validate against schema and really execute

The registry persists to ``.runtime/tool_registry/`` (memory + JSON file, no
migration), so registered tools survive a backend restart.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel, Field, model_validator

from ..deps import csrf_protected, get_actor
from ...services.tool_registry import tool_registry

router = APIRouter(prefix="/api/tools", tags=["tool-calling"])


class ToolEntryIn(BaseModel):
    type: str = Field(pattern="^(builtin|http|mcp)$")
    executor: str | None = None
    url: str | None = None
    server: str | None = None
    remote_tool: str | None = None

    @model_validator(mode="after")
    def validate_mcp_fields(self):
        if self.type == "mcp" and not (self.server and self.remote_tool):
            raise ValueError("mcp entry requires 'server' and 'remote_tool'")
        return self


class ToolRegisterBody(BaseModel):
    name: str = Field(min_length=2, max_length=64)
    description: str = Field(min_length=1, max_length=2000)
    parameters: dict = Field(default_factory=dict)
    entry: ToolEntryIn


class ToolInvokeBody(BaseModel):
    arguments: dict = Field(default_factory=dict)


@router.post("/register")
def register_tool(body: ToolRegisterBody, actor: object = Depends(csrf_protected)) -> dict:
    meta = tool_registry.register(
        name=body.name,
        description=body.description,
        parameters=body.parameters,
        entry=body.entry.model_dump(exclude_none=True),
    )
    return {"registered": True, "tool": meta}


@router.get("/discover")
def discover_tools(actor: object = Depends(get_actor)) -> dict:
    tools = tool_registry.list_tools()
    return {"count": len(tools), "tools": tools}


@router.get("/calls")
def call_history(actor: object = Depends(get_actor),
                 limit: int = Query(default=50, ge=1, le=500)) -> dict:
    calls = tool_registry.recent_calls(limit=limit)
    return {"count": len(calls), "calls": calls}


@router.post("/{tool_name}/invoke")
def invoke_tool(tool_name: str, body: ToolInvokeBody,
                actor: object = Depends(csrf_protected)) -> dict:
    return tool_registry.invoke(tool_name, body.arguments)
