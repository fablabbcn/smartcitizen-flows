"""Syncs of the jobs are runs too: runs without device

Revision ID: f1a9c4e7b2d3
Revises: e8a3c6d2f1b4
Create Date: 2026-10-11 16:00:00.000000

"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = 'f1a9c4e7b2d3'
down_revision = 'e8a3c6d2f1b4'
branch_labels = None
depends_on = None


def upgrade():
    with op.batch_alter_table('job_run', schema=None) as batch_op:
        batch_op.alter_column('device_id', existing_type=sa.Integer(), nullable=True)


def downgrade():
    op.execute("DELETE FROM job_run WHERE device_id IS NULL")
    with op.batch_alter_table('job_run', schema=None) as batch_op:
        batch_op.alter_column('device_id', existing_type=sa.Integer(), nullable=False)
