"""Hardware blueprints: a list per hardware

Each hardware lists one or two blueprints of different kinds (process, long,
backup) instead of one. The current blueprint of each hardware becomes the
first of its list.

Revision ID: d5f2b8c1e4a7
Revises: c3e8a1f5d7b9
Create Date: 2026-10-08 15:00:00.000000

"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = 'd5f2b8c1e4a7'
down_revision = 'c3e8a1f5d7b9'
branch_labels = None
depends_on = None

# Same names as 3c41f0a7b2d9
NAMING = {'fk': 'fk_%(table_name)s_%(column_0_name)s_%(referred_table_name)s'}


def foreign_key_name():
    if op.get_bind().dialect.name == 'postgresql':
        return 'hardware_blueprint_id_fkey'
    return 'fk_hardware_blueprint_id_blueprint'


def upgrade():
    op.create_table('hardware_blueprint',
    sa.Column('hardware_id', sa.Integer(), nullable=False),
    sa.Column('blueprint_id', sa.Integer(), nullable=False),
    sa.Column('position', sa.Integer(), nullable=False),
    sa.ForeignKeyConstraint(['hardware_id'], ['hardware.id'], ondelete='CASCADE'),
    sa.ForeignKeyConstraint(['blueprint_id'], ['blueprint.id'], ondelete='RESTRICT'),
    sa.PrimaryKeyConstraint('hardware_id', 'blueprint_id')
    )
    op.execute('INSERT INTO hardware_blueprint (hardware_id, blueprint_id, position) '
               'SELECT id, blueprint_id, 0 FROM hardware')
    with op.batch_alter_table('hardware', naming_convention=NAMING) as batch_op:
        batch_op.drop_constraint(foreign_key_name(), type_='foreignkey')
        batch_op.drop_column('blueprint_id')


def downgrade():
    with op.batch_alter_table('hardware', naming_convention=NAMING) as batch_op:
        batch_op.add_column(sa.Column('blueprint_id', sa.Integer(), nullable=True))
    op.execute('UPDATE hardware SET blueprint_id = (SELECT blueprint_id FROM hardware_blueprint '
               'WHERE hardware_blueprint.hardware_id = hardware.id ORDER BY position LIMIT 1)')
    op.execute('DELETE FROM hardware_version WHERE hardware_id IN (SELECT id FROM hardware WHERE blueprint_id IS NULL)')
    op.execute('DELETE FROM hardware WHERE blueprint_id IS NULL')
    with op.batch_alter_table('hardware', naming_convention=NAMING) as batch_op:
        batch_op.alter_column('blueprint_id', existing_type=sa.Integer(), nullable=False)
        batch_op.create_foreign_key(foreign_key_name(), 'blueprint', ['blueprint_id'], ['id'], ondelete='RESTRICT')
    op.drop_table('hardware_blueprint')
