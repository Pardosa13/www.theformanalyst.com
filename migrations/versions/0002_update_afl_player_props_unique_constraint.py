"""Update afl_player_props UNIQUE constraint to include line column

Revision ID: 0002
Revises: 0001
Create Date: 2026-04-21 00:00:00.000000

"""
from alembic import op


# revision identifiers, used by Alembic.
revision = '0002'
down_revision = '0001'
branch_labels = None
depends_on = None


def upgrade() -> None:
    """Replace the 5-column unique constraint with a 6-column one that includes line."""
    op.drop_constraint(
        'afl_player_props_event_id_bookmaker_market_player_name_line_t_key',
        'afl_player_props',
        type_='unique',
    )
    op.create_unique_constraint(
        'afl_player_props_event_id_bookmaker_market_player_name_line_type_line_key',
        'afl_player_props',
        ['event_id', 'bookmaker', 'market', 'player_name', 'line_type', 'line'],
    )


def downgrade() -> None:
    """Restore the original 5-column unique constraint."""
    op.drop_constraint(
        'afl_player_props_event_id_bookmaker_market_player_name_line_type_line_key',
        'afl_player_props',
        type_='unique',
    )
    op.create_unique_constraint(
        'afl_player_props_event_id_bookmaker_market_player_name_line_t_key',
        'afl_player_props',
        ['event_id', 'bookmaker', 'market', 'player_name', 'line_type'],
    )
