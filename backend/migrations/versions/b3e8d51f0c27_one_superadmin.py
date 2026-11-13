"""exactly one super admin: unique partial index on users.role

Revision ID: b3e8d51f0c27
Revises: a7c19e4d2b6f
Create Date: 2026-11-14 12:00:00

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'b3e8d51f0c27'
down_revision: Union[str, Sequence[str], None] = 'a7c19e4d2b6f'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

users = sa.table('users', sa.column('id', sa.Integer), sa.column('role', sa.String),
                 sa.column('disabled_at', sa.DateTime))
WHERE = sa.text("role = 'superadmin'")


def upgrade() -> None:
    """Keep one super admin (the earliest active one, else the earliest) and make the rest admins."""
    bind = op.get_bind()
    supers = bind.execute(
        sa.select(users.c.id, users.c.disabled_at).where(users.c.role == 'superadmin').order_by(users.c.id)
    ).all()
    if len(supers) > 1:
        keep = next((r.id for r in supers if r.disabled_at is None), supers[0].id)
        bind.execute(sa.update(users).where(users.c.role == 'superadmin', users.c.id != keep).values(role='admin'))
    op.create_index('uq_users_one_superadmin', 'users', ['role'], unique=True,
                    sqlite_where=WHERE, postgresql_where=WHERE)


def downgrade() -> None:
    op.drop_index('uq_users_one_superadmin', table_name='users')
