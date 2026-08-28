# Changelog

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
