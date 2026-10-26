from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, HTTPException, status

from app.api.deps import AdminUser, CurrentUser
from app.api.schemas import APIKeyCreate, APIKeyIssued, APIKeyOut
from app.db.session import session_scope
from app.repositories.api_key_repository import APIKeyRepository
from app.repositories.user_repository import UserRepository
from app.services.api_key_service import APIKeyMetadata, APIKeyService, IssuedAPIKey
from app.services.user_service import UserService

router = APIRouter(tags=["API keys"])


def _out(meta: APIKeyMetadata, emails: dict[int, str] | None = None) -> APIKeyOut:
    return APIKeyOut(
        id=meta.id,
        key_id=meta.key_id,
        name=meta.name,
        user_id=meta.user_id,
        user_email=(emails or {}).get(meta.user_id),
        created_at=meta.created_at,
        expires_at=meta.expires_at,
        revoked_at=meta.revoked_at,
    )


def _issue(user_id: int, body: APIKeyCreate) -> IssuedAPIKey:
    expires_at = (
        datetime.now(timezone.utc) + timedelta(days=body.expires_in_days)
        if body.expires_in_days
        else None
    )
    with session_scope() as session:
        return APIKeyService(APIKeyRepository(session)).issue_key(
            user_id=user_id, name=body.name, expires_at=expires_at
        )


def _issued_out(issued: IssuedAPIKey) -> APIKeyIssued:
    return APIKeyIssued(**_out(issued.metadata).model_dump(), key=issued.plaintext_key)


@router.get("/keys", response_model=list[APIKeyOut])
def list_my_keys(user: CurrentUser) -> list[APIKeyOut]:
    with session_scope() as session:
        return [_out(k) for k in APIKeyService(APIKeyRepository(session)).list_keys(user_id=user.id)]


@router.post("/keys", response_model=APIKeyIssued, status_code=status.HTTP_201_CREATED)
def create_my_key(body: APIKeyCreate, user: CurrentUser) -> APIKeyIssued:
    """Generate a key for yourself. The key is shown once and only a hash is stored."""
    return _issued_out(_issue(user.id, body))


@router.delete("/keys/{key_id}", response_model=APIKeyOut)
def revoke_key(key_id: str, user: CurrentUser) -> APIKeyOut:
    """Revoke a key. Members may revoke their own; admins may revoke anyone's."""
    with session_scope() as session:
        service = APIKeyService(APIKeyRepository(session))
        meta = service.get_key(key_id)
        # Same 404 whether the key is missing or someone else's, so key ids cannot be probed.
        if meta is None or (meta.user_id != user.id and user.role != "admin"):
            raise HTTPException(status.HTTP_404_NOT_FOUND, detail="API key not found")
        revoked = service.revoke_key(key_id)
    assert revoked is not None
    return _out(revoked)


@router.get("/admin/keys", response_model=list[APIKeyOut])
def list_all_keys(_: AdminUser) -> list[APIKeyOut]:
    with session_scope() as session:
        keys = APIKeyService(APIKeyRepository(session)).list_keys()
        emails = {u.id: u.email for u in UserService(UserRepository(session)).list_users()}
    return [_out(k, emails) for k in keys]


@router.post(
    "/users/{user_id}/keys", response_model=APIKeyIssued, status_code=status.HTTP_201_CREATED
)
def create_key_for_user(user_id: int, body: APIKeyCreate, _: AdminUser) -> APIKeyIssued:
    with session_scope() as session:
        if UserService(UserRepository(session)).find_user(user_id) is None:
            raise HTTPException(status.HTTP_404_NOT_FOUND, detail="User not found")
    return _issued_out(_issue(user_id, body))
