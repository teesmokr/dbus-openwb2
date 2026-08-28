# Changelog

## [1.5.0] – 2026-08-28

Ergebnis eines umfassenden Multi-Agenten-Reviews (Korrektheit, Sicherheit,
Robustheit, Protokolltreue, Shell/Install, UX) mit adversarialer Verifikation.

### Sicherheit
- **XSS behoben**: alle openWB-/Config-Werte im Web-Interface werden escaped;
  Ladepunkt-Auswahl per Event-Delegation statt interpoliertem `onclick`.
- **CSRF-Schutz**: POST-Endpunkte verlangen JSON-Content-Type + eigenen Header
  und prüfen die Origin gegen den Host.
- **Keine Geheimnisse mehr ans UI**: MQTT-Passwort wird (wie der Web-Passwort-Hash)
  nicht mehr über `/api/config` ausgeliefert; `config.ini` wird auf `600` gesetzt.
- POST-Body-Größe begrenzt.

### Behoben (Korrektheit/Robustheit)
- **install.sh nutzte `python`** (existiert auf Venus OS nicht) → durchgängig
  `python3`; kein `opkg update` mehr bei jedem Boot.
- **Log-Viewer las falschen Pfad** (`/data/log` statt `/var/log`) → jetzt korrekt.
- **Watchdog**: `os._exit` statt `sys.exit` im GLib-Callback (Neustart greift jetzt);
  feuert auch, wenn nie eine Nachricht ankam; `periodic()` gegen Exceptions gehärtet.
- **MQTT-Reconnect**: `connect_async` + Backoff — kein Crash-Loop mehr, wenn die
  openWB beim Start nicht erreichbar ist.
- **Kaputte/halbe `config.ini`** wird abgefangen (kein Sekundentakt-Crash);
  `config.ini` wird atomar geschrieben (tmp + fsync + rename).
- **Web-„Speichern" bewahrt** nun per SSH gepflegte Werte (`tls_enabled`, `timeout`,
  `nominal_voltage`, MQTT-Passwort) durch Merge statt Überschreiben; DEFAULT-Keys
  werden nicht mehr in andere Sektionen dupliziert.
- **`/MaxCurrent`** löst keinen Ladebefehl mehr aus (nur lokales Limit);
  `/SetCurrent` wird auf 6…Max A begrenzt.
- `/Status` meldet zusätzlich **4 = „Warte auf Sonne"** bei PV-Laden.
- Scan-Client-ID eindeutig (uuid).

### Robustheit/Betrieb
- **`status.json` im tmpfs** (`/run`) statt auf der eMMC → kein Flash-Verschleiß.
- **`paho-mqtt` gebündelt** (`ext/paho-mqtt/`) → Installation ohne Internet/pip;
  systemweites paho wird bevorzugt.
- **SetupHelper**: `run`-Scripts pfad-unabhängig (behebt Crash-Loop bei Installation
  nach `/data/<pkg>`); `setup` nutzt `installService`/`removeService` und sichert die
  Reboot-Persistenz zusätzlich über einen `rc.local`-Eintrag; `install.sh` wartet
  per `svok` auf supervise; `uninstall.sh` stoppt Log-Runner/multilog sauber.

### Doku
- Log-Pfade, Sitzungs-/SoC-/Status-Zuordnung, Mehrfach-Ladepunkt-Hinweis im UI,
  Sicherheitsabschnitt, korrigierter Satz in den Voraussetzungen.

## [1.4.0] – 2026-08-28

### Neu
- **Sitzungsenergie auf der EVCS-Kachel**: Der Treiber publiziert jetzt
  `/Session/Energy` und `/Session/Time` – die Venus-EVCS-Kachel liest genau
  diese Pfade und zeigt damit während des Ladens die **geladene kWh** und die
  **Ladedauer der aktuellen Sitzung** (vorher „--kWh"). Sitzung = seit Anstecken;
  beim Abstecken wird sie zurückgesetzt (Kachel zeigt „--").
- Live-Status im Web-Interface zeigt zusätzlich **kWh Sitzung**.

### Intern
- Sitzungslogik über `plug_state`/`imported`-Delta statt Leistungs-Heuristik.

## [1.3.1] – 2026-08-28

### Behoben
- **Update greift jetzt sofort**: `install.sh` und das SetupHelper-`setup`
  starten nach einem Update **beide Dienste neu** (Treiber *und*
  Web-Interface). Vorher lief das Web-Interface nach einem Update noch mit der
  alten Version weiter (fehlende neue Karten wie „Sicherheit").
- **Kein veralteter Browser-Cache** mehr: Das Web-Interface sendet
  `Cache-Control: no-store`.

### Verbessert
- **SoC robuster**: akzeptiert JSON (`{"soc": …}`) *und* nackte Zahl; Debug-Log
  bei Empfang.
- **openWB-Scan** zeigt jetzt pro Ladepunkt den **SoC** (oder „kein SoC") – so
  ist sofort sichtbar, ob die openWB überhaupt einen Ladestand liefert.

> Hinweis bei SoC-Problemen: Die openWB sendet nur dann einen Fahrzeug-Ladestand,
> wenn für das Fahrzeug ein **SoC-Modul** konfiguriert ist. Ohne SoC-Quelle in
> der openWB bleibt der Wert leer.

## [1.3.0] – 2026-08-28

### Neu
- **Passwortschutz fürs Web-Interface** (optional, HTTP Basic Auth):
  - Benutzername + Passwort im UI konfigurierbar (Abschnitt „Sicherheit")
  - Passwort wird nur als **SHA-256-Hash** gespeichert, nie im Klartext und
    nie über `/api/config` ausgeliefert
  - abschaltbar; bei vergessenem Passwort `[WEB] password_hash` per SSH leeren
- **README: Update-Anleitung** – wie man eine installierte Version auf dem
  Cerbo aktualisiert (SetupHelper und manuell), `config.ini` bleibt erhalten.

## [1.2.0] – 2026-08-28

### Neu
- **Mehrere Ladepunkte**: `chargepoint_id` akzeptiert eine Liste (`1,2`),
  je Ladepunkt entsteht ein eigener `evcharger`-Service (fortlaufende Instanz).
- **Fahrzeug-Ladestand (SoC)** auf `/Soc` und **`/Ac/Frequency`** neu.
- **Live-Status im Web-Interface**: Leistung, Sollstrom, Phasen, kWh und SoC
  je Ladepunkt mit Status-Pill, Auto-Refresh (`/api/live`, Treiber-`status.json`).
- **Log-Viewer im Web-Interface** (`/api/log`, `tail` + `tai64nlocal`).
- **SetupHelper-Kompatibilität** (Beta): `setup`-Script + `version` für den
  Package Manager von Kevin Windrem.
- **GitHub Actions CI** (py_compile, pyflakes, bash -n, ShellCheck),
  Issue-/PR-Templates, `CONTRIBUTING.md`, VRM-Screenshot in der README.

### Intern
- Treiber um eine `ChargePoint`-Klasse refaktoriert (sauberes Multi-Instanz-Handling).

## [1.1.0] – 2026-08-28

### Neu / Verbessert
- Deutlich hochwertigeres Web-Interface: Hero-Header mit Farbverlauf
  (openWB-Grün → Victron-Blau), animierter Energiefluss (Wallbox → Speicher),
  Marken-Badges (openWB / Victron Energy), Sektions-Icons, Verlaufs-Button
  und Footer. Alle Grafiken als eingebettetes SVG → voll offline-fähig.
- README: Screenshot des Web-Interface (`docs/web-interface.png`).
- README: Abschnitt „openWB vorbereiten (MQTT)" – Direktverbindung (Port 1883)
  und MQTT-Brücke als Alternative, inkl. Feld-für-Feld-Tabelle.
- `.gitattributes`: Binärdateien (PNG/JPG/ZIP) explizit als `binary` markiert.

## [1.0.0] – 2026-08-28

Erste Veröffentlichung.

### Neu
- Treiber `dbus-openwb2.py`: openWB 2.x (software2) als
  `com.victronenergy.evcharger` in Venus OS.
- Vollständiges openWB-2-MQTT-Mapping (power, imported, currents, voltages,
  phases_in_use, evse_current, plug_state, charge_state, connected_vehicle/config).
- Dediziertes Web-Interface (`webconfig.py`, Port 8088) mit Live-Scan der
  openWB, Ladepunkt-Erkennung, Speichern und Dienst-Neustart.
- Optionale Steuerung (Start/Stop, Ladestrom, Modus) über die openWB-2
  `charge_template`-Set-Topics; `charge_template`-ID wird automatisch erkannt.
- Kompatibilität mit paho-mqtt 1.x und 2.x.
- Installer mit daemontools-Diensten, Autostart via `rc.local`, CRLF-Schutz.
