# Pflichtenheft: Schulfest-Helfer-App

*Version 1.0 – zur Umsetzung durch Claude Code*

> **Hinweis an die Umsetzung:** Bitte zuerst den **MVP (Abschnitt 4.1)** vollständig und gemäß den **Abnahmekriterien (Abschnitt 12)** implementieren. Abschnitt 13 (Ausbau) erst danach. Stack ist verbindlich (Abschnitt 3). Bei Unklarheiten gelten die Annahmen in Abschnitt 14; Abweichungen bitte als TODO im Code markieren.

---

## 1. Ziel

Eine selbst gehostete Web-App, mit der eine Grundschule für Veranstaltungen Elternhilfe organisiert. Eltern buchen Helfer-Schichten und sagen Sachspenden **selbst** zu und sehen dabei live die Belegung. Die Schulleitung konfiguriert Events, überwacht den Füllstand, schließt die Anmeldung und versendet die Bestätigung. Eine manuelle Auswertung soll nicht mehr nötig sein.

## 2. Akteure und Rollen

- **Öffentlich/Eltern** (ohne Konto): Zugriff über Event-Link und persönlichen Token-Link. Keine Registrierung, kein Passwort.
- **Admin** (Schulleitung, 1–n Personen): Login mit Benutzername + Passwort. Verwaltet Events, Stationen, Klassenliste, Dashboard, Versand, Export.

## 3. Technische Rahmenbedingungen (verbindlich)

- **Sprache/Runtime:** Python ≥ 3.12
- **Framework:** FastAPI + Uvicorn
- **Templates:** Jinja2 (serverseitiges Rendering)
- **Interaktivität:** HTMX für Live-Belegung und Formular-Submits (kein SPA-Framework)
- **CSS:** schlankes Framework (Pico.css oder Tailwind via Build); responsiv, mobil zuerst
- **Datenbank:** SQLite über SQLAlchemy (oder SQLModel); Migrationen via Alembic
- **Passwort-Hashing:** Argon2 (argon2-cffi) oder bcrypt
- **Hintergrundjobs:** APScheduler (für Löschfrist), alternativ systemd-Timer
- **Betrieb:** VPS in Deutschland, hinter Reverse-Proxy (Caddy oder nginx) mit automatischem HTTPS
- **Auslieferung:** Dockerfile **und** Anleitung für nativen Betrieb (systemd-Unit); `.env`-basierte Konfiguration
- **Tests:** pytest
- **Lokalisierung:** UI durchgängig **Deutsch**; Texte zentral in Templates/Konstanten, damit später übersetzbar (i18n nicht im MVP nötig)

## 4. Geltungsbereich

### 4.1 MVP (zuerst umsetzen)

- Admin-Login
- Event anlegen/bearbeiten/löschen, Statuswechsel
- Klassenliste pflegen (manuell **und** CSV-Import)
- Helfer-Stationen mit Schichten (Zeit/Dauer/Soll), Spenden-Stationen mit optionaler Soll-Menge
- Öffentliche Event-Seite mit Live-Belegung
- Eltern-Selbstbuchung (Schicht + Spende) mit Pflichtfeldern Name, E-Mail, Klasse
- Persönlicher Token-Link (Statusseite) inkl. Anzeige am Bildschirm + Versand per Mail
- Link-Wiederherstellung per E-Mail (Token-Rotation)
- Storno durch Eltern bis zur Frist
- Admin-Dashboard mit Füllstand und Lücken
- „Anmeldung schließen" und „Plan veröffentlichen" → Bestätigungsmail an alle
- Export: CSV und PDF-Helferplan
- Löschjob (automatische Löschung nach Frist)

### 4.2 Späterer Ausbau (Abschnitt 13)

Event-Duplizierung/Vorlagen, Warteliste, Erinnerungsmails, iCal-Export, QR-Code, Mehrsprachigkeit, optionale Vornamen-Anzeige.

## 5. Datenmodell

Beziehungen: `Event 1—n Station`, `Station(helper) 1—n Shift`, `Participant n—m Shift` (über `Booking`), `Participant 1—n Donation`, `Klasse 1—n Participant`. `Participant` ist **pro Event** eindeutig.

```sql
-- Admins
admin_user(
  id INTEGER PK,
  username TEXT UNIQUE NOT NULL,
  password_hash TEXT NOT NULL,
  created_at DATETIME NOT NULL
)

-- Klassenliste (schulweit, je Schuljahr)
klasse(
  id INTEGER PK,
  label TEXT NOT NULL,            -- z. B. "3b"
  school_year TEXT NOT NULL,      -- z. B. "2026/27"
  active BOOLEAN NOT NULL DEFAULT 1,
  UNIQUE(label, school_year)
)

-- Veranstaltung
event(
  id INTEGER PK,
  name TEXT NOT NULL,
  event_date DATE NOT NULL,
  location TEXT,
  description TEXT,
  registration_deadline DATETIME,
  status TEXT NOT NULL DEFAULT 'draft', -- draft|open|closed|published|archived
  access_code TEXT,              -- optional; wenn gesetzt, für öffentliche Ansicht erforderlich
  retention_weeks INTEGER NOT NULL DEFAULT 4,
  created_at DATETIME NOT NULL
)

-- Station (zwei Typen)
station(
  id INTEGER PK,
  event_id INTEGER FK->event NOT NULL,
  type TEXT NOT NULL,            -- helper|donation
  name TEXT NOT NULL,
  description TEXT,
  target_count INTEGER,         -- nur donation: optionale Soll-Menge (z. B. 15 Kuchen)
  sort_order INTEGER NOT NULL DEFAULT 0
)

-- Schicht (nur helper-Stationen)
shift(
  id INTEGER PK,
  station_id INTEGER FK->station NOT NULL,
  start_time DATETIME NOT NULL,
  duration_min INTEGER NOT NULL,
  required_count INTEGER NOT NULL, -- Soll-Personen
  sort_order INTEGER NOT NULL DEFAULT 0
)

-- Teilnehmer (pro Event eindeutig über E-Mail)
participant(
  id INTEGER PK,
  event_id INTEGER FK->event NOT NULL,
  name TEXT NOT NULL,
  email TEXT NOT NULL,
  klasse_id INTEGER FK->klasse NOT NULL,
  token_hash TEXT NOT NULL,     -- SHA-256 des Zugangstokens (Token selbst NICHT speichern)
  created_at DATETIME NOT NULL,
  UNIQUE(event_id, email)
)

-- Schicht-Buchung
booking(
  id INTEGER PK,
  participant_id INTEGER FK->participant NOT NULL,
  shift_id INTEGER FK->shift NOT NULL,
  created_at DATETIME NOT NULL,
  UNIQUE(participant_id, shift_id)
)

-- Sachspende
donation(
  id INTEGER PK,
  participant_id INTEGER FK->participant NOT NULL,
  station_id INTEGER FK->station NOT NULL,
  item_text TEXT NOT NULL,      -- z. B. "Marmorkuchen"
  quantity INTEGER NOT NULL DEFAULT 1,
  created_at DATETIME NOT NULL
)

-- Optionales E-Mail-Protokoll (Versandnachweis, ohne Inhalt)
email_log(
  id INTEGER PK,
  participant_id INTEGER FK->participant,
  kind TEXT NOT NULL,           -- access_link|recovery|confirmation
  sent_at DATETIME NOT NULL,
  success BOOLEAN NOT NULL
)
```

## 6. Funktionale Anforderungen – Öffentlich/Eltern

1. **Event-Ansicht** (`/e/{event_id}`): zeigt Eventdaten und alle Stationen. Pro Helfer-Schicht „Ist/Soll" (z. B. „3/5"); volle Schichten ausgegraut/nicht buchbar. Pro Spenden-Station die Liste zugesagter Posten (nur Posten + Menge, **keine Namen**) und ggf. Fortschritt „8/15". Ist `access_code` gesetzt, vorher Code-Eingabe.
2. **Buchung/Spende:** Eltern wählen eine oder mehrere Schichten und/oder tragen Spenden ein und füllen **Pflichtfelder** aus: Name, E-Mail, Klasse (Dropdown aus aktiver Klassenliste). Mehrfachauswahl möglich.
3. **Identität/Token:** Beim ersten Absenden wird ein `participant` angelegt und ein Zufallstoken erzeugt. Existiert die E-Mail im Event bereits, wird **nicht** gebucht, sondern auf die Wiederherstellung verwiesen (Abschnitt 8, Geschäftsregel G4).
4. **Zugangslink:** Der Token-Link wird nach dem Absenden **am Bildschirm angezeigt** (mit Hinweis „als Lesezeichen speichern") **und** per Mail versandt.
5. **Statusseite** (`/s/{token}`): zeigt alle eigenen Buchungen/Spenden, erlaubt Ergänzen und Stornieren bis `registration_deadline`, zeigt weiterhin Live-Belegung. Ungültiger/abgelaufener Token → neutrale Fehlerseite mit Link zur Wiederherstellung.
6. **Link-Wiederherstellung** (`/e/{event_id}/recover`): E-Mail-Eingabe → bei Treffer wird ein **neuer** Token erzeugt (alter wird ungültig) und per Mail verschickt. Antwort an den Nutzer ist **immer neutral** („Falls eine Anmeldung existiert, wurde eine Mail versandt"), um Enumeration zu vermeiden.

## 7. Funktionale Anforderungen – Admin

1. **Login/Logout** (Session-Cookie, `Secure`, `HttpOnly`, `SameSite=Lax`).
2. **Event-CRUD** inkl. Statuswechsel `draft→open→closed→published→archived`. Öffentliche Buchung nur bei `open`.
3. **Stationen/Schichten/Spenden-CRUD** je Event, inkl. Sortierung.
4. **Klassenliste:** CRUD + **CSV-Import** (Spalten `label`, `school_year`); Aktiv-Schalter.
5. **Dashboard** (`/admin/events/{id}/dashboard`): Füllstand je Schicht/Station, **unbesetzte/unterbesetzte Schichten farblich hervorgehoben**, Spendenfortschritt, Teilnehmerzahl. Admin sieht hier auch Namen, Klasse und E-Mail (Detailansicht je Schicht/Station).
6. **Manuelle Korrektur:** Buchung hinzufügen/entfernen, Teilnehmer in andere Schicht verschieben.
7. **Anmeldung schließen** (`closed`) und **Plan veröffentlichen** (`published`): Letzteres versendet die **Bestätigungsmail** an alle Teilnehmer mit ihrer persönlichen Einteilung + Status-Link.
8. **Export:** CSV (alle Buchungen/Spenden mit Name, Klasse, E-Mail) und **PDF-Helferplan** (gegliedert nach Station/Schicht und Uhrzeit, druckbar).

## 8. Geschäftsregeln und Validierung

- **G1 – Kapazität:** Eine Schicht-Buchung ist nur möglich, solange `COUNT(booking) < shift.required_count`. Prüfung und Insert **in einer Transaktion**, um Überbuchung bei gleichzeitigen Zugriffen zu verhindern.
- **G2 – Frist:** Buchung/Storno durch Eltern nur, wenn `now < registration_deadline` und `event.status = open`.
- **G3 – Pflichtfelder:** Name (nicht leer), E-Mail (valides Format), Klasse (gültige aktive `klasse_id`). Serverseitige Validierung ist maßgeblich.
- **G4 – E-Mail eindeutig je Event:** Zweiter Eintrag mit existierender E-Mail wird abgelehnt; Hinweis auf Wiederherstellung. So bleibt der Token der alleinige Eigentumsnachweis.
- **G5 – Token:** ≥ 256 Bit Entropie (`secrets.token_urlsafe(32)`); im Klartext **nie** gespeichert (nur `token_hash`). „Neuer Link" rotiert den Token und invalidiert den alten.
- **G6 – Spendenmenge:** `quantity ≥ 1`. `target_count` ist Anzeige-/Richtwert, **keine** harte Sperre (Überzeichnung erlaubt, wird im UI markiert).
- **G7 – Sichtbarkeit:** Öffentliche Endpunkte geben **niemals** Name, E-Mail oder Klasse zurück – nur Zahlen und Spendenposten.

## 9. E-Mail-Versand

- **Trigger:** (a) erster Eintrag → Zugangslink; (b) Wiederherstellung → neuer Link; (c) Veröffentlichung → Bestätigung mit Einteilung.
- **Transport:** SMTP mit Authentifizierung, konfiguriert über `.env` (`SMTP_HOST`, `SMTP_PORT`, `SMTP_USER`, `SMTP_PASS`, `SMTP_FROM`, `SMTP_STARTTLS`). Empfehlung Betrieb: Versand über das Microsoft-365-Konto der Schule **mit aktiviertem DKIM** (DNS-seitig vorhanden vorausgesetzt).
- **Format:** Text + einfache HTML-Variante; deutscher Inhalt; Links absolut (Basis-URL aus `.env`).
- **Fehlertoleranz:** Versandfehler werden in `email_log` protokolliert und brechen die Buchung **nicht** ab (der Link steht ja am Bildschirm). Sammelversand bei Veröffentlichung gedrosselt (konfigurierbare Pause zwischen Mails).

## 10. Nicht-funktionale Anforderungen

- **Sicherheit:** ausschließlich HTTPS; CSRF-Schutz für alle state-ändernden POSTs; Rate-Limiting auf `/recover` und Login (z. B. pro IP/Minute); keine Token in Server-Logs oder Referrer; Security-Header (HSTS, X-Content-Type-Options, Referrer-Policy `same-origin`).
- **Datenschutz:** Datensparsamkeit gemäß Datenmodell; Klasse/E-Mail nie öffentlich; **Löschjob** entfernt `participant`, `booking`, `donation` automatisch `event.retention_weeks` nach `event_date`; im Anmeldeformular Pflicht-Checkbox „Datenschutzhinweis gelesen" mit Link auf eine konfigurierbare Hinweisseite.
- **Performance:** ausgelegt auf einige hundert Teilnehmer je Event; Live-Belegung als HTMX-Teil-Refresh.
- **Barrierearmut:** semantisches HTML, Labels, Tastaturbedienbarkeit, ausreichende Kontraste.
- **Betrieb:** tägliches Backup der SQLite-Datei; strukturierte Logs ohne PII; Konfiguration vollständig über `.env`.

## 11. Routen-Übersicht

| Methode | Pfad | Zweck |
|---|---|---|
| GET | `/e/{event_id}` | öffentliche Event-Ansicht (ggf. Code) |
| POST | `/e/{event_id}/book` | Buchung/Spende anlegen (HTMX) |
| GET/POST | `/e/{event_id}/recover` | neuen Token-Link anfordern |
| GET | `/s/{token}` | persönliche Statusseite |
| POST | `/s/{token}/add` | weitere Buchung/Spende |
| POST | `/s/{token}/cancel` | Storno einer Buchung/Spende |
| GET | `/admin/login`, POST gleiche URL | Admin-Login |
| POST | `/admin/logout` | Logout |
| GET | `/admin` | Event-Liste |
| * | `/admin/events…` | Event-CRUD, Status, Stationen, Schichten, Spenden |
| * | `/admin/classes…` | Klassenliste-CRUD + CSV-Import |
| GET | `/admin/events/{id}/dashboard` | Füllstand |
| POST | `/admin/events/{id}/close` / `/publish` | schließen / veröffentlichen (+Mailversand) |
| GET | `/admin/events/{id}/export.csv` / `export.pdf` | Exporte |

## 12. Abnahmekriterien (testbar)

1. Eltern können ohne Konto eine Schicht buchen; die Live-Belegung erhöht sich sofort; eine volle Schicht ist nicht buchbar (G1 verifiziert auch bei zwei quasi-gleichzeitigen Buchungen – kein Überbuchen).
2. Pflichtfelder werden serverseitig erzwungen; ungültige E-Mail/Klasse wird abgelehnt.
3. Nach Buchung wird ein Token-Link angezeigt **und** versandt; die Statusseite zeigt nur die eigenen Einträge.
4. Zweiter Eintrag mit gleicher E-Mail im selben Event wird abgelehnt und verweist auf Wiederherstellung (G4).
5. Wiederherstellung erzeugt neuen Token, invalidiert den alten; Antwort ist neutral (keine Existenz-Auskunft).
6. Öffentliche Endpunkte liefern in keiner Antwort Name/E-Mail/Klasse (G7) – per Test geprüft.
7. Storno ist nur vor Frist und im Status `open` möglich (G2).
8. Admin-Dashboard markiert unterbesetzte Schichten; CSV- und PDF-Export enthalten korrekte Daten.
9. „Veröffentlichen" versendet je Teilnehmer genau eine Bestätigung mit korrekter Einteilung; Versand wird in `email_log` protokolliert.
10. Der Löschjob entfernt Teilnehmerdaten nach Ablauf von `retention_weeks` (per verkürzter Frist im Test verifiziert).
11. CSV-Import der Klassenliste legt Klassen korrekt an; doppelte (label, school_year) werden nicht dupliziert.
12. Token werden nur als Hash gespeichert (G5) – per DB-Inspektion im Test geprüft.

## 13. Backlog (nach MVP)

Event-Duplizierung & Vorlagen; Warteliste für volle Schichten mit Auto-Nachrücken; Erinnerungsmail X Tage vorher; iCal-Export der eigenen Schicht; QR-Code zum Token-Link; optionale Vornamen-Anzeige je Schicht (Konfig-Schalter, Standard aus); Mehrsprachigkeit.

## 14. Annahmen (gelten bis zur Korrektur)

- „Klasse aus den Stammdaten" = Auswahl aus einer gepflegten **Klassenliste** (kontrolliertes Vokabular), **ohne** Anbindung an einzelne Schülerdatensätze.
- Eine Person, die zu einem zweiten Event hilft, ist ein neuer `participant`-Datensatz (Teilnehmer ist eventgebunden).
- Pro Event genügt ein optionaler `access_code`; keine Pflicht zur Code-Sicherung.
- Es gibt zunächst keine Rollendifferenzierung innerhalb des Admin-Bereichs (alle Admins gleichberechtigt).

## 15. Liefergegenstände

- Lauffähiges Repository mit Projektstruktur (`app/`, `templates/`, `static/`, `tests/`, `migrations/`).
- `README.md` mit Setup (nativ + Docker), `.env.example`, Reverse-Proxy-Beispiel (Caddy), systemd-Unit.
- Alembic-Migrationen + Seed-Skript (Demo-Event, Beispiel-Klassenliste, Admin-Anlage per CLI).
- pytest-Suite, die die Abnahmekriterien aus Abschnitt 12 abdeckt.
- Kurze Betriebsdoku: Backup der SQLite-Datei, Löschjob-Konfiguration.

---

### Beispiel `.env`

```
BASE_URL=https://helfer.ggs-ndk.de
SECRET_KEY=<zufallswert>
DATABASE_URL=sqlite:///./data/app.db
SMTP_HOST=smtp.office365.com
SMTP_PORT=587
SMTP_STARTTLS=true
SMTP_USER=helfer@ggs-ndk.de
SMTP_PASS=<geheim>
SMTP_FROM="GGS Niederkassel <helfer@ggs-ndk.de>"
SMTP_SEND_DELAY_MS=400
PRIVACY_NOTICE_URL=https://www.ggs-niederkassel.de/datenschutz-helfer
```
