# Schulhelfer: Container- und Plattformvertrag

Python 3.12.12, amd64, fixierter Basisdigest und Hash-Lockfiles. Nur SQLite ist
für dieses Release geprüft. Lokale Adminanmeldung und öffentliche Veranstaltungen,
Buchungen und persönliche Elternlinks bleiben erhalten. Kein OIDC-Zwang.

## Standalone ohne Plattform

Im Repository eine private Sessiondatei anlegen; der Inhalt wird nicht ausgegeben:

```sh
python3 prepare_secret.py .private-secrets
export SCHULHELFER_SECRET_FILE="$PWD/.private-secrets/session-key"
docker compose build
docker compose up -d --no-build
docker compose run --rm --no-deps app python -m app.migrate
docker compose run --rm --no-deps app python -m app.setup_admin
```

`.private-secrets` ist 0700. Die einzelne Schlüsseldatei ist eine 0444-Projektion
für den Container unter UID 10001; auf dem Host schützt das private Elternverzeichnis
den Zugriff. Datei und Verzeichnis niemals committen. Ein zweiter Lauf ersetzt
keinen Schlüssel. Im Plattformbetrieb übernimmt der zentrale Secretstore die Projektion.

Die persönliche Erstadministration liest Name und Passwort ausschließlich über
das kontrollierende Terminal, ohne Standardkonto oder Standardpasswort.
Wiederholte Erstadministration wird verweigert. Es gibt keine öffentliche
Adminregistrierung. Die Plattform automatisiert noch keinen interaktiven
Erstzugang. `seed.py` erfordert ebenfalls einen ausdrücklichen persönlichen
Adminnamen und ein Terminalpasswort; Demo-Daten sind nur mit --demo optional.

Der erste Webstart bleibt live, meldet aber bis zur Migration Readiness 503 und
sperrt Fachrouten. Kein create_all beim Import oder Webstart. Alembic-Job mit
exklusiver Dateisperre, BEGIN IMMEDIATE, Fremdschlüsselprüfung und user_version 1.
Bekannte Alembic-Revisionsketten werden migriert; unversionierte Bestandsdaten
und neuere/unerwartete Schemas werden abgewiesen. Keine automatische Übernahme
alter create_all-Datenbanken: vorab sichern, Schema fachlich prüfen und einen
gesonderten Adoptionspfad erstellen.

## Konfiguration, Proxy und Daten

Bestehende Namen bleiben bestehen: BASE_URL, DATABASE_URL, SECRET_KEY und SMTP_*.
Neue FILE-Varianten sind optional für SECRET_KEY und SMTP_PASS. Beide Quellen
gleichzeitig werden verweigert. Fehlender/kurzer Session-Schlüssel blockiert
den Start. BASE_URL ist eine HTTP(S)-Origin und wird zur Laufzeit gelesen;
keine öffentliche URL im Frontend-Build.

Standalone publiziert nur 127.0.0.1:${SCHULHELFER_PORT:-8007}; BASE_URL passend
zum eigenen Proxy setzen. HTTPS-BASE_URL erzeugt Secure-Sessioncookies.
PROXY_IPS enthält ausschließlich explizite vertrauenswürdige Proxy-IP-Adressen,
keine Wildcard. Der Standard vertraut keinen Forwarded-Headern. Tokenisierte
Elternlinks werden nicht in Uvicorn-Accesslogs geschrieben; Referrer-Policy
no-referrer und Cache-Control private,no-store verhindern deren Weitergabe durch
Referrer beziehungsweise gemeinsame Caches.

SMTP zeigt standardmäßig auf einen lokal hinzuzufügenden Mail-Sink namens
mailpit:1025. Es wird kein Upstream konfiguriert. Für einen späteren authentifizierten
Relaybetrieb SMTP_USER und SMTP_PASS_FILE verwenden und die betreffende private
Datei ausdrücklich per Override schreibgeschützt mounten. Das IMAGE enthält keine
.env und keine Zugangsdaten. Der Gesamt-SMTP-Aufruf hat ein Zeitlimit;
ein Ausfall bleibt unabhängig von Datenbank-Readiness.

Persistenz: /app/data/app.db einschließlich SQLite-WAL; UID:GID 10001:10001,
Verzeichnis 0700, neue Dateien 0600. Runtime nicht Root, schreibgeschütztes
Root-Dateisystem, Initprozess, 64-MiB-tmpfs, 384 MiB RAM/1 CPU/128 Prozesse als
gesetzte Limits. Kein Kapazitätsnachweis. Der feste Starter verwendet genau einen
Uvicorn-Prozess. Ein exklusiver Schedulerlock verhindert einen zweiten Scheduler
am selben Datenroot. Retention läuft höchstens einmal parallel.
ReportLab läuft in einem Thread mit genau einem PDF-Slot; Konkurrenz erhält 503
mit Retry-After. Normale Requests teilen dadurch keinen blockierten Eventloop
mit dem PDF-Renderer.

Request-Datenbanksitzungen enden vor BackgroundTasks. Einzelmail-Reader werden
vor dem SMTP-await geschlossen; Zustellprotokolle verwenden eine frische
Schreibtransaktion. Das vermeidet unter dem fixierten FastAPI die im Paralleltest
aufgetretene Bindung von DB-Verbindungen über den Mailaufruf hinweg.

## Wartung und Grenzen

Stop behält das Volume. Der Prozess wartet auf BackgroundTasks bis zum
Uvicorn-Limit von 120 Sekunden; Compose erlaubt 130 Sekunden.
Es gibt weiterhin keine persistente Mail-Outbox und keine automatische
Wiederholungszustellung nach Prozessausfall. Für Bestätigungsmails bleibt der
bestehende manuelle Wiederholungsweg. Dieser Stand ist keine Abnahme eines
verlustfreien Queue-/Drain-Konzepts.

DB-Sicherung über SQLite-.backup-API bei koordiniertem Schreibstopp, niemals
die laufende DBdatei kopieren. Restore in ein neues leeres Volume und die passende
Konfiguration samt Session-Schlüssel bereitstellen; dann ohne Migration oder
erneuten Adminsetup starten. Image-Rollback setzt keine Datenbank zurück.
Verschlüsseltes Offsitebackup, Schlüsselrestore und Rotation sind noch offen.

Die App speichert Eltern-Capabilities wie bisher zusätzlich im Klartext in der
Datenbank für Wiederholversand. Datenbank und Archive deshalb privat behandeln;
keine Tokenwerte in Berichte übernehmen. Login bleibt eine signierte
Starlette-Cookiesitzung: keine zentrale Sperrung, keine zentrale Logout-Revokation.

## Lokal geprüfte Abnahme

52 bestehende Fachtests und acht Vertragsprüfungen im gepinnten Runtimeumfeld.
Standalone-HTTP, öffentliche Elternlinks, lokale Adminanmeldung, CSRF-Negativzugriff,
PDF, Mail-Sink, SMTP-Ausfall, acht gleichzeitige Buchungen auf genau einen Platz,
Neustart und Restore in ein leeres Volume wurden mit synthetischen Daten geprüft.
Im Plattformpilot zusätzlich TLS mit lokaler CA, BASE_URL-/Mail-Linkwechsel,
automatischer Domainrückfall, zwei No-op-Läufe und Stop/Start ohne Migration.

Keine öffentliche DNS-/ACME-Prüfung, kein ARM64-Nachweis, keine globale
Last-/Dauermessung, keine vollständige Retention-/Massenversandabnahme.
Lizenzfreigabe, SBOM und veröffentlichter Registrydigest stehen aus.
