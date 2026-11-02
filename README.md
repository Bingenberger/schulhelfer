# Schulfest-Helfer-App

Selbst gehostete Helferorganisation: öffentliche Veranstaltungen, Schichten,
Sachspenden und persönliche Elternlinks; lokale Administrationsanmeldung.

Der geprüfte Containerstand und seine Grenzen stehen in [PLATFORM.md](PLATFORM.md).
Python 3.12.12, SQLite, genau ein Webprozess und ein exklusiver Scheduler.
Kein Standardkonto und keine automatische Schemaänderung beim Webstart.

## Eigenständiger Dockerbetrieb

```sh
python3 prepare_secret.py .private-secrets
export SCHULHELFER_SECRET_FILE="$PWD/.private-secrets/session-key"
docker compose build
docker compose up -d --no-build
docker compose run --rm --no-deps app python -m app.migrate
docker compose run --rm --no-deps app python -m app.setup_admin
```

Readiness bleibt vor dem expliziten Migrationsjob 503. Die persönliche
Erstadministration fragt Name und Passwort am Terminal ab. Sie ist einmalig.
Der Schlüssel wird weder ausgegeben noch bei Wiederholung ersetzt.
Das private Schlüsselverzeichnis nicht in Git aufnehmen.

Nur 127.0.0.1:${SCHULHELFER_PORT:-8007} wird veröffentlicht.
BASE_URL auf die vollständige Origin des eigenen Proxys setzen.
Bei HTTPS setzt die App Secure-Cookies automatisch. SMTP zeigt ohne eigene
Konfiguration auf einen separat bereitzustellenden lokalen Mail-Sink mailpit:1025,
ohne Upstream. App, Datenvolume und Secretdatei bleiben eigenständig.

## Nativer Betrieb

In einer Python-3.12-Umgebung für linux/amd64:

```sh
python3 -m venv .venv
. .venv/bin/activate
pip install --require-hashes -r requirements.lock
python3 prepare_secret.py .private-secrets
cp .env.example .env
# BASE_URL und private Schlüsseldatei in .env prüfen; ausschließlich eigener Mail-Sink.
python -m app.migrate
python -m app.setup_admin
uvicorn app.main:app --host 127.0.0.1 --port 8007 --workers 1 --no-access-log
```

Bei Proxybetrieb nur die tatsächlichen Proxy-IP-Adressen ausdrücklich freigeben.
Die systemd- und Proxydateien sind Beispiele; Bestandskonfigurationen vor einer
Übernahme prüfen. Ein nicht versioniertes Bestands-SQLite-Schema wird nicht
automatisch übernommen. Bestehende IDs und Elternlinks nicht durch Demo-Seeding ersetzen.

## Migration, Tests und Export

Anwendungsmigration nur über `python -m app.migrate`, bei pausierten Writern.
Der Job verwendet die geprüfte Alembic-Kette mit exklusiver Sperre.
Unbekannte Schema-Versionen blockieren die Freigabe.
Neue Migrationen können mit Alembic entwickelt werden, benötigen aber eine
eigene Prüfung und neue Releasefreigabe.

```sh
pip install --require-hashes -r requirements-test.lock
# Nur synthetischer Testschlüssel, keine Produktivdatei:
SECRET_KEY=synthetic-session-fixture-key-0123456789 pytest tests/ contract_tests/
```

Der Integrationsnachweis verwendet getrennte Testläufe für Fachsuite und
Vertragsprüfungen in einem isolierten Testimage. SMTP ist in Fachtests absichtlich
unerreichbar. Echte lokale Sink-Zustellung und parallele Buchungen sind separat
im Plattformrepository nachgewiesen. PDF ist auf einen Slot im einzigen
Webprozess begrenzt; belegter Slot ergibt 503 mit Retry-After.

## Sicherung, Retention und Mail

SQLite ausschließlich über die .backup-API sichern, niemals die laufende Datei
mit cp kopieren. Mit passendem Sicherungsdienst koordiniert Writer pausieren.
Die eigenständige Datenbank liegt unter data/app.db, im Container unter
/app/data/app.db. Session-Schlüssel und Konfiguration zusätzlich privat sichern.
Restore in eine leere Umgebung und mit passendem Image prüfen. Ein Image-Rollback
macht keine DB-Migration rückgängig.

Retention läuft alle 24 Stunden und soll Teilnehmerdaten nach dem konfigurierten
Zeitraum entfernen. Eine vollständige Retention-Abnahme steht aus.
Mailversand läuft als BackgroundTask mit Gesamtzeitlimit und Zustellprotokoll.
Es gibt noch keine persistente Outbox für Prozessausfälle; fehlgeschlagene
Bestätigungsmails lassen sich über den vorhandenen Verwaltungsweg wiederholen.
Ein SMTP-Ausfall beeinflusst die eigene Datenbank-Readiness nicht.

BASE_URL, DATABASE_URL, SECRET_KEY und SMTP_* bleiben gültige Konfigurationsnamen.
Für Secrets bevorzugt SECRET_KEY_FILE und SMTP_PASS_FILE verwenden.
Bei Compose eine zusätzliche SMTP-Passwortdatei nur über einen ausdrücklichen
schreibgeschützten Mount bereitstellen. Kein externer SMTP-Upstream im Testaufbau.

Eltern-Capabilities werden für Wiederholversand weiterhin auch im Klartext in der
Datenbank gespeichert. Datenbank und Archive schützen. Tokenisierte Pfade nicht
in Accesslogs schreiben. Es gibt keine zentrale Sperrung oder Logout-Revokation.

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
