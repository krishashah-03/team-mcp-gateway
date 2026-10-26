"""End-to-end check of the running system.

Expects the gateway (:8000) and the WooCommerce MCP server (:8001) to be running and the
bootstrap admin from backend/.env to exist. It starts its own fixture upstream (:8002).

    python tests/e2e_smoke.py
"""

import asyncio
import json
import secrets
import subprocess
import sys
import time
from datetime import date
from pathlib import Path

import httpx
import httpx2
from dotenv import dotenv_values
from mcp import Client
from mcp.client.streamable_http import streamable_http_client

BASE = "http://127.0.0.1:8000"
API = f"{BASE}/api/v1"
ROOT = Path(__file__).resolve().parent.parent
ENV = dotenv_values(ROOT / ".env")
SUFFIX = secrets.token_hex(3)
PASSWORD = "A-long-test-password-1"
_failures: list[str] = []


def check(label: str, condition: bool, detail: str = "") -> None:
    print(f"  [{'PASS' if condition else 'FAIL'}] {label}" + (f"  ({detail})" if detail and not condition else ""))
    if not condition:
        _failures.append(label)


def kill_tree(process: subprocess.Popen) -> None:
    """Stop a child and its descendants (a Windows venv python.exe is a launcher stub)."""
    if process.poll() is None:
        if sys.platform == "win32":
            subprocess.run(["taskkill", "/F", "/T", "/PID", str(process.pid)], capture_output=True)
        else:
            process.terminate()
        process.wait(timeout=10)


def login(client: httpx.Client, email: str, password: str) -> dict[str, str]:
    r = client.post(f"{API}/auth/login", json={"email": email, "password": password})
    r.raise_for_status()
    return {"Authorization": f"Bearer {r.json()['access_token']}"}


async def mcp_session(key: str):
    http = httpx2.AsyncClient(headers={"Authorization": f"Bearer {key}"}, timeout=60)
    return http, Client(streamable_http_client(f"{BASE}/mcp", http_client=http))


async def mcp_tools(key: str) -> list[str]:
    http, client = await mcp_session(key)
    async with http, client:
        return [t.name for t in (await client.list_tools()).tools]


async def mcp_call(key: str, name: str, args: dict | None = None):
    http, client = await mcp_session(key)
    async with http, client:
        return await client.call_tool(name, args or {})


async def main() -> None:
    api = httpx.Client(timeout=60)
    fixture = subprocess.Popen(
        [sys.executable, str(ROOT / "tests/fixtures/fixture_server.py"), "8002"],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    time.sleep(3)
    try:
        print("== Auth and roles")
        admin = login(api, ENV["BOOTSTRAP_ADMIN_EMAIL"], ENV["BOOTSTRAP_ADMIN_PASSWORD"])
        check("bad password rejected", api.post(f"{API}/auth/login", json={"email": ENV["BOOTSTRAP_ADMIN_EMAIL"], "password": "wrong-password"}).status_code == 401)
        check("no token rejected", api.get(f"{API}/servers").status_code == 401)
        check("garbage token rejected", api.get(f"{API}/servers", headers={"Authorization": "Bearer abc"}).status_code == 401)

        user_a = api.post(f"{API}/users", headers=admin, json={"email": f"a-{SUFFIX}@example.com", "password": PASSWORD, "role": "member"}).json()
        user_b = api.post(f"{API}/users", headers=admin, json={"email": f"b-{SUFFIX}@example.com", "password": PASSWORD, "role": "member"}).json()
        a_jwt = login(api, user_a["email"], PASSWORD)
        check("member cannot list servers", api.get(f"{API}/servers", headers=a_jwt).status_code == 403)
        check("member cannot read audit log", api.get(f"{API}/audit", headers=a_jwt).status_code == 403)
        check("member cannot create users", api.post(f"{API}/users", headers=a_jwt, json={"email": "x@example.com", "password": PASSWORD}).status_code == 403)
        check("duplicate user rejected", api.post(f"{API}/users", headers=admin, json={"email": user_a["email"], "password": PASSWORD}).status_code == 409)

        print("== Servers")
        servers = api.get(f"{API}/servers", headers=admin).json()
        woo = next((s for s in servers if s["slug"] == "woocommerce"), None)
        check("woocommerce registered as a server", woo is not None)
        check("woocommerce connected with 5 tools", woo and woo["status"] == "connected" and len(woo["tools"]) == 5, str(woo and woo["status"]))
        check("credential never returned", "credential" not in json.dumps(woo) or woo["has_credential"] is True and ENV["SEED_WOOCOMMERCE_TOKEN"] not in json.dumps(servers))

        r = api.post(f"{API}/servers", headers=admin, json={"name": "Fixture", "slug": "fixture", "endpoint_url": "http://127.0.0.1:8002/mcp"})
        if r.status_code == 409:
            existing = next(s for s in servers if s["slug"] == "fixture")
            r = api.patch(f"{API}/servers/{existing['id']}", headers=admin, json={"is_enabled": True})
        fixture_server = r.json()
        check("fixture server registered and connected", fixture_server["status"] == "connected", str(fixture_server))
        check("bad slug rejected", api.post(f"{API}/servers", headers=admin, json={"name": "x", "slug": "Bad_Slug", "endpoint_url": "http://x.test/mcp"}).status_code == 422)
        check("bad url rejected", api.post(f"{API}/servers", headers=admin, json={"name": "x", "slug": "badurl", "endpoint_url": "ftp://x.test/mcp"}).status_code == 422)

        print("== External MCP client as user B")
        key_b = api.post(f"{API}/users/{user_b['id']}/keys", headers=admin, json={"name": "b-laptop"}).json()
        key_a = api.post(f"{API}/keys", headers=a_jwt, json={"name": "a-cursor"}).json()
        check("key looks like gwk_", key_b["key"].startswith("gwk_"))
        check("listing keys hides the secret", "key" not in api.get(f"{API}/keys", headers=a_jwt).json()[0])

        tools = await mcp_tools(key_b["key"])
        check("tools from both servers listed", "woocommerce__list_orders" in tools and "fixture__echo" in tools, str(tools))
        check("same-named tools do not collide", "woocommerce__list_orders" in tools and "fixture__list_orders" in tools)
        print("    tools:", ", ".join(sorted(tools)))

        orders = await mcp_call(key_b["key"], "woocommerce__list_orders", {"after": date.today().isoformat()})
        check("orders question answered via gateway", not orders.is_error, str(orders.content))
        fixture_orders = await mcp_call(key_b["key"], "fixture__list_orders")
        check("collision routed to the right server", not fixture_orders.is_error and "fixture-order-1" in json.dumps(fixture_orders.structured_content))
        both = [await mcp_call(key_b["key"], "fixture__echo", {"text": "hi"}), await mcp_call(key_b["key"], "woocommerce__list_products", {"per_page": 1})]
        check("tools from two upstreams in one session", all(not r.is_error for r in both))
        unknown = await mcp_call(key_b["key"], "nope__tool")
        check("unknown tool is a clean error", unknown.is_error)

        print("== Timeout and dead upstream")
        started = time.monotonic()
        slow = await mcp_call(key_b["key"], "fixture__slow", {"seconds": 60})
        elapsed = time.monotonic() - started
        check("slow upstream times out with a clear error", slow.is_error and "did not respond" in slow.content[0].text, slow.content[0].text)
        check("timeout respected (<25s)", elapsed < 25, f"{elapsed:.1f}s")

        kill_tree(fixture)
        # Statuses come from a background check that runs every few seconds; allow for a round or two.
        deadline = time.monotonic() + 30
        while True:
            listed = api.get(f"{API}/servers", headers=admin).json()
            dead = next(s for s in listed if s["slug"] == "fixture")
            if dead["status"] == "unreachable" or time.monotonic() > deadline:
                break
            time.sleep(1)
        check("dead server shown as unreachable", dead["status"] == "unreachable", dead["status"])
        check("unreachable server explains why", bool(dead["status_detail"]), str(dead["status_detail"]))
        woo_after = next(s for s in listed if s["slug"] == "woocommerce")
        check("other server still connected", woo_after["status"] == "connected")
        tools_dead = await mcp_tools(key_b["key"])
        check("dead server's tools gone, others remain", "fixture__echo" not in tools_dead and "woocommerce__list_orders" in tools_dead)
        dead_call = await mcp_call(key_b["key"], "fixture__echo", {"text": "x"})
        check("call to dead server is a clear error", dead_call.is_error and "fixture" in dead_call.content[0].text, dead_call.content[0].text)
        still = await mcp_call(key_b["key"], "woocommerce__list_products", {"per_page": 1})
        check("calls to other servers keep working", not still.is_error)

        print("== Disable a server")
        woo_id = woo["id"]
        api.patch(f"{API}/servers/{woo_id}", headers=admin, json={"is_enabled": False})
        check("disabled server's tools disappear (MCP client)", not any(t.startswith("woocommerce__") for t in await mcp_tools(key_a["key"])))
        check("disabled server's tools disappear (dashboard/chat)", not any(t["name"].startswith("woocommerce__") for t in api.get(f"{API}/tools", headers=a_jwt).json()))
        check("calling a disabled server's tool fails", (await mcp_call(key_a["key"], "woocommerce__list_orders")).is_error)
        status_disabled = next(s for s in api.get(f"{API}/servers", headers=admin).json() if s["slug"] == "woocommerce")["status"]
        check("dashboard shows it as disabled", status_disabled == "disabled")
        api.patch(f"{API}/servers/{woo_id}", headers=admin, json={"is_enabled": True})
        check("re-enabled server's tools return", "woocommerce__list_orders" in await mcp_tools(key_a["key"]))

        print("== Revocation")
        check("revoke requires auth", api.delete(f"{API}/keys/{key_b['key_id']}").status_code == 401)
        check("member cannot revoke another user's key", api.delete(f"{API}/keys/{key_b['key_id']}", headers=a_jwt).status_code == 404)
        check("admin revokes user B's key", api.delete(f"{API}/keys/{key_b['key_id']}", headers=admin).status_code == 200)
        http, _ = await mcp_session(key_b["key"])
        async with http:
            rejected = await http.post(f"{BASE}/mcp", json={"jsonrpc": "2.0", "id": 1, "method": "tools/list"}, headers={"Accept": "application/json, text/event-stream"})
        check("revoked key is rejected on its next call", rejected.status_code == 401, str(rejected.status_code))
        check("user A's key still works", "woocommerce__list_orders" in await mcp_tools(key_a["key"]))

        api.patch(f"{API}/users/{user_a['id']}", headers=admin, json={"is_active": False})
        http, _ = await mcp_session(key_a["key"])
        async with http:
            rejected = await http.post(f"{BASE}/mcp", json={"jsonrpc": "2.0", "id": 1, "method": "tools/list"}, headers={"Accept": "application/json, text/event-stream"})
        check("disabling a user blocks their keys", rejected.status_code == 401)
        check("disabled user's dashboard token stops working", api.get(f"{API}/auth/me", headers=a_jwt).status_code == 401)

        print("== Audit log")
        audit = api.get(f"{API}/audit", headers=admin, params={"limit": 200}).json()
        mine = [e for e in audit["items"] if e["actor_email"] in (user_a["email"], user_b["email"])]
        check("calls are attributed to users", any(e["actor_email"] == user_b["email"] and e["tool"] == "list_orders" and e["server"] == "woocommerce" and e["outcome"] == "success" for e in mine), str(mine[:2]))
        check("failures are recorded with a category", any(e["outcome"] == "failure" and e["error_category"] == "timeout" for e in mine))
        check("durations are recorded", all(e["duration_ms"] >= 0 for e in mine) and any(e["duration_ms"] > 5000 for e in mine))
        check("audit channel recorded", any(e["channel"] == "mcp" for e in mine))
        only_b = api.get(f"{API}/audit", headers=admin, params={"user_id": user_b["id"]}).json()
        check("audit filter by user", only_b["total"] > 0 and all(e["actor_user_id"] == user_b["id"] for e in only_b["items"]))

        print("== Cleanup")
        fixture_id = fixture_server["id"]
        check("remove server", api.delete(f"{API}/servers/{fixture_id}", headers=admin).status_code == 204)
    finally:
        kill_tree(fixture)

    print()
    if _failures:
        print(f"{len(_failures)} FAILED: {_failures}")
        sys.exit(1)
    print("ALL CHECKS PASSED")


asyncio.run(main())
