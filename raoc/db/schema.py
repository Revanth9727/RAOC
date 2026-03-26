"""SQLAlchemy Core table definitions and engine factory for RAOC.

All three tables (jobs, actions, audit_log) are defined here.
Call create_tables() at startup to create them if they do not exist.
Missing columns are added automatically via _sync_columns().
"""

import logging

from sqlalchemy import (
    Column,
    ForeignKey,
    Integer,
    MetaData,
    Table,
    Text,
    create_engine,
    text,
)
from sqlalchemy.engine import Engine

logger = logging.getLogger(__name__)

from raoc import config

metadata = MetaData()

jobs = Table(
    'jobs',
    metadata,
    Column('job_id',          Text,    primary_key=True),
    Column('raw_request',     Text,    nullable=False),
    Column('task_type',       Text),
    Column('target_path',     Text),
    Column('status',          Text,    nullable=False),
    Column('created_at',      Text,    nullable=False),
    Column('updated_at',      Text,    nullable=False),
    Column('error_message',          Text),
    Column('approval_granted',       Integer),
    Column('clarification_question', Text),
    Column('output_path',            Text),
    Column('zip_source_path',        Text),
    Column('query_intent',           Text),
    Column('found_file_path',        Text),
    Column('implied_task_type',      Text),
    Column('action_instruction',     Text),
)

actions = Table(
    'actions',
    metadata,
    Column('action_id',          Text,    primary_key=True),
    Column('job_id',             Text,    ForeignKey('jobs.job_id'), nullable=False),
    Column('step_index',         Integer, nullable=False),
    Column('action_type',        Text,    nullable=False),
    Column('risk_level',         Text,    nullable=False),
    Column('target_path',        Text,    nullable=False),
    Column('intent',             Text,    nullable=False),
    Column('command',            Text),
    Column('change_summary',     Text),
    Column('status',             Text,    nullable=False, default='pending'),
    Column('execution_output',   Text),
    Column('verification_result', Text),
    Column('created_at',         Text,    nullable=False),
    Column('completed_at',       Text),
    Column('policy_decision',    Text),
    Column('policy_reason',      Text),
    Column('target_zone',        Text),
)

audit_log = Table(
    'audit_log',
    metadata,
    Column('id',         Integer, primary_key=True, autoincrement=True),
    Column('job_id',     Text,    nullable=False),
    Column('event',      Text,    nullable=False),
    Column('detail',     Text),
    Column('created_at', Text,    nullable=False),
)


def get_engine(db_path=None) -> Engine:
    """Return a SQLAlchemy engine connected to the RAOC database.

    Uses config.DB_PATH by default. Pass db_path to override (e.g. in tests).
    """
    path = db_path if db_path is not None else config.DB_PATH
    return create_engine(f'sqlite:///{path}')


def _sync_columns(engine: Engine) -> None:
    """Add any columns present in the schema but missing from the live database.

    Uses PRAGMA table_info to inspect each table and issues ALTER TABLE … ADD COLUMN
    for every missing column. Runs after create_all so new tables are never touched.
    """
    with engine.connect() as conn:
        for table in metadata.sorted_tables:
            result = conn.execute(text(f'PRAGMA table_info("{table.name}")'))
            existing = {row[1] for row in result}
            for col in table.columns:
                if col.name not in existing:
                    col_type = col.type.compile(engine.dialect)
                    conn.execute(
                        text(f'ALTER TABLE "{table.name}" ADD COLUMN "{col.name}" {col_type}')
                    )
                    logger.info('Schema sync: added column %s.%s', table.name, col.name)


def create_tables(engine: Engine = None) -> None:
    """Create all tables if they do not exist, then sync any missing columns.

    Uses the default engine if none is provided.
    """
    if engine is None:
        engine = get_engine()
    metadata.create_all(engine)
    _sync_columns(engine)
