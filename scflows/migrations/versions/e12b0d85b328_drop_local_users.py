"""Drop local users

Revision ID: e12b0d85b328
Revises: 3c41f0a7b2d9
Create Date: 2026-10-06 18:21:02.559907

"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = 'e12b0d85b328'
down_revision = '3c41f0a7b2d9'
branch_labels = None
depends_on = None


def upgrade():
    op.drop_table('user')


def downgrade():
    op.create_table('user',
    sa.Column('id', sa.INTEGER(), nullable=False),
    sa.Column('password', sa.VARCHAR(length=100), nullable=True),
    sa.Column('name', sa.VARCHAR(length=1000), nullable=True),
    sa.PrimaryKeyConstraint('id')
    )
