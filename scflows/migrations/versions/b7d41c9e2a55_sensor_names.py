"""Sensor names

Revision ID: b7d41c9e2a55
Revises: e46ad59b205f
Create Date: 2026-10-08 11:20:00.000000

"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = 'b7d41c9e2a55'
down_revision = 'e46ad59b205f'
branch_labels = None
depends_on = None


def upgrade():
    op.create_table('sensor_name',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('name', sa.String(length=64), nullable=False),
    sa.Column('sensor_id', sa.Integer(), nullable=False),
    sa.Column('description', sa.String(length=255), nullable=False),
    sa.Column('unit', sa.String(length=32), nullable=False),
    sa.Column('position', sa.Integer(), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), nullable=False),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('name')
    )
    with op.batch_alter_table('sensor_name', schema=None) as batch_op:
        batch_op.create_index(batch_op.f('ix_sensor_name_sensor_id'), ['sensor_id'], unique=False)
        batch_op.create_index(batch_op.f('ix_sensor_name_position'), ['position'], unique=False)


def downgrade():
    with op.batch_alter_table('sensor_name', schema=None) as batch_op:
        batch_op.drop_index(batch_op.f('ix_sensor_name_position'))
        batch_op.drop_index(batch_op.f('ix_sensor_name_sensor_id'))
    op.drop_table('sensor_name')
