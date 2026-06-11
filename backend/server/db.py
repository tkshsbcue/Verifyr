"""Database engine + session. SQLite by default, any SQLAlchemy URL via DATABASE_URL."""

from __future__ import annotations

from sqlalchemy import create_engine, inspect, text
from sqlalchemy.orm import DeclarativeBase, sessionmaker

from .settings import server_settings

_url = server_settings.database_url
# SQLite needs check_same_thread=False because the background runner uses its own thread.
_connect_args = {"check_same_thread": False} if _url.startswith("sqlite") else {}

engine = create_engine(_url, connect_args=_connect_args, future=True)
SessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False, expire_on_commit=False)


class Base(DeclarativeBase):
    pass


def init_db() -> None:
    # Import models so they register on Base before create_all.
    from . import models  # noqa: F401

    Base.metadata.create_all(bind=engine)
    if engine.dialect.name == "sqlite":
        # create_all only creates missing *tables*, never missing *columns*. A
        # SQLite file from an older schema (e.g. before `user_id` was added, or a
        # persisted Docker volume) would otherwise crash at query time. Add any
        # columns the models expect but the existing tables lack. Postgres
        # (Supabase) is the production store and is migrated separately.
        _sync_sqlite_columns()


def _sync_sqlite_columns() -> None:
    insp = inspect(engine)
    existing_tables = set(insp.get_table_names())
    with engine.begin() as conn:
        for table in Base.metadata.sorted_tables:
            if table.name not in existing_tables:
                continue  # freshly created by create_all
            have = {c["name"] for c in insp.get_columns(table.name)}
            for col in table.columns:
                if col.name in have:
                    continue
                # Add as nullable regardless of the model's NOT NULL: SQLite
                # can't add a NOT NULL column to a table that already has rows
                # without a default, and back-filling a value (e.g. user_id) for
                # pre-existing rows isn't meaningful. New rows are written with
                # the column populated by the ORM.
                type_sql = col.type.compile(dialect=engine.dialect)
                try:
                    conn.execute(text(f'ALTER TABLE {table.name} ADD COLUMN "{col.name}" {type_sql}'))
                    print(f"[db] migrated: added {table.name}.{col.name}", flush=True)
                except Exception as err:  # never block startup on a best-effort migration
                    print(f"[db] could not add {table.name}.{col.name}: {err}", flush=True)


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
