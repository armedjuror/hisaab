"""add_group_splitwise_feature

Revision ID: 1fb57c9ac48f
Revises: add_email_verification_fields
Create Date: 2026-06-01 23:03:01.851798

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '1fb57c9ac48f'
down_revision: Union[str, Sequence[str], None] = 'add_email_verification_fields'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # bot_conversation_states — scratch columns for multi-step auth flows
    op.add_column('bot_conversation_states', sa.Column('temp_currency', sa.String(10), nullable=True))
    op.add_column('bot_conversation_states', sa.Column('temp_otp', sa.String(10), nullable=True))
    op.add_column('bot_conversation_states', sa.Column('temp_otp_expires_at', sa.DateTime(), nullable=True))

    # accounts — flag for non-deletable system accounts
    op.add_column('accounts', sa.Column('is_protected', sa.Boolean(), nullable=False, server_default='false'))

    # users — guest flag + currency preference
    op.add_column('users', sa.Column('is_guest', sa.Boolean(), nullable=False, server_default='false'))
    op.add_column('users', sa.Column('currency', sa.String(10), nullable=False, server_default='INR'))
    op.alter_column('users', 'email', nullable=True)

    # group_chats
    op.create_table(
        'group_chats',
        sa.Column('id',         sa.Integer(),     primary_key=True),
        sa.Column('platform',   sa.String(50),    nullable=False),
        sa.Column('chat_id',    sa.String(200),   nullable=False),
        sa.Column('name',       sa.String(200),   nullable=True),
        sa.Column('is_closed',  sa.Boolean(),     nullable=False, server_default='false'),
        sa.Column('created_at', sa.DateTime(),    server_default=sa.text('now()')),
        sa.UniqueConstraint('platform', 'chat_id', name='uq_group_chat'),
    )

    # group_members
    op.create_table(
        'group_members',
        sa.Column('id',               sa.Integer(),    primary_key=True),
        sa.Column('group_chat_id',    sa.Integer(),    sa.ForeignKey('group_chats.id'), nullable=False),
        sa.Column('user_id',          sa.Integer(),    sa.ForeignKey('users.id'),       nullable=True),
        sa.Column('platform_user_id', sa.String(200),  nullable=False),
        sa.Column('display_name',     sa.String(200),  nullable=False),
        sa.Column('username',         sa.String(200),  nullable=True),
        sa.Column('joined_at',        sa.DateTime(),   server_default=sa.text('now()')),
        sa.UniqueConstraint('group_chat_id', 'platform_user_id', name='uq_group_member'),
    )

    # group_expenses
    op.create_table(
        'group_expenses',
        sa.Column('id',                sa.Integer(),    primary_key=True),
        sa.Column('group_chat_id',     sa.Integer(),    sa.ForeignKey('group_chats.id'),   nullable=False),
        sa.Column('paid_by_member_id', sa.Integer(),    sa.ForeignKey('group_members.id'), nullable=False),
        sa.Column('amount',            sa.Float(),      nullable=False),
        sa.Column('description',       sa.String(255),  nullable=False),
        sa.Column('date',              sa.Date(),       nullable=False),
        sa.Column('created_at',        sa.DateTime(),   server_default=sa.text('now()')),
    )

    # group_expense_shares
    op.create_table(
        'group_expense_shares',
        sa.Column('id',             sa.Integer(), primary_key=True),
        sa.Column('expense_id',     sa.Integer(), sa.ForeignKey('group_expenses.id'),  nullable=False),
        sa.Column('member_id',      sa.Integer(), sa.ForeignKey('group_members.id'),   nullable=False),
        sa.Column('share_ratio',    sa.Float(),   nullable=False, server_default='1.0'),
        sa.Column('share_amount',   sa.Float(),   nullable=False),
        sa.Column('is_settled',     sa.Boolean(), nullable=False, server_default='false'),
        sa.Column('transaction_id', sa.Integer(), sa.ForeignKey('transactions.id'),    nullable=True),
    )


def downgrade() -> None:
    op.drop_table('group_expense_shares')
    op.drop_table('group_expenses')
    op.drop_table('group_members')
    op.drop_table('group_chats')
    op.drop_column('accounts', 'is_protected')
    op.drop_column('users', 'currency')
    op.drop_column('users', 'is_guest')
    op.alter_column('users', 'email', nullable=False)
    op.drop_column('bot_conversation_states', 'temp_otp_expires_at')
    op.drop_column('bot_conversation_states', 'temp_otp')
    op.drop_column('bot_conversation_states', 'temp_currency')
