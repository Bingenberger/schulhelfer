from pathlib import Path
from urllib.parse import urlsplit
from pydantic import Field, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", hide_input_in_errors=True)

    BASE_URL: str = "http://localhost:8000"
    SECRET_KEY: str = Field(default="", repr=False)
    SECRET_KEY_FILE: str = ""
    PROXY_IPS: str = ""
    DATABASE_URL: str = "sqlite:///./data/app.db"

    SMTP_HOST: str = "localhost"
    SMTP_PORT: int = 587
    # STARTTLS (Port 587): Verbindung startet unverschlüsselt, wird per STARTTLS-
    # Kommando hochgestuft. USE_TLS (Port 465, "implicit TLS"/SMTPS): Verbindung
    # ist von Anfang an verschlüsselt. Nur eines von beiden auf True setzen.
    SMTP_STARTTLS: bool = True
    SMTP_USE_TLS: bool = False
    SMTP_USER: str = ""
    SMTP_PASS: str = Field(default="", repr=False)
    SMTP_PASS_FILE: str = ""
    SMTP_FROM: str = "Schulfest <noreply@example.com>"
    SMTP_SEND_DELAY_MS: int = 400
    # Ein hängender Mailserver darf einen Hintergrund-Worker nicht dauerhaft binden.
    SMTP_TIMEOUT_S: int = 15

    PRIVACY_NOTICE_URL: str = "/datenschutz"

    TIMEZONE: str = "Europe/Berlin"
    ORGANISATION: str = "Das Organisationsteam"

    @model_validator(mode="after")
    def runtime_contract(self):
        if self.SECRET_KEY_FILE:
            if self.SECRET_KEY:
                raise ValueError("Configure one session key source")
            self.SECRET_KEY = Path(self.SECRET_KEY_FILE).read_text().strip()
        if len(self.SECRET_KEY) < 32 or self.SECRET_KEY.startswith(("changeme", "<")):
            raise ValueError("A private session key of at least 32 characters is required")
        if self.SMTP_PASS_FILE:
            if self.SMTP_PASS:
                raise ValueError("Configure one SMTP password source")
            self.SMTP_PASS = Path(self.SMTP_PASS_FILE).read_text().strip()
        origin = urlsplit(self.BASE_URL)
        if (origin.scheme not in ("http", "https") or not origin.hostname or origin.username or origin.password or
                origin.query or origin.fragment or origin.path not in ("", "/")):
            raise ValueError("BASE_URL must be an HTTP(S) origin")
        _ = origin.port
        self.BASE_URL = self.BASE_URL.rstrip("/")
        if self.SMTP_STARTTLS and self.SMTP_USE_TLS:
            raise ValueError("Choose one SMTP TLS mode")
        return self


settings = Settings()
