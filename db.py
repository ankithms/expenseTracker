import os
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine


DEFAULT_DATABASE_URL = (
    "postgresql+asyncpg://postgres:postgres@localhost:5432/expense_tracker"
)


def normalize_database_url(url):
    split_url = urlsplit(url)
    query = [
        (key, value)
        for key, value in parse_qsl(split_url.query, keep_blank_values=True)
        if key not in ("ssl", "sslmode")
    ]
    url = urlunsplit(split_url._replace(query=urlencode(query)))

    if url.startswith("postgres://"):
        return "postgresql+asyncpg://" + url.removeprefix("postgres://")

    if url.startswith("postgresql://"):
        return "postgresql+asyncpg://" + url.removeprefix("postgresql://")

    return url


def needs_ssl(url):
    query = dict(parse_qsl(urlsplit(url).query))
    return query.get("sslmode") in ("require", "verify-ca", "verify-full") or (
        query.get("ssl") in ("true", "1", "require")
    )


RAW_DATABASE_URL = (
    os.environ.get("DATABASE_URL")
    or os.environ.get("POSTGRES_URL")
    or DEFAULT_DATABASE_URL
)
DATABASE_URL = normalize_database_url(RAW_DATABASE_URL)
DATABASE_CONNECT_ARGS = {"ssl": True} if needs_ssl(RAW_DATABASE_URL) else {}

engine = create_async_engine(
    DATABASE_URL,
    pool_pre_ping=True,
    connect_args=DATABASE_CONNECT_ARGS,
)
AsyncSessionLocal = async_sessionmaker(engine, expire_on_commit=False)


def get_session():
    return AsyncSessionLocal()
