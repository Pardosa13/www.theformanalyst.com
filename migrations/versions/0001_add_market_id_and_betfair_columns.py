"""Add market_id to races and Betfair result columns to horses

Revision ID: 0001
Revises: None
Create Date: 2025-01-01 00:00:00.000000

"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = '0001'
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    """Add market_id column to races table and Betfair result columns to horses table."""
    # Add market_id to races table
    op.add_column('races', sa.Column('market_id', sa.String(length=255), nullable=True))

    # Add Betfair result columns to horses table
    op.add_column('horses', sa.Column('betfair_selection_id', sa.Integer(), nullable=True))
    op.add_column('horses', sa.Column('final_position', sa.Integer(), nullable=True))
    op.add_column('horses', sa.Column('final_odds', sa.Float(), nullable=True))
    op.add_column('horses', sa.Column('result_settled_at', sa.DateTime(), nullable=True))
    op.add_column('horses', sa.Column('result_source', sa.String(length=50), nullable=True))


def downgrade() -> None:
    """Remove market_id column from races table and Betfair result columns from horses table."""
    # Remove Betfair result columns from horses table
    op.drop_column('horses', 'result_source')
    op.drop_column('horses', 'result_settled_at')
    op.drop_column('horses', 'final_odds')
    op.drop_column('horses', 'final_position')
    op.drop_column('horses', 'betfair_selection_id')

    # Remove market_id from races table
    op.drop_column('races', 'market_id')
