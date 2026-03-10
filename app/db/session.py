from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker, AsyncSession
from app.core.config import settings

# Properly handle the URL for asyncpg
raw_url = settings.DATABASE_URL
database_url = raw_url.replace("postgresql://", "postgresql+asyncpg://", 1)

# Remove unsupported query params for asyncpg
if "?" in database_url:
    base_url, query = database_url.split("?", 1)
    params = [p for p in query.split("&") if not p.startswith("sslmode=") and not p.startswith("channel_binding=")]
    database_url = f"{base_url}?{'&'.join(params)}" if params else base_url

# Neon requires SSL
connect_args = {}
if "neon.tech" in raw_url or "sslmode=require" in raw_url:
    import ssl
    ctx = ssl.create_default_context()
    ctx.check_hostname = False
    ctx.verify_mode = ssl.CERT_NONE
    connect_args["ssl"] = ctx

engine = create_async_engine(
    database_url,
    connect_args=connect_args,
    pool_pre_ping=True
)

# Async session factory
AsyncSessionLocal = async_sessionmaker(
    bind=engine,
    class_=AsyncSession,
    expire_on_commit=False,
)

# Dependency to get DB session
async def get_db() -> AsyncSession:
    async with AsyncSessionLocal() as session:
        yield session
