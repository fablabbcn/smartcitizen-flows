"""Name of the device of each job, from the sync

Revision ID: a4d7e2b9c6f1
Revises: f1a9c4e7b2d3
Create Date: 2026-10-12 21:00:00.000000

"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = 'a4d7e2b9c6f1'
down_revision = 'f1a9c4e7b2d3'
branch_labels = None
depends_on = None


def upgrade():
    with op.batch_alter_table('job', schema=None) as batch_op:
        batch_op.add_column(sa.Column('device_name', sa.String(length=255), nullable=True))


def downgrade():
    with op.batch_alter_table('job', schema=None) as batch_op:
        batch_op.drop_column('device_name')
