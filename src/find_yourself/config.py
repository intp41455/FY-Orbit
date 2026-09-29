from functools import lru_cache
from pydantic import model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="FY_", env_file=".env", extra="ignore")
    environment: str = "local"
    database_url: str = "sqlite:///.runtime/find-yourself.db"
    owner_id: str = "owner"
    local_token: str = ""
    session_secret: str = ""
    public_url: str = "http://127.0.0.1:8000"
    oidc_issuer: str = ""
    oidc_client_id: str = ""
    oidc_client_secret: str = ""
    oidc_owner_sub: str = ""
    temporal_address: str = ""
    temporal_namespace: str = "default"
    temporal_queue: str = "find-yourself"
    model_api_key: str = ""
    model_base_url: str = ""
    s3_endpoint: str = ""
    s3_bucket: str = "find-yourself"
    s3_region: str = "us-east-1"
    artifacts_path: str = ".runtime/artifacts"
    otlp_endpoint: str = ""
    agent_endpoints: dict[str, str] = {}

    @model_validator(mode="after")
    def validate_security(self):
        if self.environment not in {"local", "production", "test"}:
            raise ValueError("Invalid FY_ENVIRONMENT")
        if len(self.session_secret) < 32:
            raise ValueError("FY_SESSION_SECRET must contain at least 32 characters")
        if self.environment == "production":
            if not all([self.oidc_issuer, self.oidc_client_id, self.oidc_owner_sub]):
                raise ValueError("Production requires OIDC and an exact owner subject")
            if not self.public_url.startswith("https://") or self.local_token:
                raise ValueError("Production requires HTTPS and disables local tokens")
            if not self.database_url.startswith("postgresql") or not self.temporal_address:
                raise ValueError("Production requires PostgreSQL and Temporal")
            if not self.s3_endpoint:
                raise ValueError("Production requires private object storage")
        return self


@lru_cache
def settings():
    return Settings()
