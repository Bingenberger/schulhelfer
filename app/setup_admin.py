"""Personal first administrator, local controlling terminal only; no defaults."""
import getpass
from sqlalchemy import select
from app.database import SessionLocal
from app.models import AdminUser
from app.platform import data_lock, schema_ready
from app.security import hash_password


def setup(username, password):
    if not username.strip() or len(username) > 80 or not 12 <= len(password) <= 1024:
        raise ValueError("Invalid local account fields")
    if not schema_ready():
        raise ValueError("Migrate first")
    with data_lock("admin-setup"):
        with SessionLocal() as db:
            db.connection().exec_driver_sql("BEGIN IMMEDIATE")
            if db.execute(select(AdminUser.id)).first():
                raise ValueError("Initial setup already completed")
            db.add(AdminUser(username=username.strip(), password_hash=hash_password(password)))
            db.commit()


def main():
    with open("/dev/tty", "r+") as terminal:
        terminal.write("Persönlicher Adminname: ")
        terminal.flush()
        username = terminal.readline().strip()
        password = getpass.getpass("Passwort (mindestens 12 Zeichen): ", stream=terminal)
        confirmation = getpass.getpass("Passwort wiederholen: ", stream=terminal)
        if password != confirmation:
            raise ValueError("Password confirmation differs")
        setup(username, password)
        terminal.write("Persönlicher Erstzugang eingerichtet.\n")


if __name__ == "__main__":
    try:
        main()
    except Exception:
        raise SystemExit("Lokale Einrichtung fehlgeschlagen; Terminal, Schema und vorhandene Konten prüfen.")
