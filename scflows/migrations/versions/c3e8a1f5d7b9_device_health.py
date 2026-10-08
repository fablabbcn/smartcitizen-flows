"""Device health

Revision ID: c3e8a1f5d7b9
Revises: b7d41c9e2a55
Create Date: 2026-10-08 13:00:00.000000

"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


# revision identifiers, used by Alembic.
revision = 'c3e8a1f5d7b9'
down_revision = 'b7d41c9e2a55'
branch_labels = None
depends_on = None


def upgrade():
    op.create_table('device_health',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('device_id', sa.Integer(), nullable=False),
    sa.Column('device_name', sa.String(length=255), nullable=True),
    sa.Column('blueprint', sa.String(length=64), nullable=True),
    sa.Column('run_id', sa.Integer(), nullable=True),
    sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
    sa.Column('start', sa.DateTime(timezone=True), nullable=True),
    sa.Column('end', sa.DateTime(timezone=True), nullable=True),
    sa.Column('rows', sa.Integer(), nullable=False),
    sa.Column('status', sa.String(length=16), nullable=False),
    sa.Column('checks', sa.JSON().with_variant(postgresql.JSONB(astext_type=sa.Text()), 'postgresql'), nullable=False),
    sa.ForeignKeyConstraint(['run_id'], ['job_run.id'], ondelete='SET NULL'),
    sa.PrimaryKeyConstraint('id')
    )
    with op.batch_alter_table('device_health', schema=None) as batch_op:
        batch_op.create_index(batch_op.f('ix_device_health_device_id'), ['device_id'], unique=False)
        batch_op.create_index(batch_op.f('ix_device_health_run_id'), ['run_id'], unique=False)
        batch_op.create_index(batch_op.f('ix_device_health_created_at'), ['created_at'], unique=False)


def downgrade():
    with op.batch_alter_table('device_health', schema=None) as batch_op:
        batch_op.drop_index(batch_op.f('ix_device_health_created_at'))
        batch_op.drop_index(batch_op.f('ix_device_health_run_id'))
        batch_op.drop_index(batch_op.f('ix_device_health_device_id'))
    op.drop_table('device_health')
