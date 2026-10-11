"""Revisions

Revision ID: ed77f1ec5762
Revises: de353d2ec193
Create Date: 2026-10-06 15:49:13.170815

"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision = 'ed77f1ec5762'
down_revision = 'de353d2ec193'
branch_labels = None
depends_on = None


def upgrade():
    op.create_table('revision',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('kind', sa.String(length=32), nullable=False),
    sa.Column('key', sa.String(length=64), nullable=False),
    sa.Column('action', sa.String(length=16), nullable=False),
    sa.Column('before', sa.JSON().with_variant(postgresql.JSONB(astext_type=sa.Text()), 'postgresql'), nullable=True),
    sa.Column('after', sa.JSON().with_variant(postgresql.JSONB(astext_type=sa.Text()), 'postgresql'), nullable=True),
    sa.Column('user_id', sa.Integer(), nullable=True),
    sa.Column('username', sa.String(length=255), nullable=True),
    sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
    sa.PrimaryKeyConstraint('id')
    )
    with op.batch_alter_table('revision', schema=None) as batch_op:
        batch_op.create_index('ix_revision_kind_key', ['kind', 'key'], unique=False)



def downgrade():
    with op.batch_alter_table('revision', schema=None) as batch_op:
        batch_op.drop_index('ix_revision_kind_key')

    op.drop_table('revision')
