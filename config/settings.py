from typing import Optional

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="GOOFISH_", env_file=".env")

    # LLM
    openai_api_key: Optional[str] = None
    openai_base_url: str = "https://api.openai.com/v1"
    vlm_model: str = "gpt-4o"
    llm_model: str = "gpt-4o"
    deepseek_api_key: Optional[str] = None
    deepseek_base_url: str = "https://api.deepseek.com/v1"

    # Database
    database_url: str = "sqlite:///goofish_agent.db"

    # Browser
    browser_headless: bool = False
    browser_data_dir: str = "browser_data"
    proxy_server: Optional[str] = None

    # Scoring weights (no reference images)
    w_condition: float = 0.35
    w_price: float = 0.30
    w_credit: float = 0.20
    w_desc_match: float = 0.15

    # Scoring weights (with reference images)
    w_condition_ref: float = 0.25
    w_price_ref: float = 0.25
    w_credit_ref: float = 0.15
    w_desc_match_ref: float = 0.10
    w_image_match: float = 0.25

    # Rate limits (per hour)
    search_per_hour: int = 10
    detail_per_hour: int = 30
    favorite_per_hour: int = 20
    chat_new_per_hour: int = 5
    message_per_hour: int = 30

    # Notification
    smtp_host: Optional[str] = None
    smtp_port: int = 587
    smtp_user: Optional[str] = None
    smtp_password: Optional[str] = None
    webhook_url: Optional[str] = None

    # Storage
    media_dir: str = "media_cache"

    # Security
    encryption_key: Optional[str] = None


_settings: Optional[Settings] = None


def get_settings() -> Settings:
    global _settings
    if _settings is None:
        _settings = Settings()
    return _settings
