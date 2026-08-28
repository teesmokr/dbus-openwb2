# Changelog

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
