from app.core.config import settings
from app.core.ratelimit import SlidingWindowLimiter

# Failed logins per (client address, email): stops guessing one account's password.
login_failures_by_account = SlidingWindowLimiter(
    settings.login_max_failures, settings.login_window_seconds
)
# Failed logins per client address across all emails: stops credential stuffing.
login_failures_by_client = SlidingWindowLimiter(
    settings.login_max_failures * 4, settings.login_window_seconds
)
# Requests per user on /mcp and the chat endpoint.
api_calls_by_user = SlidingWindowLimiter(settings.rate_limit_per_minute, 60)
