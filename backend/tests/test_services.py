"""Service-layer tests against the real database; each test rolls back."""

from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import text

from app.core.security import cipher
from app.db.models import GatewayAPIKey, MCPServer
from app.repositories.api_key_repository import APIKeyRepository
from app.repositories.audit_event_repository import AuditEventRepository
from app.repositories.mcp_server_repository import MCPServerRepository
from app.repositories.tool_permission_repository import ToolPermissionRepository
from app.repositories.user_repository import UserRepository
from app.services.api_key_service import APIKeyService
from app.services.audit_event_service import AuditEventInput, AuditEventService
from app.services.conversation_service import ConversationNotFoundError, ConversationService
from app.services.mcp_server_service import (
    KEEP,
    DuplicateMCPServerSlugError,
    MCPServerCreate,
    MCPServerNotFoundError,
    MCPServerService,
    MCPServerUpdate,
    MCPServerValidationError,
)
from app.services.tool_permission_service import (
    PermissionNotFoundError,
    PermissionValidationError,
    ToolPermissionService,
)
from app.services.user_service import (
    DuplicateUserError,
    UserNotFoundError,
    UserService,
    UserValidationError,
)

pytestmark = pytest.mark.db
PASSWORD = "A-long-test-password-1"


def users(db) -> UserService:
    return UserService(UserRepository(db))


def servers(db) -> MCPServerService:
    return MCPServerService(MCPServerRepository(db), cipher)


def make_user(db, email="u@example.com", role="member"):
    return users(db).create_user(email=email, password=PASSWORD, role=role)


class TestUsers:
    def test_email_is_normalised(self, db):
        assert make_user(db, "  Mixed.Case@Example.COM ").email == "mixed.case@example.com"

    def test_duplicate_email_rejected_case_insensitively(self, db):
        make_user(db, "dup@example.com")
        with pytest.raises(DuplicateUserError):
            make_user(db, "DUP@example.com")

    @pytest.mark.parametrize("email", ["", "no-at-sign", "a@b", "two@@example.com", "spa ce@example.com"])
    def test_invalid_email_rejected(self, db, email):
        with pytest.raises(UserValidationError):
            users(db).create_user(email=email, password=PASSWORD, role="member")

    @pytest.mark.parametrize("password", ["", "short", "x" * 9, "x" * 201])
    def test_bad_password_length_rejected(self, db, password):
        with pytest.raises(UserValidationError):
            users(db).create_user(email="p@example.com", password=password, role="member")

    def test_invalid_role_rejected(self, db):
        with pytest.raises(UserValidationError):
            users(db).create_user(email="r@example.com", password=PASSWORD, role="root")

    def test_password_is_stored_hashed(self, db):
        user = make_user(db, "h@example.com")
        stored = db.execute(text("select password_hash from users where id = :i"), {"i": user.id}).scalar()
        assert PASSWORD not in stored and stored.startswith("scrypt$")

    def test_authenticate(self, db):
        make_user(db, "a@example.com")
        service = users(db)
        assert service.authenticate("a@example.com", PASSWORD) is not None
        assert service.authenticate("  A@EXAMPLE.COM ", PASSWORD) is not None
        assert service.authenticate("a@example.com", "wrong-password-1") is None
        assert service.authenticate("nobody@example.com", PASSWORD) is None

    def test_disabled_user_cannot_authenticate(self, db):
        user = make_user(db, "d@example.com")
        make_user(db, "other-admin@example.com", "admin")
        users(db).update_user(user.id, is_active=False)
        assert users(db).authenticate("d@example.com", PASSWORD) is None

    def test_last_admin_protected(self, db):
        service = users(db)
        existing_admins = UserRepository(db).count_active_admins()
        new_admin = service.create_user(email="adm@example.com", password=PASSWORD, role="admin")
        if existing_admins == 0:
            with pytest.raises(UserValidationError):
                service.update_user(new_admin.id, role="member")
            with pytest.raises(UserValidationError):
                service.update_user(new_admin.id, is_active=False)
        else:
            # With another active admin present, demoting is allowed.
            assert service.update_user(new_admin.id, role="member").role == "member"

    def test_update_unknown_user(self, db):
        with pytest.raises(UserNotFoundError):
            users(db).update_user(999_999, role="member")

    def test_password_change_takes_effect(self, db):
        user = make_user(db, "pw@example.com")
        users(db).update_user(user.id, password="Another-long-password-2")
        assert users(db).authenticate("pw@example.com", PASSWORD) is None
        assert users(db).authenticate("pw@example.com", "Another-long-password-2") is not None

    def test_bootstrap_creates_admin_only_when_empty(self, db):
        if UserRepository(db).count() > 0:
            assert users(db).ensure_bootstrap_admin("boot@example.com", PASSWORD) is False
        else:
            assert users(db).ensure_bootstrap_admin("boot@example.com", PASSWORD) is True
            assert users(db).ensure_bootstrap_admin("boot2@example.com", PASSWORD) is False


class TestAPIKeys:
    def service(self, db):
        return APIKeyService(APIKeyRepository(db))

    def test_issue_and_validate(self, db):
        user = make_user(db)
        issued = self.service(db).issue_key(user_id=user.id, name="laptop")
        assert issued.plaintext_key.startswith("gwk_")
        meta = self.service(db).validate_key(issued.plaintext_key)
        assert meta is not None and meta.user_id == user.id

    def test_only_a_hash_is_stored(self, db):
        user = make_user(db)
        issued = self.service(db).issue_key(user_id=user.id, name="k")
        secret = issued.plaintext_key.split("_", 2)[2]
        row = db.query(GatewayAPIKey).filter_by(key_id=issued.metadata.key_id).one()
        assert secret not in row.key_hash and len(row.key_hash) == 64
        assert secret not in repr(issued)  # the repr of the issuance result hides the key

    def test_wrong_secret_rejected(self, db):
        user = make_user(db)
        issued = self.service(db).issue_key(user_id=user.id, name="k")
        prefix = issued.plaintext_key.rsplit("_", 1)[0]
        assert self.service(db).validate_key(prefix + "_" + "A" * 43) is None

    @pytest.mark.parametrize("bad", ["", "gwk_", "gwk_abc_def", "nope_" + "a" * 32 + "_" + "b" * 43, "Bearer x"])
    def test_malformed_keys_rejected(self, db, bad):
        assert self.service(db).validate_key(bad) is None

    def test_revoked_key_rejected(self, db):
        user = make_user(db)
        issued = self.service(db).issue_key(user_id=user.id, name="k")
        self.service(db).revoke_key(issued.metadata.key_id)
        assert self.service(db).validate_key(issued.plaintext_key) is None

    def test_revoke_is_idempotent(self, db):
        user = make_user(db)
        issued = self.service(db).issue_key(user_id=user.id, name="k")
        first = self.service(db).revoke_key(issued.metadata.key_id)
        second = self.service(db).revoke_key(issued.metadata.key_id)
        assert first.revoked_at == second.revoked_at

    def test_expired_key_rejected(self, db):
        user = make_user(db)
        issued = self.service(db).issue_key(user_id=user.id, name="k", expires_at=datetime.now(timezone.utc) + timedelta(hours=1))
        row = db.query(GatewayAPIKey).filter_by(key_id=issued.metadata.key_id).one()
        row.expires_at = datetime.now(timezone.utc) - timedelta(seconds=1)
        db.flush()
        assert self.service(db).validate_key(issued.plaintext_key) is None

    def test_past_expiry_rejected_at_issue(self, db):
        user = make_user(db)
        with pytest.raises(ValueError):
            self.service(db).issue_key(user_id=user.id, name="k", expires_at=datetime.now(timezone.utc) - timedelta(days=1))

    def test_naive_expiry_rejected(self, db):
        user = make_user(db)
        with pytest.raises(ValueError):
            self.service(db).issue_key(user_id=user.id, name="k", expires_at=datetime.now() + timedelta(days=1))

    @pytest.mark.parametrize("name", ["", "   ", "x" * 201])
    def test_bad_names_rejected(self, db, name):
        user = make_user(db)
        with pytest.raises(ValueError):
            self.service(db).issue_key(user_id=user.id, name=name)

    def test_keys_are_listed_per_user(self, db):
        a, b = make_user(db, "a@example.com"), make_user(db, "b@example.com")
        self.service(db).issue_key(user_id=a.id, name="a1")
        self.service(db).issue_key(user_id=b.id, name="b1")
        assert [k.name for k in self.service(db).list_keys(user_id=a.id)] == ["a1"]

    def test_deleting_a_user_removes_their_keys(self, db):
        user = make_user(db, "gone@example.com")
        issued = self.service(db).issue_key(user_id=user.id, name="k")
        db.execute(text("delete from users where id = :i"), {"i": user.id})
        assert self.service(db).get_key(issued.metadata.key_id) is None


class TestServers:
    def create(self, db, **overrides):
        data = dict(name="Shop", slug="shop", endpoint_url="http://127.0.0.1:9000/mcp")
        data.update(overrides)
        return servers(db).create_server(MCPServerCreate(**data))

    def test_credential_is_encrypted_and_never_in_the_record(self, db):
        record = self.create(db, auth_type="bearer", credential="super-secret-token")
        assert record.has_credential is True
        assert "super-secret-token" not in repr(record)
        raw = db.query(MCPServer).filter_by(id=record.id).one().credential_encrypted
        assert raw and "super-secret-token" not in raw

    def test_bearer_target_header(self, db):
        record = self.create(db, auth_type="bearer", credential="tok")
        assert servers(db).get_target(record.id).headers == {"Authorization": "Bearer tok"}

    def test_custom_header_target(self, db):
        record = self.create(db, auth_type="header", auth_header_name="X-Api-Key", credential="k1")
        assert servers(db).get_target(record.id).headers == {"X-Api-Key": "k1"}

    def test_no_auth_has_no_headers(self, db):
        assert servers(db).get_target(self.create(db).id).headers == {}

    def test_target_repr_hides_headers(self, db):
        record = self.create(db, auth_type="bearer", credential="tok")
        assert "tok" not in repr(servers(db).get_target(record.id))

    @pytest.mark.parametrize("header", ["Host", "content-length", "Content-Type", "Mcp-Session-Id", "Bad Header", "", "x" * 101])
    def test_reserved_or_invalid_header_names_rejected(self, db, header):
        with pytest.raises(MCPServerValidationError):
            self.create(db, auth_type="header", auth_header_name=header, credential="k")

    @pytest.mark.parametrize("auth_type", ["bearer", "header"])
    def test_credential_required_when_auth_configured(self, db, auth_type):
        with pytest.raises(MCPServerValidationError):
            self.create(db, auth_type=auth_type, auth_header_name="X-Key", credential=None)

    def test_credential_with_line_break_rejected(self, db):
        with pytest.raises(MCPServerValidationError):
            self.create(db, auth_type="bearer", credential="abc\r\nX-Injected: 1")

    def test_unknown_auth_type_rejected(self, db):
        with pytest.raises(MCPServerValidationError):
            self.create(db, auth_type="oauth")

    @pytest.mark.parametrize("slug", ["", "Upper", "has_underscore", "-lead", "trail-", "dou--ble", "a" * 41, "spa ce"])
    def test_bad_slugs_rejected(self, db, slug):
        with pytest.raises(MCPServerValidationError):
            self.create(db, slug=slug)

    @pytest.mark.parametrize("url", ["", "ftp://x.test/mcp", "http://", "javascript:alert(1)", "http://user:pw@x.test/mcp", "x" * 3000])
    def test_bad_urls_rejected(self, db, url):
        with pytest.raises(MCPServerValidationError):
            self.create(db, endpoint_url=url)

    def test_duplicate_slug_rejected(self, db):
        self.create(db)
        with pytest.raises(DuplicateMCPServerSlugError):
            self.create(db, name="Other")

    def test_update_keeps_credential_when_not_sent(self, db):
        record = self.create(db, auth_type="bearer", credential="keep-me")
        servers(db).update_server(record.id, MCPServerUpdate(name="Renamed"))
        assert servers(db).get_target(record.id).headers == {"Authorization": "Bearer keep-me"}

    def test_update_replaces_credential(self, db):
        record = self.create(db, auth_type="bearer", credential="old")
        servers(db).update_server(record.id, MCPServerUpdate(credential="new"))
        assert servers(db).get_target(record.id).headers == {"Authorization": "Bearer new"}

    def test_switching_to_none_clears_credential(self, db):
        record = self.create(db, auth_type="bearer", credential="old")
        updated = servers(db).update_server(record.id, MCPServerUpdate(auth_type="none"))
        assert updated.has_credential is False and servers(db).get_target(record.id).headers == {}

    def test_clearing_credential_while_auth_required_is_rejected(self, db):
        record = self.create(db, auth_type="bearer", credential="old")
        with pytest.raises(MCPServerValidationError):
            servers(db).update_server(record.id, MCPServerUpdate(credential=None))

    def test_disabled_servers_are_not_targets(self, db):
        record = self.create(db)
        servers(db).update_server(record.id, MCPServerUpdate(is_enabled=False))
        assert all(t.id != record.id for t in servers(db).list_enabled_targets())

    def test_remove_and_missing(self, db):
        record = self.create(db)
        servers(db).remove_server(record.id)
        with pytest.raises(MCPServerNotFoundError):
            servers(db).get_server(record.id)

    def test_seed_is_idempotent(self, db):
        data = MCPServerCreate(name="Seed", slug="seed", endpoint_url="http://127.0.0.1:9/mcp")
        assert servers(db).ensure_seed_server(data) is True
        assert servers(db).ensure_seed_server(data) is False


class TestToolPermissionRules:
    def service(self, db):
        return ToolPermissionService(ToolPermissionRepository(db))

    def test_set_is_an_upsert(self, db):
        first = self.service(db).set_rule(role="member", user_id=None, server_slug="woocommerce", tool_name="create_product", effect="deny")
        again = self.service(db).set_rule(role="member", user_id=None, server_slug="woocommerce", tool_name="create_product", effect="allow")
        assert first.id == again.id and again.effect == "allow"

    @pytest.mark.parametrize(
        "kwargs",
        [
            dict(role=None, user_id=None),
            dict(role="member", user_id=1),
            dict(role="root", user_id=None),
            dict(role="member", user_id=None, effect="maybe"),
            dict(role="member", user_id=None, server_slug="Bad_Slug"),
            dict(role="member", user_id=None, tool_name="bad name!"),
        ],
    )
    def test_invalid_rules_rejected(self, db, kwargs):
        base = dict(role="member", user_id=None, server_slug="woocommerce", tool_name=None, effect="deny")
        base.update(kwargs)
        with pytest.raises(PermissionValidationError):
            self.service(db).set_rule(**base)

    def test_delete_missing_rule(self, db):
        with pytest.raises(PermissionNotFoundError):
            self.service(db).delete_rule(999_999)

    def test_user_rules_removed_with_the_user(self, db):
        user = make_user(db, "rule-owner@example.com")
        rule = self.service(db).set_rule(role=None, user_id=user.id, server_slug="docs", tool_name=None, effect="deny")
        db.execute(text("delete from users where id = :i"), {"i": user.id})
        assert all(r.id != rule.id for r in self.service(db).list_rules())


class TestConversations:
    def test_users_cannot_see_each_others_conversations(self, db):
        a, b = make_user(db, "a@example.com"), make_user(db, "b@example.com")
        conversation = ConversationService(db).create(a.id, "hello")
        assert ConversationService(db).get_messages(a.id, conversation.id) == []
        with pytest.raises(ConversationNotFoundError):
            ConversationService(db).get_messages(b.id, conversation.id)
        with pytest.raises(ConversationNotFoundError):
            ConversationService(db).append(b.id, conversation.id, role="user", content="intrude")
        with pytest.raises(ConversationNotFoundError):
            ConversationService(db).delete(b.id, conversation.id)
        assert ConversationService(db).list_conversations(b.id) == []

    def test_history_returns_the_last_messages_in_order(self, db):
        user = make_user(db)
        service = ConversationService(db)
        conversation = service.create(user.id, "x")
        for index in range(6):
            service.append(user.id, conversation.id, role="user" if index % 2 == 0 else "assistant", content=f"m{index}")
        assert [m.content for m in service.get_messages(user.id, conversation.id, last=3)] == ["m3", "m4", "m5"]

    def test_title_is_trimmed_and_never_empty(self, db):
        user = make_user(db)
        assert len(ConversationService(db).create(user.id, "word " * 50).title) <= 60
        assert ConversationService(db).create(user.id, "   ").title == "New conversation"

    def test_tool_calls_round_trip(self, db):
        user = make_user(db)
        service = ConversationService(db)
        conversation = service.create(user.id, "x")
        service.append(user.id, conversation.id, role="assistant", content="done", tool_calls=[{"name": "t", "is_error": False}])
        assert service.get_messages(user.id, conversation.id)[0].tool_calls == [{"name": "t", "is_error": False}]

    def test_deleting_a_conversation_deletes_its_messages(self, db):
        user = make_user(db)
        service = ConversationService(db)
        conversation = service.create(user.id, "x")
        service.append(user.id, conversation.id, role="user", content="hi")
        service.delete(user.id, conversation.id)
        assert db.execute(text("select count(*) from chat_messages where conversation_id = :c"), {"c": conversation.id}).scalar() == 0


class TestAudit:
    def service(self, db):
        return AuditEventService(AuditEventRepository(db))

    def event(self, **overrides):
        data = dict(operation="tool_call", duration_ms=5, outcome="success")
        data.update(overrides)
        return AuditEventInput(**data)

    def test_records_who_what_and_how_long(self, db):
        user = make_user(db)
        record = self.service(db).record_event(
            self.event(actor_user_id=user.id, actor_email=user.email, upstream_server_slug="woocommerce", tool_name="list_orders", duration_ms=42)
        )
        assert (record.actor_email, record.upstream_server_slug, record.tool_name, record.duration_ms) == (user.email, "woocommerce", "list_orders", 42)

    def test_control_characters_and_length_are_sanitised(self, db):
        record = self.service(db).record_event(self.event(tool_name="evil\nINJECTED LOG LINE\x00" + "x" * 500))
        assert "\n" not in record.tool_name and "\x00" not in record.tool_name and len(record.tool_name) <= 200

    @pytest.mark.parametrize(
        "overrides",
        [
            dict(outcome="maybe"),
            dict(duration_ms=-1),
            dict(duration_ms=True),
            dict(outcome="success", error_category="timeout"),
            dict(outcome="failure", error_category="made_up"),
            dict(channel="carrier-pigeon"),
            dict(operation="has spaces"),
            dict(status_code=99),
            dict(actor_user_id=0),
        ],
    )
    def test_invalid_events_rejected(self, db, overrides):
        with pytest.raises(ValueError):
            self.service(db).record_event(self.event(**overrides))

    def test_search_filters_and_orders_newest_first(self, db):
        user = make_user(db)
        service = self.service(db)
        service.record_event(self.event(actor_user_id=user.id, upstream_server_slug="a", tool_name="one"))
        service.record_event(self.event(actor_user_id=user.id, upstream_server_slug="b", tool_name="two", outcome="failure", error_category="timeout"))
        mine, total = service.search(actor_user_id=user.id)
        assert total == 2 and [e.tool_name for e in mine] == ["two", "one"]
        assert [e.tool_name for e in service.search(actor_user_id=user.id, outcome="failure")[0]] == ["two"]
        assert [e.tool_name for e in service.search(actor_user_id=user.id, server_slug="a")[0]] == ["one"]
        assert len(service.search(actor_user_id=user.id, limit=1)[0]) == 1
