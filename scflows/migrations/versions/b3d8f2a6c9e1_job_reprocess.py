"""Reprocessing of process jobs

Revision ID: b3d8f2a6c9e1
Revises: a9c2e5f7b1d4
Create Date: 2026-11-23 15:00:00.000000

"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = 'b3d8f2a6c9e1'
down_revision = 'a9c2e5f7b1d4'
branch_labels = None
depends_on = None


def upgrade():
    with op.batch_alter_table('job', schema=None) as batch_op:
        batch_op.add_column(sa.Column('reprocess', sa.String(length=16), nullable=True))
        batch_op.add_column(sa.Column('reprocess_from', sa.DateTime(timezone=True), nullable=True))


def downgrade():
    with op.batch_alter_table('job', schema=None) as batch_op:
        batch_op.drop_column('reprocess_from')
        batch_op.drop_column('reprocess')
