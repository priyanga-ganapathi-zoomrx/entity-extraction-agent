"""Configuration settings for the calculator agent."""

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class LangfuseSettings(BaseSettings):
    """Langfuse configuration settings."""

    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    LANGFUSE_PUBLIC_KEY: str = Field(default="", description="Langfuse public key")
    LANGFUSE_SECRET_KEY: str = Field(default="", description="Langfuse secret key")
    LANGFUSE_HOST: str = Field(
        default="https://cloud.langfuse.com", description="Langfuse host URL"
    )


class LLMSettings(BaseSettings):
    """Shared LLM settings.
    
    API credentials are always shared. Model settings are provided as defaults
    for backward compatibility, but should be overridden per entity
    (e.g., INDICATION_LLM_MODEL in indication/config.py).
    """

    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    # Shared credentials
    LLM_API_KEY: str = Field(default="", description="API key for LLM service")
    LLM_BASE_URL: str = Field(
        default="https://api.openai.com/v1", description="Base URL for LLM API"
    )
    
    # Default model settings (for backward compatibility with unmigrated agents)
    LLM_MODEL: str = Field(
        default="google/gemini-2.5-pro",
        description="Default model (override per entity: INDICATION_LLM_MODEL, DRUG_LLM_MODEL, etc.)"
    )
    LLM_TEMPERATURE: float = Field(default=0.0, description="Default temperature")
    LLM_MAX_TOKENS: int = Field(default=4096, description="Default max tokens")


class TavilySettings(BaseSettings):
    """Tavily search API configuration settings."""

    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    TAVILY_API_KEY: str = Field(default="", description="API key for Tavily search")
    TAVILY_MAX_RESULTS: int = Field(default=5, description="Maximum search results to return")


class GCSSettings(BaseSettings):
    """Google Cloud Storage configuration settings."""

    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    GCS_BUCKET_NAME: str = Field(default="", description="GCS bucket name for storage")
    GCS_PROJECT_ID: str = Field(default="", description="GCS project ID (required if not using service account)")
    GOOGLE_APPLICATION_CREDENTIALS: str = Field(default="", description="Path to service account JSON file")


class EMSSettings(BaseSettings):
    """EMS (Event Management System) logging configuration.

    Publishes structured ECS events to Google Cloud Pub/Sub.
    """

    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    EMS_ENABLED: bool = Field(default=False, description="Enable EMS event publishing to Pub/Sub")
    EMS_PUBSUB_TOPIC: str = Field(default="", description="Pub/Sub topic name (not full path)")


class SentrySettings(BaseSettings):
    """Sentry error monitoring configuration."""

    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    SENTRY_ENABLED: bool = Field(default=False, description="Enable Sentry error capture")
    SENTRY_DSN: str = Field(default="", description="Sentry DSN for error reporting")
    SENTRY_ENVIRONMENT: str = Field(default="dev", description="Sentry environment tag")


class AppSettings(BaseSettings):
    """Application configuration settings."""

    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    # Nested settings
    langfuse: LangfuseSettings = LangfuseSettings()
    llm: LLMSettings = LLMSettings()
    tavily: TavilySettings = TavilySettings()
    gcs: GCSSettings = GCSSettings()
    ems: EMSSettings = EMSSettings()
    sentry: SentrySettings = SentrySettings()


# Global settings instance
settings = AppSettings()

