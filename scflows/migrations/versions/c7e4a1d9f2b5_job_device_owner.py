"""Owner of the device of each job, from the sync

Revision ID: c7e4a1d9f2b5
Revises: b3d8f2a6c9e1
Create Date: 2026-11-23 16:00:00.000000

"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = 'c7e4a1d9f2b5'
down_revision = 'b3d8f2a6c9e1'
branch_labels = None
depends_on = None


def upgrade():
    with op.batch_alter_table('job', schema=None) as batch_op:
        batch_op.add_column(sa.Column('device_owner', sa.String(length=255), nullable=True))


def downgrade():
    with op.batch_alter_table('job', schema=None) as batch_op:
        batch_op.drop_column('device_owner')
