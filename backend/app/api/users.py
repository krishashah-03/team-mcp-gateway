from fastapi import APIRouter, HTTPException, status

from app.api.auth import user_out
from app.api.deps import AdminUser
from app.api.schemas import UserCreate, UserOut, UserUpdate
from app.db.session import session_scope
from app.repositories.user_repository import UserRepository
from app.services.user_service import (
    DuplicateUserError,
    UserNotFoundError,
    UserService,
    UserValidationError,
)

router = APIRouter(prefix="/users", tags=["Users"])


@router.get("", response_model=list[UserOut])
def list_users(_: AdminUser) -> list[UserOut]:
    with session_scope() as session:
        return [user_out(u) for u in UserService(UserRepository(session)).list_users()]


@router.post("", response_model=UserOut, status_code=status.HTTP_201_CREATED)
def create_user(body: UserCreate, _: AdminUser) -> UserOut:
    """Invite a team member by creating their account with an initial password."""
    try:
        with session_scope() as session:
            user = UserService(UserRepository(session)).create_user(
                email=body.email, password=body.password, role=body.role
            )
    except DuplicateUserError as exc:
        raise HTTPException(status.HTTP_409_CONFLICT, detail=str(exc)) from None
    except UserValidationError as exc:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, detail=str(exc)) from None
    return user_out(user)


@router.patch("/{user_id}", response_model=UserOut)
def update_user(user_id: int, body: UserUpdate, _: AdminUser) -> UserOut:
    try:
        with session_scope() as session:
            user = UserService(UserRepository(session)).update_user(
                user_id, role=body.role, is_active=body.is_active, password=body.password
            )
    except UserNotFoundError as exc:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail=str(exc)) from None
    except UserValidationError as exc:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, detail=str(exc)) from None
    return user_out(user)
