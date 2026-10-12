"""Parameter sets of long processing, and parameters per hardware

Revision ID: e8a3c6d2f1b4
Revises: d5f2b8c1e4a7
Create Date: 2026-10-08 17:00:00.000000

"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


# revision identifiers, used by Alembic.
revision = 'e8a3c6d2f1b4'
down_revision = 'd5f2b8c1e4a7'
branch_labels = None
depends_on = None

JSON = sa.JSON().with_variant(postgresql.JSONB(astext_type=sa.Text()), 'postgresql')


def upgrade():
    op.create_table('parameter_set',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('name', sa.String(length=64), nullable=False),
    sa.Column('description', sa.Text(), nullable=True),
    sa.Column('channels', JSON, nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), nullable=False),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('name')
    )
    with op.batch_alter_table('hardware', schema=None) as batch_op:
        batch_op.add_column(sa.Column('parameters', JSON, nullable=True))


def downgrade():
    with op.batch_alter_table('hardware', schema=None) as batch_op:
        batch_op.drop_column('parameters')
    op.drop_table('parameter_set')
