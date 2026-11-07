from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    database_url: str = "sqlite:///./barrecon.db"
    admin_password: str = "change-me"
    session_secret: str = "dev-secret-change-me"
    session_max_age_seconds: int = 60 * 60 * 12
    cookie_secure: bool = False
    # behind a reverse proxy: take the client address from X-Forwarded-For (otherwise anyone could fake it)
    trust_proxy: bool = False
    timezone: str = "America/Vancouver"
    static_dir: str = "app/static"
    # SwypePOS back office login, for pulling the Item Sales report (services/swype_api.py)
    swype_email: str = ""
    swype_password: str = ""
    swype_company: str = "caffebarney"
    swype_store_id: int = 1
    # reading store receipts with Claude (services/receipts.py); empty: receipts are entered by hand
    anthropic_api_key: str = ""
    receipt_model: str = "claude-opus-5-5"
    # nightly Swype sales import (services/swype_auto.py); off in tests
    scheduler_enabled: bool = True


settings = Settings()
