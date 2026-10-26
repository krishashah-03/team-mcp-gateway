from functools import lru_cache
from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict

_DEFAULT_DOCS_DIR = Path(__file__).resolve().parent.parent / "docs_data"


class Settings(BaseSettings):
    # Folder of .md/.txt documents to serve. Mount your own folder here in production.
    docs_dir: str | None = None

    mcp_auth_token: str | None = None
    mcp_allowed_hosts: str = "localhost:*,127.0.0.1:*"
    mcp_host: str = "0.0.0.0"
    mcp_port: int = 8004

    model_config = SettingsConfigDict(extra="ignore")

    @property
    def resolved_docs_dir(self) -> Path:
        return Path(self.docs_dir).resolve() if self.docs_dir else _DEFAULT_DOCS_DIR

    @property
    def allowed_hosts(self) -> list[str]:
        return [host.strip() for host in self.mcp_allowed_hosts.split(",") if host.strip()]


@lru_cache
def get_settings() -> Settings:
    return Settings()
