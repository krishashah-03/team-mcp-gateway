"""Creates and removes the throwaway data the browser tests use.

Run with the backend's Python from the backend folder (so app settings load):
    python ../frontend/e2e/support/e2e_data.py seed    -> prints the admin's credentials as JSON
    python ../frontend/e2e/support/e2e_data.py purge  -> deletes everything the tests created
Everything uses the reserved "@test.invalid" domain or a "tst" server prefix.
"""

import json
import secrets
import sys
from pathlib import Path

sys.path.insert(0, str(Path.cwd()))

from sqlalchemy import text  # noqa: E402

from app.db.session import session_scope  # noqa: E402
from app.repositories.user_repository import UserRepository  # noqa: E402
from app.services.user_service import UserService  # noqa: E402


def purge() -> None:
    with session_scope() as session:
        session.execute(text("delete from gateway_audit_events where actor_email like '%@test.invalid' or upstream_server_slug like 'tst%'"))
        session.execute(text("delete from tool_permissions where server_slug like 'tst%'"))
        session.execute(text("delete from users where email like '%@test.invalid'"))
        session.execute(text("delete from mcp_servers where slug like 'tst%'"))


def seed() -> None:
    purge()
    email = f"e2e-admin-{secrets.token_hex(3)}@test.invalid"
    password = "E2e-admin-password-1"
    with session_scope() as session:
        UserService(UserRepository(session)).create_user(email=email, password=password, role="admin")
    print(json.dumps({"email": email, "password": password}))


if __name__ == "__main__":
    {"seed": seed, "purge": purge}[sys.argv[1]]()
