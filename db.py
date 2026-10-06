"""Database engine / session handling. PostgreSQL-ready via SQLAlchemy."""
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker, scoped_session, declarative_base

from config import Config

connect_args = {}
if Config.DATABASE_URL.startswith("sqlite"):
    connect_args = {"check_same_thread": False}

engine = create_engine(Config.DATABASE_URL, connect_args=connect_args, pool_pre_ping=True)
Session = scoped_session(sessionmaker(bind=engine, autoflush=False, expire_on_commit=False))
Base = declarative_base()


def get_session():
    return Session()


def init_db():
    import models  # noqa: F401  (register all models)
    Base.metadata.create_all(engine)
