"""WooCommerce client and tool tests against a mocked store (no network)."""

import json

import httpx
import pytest

from woocommerce_mcp.client import (
    WooCommerceAuthenticationError,
    WooCommerceClient,
    WooCommerceHTTPError,
    WooCommerceNetworkError,
    WooCommerceResponseError,
    WooCommerceTimeoutError,
)
from woocommerce_mcp.config import Settings


def make_client(handler) -> WooCommerceClient:
    settings = Settings(
        woocommerce_base_url="https://shop.test",
        woocommerce_consumer_key="ck_test",
        woocommerce_consumer_secret="cs_test",
    )
    http = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    return WooCommerceClient(config=settings, client=http)


def ok(payload):
    return httpx.Response(200, json=payload)


ORDER = {"id": 5, "status": "processing", "total": "100.00"}
PRODUCT = {"id": 9, "name": "Mug", "status": "publish"}


class TestRequests:
    async def test_uses_basic_auth_and_the_rest_path(self):
        seen = {}

        def handler(request):
            seen["url"], seen["auth"] = str(request.url), request.headers["authorization"]
            return ok([PRODUCT])

        await make_client(handler).list_products()
        assert seen["url"].startswith("https://shop.test/wp-json/wc/v3/products")
        assert seen["auth"].startswith("Basic ")

    async def test_trailing_slash_in_base_url_is_fine(self):
        seen = {}
        settings = Settings(woocommerce_base_url="https://shop.test/", woocommerce_consumer_key="k", woocommerce_consumer_secret="s")
        http = httpx.AsyncClient(transport=httpx.MockTransport(lambda r: (seen.update(url=str(r.url)), ok([]))[1]))
        await WooCommerceClient(config=settings, client=http).list_products()
        assert "shop.test//wp-json" not in seen["url"]

    @pytest.mark.parametrize("url", ["", "ftp://shop.test", "https://user:pw@shop.test", "https://shop.test?x=1", "not a url"])
    def test_bad_store_urls_are_rejected(self, url):
        settings = Settings(woocommerce_base_url=url, woocommerce_consumer_key="k", woocommerce_consumer_secret="s")
        with pytest.raises(ValueError):
            WooCommerceClient(config=settings)


class TestOrders:
    async def test_filters_are_sent_and_dates_normalised(self):
        seen = {}

        def handler(request):
            seen.update(dict(request.url.params))
            return ok([ORDER])

        await make_client(handler).list_orders(status="processing", after="2026-10-23", per_page=5)
        assert seen["status"] == "processing" and seen["after"].startswith("2026-10-23")
        assert seen["per_page"] == "5" and seen["orderby"] == "date" and seen["order"] == "desc"

    @pytest.mark.parametrize("after", ["yesterday", "2026-13-45", "", "'; drop table", "23/10/2026"])
    async def test_bad_dates_are_rejected_before_any_request(self, after):
        def handler(request):
            raise AssertionError("no request should be made")

        with pytest.raises(ValueError, match="ISO 8601"):
            await make_client(handler).list_orders(after=after)

    async def test_unknown_status_filter_rejected(self):
        with pytest.raises(ValueError, match="Unsupported order status"):
            await make_client(lambda r: ok([])).list_orders(status="shipped")

    @pytest.mark.parametrize("kwargs", [dict(page=0), dict(page=-1), dict(per_page=0), dict(per_page=101), dict(page=True)])
    async def test_pagination_bounds(self, kwargs):
        with pytest.raises(ValueError):
            await make_client(lambda r: ok([])).list_orders(**kwargs)

    @pytest.mark.parametrize("order_id", [0, -3, True, "5", 1.5])
    async def test_order_id_must_be_a_positive_integer(self, order_id):
        with pytest.raises(ValueError):
            await make_client(lambda r: ok(ORDER)).get_order(order_id)

    async def test_update_status_sends_a_put_with_only_the_status(self):
        seen = {}

        def handler(request):
            seen["method"], seen["path"], seen["body"] = request.method, request.url.path, json.loads(request.content)
            return ok(ORDER)

        await make_client(handler).update_order_status(5, "completed")
        assert (seen["method"], seen["path"], seen["body"]) == ("PUT", "/wp-json/wc/v3/orders/5", {"status": "completed"})

    async def test_update_status_rejects_unknown_status_before_calling(self):
        def handler(request):
            raise AssertionError("no request should be made")

        with pytest.raises(ValueError, match="Unsupported order status"):
            await make_client(handler).update_order_status(5, "teleported")


class TestCreateProduct:
    async def test_sends_a_published_simple_product(self):
        seen = {}

        def handler(request):
            seen["method"], seen["body"] = request.method, json.loads(request.content)
            return ok(PRODUCT)

        await make_client(handler).create_product(name="  Mug  ", regular_price="349.50", description="Blue")
        assert seen["method"] == "POST"
        assert seen["body"] == {"name": "Mug", "type": "simple", "regular_price": "349.5", "status": "publish", "description": "Blue"}

    @pytest.mark.parametrize("price", ["abc", "-1", "NaN", "inf", "1e999999", "1e5", "", "12,5", "$5", "1.23456", "1" * 13, " ", ".5", "5."])
    async def test_invalid_prices_are_rejected(self, price):
        def handler(request):
            raise AssertionError("no request should be made")

        with pytest.raises(ValueError):
            await make_client(handler).create_product(name="Mug", regular_price=price)

    @pytest.mark.parametrize("name", ["", "   ", "x" * 201])
    async def test_invalid_names_are_rejected(self, name):
        with pytest.raises(ValueError, match="Product name"):
            await make_client(lambda r: ok(PRODUCT)).create_product(name=name, regular_price="1")

    async def test_invalid_status_rejected(self):
        with pytest.raises(ValueError, match="product status"):
            await make_client(lambda r: ok(PRODUCT)).create_product(name="Mug", regular_price="1", status="deleted")

    async def test_free_products_are_allowed(self):
        assert (await make_client(lambda r: ok(PRODUCT)).create_product(name="Freebie", regular_price="0"))["id"] == 9


class TestUpdateProduct:
    async def test_sends_only_the_changed_fields_with_put(self):
        seen = {}

        def handler(request):
            seen["method"], seen["url"] = request.method, str(request.url)
            seen["body"] = json.loads(request.content)
            return ok(PRODUCT)

        await make_client(handler).update_product(9, name=" Big Mug ", regular_price="399", stock_quantity=12)
        assert seen["method"] == "PUT" and seen["url"].endswith("/products/9")
        assert seen["body"] == {"name": "Big Mug", "regular_price": "399", "manage_stock": True, "stock_quantity": 12}

    async def test_a_call_with_nothing_to_change_is_rejected(self):
        with pytest.raises(ValueError, match="at least one field"):
            await make_client(lambda r: ok(PRODUCT)).update_product(9)

    @pytest.mark.parametrize(
        "kwargs,match",
        [
            ({"name": " "}, "Product name"),
            ({"regular_price": "1e5"}, "Price"),
            ({"status": "deleted"}, "product status"),
            ({"stock_quantity": -1}, "Stock quantity"),
            ({"stock_quantity": True}, "Stock quantity"),
        ],
    )
    async def test_invalid_values_are_rejected_before_any_request(self, kwargs, match):
        def handler(request):
            raise AssertionError("no request should be made")

        with pytest.raises(ValueError, match=match):
            await make_client(handler).update_product(9, **kwargs)

    async def test_updates_are_not_retried(self):
        attempts = []

        def handler(request):
            attempts.append(1)
            raise httpx.ReadTimeout("slow", request=request)

        with pytest.raises(WooCommerceTimeoutError):
            await make_client(handler).update_product(9, name="Mug")
        assert len(attempts) == 1

    async def test_get_product_reads_one_product(self):
        assert (await make_client(lambda r: ok(PRODUCT)).get_product(9))["id"] == 9


class TestFailureHandling:
    @pytest.mark.parametrize("status", [401, 403])
    async def test_auth_failures(self, status):
        with pytest.raises(WooCommerceAuthenticationError):
            await make_client(lambda r: httpx.Response(status, json={"code": "x"})).list_products()

    async def test_http_errors_carry_the_status_but_not_the_body(self):
        with pytest.raises(WooCommerceHTTPError) as excinfo:
            await make_client(lambda r: httpx.Response(500, text="stack trace with secrets")).list_products()
        assert excinfo.value.status_code == 500 and "secrets" not in str(excinfo.value)

    async def test_get_is_retried_once_after_a_timeout(self):
        attempts = []

        def handler(request):
            attempts.append(1)
            if len(attempts) == 1:
                raise httpx.ReadTimeout("slow", request=request)
            return ok([PRODUCT])

        assert (await make_client(handler).list_products())[0]["id"] == 9 and len(attempts) == 2

    async def test_get_gives_up_after_the_retry(self):
        attempts = []

        def handler(request):
            attempts.append(1)
            raise httpx.ReadTimeout("slow", request=request)

        with pytest.raises(WooCommerceTimeoutError):
            await make_client(handler).list_products()
        assert len(attempts) == 2

    async def test_writes_are_never_retried(self):
        attempts = []

        def handler(request):
            attempts.append(1)
            raise httpx.ReadTimeout("slow", request=request)

        with pytest.raises(WooCommerceTimeoutError):
            await make_client(handler).create_product(name="Mug", regular_price="5")
        assert len(attempts) == 1  # a retry could create the product twice

    async def test_status_updates_are_not_retried_either(self):
        attempts = []

        def handler(request):
            attempts.append(1)
            raise httpx.ConnectError("down", request=request)

        with pytest.raises(WooCommerceNetworkError):
            await make_client(handler).update_order_status(5, "completed")
        assert len(attempts) == 1

    async def test_network_errors(self):
        def handler(request):
            raise httpx.ConnectError("down", request=request)

        with pytest.raises(WooCommerceNetworkError):
            await make_client(handler).list_orders()

    @pytest.mark.parametrize("payload", ["not json at all", ""])
    async def test_malformed_json(self, payload):
        with pytest.raises(WooCommerceResponseError, match="malformed JSON"):
            await make_client(lambda r: httpx.Response(200, text=payload)).get_order(5)

    @pytest.mark.parametrize("payload", [{"unexpected": "object"}, [{"no_id": 1}], [{"id": "x"}], [{"id": -1}], "string", None, [1, 2]])
    async def test_unexpected_shapes_are_rejected(self, payload):
        with pytest.raises(WooCommerceResponseError):
            await make_client(lambda r: ok(payload)).list_orders()


class TestToolSurface:
    """The MCP tools: summaries are trimmed and failures become safe tool errors."""

    async def test_summaries_drop_bulky_fields(self):
        from woocommerce_mcp.server import _order_summary, _product_summary

        order = {
            "id": 5, "status": "processing", "total": "10", "currency": "INR", "date_created": "2026-10-23T10:00:00",
            "billing": {"first_name": "A", "last_name": "B", "email": "a@b.test", "phone": "999", "address_1": "secret street"},
            "payment_method_title": "COD",
            "line_items": [{"product_id": 9, "name": "Mug", "quantity": 2, "total": "10", "meta_data": [{"x": 1}]}],
            "meta_data": [{"huge": "blob"}],
        }
        summary = _order_summary(order)
        assert summary["customer"] == "A B" and summary["items"] == [{"product_id": 9, "name": "Mug", "quantity": 2, "total": "10"}]
        assert "address_1" not in json.dumps(summary) and "phone" not in json.dumps(summary)  # no extra personal data
        assert set(_product_summary({"id": 1, "name": "x", "description": "<p>long html</p>", "meta_data": []})) == {
            "id", "name", "sku", "status", "price", "regular_price", "stock_status", "permalink"
        }

    async def test_client_failures_become_tool_errors_without_leaking_details(self):
        from mcp.server.mcpserver.exceptions import ToolError

        from woocommerce_mcp.server import _call

        async def failing(error):
            raise error

        for error, expected in [
            (WooCommerceAuthenticationError("ck_secret rejected"), "rejected the server's credentials"),
            (WooCommerceTimeoutError("t"), "did not respond in time"),
            (ValueError("Price must be a number"), "Price must be a number"),
        ]:
            with pytest.raises(ToolError, match=expected) as excinfo:
                await _call(failing(error))
            assert "ck_secret" not in str(excinfo.value)
