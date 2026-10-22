from sqlalchemy import create_engine, inspect, text
from sqlalchemy.orm import sessionmaker
from .config import get_settings
from .models import Base


def make_engine():
    url = get_settings().database_url
    kwargs = {"connect_args": {"check_same_thread": False}} if url.startswith("sqlite") else {}
    return create_engine(url, future=True, pool_pre_ping=True, **kwargs)


engine = make_engine()
SessionLocal = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)


def init_db() -> None:
    Base.metadata.create_all(engine)
    evaluation_columns = {column["name"] for column in inspect(engine).get_columns("evaluations")}
    if "commit_fingerprint" not in evaluation_columns:
        with engine.begin() as connection:
            connection.execute(
                text("ALTER TABLE evaluations ADD COLUMN commit_fingerprint VARCHAR(64)")
            )
