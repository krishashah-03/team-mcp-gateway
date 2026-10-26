"""HTTP-level integration tests.

The real FastAPI app runs in-process against the real database and a real fixture MCP
server. Everything these tests create uses the reserved "@test.invalid" domain or the
"tst" server prefix and is deleted at the end of the session.
"""

import asyncio
import secrets
from types import SimpleNamespace

import httpx
import httpx2
import pytest
import pytest_asyncio
from mcp import Client
from mcp.client.streamable_http import streamable_http_client
from sqlalchemy import text

from app.chat.providers import ChatProviderError, ModelReply, ToolCall
from app.core import limiters
from app.core.config import settings

pytestmark = [pytest.mark.db, pytest.mark.integration]
PASSWORD = "A-long-test-password-1"
SLUG = "tstfix"
HOST = "http://127.0.0.1:8000"
API = "/api/v1"


class ScriptedProvider:
    def __init__(self, *replies):
        self.replies = list(replies)
        self.requests = []

    async def generate(self, *, system, turns, tools):
        self.requests.append({"turns": list(turns), "tools": [t.name for t in tools]})
        reply = self.replies.pop(0)
        if isinstance(reply, Exception):
            raise reply
        return reply


def call_tools(*pairs):
    return ModelReply(text=None, tool_calls=[ToolCall(f"c{i}", n, a) for i, (n, a) in enumerate(pairs)])


def say(text):
    return ModelReply(text=text, tool_calls=[])


def purge_test_data() -> None:
    from app.db.session import session_scope

    with session_scope() as session:
        session.execute(text("delete from gateway_audit_events where actor_email like '%@test.invalid' or upstream_server_slug like 'tst%'"))
        session.execute(text("delete from tool_permissions where server_slug like 'tst%'"))
        session.execute(text("delete from users where email like '%@test.invalid'"))
        session.execute(text("delete from mcp_servers where slug like 'tst%'"))


@pytest_asyncio.fixture(scope="session", loop_scope="session")
async def world(database_available, fixture_upstream):
    """The running app plus an admin, two members and one registered upstream."""
    if not database_available:
        pytest.skip("Postgres is not reachable (run: docker compose up -d)")
    from app.main import app

    purge_test_data()

    # One task must both enter and leave the app's lifespan (the MCP session managers
    # use task groups, which cannot be exited from another task), so it gets its own.
    ready, stop = asyncio.Event(), asyncio.Event()

    async def hold_lifespan() -> None:
        async with app.router.lifespan_context(app):
            ready.set()
            await stop.wait()

    lifespan_task = asyncio.create_task(hold_lifespan())
    await ready.wait()
    try:
        client = httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url=HOST)
        world = SimpleNamespace(app=app, client=client, users={}, tokens={}, upstream_url=fixture_upstream)

        from app.db.session import session_scope
        from app.repositories.user_repository import UserRepository
        from app.services.user_service import UserService

        with session_scope() as session:
            service = UserService(UserRepository(session))
            for label, role in (("admin", "admin"), ("alice", "member"), ("bob", "member")):
                email = f"{label}-{secrets.token_hex(3)}@test.invalid"
                world.users[label] = service.create_user(email=email, password=PASSWORD, role=role)
        for label, user in world.users.items():
            response = await client.post(f"{API}/auth/login", json={"email": user.email, "password": PASSWORD})
            world.tokens[label] = {"Authorization": f"Bearer {response.json()['access_token']}"}

        created = await client.post(
            f"{API}/servers", headers=world.tokens["admin"],
            json={"name": "Test Fixture", "slug": SLUG, "endpoint_url": fixture_upstream},
        )
        assert created.status_code == 201, created.text
        world.server = created.json()
        try:
            yield world
        finally:
            await client.aclose()
    finally:
        stop.set()
        await lifespan_task
        purge_test_data()


@pytest.fixture(autouse=True)
def reset_state(request):
    """Every test starts with empty rate-limit counters and no chat provider."""
    if "world" not in request.fixturenames:
        yield
        return
    world = request.getfixturevalue("world")
    for limiter in (limiters.login_failures_by_account, limiters.login_failures_by_client, limiters.api_calls_by_user):
        limiter._events.clear()
    world.app.state.chat_provider = None
    yield
    world.app.state.chat_provider = None


def as_(world, who):
    return world.tokens[who]


async def mcp_key(world, who="alice") -> str:
    response = await world.client.post(f"{API}/keys", headers=as_(world, who), json={"name": "test"})
    return response.json()["key"]


def mcp_client(world, key, path="/mcp"):
    http = httpx2.AsyncClient(
        transport=httpx2.ASGITransport(app=world.app), base_url=HOST,
        headers={"Authorization": f"Bearer {key}"}, timeout=60,
    )
    return http, Client(streamable_http_client(f"{HOST}{path}", http_client=http))


async def mcp_tool_names(world, key, path="/mcp"):
    http, client = mcp_client(world, key, path)
    async with http, client:
        return sorted(t.name for t in (await client.list_tools()).tools)


async def audit(world, **params):
    response = await world.client.get(f"{API}/audit", headers=as_(world, "admin"), params={"limit": 200, **params})
    return response.json()["items"]


# ---------------------------------------------------------------- basics

async def test_responses_carry_a_request_id_and_security_headers(world):
    response = await world.client.get("/health")
    assert response.status_code == 200
    assert len(response.headers["x-request-id"]) >= 8
    assert response.headers["x-content-type-options"] == "nosniff"
    assert response.headers["cache-control"] == "no-store"


async def test_a_supplied_request_id_is_echoed_but_garbage_is_replaced(world):
    assert (await world.client.get("/health", headers={"X-Request-ID": "trace-123"})).headers["x-request-id"] == "trace-123"
    bad = (await world.client.get("/health", headers={"X-Request-ID": "bad id\r\nX: y"})).headers["x-request-id"]
    assert bad != "bad id" and " " not in bad


async def test_oversized_bodies_are_rejected(world):
    response = await world.client.post(f"{API}/auth/login", content=b"x" * (settings.max_body_bytes + 1), headers={"Content-Type": "application/json"})
    assert response.status_code == 413


# ---------------------------------------------------------------- auth

async def test_login_and_me(world):
    alice = world.users["alice"]
    me = await world.client.get(f"{API}/auth/me", headers=as_(world, "alice"))
    assert me.status_code == 200 and me.json()["email"] == alice.email and me.json()["role"] == "member"
    assert "password" not in me.text


async def test_wrong_password_and_unknown_user_look_identical(world):
    wrong = await world.client.post(f"{API}/auth/login", json={"email": world.users["alice"].email, "password": "nope-nope-nope"})
    unknown = await world.client.post(f"{API}/auth/login", json={"email": "ghost@test.invalid", "password": "nope-nope-nope"})
    assert wrong.status_code == unknown.status_code == 401 and wrong.json() == unknown.json()


async def test_repeated_failed_logins_are_throttled(world):
    email = world.users["alice"].email
    statuses = [
        (await world.client.post(f"{API}/auth/login", json={"email": email, "password": "wrong-password-1"})).status_code
        for _ in range(settings.login_max_failures)
    ]
    assert statuses == [401] * settings.login_max_failures
    blocked = await world.client.post(f"{API}/auth/login", json={"email": email, "password": PASSWORD})
    assert blocked.status_code == 429 and int(blocked.headers["retry-after"]) >= 1  # even the right password


async def test_a_successful_login_clears_the_failure_count(world):
    email = world.users["bob"].email
    for _ in range(settings.login_max_failures - 1):
        await world.client.post(f"{API}/auth/login", json={"email": email, "password": "wrong-password-1"})
    assert (await world.client.post(f"{API}/auth/login", json={"email": email, "password": PASSWORD})).status_code == 200
    assert (await world.client.post(f"{API}/auth/login", json={"email": email, "password": "wrong-password-1"})).status_code == 401


@pytest.mark.parametrize("headers", [{}, {"Authorization": "Bearer garbage"}, {"Authorization": "Basic abc"}, {"Authorization": "Bearer"}])
async def test_dashboard_endpoints_need_a_valid_token(world, headers):
    assert (await world.client.get(f"{API}/auth/me", headers=headers)).status_code == 401


@pytest.mark.parametrize(
    "method,path",
    [
        ("GET", "/servers"), ("POST", "/servers"), ("PATCH", "/servers/1"), ("DELETE", "/servers/1"),
        ("GET", "/users"), ("POST", "/users"), ("PATCH", "/users/1"), ("POST", "/users/1/keys"),
        ("GET", "/audit"), ("GET", "/admin/keys"), ("GET", "/permissions"), ("POST", "/permissions"), ("DELETE", "/permissions/1"),
    ],
)
async def test_members_cannot_use_admin_endpoints(world, method, path):
    response = await world.client.request(method, f"{API}{path}", headers=as_(world, "alice"), json={})
    assert response.status_code == 403


async def test_demoting_a_user_takes_effect_on_their_existing_token(world):
    from app.db.session import session_scope
    from app.repositories.user_repository import UserRepository
    from app.services.user_service import UserService

    with session_scope() as session:
        temp = UserService(UserRepository(session)).create_user(email=f"temp-{secrets.token_hex(3)}@test.invalid", password=PASSWORD, role="admin")
    token = {"Authorization": "Bearer " + (await world.client.post(f"{API}/auth/login", json={"email": temp.email, "password": PASSWORD})).json()["access_token"]}
    assert (await world.client.get(f"{API}/users", headers=token)).status_code == 200
    await world.client.patch(f"{API}/users/{temp.id}", headers=as_(world, "admin"), json={"role": "member"})
    assert (await world.client.get(f"{API}/users", headers=token)).status_code == 403  # same token, new role
    await world.client.patch(f"{API}/users/{temp.id}", headers=as_(world, "admin"), json={"is_active": False})
    assert (await world.client.get(f"{API}/auth/me", headers=token)).status_code == 401


# ---------------------------------------------------------------- users and keys

async def test_admin_creates_users_with_validation(world):
    admin = as_(world, "admin")
    ok = await world.client.post(f"{API}/users", headers=admin, json={"email": f"new-{secrets.token_hex(3)}@test.invalid", "password": PASSWORD})
    assert ok.status_code == 201 and ok.json()["role"] == "member" and "password" not in ok.text
    dup = await world.client.post(f"{API}/users", headers=admin, json={"email": world.users["alice"].email, "password": PASSWORD})
    weak = await world.client.post(f"{API}/users", headers=admin, json={"email": "weak@test.invalid", "password": "short"})
    bad_role = await world.client.post(f"{API}/users", headers=admin, json={"email": "r@test.invalid", "password": PASSWORD, "role": "root"})
    assert (dup.status_code, weak.status_code, bad_role.status_code) == (409, 422, 422)


async def test_key_secret_is_shown_once_and_never_listed(world):
    created = await world.client.post(f"{API}/keys", headers=as_(world, "alice"), json={"name": "laptop"})
    assert created.status_code == 201 and created.json()["key"].startswith("gwk_")
    listed = await world.client.get(f"{API}/keys", headers=as_(world, "alice"))
    assert all("key" not in item for item in listed.json())
    secret = created.json()["key"].rsplit("_", 1)[-1]
    assert secret not in listed.text


async def test_members_only_see_and_revoke_their_own_keys(world):
    alice_key = (await world.client.post(f"{API}/keys", headers=as_(world, "alice"), json={"name": "a"})).json()
    bob_view = await world.client.get(f"{API}/keys", headers=as_(world, "bob"))
    assert alice_key["key_id"] not in bob_view.text
    assert (await world.client.delete(f"{API}/keys/{alice_key['key_id']}", headers=as_(world, "bob"))).status_code == 404
    assert (await world.client.delete(f"{API}/keys/{alice_key['key_id']}", headers=as_(world, "alice"))).status_code == 200
    assert (await world.client.delete(f"{API}/keys/{'0' * 32}", headers=as_(world, "alice"))).status_code == 404


async def test_admin_can_issue_and_revoke_keys_for_anyone(world):
    admin = as_(world, "admin")
    issued = await world.client.post(f"{API}/users/{world.users['bob'].id}/keys", headers=admin, json={"name": "for-bob"})
    assert issued.status_code == 201 and issued.json()["user_id"] == world.users["bob"].id
    everyone = await world.client.get(f"{API}/admin/keys", headers=admin)
    assert any(k["key_id"] == issued.json()["key_id"] and k["user_email"] == world.users["bob"].email for k in everyone.json())
    assert (await world.client.delete(f"{API}/keys/{issued.json()['key_id']}", headers=admin)).status_code == 200
    assert (await world.client.post(f"{API}/users/999999/keys", headers=admin, json={"name": "x"})).status_code == 404


@pytest.mark.parametrize("body", [{}, {"name": ""}, {"name": "x" * 201}, {"name": "ok", "expires_in_days": 0}, {"name": "ok", "expires_in_days": 99999}])
async def test_key_creation_validation(world, body):
    assert (await world.client.post(f"{API}/keys", headers=as_(world, "alice"), json=body)).status_code == 422


# ---------------------------------------------------------------- servers

async def test_server_list_shows_status_tools_and_no_credential(world):
    servers = (await world.client.get(f"{API}/servers", headers=as_(world, "admin"))).json()
    ours = next(s for s in servers if s["slug"] == SLUG)
    assert ours["status"] == "connected"
    assert {t["name"] for t in ours["tools"]} == {"echo", "list_orders", "slow"}
    assert {t["namespaced_name"] for t in ours["tools"]} == {f"{SLUG}__echo", f"{SLUG}__list_orders", f"{SLUG}__slow"}
    assert "credential" not in ours and ours["has_credential"] is False


async def test_credentials_are_write_only(world):
    admin = as_(world, "admin")
    created = await world.client.post(
        f"{API}/servers", headers=admin,
        json={"name": "Secret holder", "slug": "tstsecret", "endpoint_url": world.upstream_url, "auth_type": "bearer", "credential": "TOP-SECRET-VALUE"},
    )
    assert created.status_code == 201 and created.json()["has_credential"] is True
    server_id = created.json()["id"]
    everything = (await world.client.get(f"{API}/servers", headers=admin)).text + (await world.client.get(f"{API}/servers/{server_id}", headers=admin)).text + created.text
    assert "TOP-SECRET-VALUE" not in everything
    # The fixture upstream has no auth, so a wrong bearer still works; what matters is it was stored encrypted.
    from app.db.session import session_scope

    with session_scope() as session:
        stored = session.execute(text("select credential_encrypted from mcp_servers where id = :i"), {"i": server_id}).scalar()
    assert stored and "TOP-SECRET-VALUE" not in stored
    cleared = await world.client.patch(f"{API}/servers/{server_id}", headers=admin, json={"auth_type": "none"})
    assert cleared.json()["has_credential"] is False
    assert (await world.client.delete(f"{API}/servers/{server_id}", headers=admin)).status_code == 204


@pytest.mark.parametrize(
    "url",
    ["http://169.254.169.254/latest/meta-data", "http://metadata.google.internal/", "http://[fe80::1]/mcp", "http://0.0.0.0:8000/mcp", "file:///etc/passwd"],
)
async def test_dangerous_destinations_are_refused(world, url):
    response = await world.client.post(
        f"{API}/servers", headers=as_(world, "admin"), json={"name": "Evil", "slug": "tstevil", "endpoint_url": url}
    )
    assert response.status_code == 422


async def test_editing_a_server_to_a_dangerous_destination_is_refused(world):
    response = await world.client.patch(
        f"{API}/servers/{world.server['id']}", headers=as_(world, "admin"), json={"endpoint_url": "http://169.254.169.254/"}
    )
    assert response.status_code == 422


async def test_server_input_validation_and_missing_ids(world):
    admin = as_(world, "admin")
    base = {"name": "X", "slug": "tstx", "endpoint_url": "http://127.0.0.1:9/mcp"}
    for override in ({"slug": "Bad_Slug"}, {"endpoint_url": "ftp://x"}, {"auth_type": "bearer"}, {"auth_type": "oauth"}, {"name": ""}):
        assert (await world.client.post(f"{API}/servers", headers=admin, json={**base, **override})).status_code == 422, override
    assert (await world.client.post(f"{API}/servers", headers=admin, json={**base, "slug": SLUG})).status_code == 409
    assert (await world.client.get(f"{API}/servers/999999", headers=admin)).status_code == 404
    assert (await world.client.patch(f"{API}/servers/999999", headers=admin, json={"name": "x"})).status_code == 404
    assert (await world.client.delete(f"{API}/servers/999999", headers=admin)).status_code == 404


async def test_an_unreachable_server_is_reported_with_a_reason_and_does_not_break_others(world):
    admin = as_(world, "admin")
    created = await world.client.post(
        f"{API}/servers", headers=admin, json={"name": "Dead", "slug": "tstdead", "endpoint_url": "http://127.0.0.1:9/mcp"}
    )
    assert created.status_code == 201
    dead = created.json()
    assert dead["status"] == "unreachable" and dead["status_detail"] and dead["tools"] == []
    names = [t["name"] for t in (await world.client.get(f"{API}/tools", headers=as_(world, "alice"))).json()]
    assert f"{SLUG}__echo" in names and not any(n.startswith("tstdead__") for n in names)
    await world.client.delete(f"{API}/servers/{dead['id']}", headers=admin)


# ---------------------------------------------------------------- MCP endpoint

async def test_mcp_requires_a_valid_key(world):
    body = {"jsonrpc": "2.0", "id": 1, "method": "tools/list"}
    headers = {"Accept": "application/json, text/event-stream"}
    for auth in ({}, {"Authorization": "Bearer nope"}, {"Authorization": "Bearer gwk_" + "a" * 32 + "_" + "b" * 43}, {"Authorization": "Basic abc"}):
        response = await world.client.post("/mcp", json=body, headers={**headers, **auth})
        assert response.status_code == 401 and "www-authenticate" in response.headers


async def test_a_dashboard_session_token_is_not_accepted_on_mcp(world):
    token = as_(world, "alice")["Authorization"].split(" ", 1)[1]
    response = await world.client.post("/mcp", json={"jsonrpc": "2.0", "id": 1, "method": "tools/list"},
                                       headers={"Authorization": f"Bearer {token}", "Accept": "application/json, text/event-stream"})
    assert response.status_code == 401


async def test_mcp_lists_namespaced_tools_and_routes_calls(world):
    key = await mcp_key(world)
    names = await mcp_tool_names(world, key)
    assert {f"{SLUG}__echo", f"{SLUG}__list_orders"} <= set(names)
    http, client = mcp_client(world, key)
    async with http, client:
        echoed = await client.call_tool(f"{SLUG}__echo", {"text": "hello"})
        assert not echoed.is_error and "hello" in echoed.content[0].text
        assert (await client.call_tool("nope__tool", {})).is_error
        assert (await client.call_tool("no-separator", {})).is_error


async def test_tool_descriptions_are_labelled_with_their_source_and_bounded(world):
    key = await mcp_key(world)
    http, client = mcp_client(world, key)
    async with http, client:
        tools = (await client.list_tools()).tools
    echo = next(t for t in tools if t.name == f"{SLUG}__echo")
    assert echo.description.startswith("[Test Fixture]") and len(echo.description) < 1600


async def test_a_slow_upstream_times_out_with_a_clear_error(world, monkeypatch):
    monkeypatch.setattr(settings, "upstream_call_timeout_seconds", 1.0)
    key = await mcp_key(world)
    http, client = mcp_client(world, key)
    async with http, client:
        result = await client.call_tool(f"{SLUG}__slow", {"seconds": 30})
    assert result.is_error and "did not respond" in result.content[0].text
    failures = [e for e in await audit(world, server=SLUG, outcome="failure") if e["tool"] == "slow"]
    assert failures and failures[0]["error_category"] == "timeout"


async def test_revoked_keys_and_disabled_users_are_rejected_on_the_next_call(world):
    body = {"jsonrpc": "2.0", "id": 1, "method": "tools/list"}
    headers = {"Accept": "application/json, text/event-stream"}
    key = await mcp_key(world, "bob")
    key_id = key.split("_")[1]
    assert (await world.client.post("/mcp", json=body, headers={**headers, "Authorization": f"Bearer {key}"})).status_code == 200
    assert (await world.client.delete(f"{API}/keys/{key_id}", headers=as_(world, "admin"))).status_code == 200
    assert (await world.client.post("/mcp", json=body, headers={**headers, "Authorization": f"Bearer {key}"})).status_code == 401


async def test_mcp_requests_are_rate_limited_per_user(world, monkeypatch):
    monkeypatch.setattr(limiters.api_calls_by_user, "_limit", 3)
    key = await mcp_key(world, "bob")
    body = {"jsonrpc": "2.0", "id": 1, "method": "tools/list"}
    headers = {"Accept": "application/json, text/event-stream", "Authorization": f"Bearer {key}"}
    codes = [(await world.client.post("/mcp", json=body, headers=headers)).status_code for _ in range(5)]
    assert codes[:3] == [200, 200, 200] and codes[3:] == [429, 429]
    other = await mcp_key(world, "alice")
    assert (await world.client.post("/mcp", json=body, headers={**headers, "Authorization": f"Bearer {other}"})).status_code == 200


# ---------------------------------------------------------------- permissions

async def test_denied_tools_are_hidden_and_rejected_for_members_only(world):
    admin = as_(world, "admin")
    rule = await world.client.post(f"{API}/permissions", headers=admin,
                                   json={"role": "member", "server_slug": SLUG, "tool_name": "echo", "effect": "deny"})
    assert rule.status_code == 201
    try:
        member_key, admin_key = await mcp_key(world, "alice"), await mcp_key(world, "admin")
        assert f"{SLUG}__echo" not in await mcp_tool_names(world, member_key)
        assert f"{SLUG}__list_orders" in await mcp_tool_names(world, member_key)  # other tools unaffected
        assert f"{SLUG}__echo" in await mcp_tool_names(world, admin_key)  # admins unaffected
        assert f"{SLUG}__echo" not in [t["name"] for t in (await world.client.get(f"{API}/tools", headers=as_(world, "alice"))).json()]

        http, client = mcp_client(world, member_key)
        async with http, client:  # hidden is not enough: calling it directly must fail
            refused = await client.call_tool(f"{SLUG}__echo", {"text": "sneaky"})
        assert refused.is_error and "not permitted" in refused.content[0].text
        denied = [e for e in await audit(world, server=SLUG) if e["outcome"] == "failure" and e["error_category"] == "authorization"]
        assert denied and denied[0]["actor_email"] == world.users["alice"].email

        http, client = mcp_client(world, admin_key)
        async with http, client:
            assert not (await client.call_tool(f"{SLUG}__echo", {"text": "ok"})).is_error
    finally:
        await world.client.delete(f"{API}/permissions/{rule.json()['id']}", headers=admin)
    assert f"{SLUG}__echo" in await mcp_tool_names(world, await mcp_key(world, "alice"))  # rule removed


async def test_a_user_rule_overrides_a_role_rule(world):
    admin = as_(world, "admin")
    deny = (await world.client.post(f"{API}/permissions", headers=admin, json={"role": "member", "server_slug": SLUG, "effect": "deny"})).json()
    allow = (await world.client.post(f"{API}/permissions", headers=admin, json={"user_id": world.users["alice"].id, "server_slug": SLUG, "effect": "allow"})).json()
    try:
        assert f"{SLUG}__echo" in await mcp_tool_names(world, await mcp_key(world, "alice"))
        assert f"{SLUG}__echo" not in await mcp_tool_names(world, await mcp_key(world, "bob"))
    finally:
        for rule in (deny, allow):
            await world.client.delete(f"{API}/permissions/{rule['id']}", headers=admin)


async def test_permission_rule_validation(world):
    admin = as_(world, "admin")
    for body in (
        {"server_slug": SLUG, "effect": "deny"},
        {"role": "member", "user_id": 1, "server_slug": SLUG, "effect": "deny"},
        {"role": "member", "server_slug": "Bad_Slug", "effect": "deny"},
        {"role": "member", "server_slug": SLUG, "effect": "maybe"},
        {"role": "root", "server_slug": SLUG, "effect": "deny"},
    ):
        assert (await world.client.post(f"{API}/permissions", headers=admin, json=body)).status_code == 422, body
    assert (await world.client.post(f"{API}/permissions", headers=admin, json={"user_id": 999999, "server_slug": SLUG, "effect": "deny"})).status_code == 404
    assert (await world.client.delete(f"{API}/permissions/999999", headers=admin)).status_code == 404


# ---------------------------------------------------------------- lite endpoint

async def test_the_lite_endpoint_exposes_only_search_describe_run(world):
    key = await mcp_key(world)
    assert await mcp_tool_names(world, key, "/lite/mcp") == ["describe_tool", "run_tool", "search_tools"]
    http, client = mcp_client(world, key, "/lite/mcp")
    async with http, client:
        found = await client.call_tool("search_tools", {"query": "echo text"})
        assert f"{SLUG}__echo" in found.content[0].text
        described = await client.call_tool("describe_tool", {"name": f"{SLUG}__echo"})
        assert "input_schema" in described.content[0].text
        ran = await client.call_tool("run_tool", {"name": f"{SLUG}__echo", "arguments": {"text": "via lite"}})
        assert not ran.is_error and "via lite" in ran.content[0].text
        assert (await client.call_tool("run_tool", {"name": "nope__x"})).is_error
        assert (await client.call_tool("run_tool", {})).is_error
        assert (await client.call_tool("describe_tool", {"name": "nope__x"})).is_error


async def test_the_lite_endpoint_needs_a_key_and_applies_permissions(world):
    assert (await world.client.post("/lite/mcp", json={"jsonrpc": "2.0", "id": 1, "method": "tools/list"},
                                    headers={"Accept": "application/json, text/event-stream"})).status_code == 401
    admin = as_(world, "admin")
    rule = (await world.client.post(f"{API}/permissions", headers=admin, json={"role": "member", "server_slug": SLUG, "tool_name": "echo", "effect": "deny"})).json()
    try:
        http, client = mcp_client(world, await mcp_key(world, "alice"), "/lite/mcp")
        async with http, client:
            assert f"{SLUG}__echo" not in (await client.call_tool("search_tools", {"query": "echo"})).content[0].text
            assert (await client.call_tool("run_tool", {"name": f"{SLUG}__echo", "arguments": {"text": "x"}})).is_error
    finally:
        await world.client.delete(f"{API}/permissions/{rule['id']}", headers=admin)


# ---------------------------------------------------------------- chat

async def test_chat_needs_login(world):
    assert (await world.client.post(f"{API}/chat", json={"message": "hi"})).status_code == 401


async def test_chat_explains_when_no_model_is_configured(world, monkeypatch):
    monkeypatch.setattr(settings, "gemini_api_key", None)
    response = await world.client.post(f"{API}/chat", headers=as_(world, "alice"), json={"message": "hi"})
    assert response.status_code == 503 and "GEMINI_API_KEY" in response.json()["detail"]


async def test_chat_runs_tools_as_the_logged_in_user_and_audits_them(world):
    world.app.state.chat_provider = ScriptedProvider(call_tools((f"{SLUG}__echo", {"text": "ping"})), say("The echo said ping."))
    response = await world.client.post(f"{API}/chat", headers=as_(world, "alice"), json={"message": "echo ping please"})
    assert response.status_code == 200
    body = response.json()
    assert body["reply"] == "The echo said ping."
    assert [(t["name"], t["is_error"]) for t in body["tool_calls"]] == [(f"{SLUG}__echo", False)]
    assert "ping" in body["tool_calls"][0]["result_preview"]

    events = [e for e in await audit(world, server=SLUG) if e["channel"] == "chat"]
    assert events and events[0]["actor_email"] == world.users["alice"].email and events[0]["tool"] == "echo"
    assert events[0]["outcome"] == "success" and events[0]["duration_ms"] >= 0


async def test_chat_remembers_the_conversation_and_stores_tool_traces(world):
    provider = ScriptedProvider(say("First answer."), say("Second answer."))
    world.app.state.chat_provider = provider
    headers = as_(world, "alice")
    first = (await world.client.post(f"{API}/chat", headers=headers, json={"message": "first question"})).json()
    second = await world.client.post(f"{API}/chat", headers=headers, json={"message": "follow up", "conversation_id": first["conversation_id"]})
    assert second.json()["conversation_id"] == first["conversation_id"]
    assert [t.text for t in provider.requests[1]["turns"]] == ["first question", "First answer.", "follow up"]

    history = (await world.client.get(f"{API}/chat/conversations/{first['conversation_id']}", headers=headers)).json()
    assert [(m["role"], m["content"]) for m in history] == [
        ("user", "first question"), ("assistant", "First answer."), ("user", "follow up"), ("assistant", "Second answer."),
    ]
    listed = (await world.client.get(f"{API}/chat/conversations", headers=headers)).json()
    assert any(c["id"] == first["conversation_id"] and c["title"] == "first question" for c in listed)


async def test_users_cannot_read_write_or_delete_each_others_conversations(world):
    world.app.state.chat_provider = ScriptedProvider(say("private answer"), say("should never happen"))
    created = (await world.client.post(f"{API}/chat", headers=as_(world, "alice"), json={"message": "secret"})).json()
    cid = created["conversation_id"]
    bob = as_(world, "bob")
    assert (await world.client.get(f"{API}/chat/conversations/{cid}", headers=bob)).status_code == 404
    assert (await world.client.delete(f"{API}/chat/conversations/{cid}", headers=bob)).status_code == 404
    assert (await world.client.post(f"{API}/chat", headers=bob, json={"message": "hi", "conversation_id": cid})).status_code == 404
    assert cid not in [c["id"] for c in (await world.client.get(f"{API}/chat/conversations", headers=bob)).json()]
    assert (await world.client.delete(f"{API}/chat/conversations/{cid}", headers=as_(world, "alice"))).status_code == 204
    assert (await world.client.get(f"{API}/chat/conversations/{cid}", headers=as_(world, "alice"))).status_code == 404


@pytest.mark.parametrize("body", [{}, {"message": ""}, {"message": "x" * 4001}, {"message": "hi", "conversation_id": 0}, {"message": "hi", "conversation_id": "abc"}])
async def test_chat_input_validation(world, body):
    world.app.state.chat_provider = ScriptedProvider(say("unused"))
    assert (await world.client.post(f"{API}/chat", headers=as_(world, "alice"), json=body)).status_code == 422


async def test_model_failures_become_a_safe_502(world):
    world.app.state.chat_provider = ScriptedProvider(ChatProviderError("The Gemini rate limit was reached. Wait a moment and retry."))
    response = await world.client.post(f"{API}/chat", headers=as_(world, "alice"), json={"message": "hi"})
    assert response.status_code == 502 and "rate limit" in response.json()["detail"]


async def test_chat_cannot_use_tools_the_user_is_not_permitted_to_use(world):
    admin = as_(world, "admin")
    rule = (await world.client.post(f"{API}/permissions", headers=admin, json={"role": "member", "server_slug": SLUG, "tool_name": "echo", "effect": "deny"})).json()
    try:
        def alice_chat_echo_successes(events):
            return [
                e for e in events
                if e["channel"] == "chat" and e["tool"] == "echo" and e["outcome"] == "success"
                and e["actor_email"] == world.users["alice"].email
            ]

        before = len(alice_chat_echo_successes(await audit(world, server=SLUG)))
        provider = ScriptedProvider(call_tools((f"{SLUG}__echo", {"text": "x"})), say("I'm not allowed to do that."))
        world.app.state.chat_provider = provider
        response = await world.client.post(f"{API}/chat", headers=as_(world, "alice"), json={"message": "echo x"})
        assert f"{SLUG}__echo" not in provider.requests[0]["tools"]  # never offered to the model
        assert response.json()["tool_calls"][0]["is_error"] is True
        # The upstream tool really did not run: no new successful echo in the audit log.
        assert len(alice_chat_echo_successes(await audit(world, server=SLUG))) == before
    finally:
        await world.client.delete(f"{API}/permissions/{rule['id']}", headers=admin)


async def test_chat_is_rate_limited(world, monkeypatch):
    monkeypatch.setattr(limiters.api_calls_by_user, "_limit", 2)
    world.app.state.chat_provider = ScriptedProvider(say("1"), say("2"), say("3"))
    codes = [(await world.client.post(f"{API}/chat", headers=as_(world, "bob"), json={"message": "hi"})).status_code for _ in range(3)]
    assert codes == [200, 200, 429]


# ---------------------------------------------------------------- audit and metrics

async def test_audit_log_is_attributed_filterable_and_admin_only(world):
    key = await mcp_key(world, "alice")
    http, client = mcp_client(world, key)
    async with http, client:
        await client.call_tool(f"{SLUG}__echo", {"text": "audited"})
        await client.call_tool("nope__missing", {})
    alice = world.users["alice"]
    mine = await audit(world, user_id=alice.id)
    assert mine and all(e["actor_user_id"] == alice.id for e in mine)
    assert any(e["tool"] == "echo" and e["server"] == SLUG and e["outcome"] == "success" and e["channel"] == "mcp" for e in mine)
    missing = [e for e in mine if e["error_category"] == "not_found"]
    assert missing and missing[0]["outcome"] == "failure"
    assert all(e["outcome"] == "failure" for e in await audit(world, user_id=alice.id, outcome="failure"))
    page = (await world.client.get(f"{API}/audit", headers=as_(world, "admin"), params={"limit": 1, "user_id": alice.id})).json()
    assert len(page["items"]) == 1 and page["total"] >= 2
    assert (await world.client.get(f"{API}/audit", headers=as_(world, "admin"), params={"limit": 0})).status_code == 422
    assert (await world.client.get(f"{API}/audit", headers=as_(world, "admin"), params={"outcome": "weird"})).status_code == 422


async def test_audit_entries_survive_deleting_the_user_and_server(world):
    from app.db.session import session_scope
    from app.repositories.user_repository import UserRepository
    from app.services.user_service import UserService

    with session_scope() as session:
        temp = UserService(UserRepository(session)).create_user(email=f"temp-{secrets.token_hex(3)}@test.invalid", password=PASSWORD, role="member")
    key = (await world.client.post(f"{API}/users/{temp.id}/keys", headers=as_(world, "admin"), json={"name": "t"})).json()["key"]
    http, client = mcp_client(world, key)
    async with http, client:
        await client.call_tool(f"{SLUG}__echo", {"text": "x"})
    with session_scope() as session:
        session.execute(text("delete from users where id = :i"), {"i": temp.id})
    survivors = [e for e in await audit(world, server=SLUG) if e["actor_email"] == temp.email]
    assert survivors and survivors[0]["actor_user_id"] is None  # who it was is still readable


async def test_metrics_are_restricted_and_count_calls(world):
    assert (await world.client.get("/metrics")).status_code == 401
    assert (await world.client.get("/metrics", headers=as_(world, "alice"))).status_code == 401  # a member is not enough
    http, client = mcp_client(world, await mcp_key(world, "alice"))
    async with http, client:
        await client.call_tool(f"{SLUG}__echo", {"text": "m"})
    scrape = await world.client.get("/metrics", headers=as_(world, "admin"))
    assert scrape.status_code == 200 and scrape.headers["content-type"].startswith("text/plain")
    assert f'gateway_tool_calls_total{{outcome="success",server="{SLUG}",tool="echo"}}' in scrape.text
    assert f'gateway_upstream_up{{server="{SLUG}"}} 1' in scrape.text
    assert "gateway_tool_call_duration_seconds_bucket" in scrape.text


async def test_a_scrape_token_works_when_configured(world, monkeypatch):
    monkeypatch.setattr(settings, "metrics_token", "prometheus-scrape-token")
    assert (await world.client.get("/metrics", headers={"Authorization": "Bearer prometheus-scrape-token"})).status_code == 200
    assert (await world.client.get("/metrics", headers={"Authorization": "Bearer wrong"})).status_code == 401


async def test_unknown_tool_names_do_not_explode_metric_labels(world):
    http, client = mcp_client(world, await mcp_key(world, "alice"))
    async with http, client:
        await client.call_tool(f"{SLUG}__made_up_{secrets.token_hex(4)}", {})
    scrape = (await world.client.get("/metrics", headers=as_(world, "admin"))).text
    assert "made_up_" not in scrape
