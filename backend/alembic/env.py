from alembic import context
from app import models  # noqa: F401  (registers tables)
from app.config import get_settings
from app.db import Base, make_engine

target_metadata = Base.metadata


def run_offline() -> None:
    context.configure(
        url=get_settings().db_url, target_metadata=target_metadata, literal_binds=True, render_as_batch=True
    )
    with context.begin_transaction():
        context.run_migrations()


def run_online() -> None:
    conn = context.config.attributes.get("connection")
    if conn is not None:
        context.configure(connection=conn, target_metadata=target_metadata, render_as_batch=True)
        with context.begin_transaction():
            context.run_migrations()
        return
    eng = make_engine(get_settings().db_url)
    with eng.begin() as c:
        context.configure(connection=c, target_metadata=target_metadata, render_as_batch=True)
        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_offline()
else:
    run_online()
