"""Process parameters per hardware

Revision ID: a9c2e5f7b1d4
Revises: d5b2e8f1a3c7
Create Date: 2026-11-20 10:00:00.000000

"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


# revision identifiers, used by Alembic.
revision = 'a9c2e5f7b1d4'
down_revision = 'd5b2e8f1a3c7'
branch_labels = None
depends_on = None

JSON = sa.JSON().with_variant(postgresql.JSONB(astext_type=sa.Text()), 'postgresql')


def upgrade():
    with op.batch_alter_table('hardware', schema=None) as batch_op:
        batch_op.add_column(sa.Column('process_parameters', JSON, nullable=True))


def downgrade():
    with op.batch_alter_table('hardware', schema=None) as batch_op:
        batch_op.drop_column('process_parameters')
