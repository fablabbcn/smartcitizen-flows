"""Hardware requires a blueprint in flows

Hardware without a blueprint in flows is deleted, the blueprint_url column is
dropped and blueprints in use cannot be deleted. The downgrade restores the
schema only, not the deleted hardware nor the blueprint_url values: back the
database up first.

Revision ID: 3c41f0a7b2d9
Revises: ed77f1ec5762
Create Date: 2026-10-06 17:00:00

"""
from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision = '3c41f0a7b2d9'
down_revision = 'ed77f1ec5762'
branch_labels = None
depends_on = None

# Names the unnamed foreign key of the initial schema on SQLite
NAMING = {'fk': 'fk_%(table_name)s_%(column_0_name)s_%(referred_table_name)s'}


def foreign_key_name():
    if op.get_bind().dialect.name == 'postgresql':
        return 'hardware_blueprint_id_fkey'
    return 'fk_hardware_blueprint_id_blueprint'


def upgrade():
    op.execute('DELETE FROM hardware_version WHERE hardware_id IN '
               '(SELECT id FROM hardware WHERE blueprint_id IS NULL)')
    op.execute('DELETE FROM hardware WHERE blueprint_id IS NULL')

    name = foreign_key_name()
    with op.batch_alter_table('hardware', naming_convention=NAMING) as batch_op:
        batch_op.drop_constraint(name, type_='foreignkey')
        batch_op.create_foreign_key(name, 'blueprint', ['blueprint_id'], ['id'], ondelete='RESTRICT')
        batch_op.alter_column('blueprint_id', existing_type=sa.Integer(), nullable=False)
        batch_op.drop_column('blueprint_url')


def downgrade():
    name = foreign_key_name()
    with op.batch_alter_table('hardware', naming_convention=NAMING) as batch_op:
        batch_op.add_column(sa.Column('blueprint_url', sa.Text(), nullable=True))
        batch_op.alter_column('blueprint_id', existing_type=sa.Integer(), nullable=True)
        batch_op.drop_constraint(name, type_='foreignkey')
        batch_op.create_foreign_key(name, 'blueprint', ['blueprint_id'], ['id'], ondelete='SET NULL')
