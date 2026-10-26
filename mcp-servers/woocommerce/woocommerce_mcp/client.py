import re
from datetime import datetime
from decimal import Decimal, InvalidOperation
from typing import Any
from urllib.parse import urlsplit

import httpx

from woocommerce_mcp.config import Settings, get_settings


SUPPORTED_ORDER_STATUSES = frozenset(
    {"pending", "processing", "on-hold", "completed", "cancelled", "refunded", "failed"}
)
_PRICE_PATTERN = re.compile(r"^\d{1,12}(\.\d{1,4})?$")
SUPPORTED_PRODUCT_STATUSES = frozenset({"publish", "draft", "pending", "private"})
_API_PATH = "/wp-json/wc/v3"
_DEFAULT_TIMEOUT_SECONDS = 10.0
_MAX_PER_PAGE = 100


class WooCommerceAPIError(Exception):
    """Base class for safe WooCommerce client errors."""


class WooCommerceAuthenticationError(WooCommerceAPIError):
    pass


class WooCommerceTimeoutError(WooCommerceAPIError):
    pass


class WooCommerceNetworkError(WooCommerceAPIError):
    pass


class WooCommerceResponseError(WooCommerceAPIError):
    pass


class WooCommerceHTTPError(WooCommerceAPIError):
    def __init__(self, operation: str, status_code: int) -> None:
        self.status_code = status_code
        super().__init__(f"WooCommerce {operation} failed with HTTP {status_code}")


class WooCommerceClient:
    def __init__(
        self,
        *,
        config: Settings | None = None,
        timeout: float = _DEFAULT_TIMEOUT_SECONDS,
        client: httpx.AsyncClient | None = None,
    ) -> None:
        if timeout <= 0:
            raise ValueError("Timeout must be greater than zero")

        config = config or get_settings()
        self._config = config
        self._timeout = timeout
        self._api_base_url = self._build_api_base_url(config.woocommerce_base_url)
        self._owns_client = client is None
        self._client = client or httpx.AsyncClient(
            timeout=timeout,
            verify=config.woocommerce_ca_bundle or True,
        )

    async def __aenter__(self) -> "WooCommerceClient":
        return self

    async def __aexit__(self, *_: object) -> None:
        await self.aclose()

    async def aclose(self) -> None:
        if self._owns_client:
            await self._client.aclose()

    async def list_products(
        self, *, page: int = 1, per_page: int = 20
    ) -> list[dict[str, Any]]:
        self._validate_pagination(page, per_page)
        payload = await self._request_json(
            "GET",
            "products",
            operation="list products",
            params={"page": page, "per_page": per_page},
        )
        return self._require_list(payload, "list products")

    async def get_product(self, product_id: int) -> dict[str, Any]:
        self._validate_id(product_id, "product")
        payload = await self._request_json(
            "GET", f"products/{product_id}", operation="retrieve product"
        )
        return self._require_object(payload, "retrieve product")

    async def create_product(
        self,
        *,
        name: str,
        regular_price: str,
        description: str | None = None,
        status: str = "publish",
    ) -> dict[str, Any]:
        name = name.strip()
        if not name or len(name) > 200:
            raise ValueError("Product name must be 1 to 200 characters")
        if status not in SUPPORTED_PRODUCT_STATUSES:
            raise ValueError("Unsupported product status")
        price = self._validate_price(regular_price)
        body: dict[str, Any] = {
            "name": name,
            "type": "simple",
            "regular_price": price,
            "status": status,
        }
        if description:
            body["description"] = description
        payload = await self._request_json(
            "POST", "products", operation="create product", json_body=body
        )
        return self._require_object(payload, "create product")

    async def update_product(
        self,
        product_id: int,
        *,
        name: str | None = None,
        regular_price: str | None = None,
        description: str | None = None,
        status: str | None = None,
        stock_quantity: int | None = None,
    ) -> dict[str, Any]:
        self._validate_id(product_id, "product")
        body: dict[str, Any] = {}
        if name is not None:
            name = name.strip()
            if not name or len(name) > 200:
                raise ValueError("Product name must be 1 to 200 characters")
            body["name"] = name
        if regular_price is not None:
            body["regular_price"] = self._validate_price(regular_price)
        if description is not None:
            body["description"] = description
        if status is not None:
            if status not in SUPPORTED_PRODUCT_STATUSES:
                raise ValueError("Unsupported product status")
            body["status"] = status
        if stock_quantity is not None:
            if isinstance(stock_quantity, bool) or not isinstance(stock_quantity, int) or not 0 <= stock_quantity <= 1_000_000:
                raise ValueError("Stock quantity must be a whole number from 0 to 1000000")
            body["manage_stock"] = True
            body["stock_quantity"] = stock_quantity
        if not body:
            raise ValueError("Provide at least one field to change")
        payload = await self._request_json(
            "PUT", f"products/{product_id}", operation="update product", json_body=body
        )
        return self._require_object(payload, "update product")

    async def list_orders(
        self,
        *,
        page: int = 1,
        per_page: int = 20,
        status: str | None = None,
        after: str | None = None,
    ) -> list[dict[str, Any]]:
        self._validate_pagination(page, per_page)
        params: dict[str, int | str] = {
            "page": page,
            "per_page": per_page,
            "orderby": "date",
            "order": "desc",
        }
        if status is not None:
            self._validate_status(status)
            params["status"] = status
        if after is not None:
            params["after"] = self._validate_after(after)
        payload = await self._request_json(
            "GET", "orders", operation="list orders", params=params
        )
        return self._require_list(payload, "list orders")

    async def get_order(self, order_id: int) -> dict[str, Any]:
        self._validate_id(order_id, "order")
        payload = await self._request_json(
            "GET", f"orders/{order_id}", operation="retrieve order"
        )
        return self._require_object(payload, "retrieve order")

    async def update_order_status(self, order_id: int, status: str) -> dict[str, Any]:
        self._validate_id(order_id, "order")
        if status not in SUPPORTED_ORDER_STATUSES:
            allowed = ", ".join(sorted(SUPPORTED_ORDER_STATUSES))
            raise ValueError(f"Unsupported order status. Supported statuses: {allowed}")

        payload = await self._request_json(
            "PUT",
            f"orders/{order_id}",
            operation="update order status",
            json_body={"status": status},
        )
        return self._require_object(payload, "update order status")

    async def _request_json(
        self,
        method: str,
        resource: str,
        *,
        operation: str,
        params: dict[str, int | str] | None = None,
        json_body: dict[str, Any] | None = None,
    ) -> Any:
        # Reads are safe to repeat, so retry them once; a write is never retried because
        # the first attempt may already have been applied.
        attempts = 2 if method == "GET" else 1
        for attempt in range(1, attempts + 1):
            try:
                response = await self._client.request(
                    method,
                    f"{self._api_base_url}/{resource}",
                    params=params,
                    json=json_body,
                    auth=(
                        self._config.woocommerce_consumer_key,
                        self._config.woocommerce_consumer_secret,
                    ),
                    timeout=self._timeout,
                )
                break
            except httpx.TimeoutException:
                if attempt < attempts:
                    continue
                raise WooCommerceTimeoutError(
                    f"WooCommerce {operation} timed out"
                ) from None
            except httpx.RequestError:
                if attempt < attempts:
                    continue
                raise WooCommerceNetworkError(
                    f"WooCommerce {operation} could not reach the store"
                ) from None

        if response.status_code in {401, 403}:
            raise WooCommerceAuthenticationError(
                f"WooCommerce authentication failed during {operation}"
            )
        if not response.is_success:
            raise WooCommerceHTTPError(operation, response.status_code)

        try:
            return response.json()
        except (ValueError, UnicodeDecodeError):
            raise WooCommerceResponseError(
                f"WooCommerce returned malformed JSON while attempting to {operation}"
            ) from None

    @staticmethod
    def _build_api_base_url(base_url: str) -> str:
        normalized = base_url.strip().rstrip("/")
        try:
            parsed = urlsplit(normalized)
            valid = (
                parsed.scheme in {"http", "https"}
                and bool(parsed.hostname)
                and parsed.username is None
                and parsed.password is None
                and not parsed.query
                and not parsed.fragment
            )
            _ = parsed.port
        except ValueError:
            valid = False

        if not valid:
            raise ValueError("WooCommerce store URL must be a valid HTTP(S) base URL")
        return f"{normalized}{_API_PATH}"

    @staticmethod
    def _validate_status(status: str) -> None:
        if status not in SUPPORTED_ORDER_STATUSES:
            allowed = ", ".join(sorted(SUPPORTED_ORDER_STATUSES))
            raise ValueError(f"Unsupported order status. Supported statuses: {allowed}")

    @staticmethod
    def _validate_after(after: str) -> str:
        try:
            parsed = datetime.fromisoformat(after.strip())
        except ValueError:
            raise ValueError(
                "after must be an ISO 8601 date or datetime, e.g. 2026-10-22"
            ) from None
        return parsed.isoformat()

    @staticmethod
    def _validate_price(price: str) -> str:
        # Plain decimal notation only. Exponent forms such as 1e999999 are valid numbers
        # but would expand to enormous strings, so they are refused outright.
        text = str(price).strip()
        if not _PRICE_PATTERN.fullmatch(text):
            raise ValueError(
                "Price must be a plain non-negative number such as 499 or 499.50 "
                "(at most 12 digits before and 4 after the decimal point)"
            )
        try:
            return format(Decimal(text).normalize(), "f")
        except InvalidOperation:
            raise ValueError("Price must be a number such as 499 or 499.50") from None

    @staticmethod
    def _validate_pagination(page: int, per_page: int) -> None:
        if isinstance(page, bool) or not isinstance(page, int) or page < 1:
            raise ValueError("Page must be a positive integer")
        if (
            isinstance(per_page, bool)
            or not isinstance(per_page, int)
            or not 1 <= per_page <= _MAX_PER_PAGE
        ):
            raise ValueError(f"per_page must be between 1 and {_MAX_PER_PAGE}")

    @staticmethod
    def _validate_id(record_id: int, resource: str) -> None:
        if isinstance(record_id, bool) or not isinstance(record_id, int) or record_id < 1:
            raise ValueError(f"{resource.capitalize()} ID must be a positive integer")

    @staticmethod
    def _require_list(payload: Any, operation: str) -> list[dict[str, Any]]:
        if not isinstance(payload, list) or any(
            not isinstance(item, dict)
            or isinstance(item.get("id"), bool)
            or not isinstance(item.get("id"), int)
            or item["id"] < 1
            for item in payload
        ):
            raise WooCommerceResponseError(
                f"WooCommerce returned an unexpected response while attempting to {operation}"
            )
        return payload

    @staticmethod
    def _require_object(payload: Any, operation: str) -> dict[str, Any]:
        if (
            not isinstance(payload, dict)
            or isinstance(payload.get("id"), bool)
            or not isinstance(payload.get("id"), int)
            or payload["id"] < 1
        ):
            raise WooCommerceResponseError(
                f"WooCommerce returned an unexpected response while attempting to {operation}"
            )
        return payload
