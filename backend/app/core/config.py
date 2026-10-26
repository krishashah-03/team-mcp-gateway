from pydantic import field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    database_url: str

    # Signs dashboard session tokens (JWT). Use a long random value, e.g.
    # python -c "import secrets; print(secrets.token_urlsafe(48))"
    jwt_secret: str
    jwt_ttl_minutes: int = 60

    # Fernet key that encrypts upstream credentials at rest, e.g.
    # python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"
    credential_encryption_key: str

    # First admin, created at startup only when no users exist yet.
    bootstrap_admin_email: str | None = None
    bootstrap_admin_password: str | None = None

    # Registers the WooCommerce MCP server as the first upstream when set.
    seed_woocommerce_url: str | None = None
    seed_woocommerce_token: str | None = None
    # Same for the two other bundled servers.
    seed_utilities_url: str | None = None
    seed_utilities_token: str | None = None
    seed_docs_url: str | None = None
    seed_docs_token: str | None = None

    log_format: str = "text"  # "text" for development, "json" for log collectors
    log_level: str = "INFO"
    # Scrape token for GET /metrics. Without it only an admin session may read metrics.
    metrics_token: str | None = None

    # Chat agent. The key is optional: without it the chat endpoint answers 503 and
    # everything else works. Set GEMINI_API_KEY in the environment to enable it.
    llm_provider: str = "gemini"
    gemini_api_key: str | None = None
    gemini_model: str
    chat_max_steps: int = 6
    # Above this many visible tools the agent gets search/describe/run instead of all of them.
    chat_max_direct_tools: int = 40
    chat_history_messages: int = 20
    chat_timeout_seconds: float = 60.0

    upstream_call_timeout_seconds: float = 15.0
    upstream_list_timeout_seconds: float = 5.0
    tool_cache_ttl_seconds: float = 10.0
    # How often a background task re-checks every enabled upstream. Requests read the
    # latest result instead of probing, so a dead server never slows them down. 0 disables.
    health_check_interval_seconds: float = 10.0

    # SSRF policy for upstream URLs. Cloud-metadata and link-local addresses are always
    # blocked. Loopback and private addresses are allowed by default so local development
    # works; in production set UPSTREAM_ALLOW_PRIVATE_NETWORKS=false and list the in-cluster
    # hosts that are expected in UPSTREAM_ALLOWED_HOSTS (comma-separated, wildcards allowed,
    # e.g. "*.svc.cluster.local").
    upstream_allow_private_networks: bool = True
    upstream_allowed_hosts: str = ""

    # Brute-force protection for POST /auth/login (per client address and email).
    login_max_failures: int = 5
    login_window_seconds: int = 900
    # Tool-call budget per user on /mcp and /chat; 0 disables the limit.
    rate_limit_per_minute: int = 120
    max_body_bytes: int = 1_048_576

    # Host header values accepted on /mcp (DNS rebinding protection).
    gateway_allowed_hosts: str = "localhost:*,127.0.0.1:*"
    cors_origins: str = "http://localhost:5173"

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    @field_validator("jwt_secret")
    @classmethod
    def _jwt_secret_is_long(cls, value: str) -> str:
        if len(value) < 32:
            raise ValueError("JWT_SECRET must be at least 32 characters")
        return value

    @property
    def upstream_host_allowlist(self) -> list[str]:
        return [h.strip().lower() for h in self.upstream_allowed_hosts.split(",") if h.strip()]

    @property
    def allowed_hosts(self) -> list[str]:
        return [h.strip() for h in self.gateway_allowed_hosts.split(",") if h.strip()]

    @property
    def allowed_origins(self) -> list[str]:
        return [o.strip() for o in self.cors_origins.split(",") if o.strip()]


settings = Settings()  # type: ignore[call-arg]
