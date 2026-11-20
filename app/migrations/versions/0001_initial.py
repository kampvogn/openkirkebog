"""Initial local archive database."""

import sqlalchemy as sa
from alembic import op

revision = "0001"
down_revision = None


def upgrade():
    op.create_table('sources',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('archive', sa.String(length=200), nullable=True),
        sa.Column('parish', sa.String(length=160), nullable=False),
        sa.Column('district', sa.String(length=160), nullable=True),
        sa.Column('county', sa.String(length=160), nullable=True),
        sa.Column('reference', sa.String(length=300), nullable=True),
        sa.Column('start_year', sa.Integer(), nullable=True),
        sa.Column('end_year', sa.Integer(), nullable=True),
        sa.Column('category', sa.String(length=40), nullable=False),
        sa.Column('url', sa.Text(), nullable=True),
        sa.Column('identifiers', sa.JSON(), nullable=False),
        sa.Column('license', sa.Text(), nullable=True),
        sa.Column('imported_at', sa.DateTime(), nullable=False),
        sa.PrimaryKeyConstraint('id')
    )
    op.create_index(op.f('ix_sources_parish'), 'sources', ['parish'], unique=False)
    op.create_table('pages',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('source_id', sa.Integer(), nullable=False),
        sa.Column('filename', sa.Text(), nullable=False),
        sa.Column('sha256', sa.String(length=64), nullable=False),
        sa.Column('media_type', sa.String(length=60), nullable=False),
        sa.Column('width', sa.Integer(), nullable=False),
        sa.Column('height', sa.Integer(), nullable=False),
        sa.Column('imported_at', sa.DateTime(), nullable=False),
        sa.Column('active_transcription_id', sa.Integer(), nullable=True),
        sa.Column('active_extraction_id', sa.Integer(), nullable=True),
        sa.ForeignKeyConstraint(['active_extraction_id'], ['extractions.id'], name='fk_page_extraction', use_alter=True),
        sa.ForeignKeyConstraint(['active_transcription_id'], ['transcriptions.id'], name='fk_page_transcription', use_alter=True),
        sa.ForeignKeyConstraint(['source_id'], ['sources.id'], ),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('source_id', 'sha256')
    )
    op.create_index(op.f('ix_pages_source_id'), 'pages', ['source_id'], unique=False)
    op.create_table('transcriptions',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('page_id', sa.Integer(), nullable=False),
        sa.Column('parent_id', sa.Integer(), nullable=True),
        sa.Column('origin', sa.String(length=12), nullable=False),
        sa.Column('data', sa.JSON(), nullable=False),
        sa.Column('created_at', sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(['page_id'], ['pages.id'], ),
        sa.ForeignKeyConstraint(['parent_id'], ['transcriptions.id'], ),
        sa.PrimaryKeyConstraint('id')
    )
    op.create_index(op.f('ix_transcriptions_page_id'), 'transcriptions', ['page_id'], unique=False)
    op.create_table('extractions',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('page_id', sa.Integer(), nullable=False),
        sa.Column('transcription_id', sa.Integer(), nullable=False),
        sa.Column('created_at', sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(['page_id'], ['pages.id'], ),
        sa.ForeignKeyConstraint(['transcription_id'], ['transcriptions.id'], ),
        sa.PrimaryKeyConstraint('id')
    )
    op.create_index(op.f('ix_extractions_page_id'), 'extractions', ['page_id'], unique=False)
    op.create_table('jobs',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('page_id', sa.Integer(), nullable=False),
        sa.Column('stage', sa.String(length=12), nullable=False),
        sa.Column('state', sa.String(length=12), nullable=False),
        sa.Column('provider', sa.String(length=30), nullable=False),
        sa.Column('model', sa.String(length=200), nullable=False),
        sa.Column('transcription_id', sa.Integer(), nullable=True),
        sa.Column('attempts', sa.Integer(), nullable=False),
        sa.Column('error', sa.Text(), nullable=True),
        sa.Column('created_at', sa.DateTime(), nullable=False),
        sa.Column('started_at', sa.DateTime(), nullable=True),
        sa.Column('finished_at', sa.DateTime(), nullable=True),
        sa.ForeignKeyConstraint(['page_id'], ['pages.id'], ),
        sa.ForeignKeyConstraint(['transcription_id'], ['transcriptions.id'], ),
        sa.PrimaryKeyConstraint('id')
    )
    op.create_index(op.f('ix_jobs_page_id'), 'jobs', ['page_id'], unique=False)
    op.create_index(op.f('ix_jobs_state'), 'jobs', ['state'], unique=False)
    op.create_index('uq_jobs_active_page', 'jobs', ['page_id'], unique=True, sqlite_where=sa.text("state IN ('pending', 'processing')"), postgresql_where=sa.text("state IN ('pending', 'processing')"))
    op.create_table('entries',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('extraction_id', sa.Integer(), nullable=False),
        sa.Column('data', sa.JSON(), nullable=False),
        sa.Column('raw_text', sa.Text(), nullable=False),
        sa.Column('uncertain_readings', sa.JSON(), nullable=False),
        sa.Column('bbox', sa.JSON(), nullable=True),
        sa.Column('status', sa.String(length=12), nullable=False),
        sa.Column('excluded', sa.Boolean(), nullable=False),
        sa.Column('revision', sa.Integer(), nullable=False),
        sa.ForeignKeyConstraint(['extraction_id'], ['extractions.id'], ),
        sa.PrimaryKeyConstraint('id')
    )
    op.create_index(op.f('ix_entries_extraction_id'), 'entries', ['extraction_id'], unique=False)
    op.create_table('processing_runs',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('job_id', sa.Integer(), nullable=False),
        sa.Column('page_id', sa.Integer(), nullable=False),
        sa.Column('source_id', sa.Integer(), nullable=False),
        sa.Column('transcription_id', sa.Integer(), nullable=True),
        sa.Column('extraction_id', sa.Integer(), nullable=True),
        sa.Column('provider', sa.String(length=30), nullable=False),
        sa.Column('model', sa.String(length=200), nullable=False),
        sa.Column('resolved_model', sa.String(length=200), nullable=True),
        sa.Column('stage', sa.String(length=15), nullable=False),
        sa.Column('prompt_version', sa.String(length=40), nullable=False),
        sa.Column('pipeline_version', sa.String(length=40), nullable=False),
        sa.Column('attempt', sa.Integer(), nullable=False),
        sa.Column('status', sa.String(length=12), nullable=False),
        sa.Column('started_at', sa.DateTime(), nullable=False),
        sa.Column('finished_at', sa.DateTime(), nullable=True),
        sa.Column('runtime_ms', sa.Integer(), nullable=True),
        sa.Column('usage', sa.JSON(), nullable=True),
        sa.Column('cost', sa.String(length=60), nullable=True),
        sa.Column('currency', sa.String(length=10), nullable=True),
        sa.Column('cost_origin', sa.String(length=12), nullable=True),
        sa.Column('pricing', sa.JSON(), nullable=True),
        sa.Column('entries_found', sa.Integer(), nullable=True),
        sa.Column('entries_reviewed', sa.Integer(), nullable=True),
        sa.Column('entries_verified', sa.Integer(), nullable=True),
        sa.Column('error', sa.Text(), nullable=True),
        sa.ForeignKeyConstraint(['extraction_id'], ['extractions.id'], ),
        sa.ForeignKeyConstraint(['job_id'], ['jobs.id'], ),
        sa.ForeignKeyConstraint(['page_id'], ['pages.id'], ),
        sa.ForeignKeyConstraint(['source_id'], ['sources.id'], ),
        sa.ForeignKeyConstraint(['transcription_id'], ['transcriptions.id'], ),
        sa.PrimaryKeyConstraint('id')
    )
    op.create_index(op.f('ix_processing_runs_job_id'), 'processing_runs', ['job_id'], unique=False)
    op.create_index(op.f('ix_processing_runs_page_id'), 'processing_runs', ['page_id'], unique=False)
    op.create_index(op.f('ix_processing_runs_started_at'), 'processing_runs', ['started_at'], unique=False)
    op.create_table('corrections',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('page_id', sa.Integer(), nullable=False),
        sa.Column('entry_id', sa.Integer(), nullable=True),
        sa.Column('transcription_id', sa.Integer(), nullable=True),
        sa.Column('before', sa.JSON(), nullable=False),
        sa.Column('after', sa.JSON(), nullable=False),
        sa.Column('created_at', sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(['entry_id'], ['entries.id'], ),
        sa.ForeignKeyConstraint(['page_id'], ['pages.id'], ),
        sa.ForeignKeyConstraint(['transcription_id'], ['transcriptions.id'], ),
        sa.PrimaryKeyConstraint('id')
    )
    op.create_index(op.f('ix_corrections_page_id'), 'corrections', ['page_id'], unique=False)
    op.execute("CREATE VIRTUAL TABLE entry_search USING fts5(content, tokenize='trigram')")


def downgrade():
    op.execute("DROP TABLE entry_search")
    op.drop_index(op.f('ix_corrections_page_id'), table_name='corrections')
    op.drop_table('corrections')
    op.drop_index(op.f('ix_processing_runs_started_at'), table_name='processing_runs')
    op.drop_index(op.f('ix_processing_runs_page_id'), table_name='processing_runs')
    op.drop_index(op.f('ix_processing_runs_job_id'), table_name='processing_runs')
    op.drop_table('processing_runs')
    op.drop_index(op.f('ix_entries_extraction_id'), table_name='entries')
    op.drop_table('entries')
    op.drop_index('uq_jobs_active_page', table_name='jobs', sqlite_where=sa.text("state IN ('pending', 'processing')"), postgresql_where=sa.text("state IN ('pending', 'processing')"))
    op.drop_index(op.f('ix_jobs_state'), table_name='jobs')
    op.drop_index(op.f('ix_jobs_page_id'), table_name='jobs')
    op.drop_table('jobs')
    op.drop_index(op.f('ix_extractions_page_id'), table_name='extractions')
    op.drop_table('extractions')
    op.drop_index(op.f('ix_transcriptions_page_id'), table_name='transcriptions')
    op.drop_table('transcriptions')
    op.drop_index(op.f('ix_pages_source_id'), table_name='pages')
    op.drop_table('pages')
    op.drop_index(op.f('ix_sources_parish'), table_name='sources')
    op.drop_table('sources')
