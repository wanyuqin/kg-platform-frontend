"""环境变量装配（技术设计文档 12.1）。"""

from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="KG_", env_file=".env", extra="ignore")

    database_url: str = "postgresql+asyncpg://kg:kg@localhost:5433/kg"
    redis_url: str = "redis://localhost:6379/0"

    viking_base_url: str = "http://localhost:1933"
    viking_api_key: str = "dev-local-root-key"  # 对应 ov.conf 的 server.root_api_key
    viking_timeout_ms: int = 800

    lark_app_id: str = ""
    lark_app_secret: str = ""
    session_ttl_hours: int = 12
    dev_login_enabled: bool = False  # KG_DEV_LOGIN_ENABLED：本地联调登录后门，生产严禁开启

    default_top_k: int = 5
    max_top_k: int = 20
    upload_max_mb: int = 2
    audit_retention_days: int = 180

    @property
    def alembic_database_url(self) -> str:
        # Alembic 走同步驱动
        return self.database_url.replace("+asyncpg", "+psycopg")


@lru_cache
def get_settings() -> Settings:
    return Settings()
