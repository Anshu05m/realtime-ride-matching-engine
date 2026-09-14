from sqlalchemy.orm import DeclarativeBase


class Base(DeclarativeBase):
    """Shared declarative base. Alembic's env.py points at Base.metadata to autogenerate migrations."""
