"""What each run used (device, hardware, blueprint, storage), and the hardware of each job

Revision ID: b8e1f4c2a9d6
Revises: a4d7e2b9c6f1
Create Date: 2026-10-13 10:00:00.000000

"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


# revision identifiers, used by Alembic.
revision = 'b8e1f4c2a9d6'
down_revision = 'a4d7e2b9c6f1'
branch_labels = None
depends_on = None


def upgrade():
    with op.batch_alter_table('job_run', schema=None) as batch_op:
        batch_op.add_column(sa.Column('context', sa.JSON().with_variant(postgresql.JSONB(astext_type=sa.Text()), 'postgresql'), nullable=True))
    with op.batch_alter_table('job', schema=None) as batch_op:
        batch_op.add_column(sa.Column('hardware', sa.String(length=64), nullable=True))


def downgrade():
    with op.batch_alter_table('job', schema=None) as batch_op:
        batch_op.drop_column('hardware')
    with op.batch_alter_table('job_run', schema=None) as batch_op:
        batch_op.drop_column('context')
