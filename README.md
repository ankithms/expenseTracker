# Expense Tracker MCP

Async FastMCP server backed by Postgres.

## Database

Set `DATABASE_URL` or `POSTGRES_URL` before running the server or migrations:

```bash
export DATABASE_URL="postgresql://user:password@host:5432/dbname?sslmode=require"
```

The app normalizes `postgres://` and `postgresql://` URLs to SQLAlchemy's
`postgresql+asyncpg://` driver format. `sslmode=require` is supported.

Run migrations:

```bash
uv run alembic upgrade head
```

Check migration status:

```bash
uv run alembic current
```

Run the MCP server:

```bash
uv run fastmcp run main.py:mcp --transport http --host 127.0.0.1 --port 8000
```
