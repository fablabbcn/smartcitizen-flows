"""Hardware without forwarding

Revision ID: d2a8f6c4e9b3
Revises: c7e4a1d9f2b5
Create Date: 2026-11-23 20:00:00.000000

"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = 'd2a8f6c4e9b3'
down_revision = 'c7e4a1d9f2b5'
branch_labels = None
depends_on = None


def upgrade():
    with op.batch_alter_table('hardware', schema=None) as batch_op:
        batch_op.drop_column('forwarding')


def downgrade():
    with op.batch_alter_table('hardware', schema=None) as batch_op:
        batch_op.add_column(sa.Column('forwarding', sa.String(length=64), nullable=True))
