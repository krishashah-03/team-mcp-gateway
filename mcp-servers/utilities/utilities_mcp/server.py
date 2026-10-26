import ast
import hmac
import math
import operator
from datetime import datetime
from typing import Annotated, Any
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from mcp.server.mcpserver import MCPServer
from mcp.server.mcpserver.exceptions import ToolError
from mcp.server.transport_security import TransportSecuritySettings
from pydantic import Field
from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import JSONResponse, Response
from starlette.types import ASGIApp, Receive, Scope, Send

from utilities_mcp.config import Settings, get_settings

mcp = MCPServer(
    name="utilities",
    instructions="Small, side-effect-free helpers: the time anywhere, unit conversion, arithmetic.",
)


@mcp.tool()
def current_time(
    timezone: Annotated[
        str, Field(description="IANA timezone name, e.g. Asia/Kolkata, Europe/London, UTC")
    ] = "UTC",
) -> dict[str, str]:
    """Get the current date and time in a timezone."""
    try:
        zone = ZoneInfo(timezone)
    except (ZoneInfoNotFoundError, ValueError):
        raise ToolError(
            f"Unknown timezone '{timezone}'. Use an IANA name such as Asia/Kolkata."
        ) from None
    now = datetime.now(zone)
    return {
        "timezone": timezone,
        "iso": now.isoformat(timespec="seconds"),
        "date": now.date().isoformat(),
        "time": now.strftime("%H:%M:%S"),
        "weekday": now.strftime("%A"),
    }


# Linear units are expressed as a factor to a base unit; temperature needs offsets.
_LINEAR_UNITS: dict[str, dict[str, float]] = {
    "length": {
        "mm": 0.001, "cm": 0.01, "m": 1.0, "km": 1000.0,
        "in": 0.0254, "ft": 0.3048, "yd": 0.9144, "mi": 1609.344,
    },
    "mass": {"mg": 1e-6, "g": 0.001, "kg": 1.0, "t": 1000.0, "oz": 0.028349523125, "lb": 0.45359237},
    "volume": {"ml": 0.001, "l": 1.0, "tsp": 0.00492892159375, "tbsp": 0.01478676478125,
               "cup": 0.2365882365, "gal": 3.785411784},
}
_TEMPERATURE_UNITS = {"c", "f", "k"}


def _to_celsius(value: float, unit: str) -> float:
    return {"c": value, "f": (value - 32) * 5 / 9, "k": value - 273.15}[unit]


def _from_celsius(value: float, unit: str) -> float:
    return {"c": value, "f": value * 9 / 5 + 32, "k": value + 273.15}[unit]


@mcp.tool()
def convert_units(
    value: Annotated[float, Field(description="The number to convert")],
    from_unit: Annotated[str, Field(description="Unit to convert from, e.g. km, lb, c")],
    to_unit: Annotated[str, Field(description="Unit to convert to, e.g. mi, kg, f")],
) -> dict[str, Any]:
    """Convert between units of length, mass, volume or temperature."""
    source, target = from_unit.strip().lower(), to_unit.strip().lower()

    if source in _TEMPERATURE_UNITS and target in _TEMPERATURE_UNITS:
        result = _from_celsius(_to_celsius(value, source), target)
    else:
        for units in _LINEAR_UNITS.values():
            if source in units and target in units:
                result = value * units[source] / units[target]
                break
        else:
            supported = sorted({u for g in _LINEAR_UNITS.values() for u in g} | _TEMPERATURE_UNITS)
            raise ToolError(
                f"Cannot convert '{from_unit}' to '{to_unit}'. Units must be of the same "
                f"kind. Supported: {', '.join(supported)}"
            )
    return {"value": value, "from": source, "to": target, "result": round(result, 6)}


_BINARY_OPS = {
    ast.Add: operator.add,
    ast.Sub: operator.sub,
    ast.Mult: operator.mul,
    ast.Div: operator.truediv,
    ast.FloorDiv: operator.floordiv,
    ast.Mod: operator.mod,
    ast.Pow: operator.pow,
}
_UNARY_OPS = {ast.UAdd: operator.pos, ast.USub: operator.neg}
_FUNCTIONS = {"sqrt": math.sqrt, "abs": abs, "round": round, "floor": math.floor, "ceil": math.ceil}
_MAX_EXPRESSION_LENGTH = 200
_MAX_EXPONENT = 1000


def _evaluate(node: ast.AST) -> float:
    """Evaluate arithmetic only. Anything else (names, attributes, calls to
    arbitrary functions) is rejected, so this can never execute code."""
    if isinstance(node, ast.Expression):
        return _evaluate(node.body)
    if isinstance(node, ast.Constant) and isinstance(node.value, (int, float)) and not isinstance(node.value, bool):
        return node.value
    if isinstance(node, ast.BinOp) and type(node.op) in _BINARY_OPS:
        left, right = _evaluate(node.left), _evaluate(node.right)
        if isinstance(node.op, ast.Pow) and abs(right) > _MAX_EXPONENT:
            raise ToolError("Exponent is too large")
        return _BINARY_OPS[type(node.op)](left, right)
    if isinstance(node, ast.UnaryOp) and type(node.op) in _UNARY_OPS:
        return _UNARY_OPS[type(node.op)](_evaluate(node.operand))
    if (
        isinstance(node, ast.Call)
        and isinstance(node.func, ast.Name)
        and node.func.id in _FUNCTIONS
        and not node.keywords
    ):
        return _FUNCTIONS[node.func.id](*[_evaluate(arg) for arg in node.args])
    raise ToolError("Only numbers, + - * / // % **, parentheses and sqrt/abs/round/floor/ceil are allowed")


@mcp.tool()
def calculate(
    expression: Annotated[
        str, Field(max_length=_MAX_EXPRESSION_LENGTH, description="Arithmetic, e.g. (349.5 * 3) * 1.18")
    ],
) -> dict[str, Any]:
    """Evaluate an arithmetic expression exactly as written, e.g. order totals with tax."""
    try:
        tree = ast.parse(expression.strip(), mode="eval")
        result = _evaluate(tree)
    except ZeroDivisionError:
        raise ToolError("Division by zero") from None
    except (SyntaxError, ValueError, TypeError, OverflowError):
        raise ToolError("Could not evaluate that expression") from None
    return {"expression": expression.strip(), "result": result}


class _BearerAuthMiddleware:
    """Rejects requests without the gateway's shared secret; /healthz stays open."""

    def __init__(self, app: ASGIApp, token: str) -> None:
        self._app = app
        self._token = token.encode()

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] == "http" and scope["path"] != "/healthz":
            header = Request(scope).headers.get("authorization", "")
            scheme, _, supplied = header.partition(" ")
            if scheme.lower() != "bearer" or not hmac.compare_digest(
                supplied.strip().encode(), self._token
            ):
                response: Response = JSONResponse({"error": "unauthorized"}, status_code=401)
                await response(scope, receive, send)
                return
        await self._app(scope, receive, send)


def build_app(settings: Settings | None = None) -> Starlette:
    settings = settings or get_settings()
    app = mcp.streamable_http_app(
        transport_security=TransportSecuritySettings(
            enable_dns_rebinding_protection=True,
            allowed_hosts=settings.allowed_hosts,
        ),
    )

    async def healthz(_: Request) -> Response:
        return JSONResponse({"status": "ok"})

    app.add_route("/healthz", healthz, methods=["GET"])
    if settings.mcp_auth_token:
        app.add_middleware(_BearerAuthMiddleware, token=settings.mcp_auth_token)
    return app
