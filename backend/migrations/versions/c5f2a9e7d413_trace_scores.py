"""traces: index usage_events.trace_id and add trace_scores

Revision ID: c5f2a9e7d413
Revises: b3e8d51f0c27
Create Date: 2026-11-20 12:00:00

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'c5f2a9e7d413'
down_revision: Union[str, Sequence[str], None] = 'b3e8d51f0c27'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_index('ix_usage_events_trace_id', 'usage_events', ['trace_id'])
    op.create_table(
        'trace_scores',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('external_id', sa.String(length=160), nullable=True),
        sa.Column('trace_id', sa.String(length=64), nullable=False),
        sa.Column('name', sa.String(length=100), nullable=False),
        sa.Column('data_type', sa.String(length=20), nullable=False),
        sa.Column('value', sa.Float(), nullable=True),
        sa.Column('label', sa.String(length=100), nullable=True),
        sa.Column('source', sa.String(length=20), nullable=False),
        sa.Column('timestamp', sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('external_id', name='uq_trace_scores_external_id'),
    )
    op.create_index('ix_trace_scores_trace_id', 'trace_scores', ['trace_id'])


def downgrade() -> None:
    op.drop_index('ix_trace_scores_trace_id', table_name='trace_scores')
    op.drop_table('trace_scores')
    op.drop_index('ix_usage_events_trace_id', table_name='usage_events')
