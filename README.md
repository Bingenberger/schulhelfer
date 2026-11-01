# Schulfest-Helfer-App

Selbst gehostete Web-App für die Helfer-Organisation bei Schulveranstaltungen.
Eltern buchen Schichten und sagen Sachspenden zu – ohne Registrierung.
Die Schulleitung konfiguriert Events, überwacht Füllstände und versendet Bestätigungen.

## Stack

Python 3.12 · FastAPI · Jinja2 · SQLite · Pico.css

---

## Setup – Nativ (empfohlen für Produktion)

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt

cp .env.example .env
# .env anpassen (SECRET_KEY, SMTP-Daten, BASE_URL)

# Datenbank initialisieren und Admin anlegen:
python seed.py --admin-user admin --admin-pass SICHERESPASSWORT

# Optional: Demo-Daten
python seed.py --demo

# Server starten
uvicorn app.main:app --host 127.0.0.1 --port 8000
```

### systemd-Unit

```bash
sudo cp schulfest-helfer.service /etc/systemd/system/
sudo systemctl enable --now schulfest-helfer
```

### Caddy als Reverse-Proxy

```bash
# Caddyfile.example nach /etc/caddy/Caddyfile kopieren und anpassen
sudo systemctl reload caddy
```

### nginx als Reverse-Proxy

```bash
sudo cp nginx.conf.example /etc/nginx/sites-available/schulfest-helfer
sudo ln -s /etc/nginx/sites-available/schulfest-helfer /etc/nginx/sites-enabled/
sudo nginx -t && sudo systemctl reload nginx

# TLS per Let's Encrypt (baut die Config automatisch auf HTTPS inkl. Redirect um):
sudo apt install certbot python3-certbot-nginx
sudo certbot --nginx -d helfer.ggs-ndk.de
```

Sobald HTTPS aktiv ist: `https_only=True` in `app/main.py` setzen und den Dienst
neu starten (die systemd-Unit übergibt uvicorn bereits `--proxy-headers`, damit
`X-Forwarded-Proto` von nginx ausgewertet wird).

---

## Setup – Docker

```bash
cp .env.example .env
# .env anpassen

docker build -t schulfest-helfer .
docker run -d \
  --name schulfest-helfer \
  -p 127.0.0.1:8000:8000 \
  -v $(pwd)/data:/app/data \
  --env-file .env \
  schulfest-helfer

# Admin anlegen (einmalig):
docker exec schulfest-helfer python seed.py --admin-user admin --admin-pass SICHERESPASSWORT
```

---

## Alembic-Migrationen

```bash
# Neue Migration generieren
alembic revision --autogenerate -m "beschreibung"

# Migrationen anwenden
alembic upgrade head
```

---

## Tests

```bash
pytest tests/ -v
```

---

## Betrieb

### Backup der SQLite-Datei

Die gesamte Datenbank liegt in `data/app.db`. Tägliches Backup per cron:

```cron
0 3 * * * cp /opt/schulfest-helfer/data/app.db /backup/app-$(date +\%Y\%m\%d).db
```

### Datenlöschung (Retention)

Der eingebaute APScheduler läuft täglich und löscht automatisch `Participant`-, `Booking`- und `Donation`-Datensätze, sobald `event.retention_weeks` Wochen nach dem `event_date` vergangen sind. Kein manueller Eingriff nötig.

### E-Mail (Strato)

```env
SMTP_HOST=smtp.strato.de
SMTP_PORT=465
SMTP_STARTTLS=false
SMTP_USE_TLS=true
SMTP_USER=helfer@ggs-ndk.de
SMTP_PASS=<Postfach-Passwort>
```

Strato bietet auf Port 465 ausschließlich implizites TLS (SMTPS) an, kein
STARTTLS – deshalb `SMTP_USE_TLS=true` **und** `SMTP_STARTTLS=false`. Bei
einem anderen Provider mit STARTTLS auf Port 587 ist es umgekehrt.

DKIM wird über DNS-Eintrag der Schule aktiviert – kein App-seitiger Aufwand.

### Zeitzone

Alle Datums- und Zeitangaben werden als lokale Wandzeit gespeichert und verglichen.
Maßgeblich ist `TIMEZONE` in der `.env` (Standard `Europe/Berlin`). Eine auf 18:00
gesetzte Anmeldefrist endet damit tatsächlich um 18:00 Ortszeit.

### Sicherheitshinweise

- `SECRET_KEY` muss ein langer Zufallswert sein: `python -c "import secrets; print(secrets.token_hex(32))"`
- Hinter HTTPS-Proxy betreiben (Caddy/nginx)
- `https_only=True` in `app/main.py` setzen, sobald HTTPS aktiv ist
- Zustandsändernde Admin-Formulare sind CSRF-geschützt (Token in der Session).
  Öffentliche Formulare bleiben ohne Token, damit die Anmeldung auch bei
  restriktiven Cookie-Einstellungen funktioniert; sie sind über den geheimen
  Zugangslink bzw. die Duplikatsprüfung abgesichert.
- Das Zugangstoken wird zusätzlich im Klartext gespeichert (`participant.token`),
  damit der Link der Eltern über die gesamte Laufzeit stabil bleibt – auch beim
  Veröffentlichen. Wer die Datenbank lesen kann, sieht ohnehin alle Daten, auf
  die ein solcher Link Zugriff gibt.

---

## Routen

| Methode | Pfad | Zweck |
|---------|------|-------|
| GET | `/` | Öffentliche Startseite mit laufenden Veranstaltungen |
| GET | `/e/{id}` | Öffentliche Event-Ansicht |
| POST | `/e/{id}/code` | Zugangscode prüfen |
| POST | `/e/{id}/book` | Buchung/Spende |
| GET | `/e/{id}/availability` | Live-Belegung als JSON |
| GET/POST | `/e/{id}/recover` | Link-Wiederherstellung (mit Event) |
| GET/POST | `/wiederherstellen` | Link-Wiederherstellung ohne Event |
| GET | `/s/{token}` | Persönliche Statusseite |
| POST | `/s/{token}/add` | Weitere Buchung |
| POST | `/s/{token}/cancel` | Storno |
| POST | `/s/{token}/donation/{id}` | Sachspende korrigieren |
| GET | `/admin` | Event-Liste |
| POST | `/admin/events/{id}/stations/copy` | Aufbau aus früherer Veranstaltung |
| POST | `/admin/stations/{id}/shifts/series` | Schichtreihe anlegen |
| POST | `/admin/events/{id}/bookings/add` | Helfer manuell eintragen |
| GET | `/admin/events/{id}/dashboard` | Füllstand & Versandfortschritt |
| POST | `/admin/events/{id}/publish` | Plan veröffentlichen (Versand im Hintergrund) |
| POST | `/admin/events/{id}/resend` | Offene Bestätigungsmails erneut senden |
| GET | `/admin/events/{id}/export.csv` | CSV-Export |
| GET | `/admin/events/{id}/export.pdf` | PDF-Export |
| GET | `/admin/classes` | Klassenliste |

### E-Mail-Versand

Alle Mails werden **nach** der HTTP-Antwort im Hintergrund zugestellt – die
Anmeldung wartet also nicht auf den SMTP-Server. Der Bestätigungsversand beim
Veröffentlichen läuft als Hintergrundjob; sein Fortschritt (versandt /
fehlgeschlagen / ausstehend) steht im Dashboard, samt Knopf zum erneuten
Senden der offenen Adressen.
