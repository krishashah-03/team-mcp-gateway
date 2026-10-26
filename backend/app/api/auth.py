from fastapi import APIRouter, HTTPException, Request, status

from app.api.deps import CurrentUser
from app.api.schemas import LoginRequest, TokenResponse, UserOut
from app.core.limiters import login_failures_by_account, login_failures_by_client
from app.core.metrics import metrics
from app.core.security import create_access_token
from app.db.session import session_scope
from app.repositories.user_repository import UserRepository
from app.services.user_service import UserService

router = APIRouter()


def user_out(user) -> UserOut:
    return UserOut(
        id=user.id,
        email=user.email,
        role=user.role,
        is_active=user.is_active,
        created_at=user.created_at,
    )


@router.post("/auth/login", response_model=TokenResponse)
def login(body: LoginRequest, request: Request) -> TokenResponse:
    client = request.client.host if request.client else "unknown"
    account_key = f"{client}|{body.email.strip().lower()}"

    # Refuse before doing any password work once too many attempts have failed.
    for limiter, key in (
        (login_failures_by_account, account_key),
        (login_failures_by_client, client),
    ):
        allowed, retry_after = limiter.check(key)
        if not allowed:
            metrics.inc("gateway_rate_limited_total", scope="login")
            raise HTTPException(
                status.HTTP_429_TOO_MANY_REQUESTS,
                detail="Too many failed sign-in attempts. Try again later.",
                headers={"Retry-After": str(retry_after)},
            )

    with session_scope() as session:
        user = UserService(UserRepository(session)).authenticate(body.email, body.password)
    if user is None:
        metrics.inc("gateway_auth_failures_total", kind="login")
        login_failures_by_account.hit(account_key)
        login_failures_by_client.hit(client)
        # Same message for unknown email, wrong password and disabled account.
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, detail="Invalid email or password")

    login_failures_by_account.reset(account_key)
    token, expires_in = create_access_token(user_id=user.id, role=user.role)
    return TokenResponse(access_token=token, expires_in=expires_in, user=user_out(user))


@router.get("/auth/me", response_model=UserOut)
def me(user: CurrentUser) -> UserOut:
    return user_out(user)
