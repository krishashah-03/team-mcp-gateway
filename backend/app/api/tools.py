from typing import Any

from fastapi import APIRouter
from pydantic import BaseModel, Field

from app.api.deps import CurrentUser, Gateway, dashboard_principal
from app.api.schemas import GatewayToolOut

router = APIRouter(tags=["Tools"])


@router.get("/tools", response_model=list[GatewayToolOut])
async def list_tools(user: CurrentUser, gateway: Gateway) -> list[GatewayToolOut]:
    """The tools the gateway currently exposes: those of every enabled, reachable server."""
    tools = await gateway.list_tools(dashboard_principal(user))
    return [
        GatewayToolOut(name=t.name, description=t.description, input_schema=t.input_schema)
        for t in tools
    ]


class ToolCallIn(BaseModel):
    name: str = Field(min_length=3, max_length=130)
    arguments: dict[str, Any] = Field(default_factory=dict)


class ToolCallOut(BaseModel):
    is_error: bool
    text: str


@router.post("/tools/call", response_model=ToolCallOut)
async def call_tool(body: ToolCallIn, user: CurrentUser, gateway: Gateway) -> ToolCallOut:
    """Run one tool as the signed-in user, without involving the chat model.

    Goes through the same path as an MCP client: permissions are enforced and the call is audited.
    """
    result = await gateway.call_tool(dashboard_principal(user), body.name, body.arguments)
    text = "\n".join(c.text for c in result.content if getattr(c, "type", "") == "text")
    return ToolCallOut(is_error=bool(result.is_error), text=text[:20000])
