"""add email verification fields to bot_conversation_states

Revision ID: add_email_verification_fields
Revises: add_currency_to_users
Create Date: 2026-06-01

"""
from alembic import op
import sqlalchemy as sa

revision = 'add_email_verification_fields'
down_revision = 'add_currency_to_users'
branch_labels = None
depends_on = None


def upgrade():
    op.add_column('bot_conversation_states',
        sa.Column('temp_otp', sa.String(length=10), nullable=True)
    )
    op.add_column('bot_conversation_states',
        sa.Column('temp_otp_expires_at', sa.DateTime(), nullable=True)
    )


def downgrade():
    op.drop_column('bot_conversation_states', 'temp_otp')
    op.drop_column('bot_conversation_states', 'temp_otp_expires_at')
