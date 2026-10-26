import hmac

from fastapi import APIRouter, HTTPException, Request, status
from fastapi.responses import PlainTextResponse

from app.core.config import settings
from app.core.metrics import metrics
from app.core.security import decode_access_token
from app.db.session import session_scope
from app.repositories.user_repository import UserRepository
from app.services.user_service import UserService

router = APIRouter(include_in_schema=False)


def _authorized(request: Request) -> bool:
    scheme, _, supplied = request.headers.get("authorization", "").partition(" ")
    if scheme.lower() != "bearer" or not supplied.strip():
        return False
    supplied = supplied.strip()
    # A dedicated scrape token (for Prometheus), compared in constant time...
    if settings.metrics_token and hmac.compare_digest(supplied.encode(), settings.metrics_token.encode()):
        return True
    # ...or the session of an admin.
    user_id = decode_access_token(supplied)
    if user_id is None:
        return False
    with session_scope() as session:
        user = UserService(UserRepository(session)).find_user(user_id)
    return user is not None and user.is_active and user.role == "admin"


@router.get("/metrics")
def scrape(request: Request) -> PlainTextResponse:
    """Prometheus metrics. Restricted because label values include server and tool names."""
    if not _authorized(request):
        raise HTTPException(
            status.HTTP_401_UNAUTHORIZED,
            detail="Not authenticated",
            headers={"WWW-Authenticate": "Bearer"},
        )
    return PlainTextResponse(metrics.render(), media_type="text/plain; version=0.0.4")
