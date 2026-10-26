"""users, key ownership, upstream credentials, audit detail

Revision ID: 4ebb74effeba
Revises: 0cc1c8156efd
Create Date: 2026-10-22 23:34:03.362309

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '4ebb74effeba'
down_revision: Union[str, Sequence[str], None] = '0cc1c8156efd'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.create_table('users',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('email', sa.String(length=320), nullable=False),
    sa.Column('password_hash', sa.String(length=300), nullable=False),
    sa.Column('role', sa.String(length=16), nullable=False),
    sa.Column('is_active', sa.Boolean(), server_default='true', nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.CheckConstraint("role IN ('admin', 'member')", name='ck_users_role'),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('email')
    )
    op.add_column('gateway_api_keys', sa.Column('user_id', sa.Integer(), nullable=False))
    op.create_index(op.f('ix_gateway_api_keys_user_id'), 'gateway_api_keys', ['user_id'], unique=False)
    op.create_foreign_key('fk_gateway_api_keys_user_id_users', 'gateway_api_keys', 'users', ['user_id'], ['id'], ondelete='CASCADE')
    op.add_column('gateway_audit_events', sa.Column('actor_email', sa.String(length=320), nullable=True))
    op.add_column('gateway_audit_events', sa.Column('upstream_server_slug', sa.String(length=100), nullable=True))
    op.add_column('gateway_audit_events', sa.Column('tool_name', sa.String(length=200), nullable=True))
    op.add_column('gateway_audit_events', sa.Column('channel', sa.String(length=16), server_default='mcp', nullable=False))
    op.create_foreign_key('fk_gateway_audit_events_actor_user_id_users', 'gateway_audit_events', 'users', ['actor_user_id'], ['id'], ondelete='SET NULL')
    op.create_check_constraint('ck_gateway_audit_events_channel', 'gateway_audit_events', "channel IN ('mcp', 'chat', 'dashboard')")
    op.add_column('mcp_servers', sa.Column('auth_type', sa.String(length=16), server_default='none', nullable=False))
    op.add_column('mcp_servers', sa.Column('auth_header_name', sa.String(length=100), nullable=True))
    op.add_column('mcp_servers', sa.Column('credential_encrypted', sa.Text(), nullable=True))
    op.create_check_constraint('ck_mcp_servers_auth_type', 'mcp_servers', "auth_type IN ('none', 'bearer', 'header')")


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_constraint('ck_mcp_servers_auth_type', 'mcp_servers', type_='check')
    op.drop_column('mcp_servers', 'credential_encrypted')
    op.drop_column('mcp_servers', 'auth_header_name')
    op.drop_column('mcp_servers', 'auth_type')
    op.drop_constraint('ck_gateway_audit_events_channel', 'gateway_audit_events', type_='check')
    op.drop_constraint('fk_gateway_audit_events_actor_user_id_users', 'gateway_audit_events', type_='foreignkey')
    op.drop_column('gateway_audit_events', 'channel')
    op.drop_column('gateway_audit_events', 'tool_name')
    op.drop_column('gateway_audit_events', 'upstream_server_slug')
    op.drop_column('gateway_audit_events', 'actor_email')
    op.drop_constraint('fk_gateway_api_keys_user_id_users', 'gateway_api_keys', type_='foreignkey')
    op.drop_index(op.f('ix_gateway_api_keys_user_id'), table_name='gateway_api_keys')
    op.drop_column('gateway_api_keys', 'user_id')
    op.drop_table('users')
