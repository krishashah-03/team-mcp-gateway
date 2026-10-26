import socket
import subprocess
import sys
import time
from collections.abc import Generator
from pathlib import Path

import pytest
from sqlalchemy import text
from sqlalchemy.orm import Session

BACKEND = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BACKEND))


def _database_reachable() -> bool:
    try:
        from app.db.session import engine

        with engine.connect() as connection:
            connection.execute(text("select 1"))
        return True
    except Exception:  # noqa: BLE001
        return False


@pytest.fixture(scope="session")
def database_available() -> bool:
    return _database_reachable()


@pytest.fixture
def db(database_available: bool) -> Generator[Session]:
    """A session whose work is rolled back after the test, leaving the database untouched.

    Services only flush, never commit, so everything they do disappears with the rollback.
    """
    if not database_available:
        pytest.skip("Postgres is not reachable (run: docker compose up -d)")
    from app.db.session import engine

    connection = engine.connect()
    transaction = connection.begin()
    session = Session(bind=connection, autoflush=False, expire_on_commit=False)
    try:
        yield session
    finally:
        session.close()
        transaction.rollback()
        connection.close()


def kill_tree(process: subprocess.Popen) -> None:
    """Stop a child and its descendants (a Windows venv python.exe is a launcher stub)."""
    if process.poll() is None:
        if sys.platform == "win32":
            subprocess.run(["taskkill", "/F", "/T", "/PID", str(process.pid)], capture_output=True)
        else:
            process.terminate()
        process.wait(timeout=10)


def _wait_for_port(port: int, timeout: float = 20.0) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        with socket.socket() as probe:
            probe.settimeout(0.3)
            if probe.connect_ex(("127.0.0.1", port)) == 0:
                return
        time.sleep(0.2)
    raise RuntimeError(f"Nothing listened on port {port} within {timeout}s")


@pytest.fixture(scope="session")
def fixture_upstream() -> Generator[str]:
    """The fixture MCP server (echo, a colliding list_orders, slow) on a spare port."""
    port = 8112
    process = subprocess.Popen(
        [sys.executable, str(BACKEND / "tests/fixtures/fixture_server.py"), str(port)],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    try:
        _wait_for_port(port)
        yield f"http://127.0.0.1:{port}/mcp"
    finally:
        kill_tree(process)
