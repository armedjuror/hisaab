"""add currency to users and bot_conversation_states

Revision ID: add_currency_to_users
Revises: 48d229e32e9f
Create Date: 2026-06-01

"""
from alembic import op
import sqlalchemy as sa

revision = 'add_currency_to_users'
down_revision = '48d229e32e9f'
branch_labels = None
depends_on = None


def upgrade():
    op.add_column('users',
        sa.Column('currency', sa.String(length=10), nullable=False, server_default='INR')
    )
    op.add_column('bot_conversation_states',
        sa.Column('temp_currency', sa.String(length=10), nullable=True)
    )


def downgrade():
    op.drop_column('users', 'currency')
    op.drop_column('bot_conversation_states', 'temp_currency')