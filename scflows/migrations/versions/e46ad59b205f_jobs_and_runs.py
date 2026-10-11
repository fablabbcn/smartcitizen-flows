"""Jobs and runs

Revision ID: e46ad59b205f
Revises: e12b0d85b328
Create Date: 2026-10-06 19:42:44.731216

"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision = 'e46ad59b205f'
down_revision = 'e12b0d85b328'
branch_labels = None
depends_on = None


def upgrade():
    op.create_table('job',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('device_id', sa.Integer(), nullable=False),
    sa.Column('task', sa.String(length=16), nullable=False),
    sa.Column('source', sa.String(length=16), nullable=False),
    sa.Column('enabled', sa.Boolean(), nullable=False),
    sa.Column('paused', sa.Boolean(), nullable=False),
    sa.Column('interval_hours', sa.Integer(), nullable=False),
    sa.Column('next_run_at', sa.DateTime(timezone=True), nullable=False),
    sa.Column('last_queued_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), nullable=False),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('device_id', 'task', name='uq_job_device_task')
    )
    with op.batch_alter_table('job', schema=None) as batch_op:
        batch_op.create_index(batch_op.f('ix_job_next_run_at'), ['next_run_at'], unique=False)

    op.create_table('job_run',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('job_id', sa.Integer(), nullable=True),
    sa.Column('device_id', sa.Integer(), nullable=False),
    sa.Column('task', sa.String(length=16), nullable=False),
    sa.Column('dry_run', sa.Boolean(), nullable=False),
    sa.Column('state', sa.String(length=16), nullable=False),
    sa.Column('message', sa.Text(), nullable=True),
    sa.Column('log', sa.JSON().with_variant(postgresql.JSONB(astext_type=sa.Text()), 'postgresql'), nullable=True),
    sa.Column('username', sa.String(length=255), nullable=True),
    sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
    sa.Column('started_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('finished_at', sa.DateTime(timezone=True), nullable=True),
    sa.ForeignKeyConstraint(['job_id'], ['job.id'], ondelete='SET NULL'),
    sa.PrimaryKeyConstraint('id')
    )
    with op.batch_alter_table('job_run', schema=None) as batch_op:
        batch_op.create_index(batch_op.f('ix_job_run_device_id'), ['device_id'], unique=False)
        batch_op.create_index(batch_op.f('ix_job_run_job_id'), ['job_id'], unique=False)



def downgrade():
    with op.batch_alter_table('job_run', schema=None) as batch_op:
        batch_op.drop_index(batch_op.f('ix_job_run_job_id'))
        batch_op.drop_index(batch_op.f('ix_job_run_device_id'))

    op.drop_table('job_run')
    with op.batch_alter_table('job', schema=None) as batch_op:
        batch_op.drop_index(batch_op.f('ix_job_next_run_at'))

    op.drop_table('job')
