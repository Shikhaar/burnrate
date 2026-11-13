"""super admin role: promote the earliest active all-teams admin

Revision ID: a7c19e4d2b6f
Revises: 255d2e24cd45
Create Date: 2026-11-14 10:00:00

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'a7c19e4d2b6f'
down_revision: Union[str, Sequence[str], None] = '255d2e24cd45'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

users = sa.table(
    'users',
    sa.column('id', sa.Integer),
    sa.column('role', sa.String),
    sa.column('teams', sa.JSON),
    sa.column('disabled_at', sa.DateTime),
)


def upgrade() -> None:
    """Existing installs keep someone who can manage admins: the first admin who sees every team."""
    bind = op.get_bind()
    # "All teams" was sometimes saved as JSON null rather than SQL NULL, which hid those admins from
    # the last-admin checks. Normalise it first.
    op.execute("UPDATE users SET teams = NULL WHERE CAST(teams AS TEXT) = 'null'")
    if bind.execute(sa.select(users.c.id).where(users.c.role == 'superadmin')).first():
        return
    first = bind.execute(
        sa.select(users.c.id)
        .where(users.c.role == 'admin', users.c.disabled_at.is_(None), users.c.teams.is_(None))
        .order_by(users.c.id)
        .limit(1)
    ).scalar()
    if first is not None:
        bind.execute(sa.update(users).where(users.c.id == first).values(role='superadmin'))


def downgrade() -> None:
    op.execute(sa.update(users).where(users.c.role == 'superadmin').values(role='admin'))
