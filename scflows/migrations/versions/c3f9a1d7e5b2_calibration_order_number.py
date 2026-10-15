"""Order number of each calibration (the Alphasense order it came with)

Revision ID: c3f9a1d7e5b2
Revises: b8e1f4c2a9d6
Create Date: 2026-10-15 12:00:00.000000

"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = 'c3f9a1d7e5b2'
down_revision = 'b8e1f4c2a9d6'
branch_labels = None
depends_on = None


def upgrade():
    with op.batch_alter_table('calibration', schema=None) as batch_op:
        batch_op.add_column(sa.Column('order_number', sa.String(length=64), nullable=True))


def downgrade():
    with op.batch_alter_table('calibration', schema=None) as batch_op:
        batch_op.drop_column('order_number')
