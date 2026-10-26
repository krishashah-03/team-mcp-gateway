from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    # Shared secret the gateway presents. When unset the server relies on network
    # isolation alone, which is acceptable only for local development.
    mcp_auth_token: str | None = None
    # Hostnames clients may use in the Host header (DNS rebinding protection).
    mcp_allowed_hosts: str = "localhost:*,127.0.0.1:*"
    mcp_host: str = "0.0.0.0"
    mcp_port: int = 8003

    model_config = SettingsConfigDict(extra="ignore")

    @property
    def allowed_hosts(self) -> list[str]:
        return [host.strip() for host in self.mcp_allowed_hosts.split(",") if host.strip()]


@lru_cache
def get_settings() -> Settings:
    return Settings()
