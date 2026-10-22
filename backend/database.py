"""
database.py – Async SQLAlchemy engine, session factory and Base declarative class.
Uses SQLite with aiosqlite for zero-config local development.
"""

from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine, async_sessionmaker
from sqlalchemy.orm import DeclarativeBase
from dotenv import load_dotenv
import os

load_dotenv()

DATABASE_URL = os.getenv("DATABASE_URL", "sqlite+aiosqlite:///./chatpdf_expert.db")

engine = create_async_engine(DATABASE_URL, echo=False, future=True)

async_session_factory = async_sessionmaker(
    engine,
    class_=AsyncSession,
    expire_on_commit=False,
)


class Base(DeclarativeBase):
    """Declarative base for all ORM models."""
    pass


async def init_db():
    """Create all tables defined by the ORM models and run schema migrations if needed."""
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
        
        # Check SQLite table schema and add missing columns if upgrading an existing DB
        def check_and_migrate(connection):
            from sqlalchemy import text
            try:
                result = connection.execute(text("PRAGMA table_info(users)"))
                columns = [row[1] for row in result.fetchall()]
                if columns:
                    if "role" not in columns:
                        connection.execute(text("ALTER TABLE users ADD COLUMN role VARCHAR(20) DEFAULT 'user'"))
                    if "is_active" not in columns:
                        connection.execute(text("ALTER TABLE users ADD COLUMN is_active INTEGER DEFAULT 1"))
                # Archivo Vivo: columnas nuevas de documents
                dcols = [row[1] for row in connection.execute(text("PRAGMA table_info(documents)")).fetchall()]
                for col, ddl in [("media_type", "VARCHAR(10) DEFAULT 'pdf'"), ("source_path", "VARCHAR(1000)"),
                                 ("duration_sec", "FLOAT"), ("file_sig", "VARCHAR(100)")]:
                    if dcols and col not in dcols:
                        connection.execute(text(f"ALTER TABLE documents ADD COLUMN {col} {ddl}"))
            except Exception as e:
                print(f"[WARN] SQLite migration check skipped: {e}")

        await conn.run_sync(check_and_migrate)

    # Automatically ensure default admin exists
    try:
        from auth import seed_default_admin
        async with async_session_factory() as session:
            await seed_default_admin(session)
            await session.commit()
    except Exception as e:
        print(f"[WARN] Seed default admin skipped in init_db: {e}")


async def get_db() -> AsyncSession:
    """FastAPI dependency – yields an async database session."""
    async with async_session_factory() as session:
        try:
            yield session
            await session.commit()
        except Exception:
            await session.rollback()
            raise
        finally:
            await session.close()
