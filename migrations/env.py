import os
from logging.config import fileConfig

from alembic import context
from sqlalchemy import pool

from pisaprices.storage.orm import Base
from pisaprices.storage.session import make_engine

config = context.config

if config.config_file_name is not None and not config.attributes.get("skip_logging_config"):
    fileConfig(config.config_file_name, disable_existing_loggers=False)

target_metadata = Base.metadata

# Created with op.execute() in the migrations; not described by the ORM models.
RAW_SQL_OBJECTS = ("products_fts", "current_prices")


def database_url() -> str:
    return (
        config.attributes.get("db_url")
        or context.get_x_argument(as_dictionary=True).get("db_url")
        or os.environ.get("PISAPRICES_DB_URL")
        or config.get_main_option("sqlalchemy.url")
    )


def include_object(obj, name, type_, reflected, compare_to) -> bool:
    return not (name or "").startswith(RAW_SQL_OBJECTS)


def run_migrations_offline() -> None:
    context.configure(
        url=database_url(),
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
        render_as_batch=True,
        include_object=include_object,
    )
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    engine = make_engine(database_url(), poolclass=pool.NullPool)
    with engine.connect() as connection:
        context.configure(
            connection=connection,
            target_metadata=target_metadata,
            render_as_batch=True,
            include_object=include_object,
        )
        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
