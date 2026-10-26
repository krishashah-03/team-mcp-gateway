from fastapi import APIRouter

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
