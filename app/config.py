from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8")

    BASE_URL: str = "http://localhost:8000"
    SECRET_KEY: str = "changeme-use-a-long-random-value"
    DATABASE_URL: str = "sqlite:///./data/app.db"

    SMTP_HOST: str = "localhost"
    SMTP_PORT: int = 587
    # STARTTLS (Port 587): Verbindung startet unverschlüsselt, wird per STARTTLS-
    # Kommando hochgestuft. USE_TLS (Port 465, "implicit TLS"/SMTPS): Verbindung
    # ist von Anfang an verschlüsselt. Nur eines von beiden auf True setzen.
    SMTP_STARTTLS: bool = True
    SMTP_USE_TLS: bool = False
    SMTP_USER: str = ""
    SMTP_PASS: str = ""
    SMTP_FROM: str = "Schulfest <noreply@example.com>"
    SMTP_SEND_DELAY_MS: int = 400
    # Ein hängender Mailserver darf einen Hintergrund-Worker nicht dauerhaft binden.
    SMTP_TIMEOUT_S: int = 15

    PRIVACY_NOTICE_URL: str = "/datenschutz"

    TIMEZONE: str = "Europe/Berlin"
    ORGANISATION: str = "Das Organisationsteam"


settings = Settings()
