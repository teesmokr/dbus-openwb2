# Changelog

## [2.0.1] – 2026-09-02

### Behoben
- **Ladezeit lief, obwohl nicht geladen wurde** ([#1](https://github.com/teesmokr/dbus-openwb2/issues/1)):
  `/ChargingTime` zählte bisher ab dem Anstecken hoch – unabhängig davon, ob
  tatsächlich geladen wurde. Jetzt zählt `/ChargingTime` nur noch, während
  wirklich geladen wird (`plug_state` **und** `charge_state`), und akkumuliert
  korrekt über Ladepausen hinweg. Die Dauer seit dem Anstecken bleibt weiterhin
  als `/Session/Time` verfügbar.

## [2.0.0] – 2026-08-29

### Geändert (Breaking)
- **Nur noch die openWB-SimpleAPI.** Auf Empfehlung des openWB-Teams
  ([openWB/core#3876](https://github.com/openWB/core/discussions/3876)) liest und
  steuert der Treiber ausschließlich über `openWB/simpleAPI/…`. Die SimpleAPI ist
  in aktuellen openWB-2-Versionen **immer aktiv** (nicht abschaltbar).
- **Interner Topic-Modus entfernt** (`api_mode`, `charge_template_id` in der
  Config entfallen). Grund: Das `charge_template`-Handling der internen Topics war
  die häufigste Ursache für zerschossene openWB-Konfigurationen – die SimpleAPI
  vermeidet das komplett.
- Findet der Scan keine SimpleAPI-Topics (sehr alte openWB), gibt es eine klare
  Meldung „openWB aktualisieren"; der Treiber beendet sich per Watchdog mit
  entsprechendem Log.

### Hinweis zum Update
Bestehende `config.ini` funktioniert weiter – `api_mode`/`charge_template_id`
werden schlicht ignoriert. Da die SimpleAPI immer aktiv ist, läuft die Anzeige
nach dem Update ohne Zutun weiter.

### Intern
- Treiber deutlich verschlankt (interne JSON-Parser, `charge_template`-Logik,
  Frequenz-Pfad und der `api_mode`-Zweig entfernt).

## [1.6.1] – 2026-08-29

### Behoben
- **SimpleAPI chargemode-Lesewert**: Die openWB-SimpleAPI reicht den chargemode
  beim Lesen **unverändert** durch (internes Vokabular, z. B. `pv_charging`) und
  benennt nur das Topic um. v1.6.0 mappte nur die Kurzform (`pv`) → `/Mode` war
  im SimpleAPI-Modus teils falsch. Jetzt werden **beide** Vokabeln abgedeckt.
  (Verifiziert gegen die openWB-core-Implementierung `simpleAPI/simpleAPI_mqtt.py`;
  die SET-Topics akzeptieren ohnehin beide Formen.)

## [1.6.0] – 2026-08-29

### Neu
- **openWB SimpleAPI-Unterstützung** (`api_mode = simple`, empfohlen vom
  openWB-Team): liest den von openWB **versionsstabil** gehaltenen Topic-Baum
  `openWB/simpleAPI/…` (power, imported, currents/1-3, voltages/1-3,
  phases_in_use, evse_current, plug_state, charge_state, soc, chargemode).
- **Deutlich einfachere Steuerung** im SimpleAPI-Modus über
  `simpleAPI/set/chargepoint/<id>/chargemode` und `/chargecurrent` – **kein
  `charge_template`-Handling** mehr nötig.
- **Scan erkennt automatisch**, ob SimpleAPI oder die internen Topics verfügbar
  sind, und stellt bei SimpleAPI direkt darauf um; Auswahl **„API"** im
  Web-Interface.
- Die internen Topics (`api_mode = internal`, Standard) bleiben als voll
  kompatibler Fallback erhalten.

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
