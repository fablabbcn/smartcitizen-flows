"""Initial schema: users and processing metadata

Revision ID: de353d2ec193
Revises: 
Create Date: 2026-10-06 13:45:42.170470

"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision = 'de353d2ec193'
down_revision = None
branch_labels = None
depends_on = None


def upgrade():
    op.create_table('blueprint',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('name', sa.String(length=64), nullable=False),
    sa.Column('body', sa.JSON().with_variant(postgresql.JSONB(astext_type=sa.Text()), 'postgresql'), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), nullable=False),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('name')
    )
    op.create_table('calibration',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('sensor_id', sa.String(length=64), nullable=False),
    sa.Column('kind', sa.String(length=32), nullable=False),
    sa.Column('data', sa.JSON().with_variant(postgresql.JSONB(astext_type=sa.Text()), 'postgresql'), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), nullable=False),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('sensor_id')
    )
    with op.batch_alter_table('calibration', schema=None) as batch_op:
        batch_op.create_index(batch_op.f('ix_calibration_kind'), ['kind'], unique=False)

    op.create_table('user',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('password', sa.String(length=100), nullable=True),
    sa.Column('name', sa.String(length=1000), nullable=True),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_table('hardware',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('name', sa.String(length=64), nullable=False),
    sa.Column('description', sa.Text(), nullable=True),
    sa.Column('comment', sa.Text(), nullable=True),
    sa.Column('forwarding', sa.String(length=64), nullable=True),
    sa.Column('blueprint_url', sa.Text(), nullable=True),
    sa.Column('blueprint_id', sa.Integer(), nullable=True),
    sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), nullable=False),
    sa.ForeignKeyConstraint(['blueprint_id'], ['blueprint.id'], ondelete='SET NULL'),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('name')
    )
    op.create_table('hardware_version',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('hardware_id', sa.Integer(), nullable=False),
    sa.Column('from_date', sa.Date(), nullable=True),
    sa.Column('to_date', sa.Date(), nullable=True),
    sa.Column('ids', sa.JSON().with_variant(postgresql.JSONB(astext_type=sa.Text()), 'postgresql'), nullable=False),
    sa.ForeignKeyConstraint(['hardware_id'], ['hardware.id'], ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id')
    )


def downgrade():
    op.drop_table('hardware_version')
    op.drop_table('hardware')
    op.drop_table('user')
    with op.batch_alter_table('calibration', schema=None) as batch_op:
        batch_op.drop_index(batch_op.f('ix_calibration_kind'))

    op.drop_table('calibration')
    op.drop_table('blueprint')
