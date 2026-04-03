from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    DATABASE_URL: str = "postgresql+asyncpg://postgres:password@localhost:5432/agentgate"
    SUPABASE_URL: str = ""
    SUPABASE_ANON_KEY: str = ""
    JWT_SECRET: str = "placeholder-secret"
    JWT_ALGORITHM: str = "HS256"
    APPROVAL_TOKEN_TTL_SECONDS: int = 3600
    RATE_LIMIT_REQUESTS: int = 10
    RATE_LIMIT_WINDOW_SECONDS: int = 60
    RESEND_API_KEY: str = ""
    DASHBOARD_URL: str = "https://app.agentgate.dev"
    ENVIRONMENT: str = "development"
    CORS_ORIGINS: str = "http://localhost:3000,http://127.0.0.1:3000"
    API_URL: str = "https://api.spendnod.com"
    STRIPE_SECRET_KEY: str = ""
    STRIPE_PUBLISHABLE_KEY: str = ""
    STRIPE_WEBHOOK_SECRET: str = ""
    STRIPE_STARTER_PRICE_ID: str = ""
    STRIPE_PRO_PRICE_ID: str = ""


settings = Settings()
