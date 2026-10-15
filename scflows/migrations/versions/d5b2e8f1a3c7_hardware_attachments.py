"""Files attached to hardware (certificates, calibration reports)

Revision ID: d5b2e8f1a3c7
Revises: c3f9a1d7e5b2
Create Date: 2026-10-15 14:00:00.000000

"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = 'd5b2e8f1a3c7'
down_revision = 'c3f9a1d7e5b2'
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        'attachment',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('hardware', sa.String(length=64), nullable=False),
        sa.Column('filename', sa.String(length=255), nullable=False),
        sa.Column('content_type', sa.String(length=128), nullable=False),
        sa.Column('size', sa.Integer(), nullable=False),
        sa.Column('location', sa.String(length=512), nullable=False),
        sa.Column('description', sa.String(length=255), nullable=False),
        sa.Column('user_id', sa.Integer(), nullable=True),
        sa.Column('username', sa.String(length=255), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint('id'),
    )
    with op.batch_alter_table('attachment', schema=None) as batch_op:
        batch_op.create_index(batch_op.f('ix_attachment_hardware'), ['hardware'], unique=False)


def downgrade():
    with op.batch_alter_table('attachment', schema=None) as batch_op:
        batch_op.drop_index(batch_op.f('ix_attachment_hardware'))
    op.drop_table('attachment')
