from typing import Annotated

from fastapi import Depends, HTTPException, Request, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from app.core.security import decode_access_token
from app.db.session import session_scope
from app.gateway.service import GatewayService, Principal
from app.repositories.user_repository import UserRepository
from app.services.user_service import UserRecord, UserService

_bearer = HTTPBearer(auto_error=False, description="Session token from POST /auth/login")


def get_current_user(
    credentials: Annotated[HTTPAuthorizationCredentials | None, Depends(_bearer)],
) -> UserRecord:
    """Authenticate a dashboard request from its session token.

    The role is read from the database on every request, never trusted from the token,
    so demoting or disabling a user takes effect immediately.
    """
    unauthorized = HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="Not authenticated",
        headers={"WWW-Authenticate": "Bearer"},
    )
    if credentials is None:
        raise unauthorized
    user_id = decode_access_token(credentials.credentials)
    if user_id is None:
        raise unauthorized
    with session_scope() as session:
        user = UserService(UserRepository(session)).find_user(user_id)
    if user is None or not user.is_active:
        raise unauthorized
    return user


def require_admin(user: Annotated[UserRecord, Depends(get_current_user)]) -> UserRecord:
    if user.role != "admin":
        raise HTTPException(status.HTTP_403_FORBIDDEN, detail="Admin access required")
    return user


def get_gateway(request: Request) -> GatewayService:
    return request.app.state.gateway


def dashboard_principal(user: UserRecord, channel: str = "dashboard") -> Principal:
    return Principal(user_id=user.id, email=user.email, role=user.role, channel=channel)


CurrentUser = Annotated[UserRecord, Depends(get_current_user)]
AdminUser = Annotated[UserRecord, Depends(require_admin)]
Gateway = Annotated[GatewayService, Depends(get_gateway)]
