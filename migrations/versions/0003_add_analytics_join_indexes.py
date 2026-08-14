"""Add indexes on FK/date columns used by the data analytics joins

The component-analysis route (and the rest of /data's analytics panels)
join meetings -> races -> horses -> predictions/results on every request.
None of these foreign key columns were indexed, so every join fell back to
a sequential scan once the tables grew, which is the main reason the
Component Analysis panel was timing out. Meeting.uploaded_at is indexed too
since it drives both the default ordering and the date_from/date_to filters.

Revision ID: 0003
Revises: 0002
Create Date: 2026-08-14 00:00:00.000000

"""
from alembic import op


# revision identifiers, used by Alembic.
revision = '0003'
down_revision = '0002'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_index('ix_meetings_uploaded_at', 'meetings', ['uploaded_at'], unique=False)
    op.create_index('ix_races_meeting_id', 'races', ['meeting_id'], unique=False)
    op.create_index('ix_horses_race_id', 'horses', ['race_id'], unique=False)
    op.create_index('ix_predictions_horse_id', 'predictions', ['horse_id'], unique=False)
    op.create_index('ix_results_horse_id', 'results', ['horse_id'], unique=False)


def downgrade() -> None:
    op.drop_index('ix_results_horse_id', table_name='results')
    op.drop_index('ix_predictions_horse_id', table_name='predictions')
    op.drop_index('ix_horses_race_id', table_name='horses')
    op.drop_index('ix_races_meeting_id', table_name='races')
    op.drop_index('ix_meetings_uploaded_at', table_name='meetings')
