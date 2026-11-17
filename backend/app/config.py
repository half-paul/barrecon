from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    database_url: str = "sqlite:///./barrecon.db"
    admin_password: str = "change-me"
    session_secret: str = "dev-secret-change-me"
    # the platform console login (/platform), made at first start; an empty password uses ADMIN_PASSWORD
    platform_admin_username: str = "platform"
    platform_admin_password: str = ""
    first_tenant_name: str = "First tenant"  # the tenant an existing single-bar database becomes; rename it in the console
    session_max_age_seconds: int = 60 * 60 * 12
    cookie_secure: bool = False
    # behind a reverse proxy: take the client address from X-Forwarded-For (otherwise anyone could fake it)
    trust_proxy: bool = False
    timezone: str = "America/Vancouver"
    static_dir: str = "app/static"
    # The SwypePOS back office login is per business, stored encrypted on the business (Setup -> Businesses).
    # reading store receipts with Claude (services/receipts.py); empty: receipts are entered by hand
    anthropic_api_key: str = ""
    receipt_model: str = "claude-opus-5-5"
    # USD per million tokens, for the platform console's cost figures: {"model": [input, output]}. Models not listed
    # here use services/usage.PRICES.
    ai_prices: dict[str, list[float]] = {}
    # nightly Swype sales import (services/swype_auto.py); off in tests
    scheduler_enabled: bool = True
    # admins log in with a password and an authenticator code. false: password only (local development and tests)
    admin_mfa_required: bool = False  # true: admins must set up two-step login; anyone may turn it on either way
    # email for invites, password resets and MFA recovery (services/mail.py)
    # Mailgun when its key and domain are set, else SMTP; neither: no email is sent
    public_url: str = ""  # the address people open the portal at; email links are built from it
    mailgun_api_key: str = ""
    mailgun_domain: str = ""
    mailgun_api_base: str = "https://api.mailgun.net"  # EU-region domains: https://api.eu.mailgun.net
    smtp_host: str = ""
    smtp_port: int = 587
    smtp_user: str = ""
    smtp_password: str = ""
    smtp_tls: bool = True
    mail_from: str = ""


settings = Settings()
