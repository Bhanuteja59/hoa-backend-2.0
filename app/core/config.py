from pydantic import field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    ENV: str = "dev"
    
    # Database
    DATABASE_URL: str

    @property
    def SYNC_DATABASE_URL(self) -> str:
        db_url = self.DATABASE_URL
        if "asyncpg" in db_url:
            return db_url.replace("+asyncpg", "+psycopg2")
        if db_url.startswith("postgres://"):
            return db_url.replace("postgres://", "postgresql+psycopg2://", 1)
        if db_url.startswith("postgresql+psycopg://"):
            return db_url.replace("postgresql+psycopg://", "postgresql+psycopg2://", 1)
        if db_url.startswith("postgresql://"):
            return db_url.replace("postgresql://", "postgresql+psycopg2://", 1)
        return db_url

    @field_validator("DATABASE_URL")
    @classmethod
    def assemble_db_connection(cls, v: str | None) -> str:
        if not v:
            return v
        # Ensure async driver
        if v.startswith("postgres://"):
            return v.replace("postgres://", "postgresql+asyncpg://", 1)
        if v.startswith("postgresql://"):
            return v.replace("postgresql://", "postgresql+asyncpg://", 1)
        # Fix explicit psycopg (v3) scheme if present
        if v.startswith("postgresql+psycopg://"):
            return v.replace("postgresql+psycopg://", "postgresql+asyncpg://", 1)
        return v




    # JWT
    JWT_SECRET_KEY: str = "dev-secret-change"   # 🔥 match expected name
    JWT_ALGORITHM: str = "HS256"
    ACCESS_TOKEN_EXPIRE_MINUTES: int = 60
    REFRESH_DAYS: int = 7
    ALGORITHM: str = "HS256" 
    JWT_AUDIENCE: str = "hoa-ui"
    JWT_ISSUER: str = "hoa-saas"
    # File uploads
    UPLOAD_DIR: str = "uploads"
    MAX_UPLOAD_MB: int = 20

    # Frontend Host (for invitation links)
    FRONTEND_URL: str = "http://localhost:3000"

    # Tenant dev override
    DEV_ALLOW_TENANT_HEADER: bool = True
    
    # CORS
    @field_validator("BACKEND_CORS_ORIGINS", mode="before")
    @classmethod
    def assemble_cors_origins(cls, v: str | list[str]) -> list[str] | str:
        if isinstance(v, str) and not v.startswith("["):
            return [i.strip() for i in v.split(",")]
        elif isinstance(v, (list, str)):
            return v
        raise ValueError(v)

    BACKEND_CORS_ORIGINS: list[str] = [
        "http://localhost:3000",
        "http://localhost:3001",
        "https://hoa-frontend-beryl.vercel.app",
        "https://hoa-frontend-three.vercel.app",
    ]

    # Chatbot
    QDRANT_URL: str = "http://localhost:6333"
    QDRANT_API_KEY: str | None = None
    OPENAI_API_KEY: str = "sk-proj-placeholder"
    QDRANT_COLLECTION: str = "community_docs"

    # Stripe
    STRIPE_API_KEY: str = "sk_test_..."
    STRIPE_PUBLIC_KEY: str = "pk_test_..."

    # SMTP (Email)
    SMTP_SERVER: str | None = None
    SMTP_PORT: int = 587
    SMTP_USERNAME: str | None = None
    SMTP_PASSWORD: str | None = None
    FROM_EMAIL: str | None = None

settings = Settings()
