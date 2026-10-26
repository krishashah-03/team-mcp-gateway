"""Pure-logic tests: SSRF guard, rate limiter, permission rules, tool ranking, naming."""

import mcp_types as types
import pytest

from app.core.ratelimit import SlidingWindowLimiter
from app.core.urlguard import UnsafeURLError, check_upstream_url
from app.gateway.discovery import rank_tools, summarize
from app.gateway.service import namespaced, split_namespaced
from app.services.tool_permission_service import Rule, is_allowed


def blocked(url: str, allow_private: bool = True, allowed: tuple[str, ...] = ()) -> bool:
    try:
        check_upstream_url(url, allow_private=allow_private, allowed_hosts=list(allowed))
        return False
    except UnsafeURLError:
        return True


class TestUrlGuard:
    @pytest.mark.parametrize(
        "url",
        [
            "http://169.254.169.254/latest/meta-data",
            "http://[::ffff:169.254.169.254]/",
            "http://[fe80::1]/",
            "http://metadata.google.internal/computeMetadata/v1/",
            "http://0.0.0.0:8000/",
            "http://100.100.100.200/",
            "file:///etc/passwd",
            "ftp://example.com/",
            "http:///nohost",
        ],
    )
    def test_always_blocked_even_when_private_allowed(self, url):
        assert blocked(url, allow_private=True)

    @pytest.mark.parametrize("url", ["http://127.0.0.1:8001/mcp", "http://[::1]:8001/mcp", "http://10.1.2.3/mcp", "http://192.168.0.5/mcp"])
    def test_private_allowed_in_dev_blocked_in_prod(self, url):
        assert not blocked(url, allow_private=True)
        assert blocked(url, allow_private=False)

    def test_allowlist_overrides_private_block(self):
        assert not blocked("http://localhost:8001/mcp", allow_private=False, allowed=("localhost",))
        assert not blocked("http://woo.shop.svc.cluster.local/mcp", allow_private=False, allowed=("*.svc.cluster.local",))

    def test_allowlist_does_not_unlock_metadata(self):
        assert blocked("http://169.254.169.254/", allow_private=False, allowed=("169.254.169.254",))

    def test_public_address_ok(self):
        assert not blocked("http://93.184.216.34/mcp", allow_private=False)


class TestRateLimiter:
    def test_blocks_after_limit(self):
        limiter = SlidingWindowLimiter(3, 60)
        assert all(limiter.hit("k")[0] for _ in range(3))
        allowed, retry_after = limiter.hit("k")
        assert not allowed and retry_after >= 1

    def test_keys_are_independent(self):
        limiter = SlidingWindowLimiter(1, 60)
        assert limiter.hit("a")[0] and limiter.hit("b")[0]

    def test_check_does_not_consume(self):
        limiter = SlidingWindowLimiter(1, 60)
        assert limiter.check("a")[0] and limiter.check("a")[0] and limiter.hit("a")[0]
        assert not limiter.check("a")[0]

    def test_reset_clears(self):
        limiter = SlidingWindowLimiter(1, 60)
        limiter.hit("a")
        limiter.reset("a")
        assert limiter.hit("a")[0]

    def test_zero_disables(self):
        assert SlidingWindowLimiter(0, 60).hit("a") == (True, 0)

    def test_window_expires(self):
        limiter = SlidingWindowLimiter(1, 0.05)
        limiter.hit("a")
        import time

        time.sleep(0.08)
        assert limiter.hit("a")[0]


def rule(i, *, role=None, user=None, tool=None, effect="deny", server="woocommerce"):
    return Rule(i, role, user, server, tool, effect)


ME = dict(role="member", user_id=5, server_slug="woocommerce")


class TestPermissions:
    def test_default_allows(self):
        assert is_allowed([], **ME, tool_name="create_product")

    def test_tool_deny(self):
        assert not is_allowed([rule(1, role="member", tool="create_product")], **ME, tool_name="create_product")

    def test_other_tool_unaffected(self):
        assert is_allowed([rule(1, role="member", tool="create_product")], **ME, tool_name="list_orders")

    def test_other_role_unaffected(self):
        assert is_allowed(
            [rule(1, role="member", tool="create_product")],
            role="admin", user_id=1, server_slug="woocommerce", tool_name="create_product",
        )

    def test_other_server_unaffected(self):
        assert is_allowed(
            [rule(1, role="member", tool="create_product")],
            role="member", user_id=5, server_slug="docs", tool_name="create_product",
        )

    def test_server_wide_deny(self):
        assert not is_allowed([rule(1, role="member")], **ME, tool_name="anything")

    def test_tool_allow_beats_server_deny(self):
        rules = [rule(1, role="member"), rule(2, role="member", tool="list_orders", effect="allow")]
        assert is_allowed(rules, **ME, tool_name="list_orders")
        assert not is_allowed(rules, **ME, tool_name="create_product")

    def test_user_rule_beats_role_rule(self):
        rules = [rule(1, role="member", tool="create_product"), rule(2, user=5, tool="create_product", effect="allow")]
        assert is_allowed(rules, **ME, tool_name="create_product")

    def test_user_rule_only_for_that_user(self):
        rules = [rule(1, role="member", tool="create_product"), rule(2, user=9, tool="create_product", effect="allow")]
        assert not is_allowed(rules, **ME, tool_name="create_product")

    def test_deny_wins_a_tie(self):
        rules = [rule(1, role="member", tool="x", effect="allow"), rule(2, role="member", tool="x")]
        assert not is_allowed(rules, **ME, tool_name="x")

    def test_user_server_rule_outranks_role_tool_rule(self):
        rules = [rule(1, role="member", tool="create_product"), rule(2, user=5, effect="allow")]
        assert is_allowed(rules, **ME, tool_name="create_product")


def make_tool(name, description, props=None, required=()):
    return types.Tool(
        name=name, description=description,
        input_schema={"type": "object", "properties": props or {}, "required": list(required)},
    )


CATALOGUE = [
    make_tool("woocommerce__list_orders", "List orders, newest first"),
    make_tool("woocommerce__create_product", "Create a simple product", {"name": {"type": "string"}, "price": {"type": "string"}}, ["name"]),
    make_tool("utilities__calculate", "Evaluate an arithmetic expression"),
    make_tool("docs__search_documents", "Find documents that mention the query words"),
    make_tool("utilities__current_time", "Get the current date and time in a timezone"),
]


class TestDiscovery:
    @pytest.mark.parametrize(
        "query,expected",
        [
            ("list recent orders", "woocommerce__list_orders"),
            ("create a new product", "woocommerce__create_product"),
            ("what time is it", "utilities__current_time"),
            ("arithmetic math", "utilities__calculate"),
            ("find a policy document", "docs__search_documents"),
        ],
    )
    def test_best_match_first(self, query, expected):
        assert rank_tools(CATALOGUE, query)[0].name == expected

    def test_no_match_is_empty(self):
        assert rank_tools(CATALOGUE, "zzzzqqq") == []

    def test_blank_query_is_empty(self):
        assert rank_tools(CATALOGUE, "   ") == []

    def test_limit_respected(self):
        assert len(rank_tools(CATALOGUE, "list create find get evaluate", limit=2)) == 2

    def test_summary_marks_required_arguments(self):
        arguments = {a["name"]: a["required"] for a in summarize(CATALOGUE[1])["arguments"]}
        assert arguments == {"name": True, "price": False}


class TestNamespacing:
    def test_round_trip(self):
        assert split_namespaced(namespaced("woocommerce", "list_orders")) == ("woocommerce", "list_orders")

    def test_tool_name_may_contain_underscores(self):
        assert split_namespaced("docs__read__document") == ("docs", "read__document")

    @pytest.mark.parametrize("bad", ["", "nodelimiter", "__tool", "server__", "__"])
    def test_malformed_names(self, bad):
        assert split_namespaced(bad) is None

    def test_same_tool_name_on_two_servers_is_distinct(self):
        assert namespaced("a", "list_orders") != namespaced("b", "list_orders")
