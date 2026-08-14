"""Add predictions.parsed_components cache column

Component Analysis (and other analytics that read Prediction.notes) re-runs
~250 regex patterns against the notes text on every request, for every horse
row in the selected race window. At 8,000+ races this makes the page take
minutes and time out. This column caches parse_notes_components(notes) at
write time so those routes can read pre-parsed JSON instead. A
before_insert/before_update listener (app.py) keeps it in sync going
forward; scripts/backfill_prediction_components.py populates it for
existing rows.

Revision ID: 0004
Revises: 0003
Create Date: 2026-08-14 00:00:00.000000

"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = '0004'
down_revision = '0003'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column('predictions', sa.Column('parsed_components', sa.JSON(), nullable=True))


def downgrade() -> None:
    op.drop_column('predictions', 'parsed_components')
