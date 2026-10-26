from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    woocommerce_base_url: str
    woocommerce_consumer_key: str
    woocommerce_consumer_secret: str
    # Optional CA file for stores served with a private CA (local development only).
    woocommerce_ca_bundle: str | None = None

    # Shared secret the gateway presents to this server. When unset the server relies on
    # network isolation alone, which is acceptable only for local development.
    mcp_auth_token: str | None = None
    # Hostnames clients may use in the Host header (DNS rebinding protection).
    mcp_allowed_hosts: str = "localhost:*,127.0.0.1:*"
    mcp_host: str = "0.0.0.0"
    mcp_port: int = 8001

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    @property
    def allowed_hosts(self) -> list[str]:
        return [host.strip() for host in self.mcp_allowed_hosts.split(",") if host.strip()]


@lru_cache
def get_settings() -> Settings:
    return Settings()  # type: ignore[call-arg]
