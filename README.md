# dbus-openwb2

Integriert eine **openWB 2.x** (software2) Wallbox als Ladestation
(`com.victronenergy.evcharger`) in **Venus OS** – inklusive **dediziertem
Web-Interface** zur Konfiguration und **optionaler Steuerung** aus Venus OS.

Inspiriert von [gvzdus/dbus-mqtt-openwb](https://github.com/gvzdus/dbus-mqtt-openwb)
(nur openWB 1.9), neu geschrieben für die openWB-2-Topic-Struktur.

![dbus-openwb2 Web-Interface](docs/web-interface.png)

---

## Was es kann

- **Anzeige** der openWB in Venus-GUI und VRM: Leistung (gesamt + pro Phase),
  Energie, Ladestrom, Frequenz, Status (getrennt/verbunden/lädt), Phasenzahl
  und **Fahrzeug-Ladestand (SoC)**.
- **Mehrere Ladepunkte**: je openWB-Ladepunkt ein eigener Venus-Ladestation-Service.
- **Web-Interface** unter `http://<venus-ip>:8088`:
  - **Live-Status** (Leistung, Sollstrom, Phasen, kWh, SoC) mit Auto-Refresh
  - **Log-Viewer** – Treiber-Log im Browser, ohne SSH
  - openWB live **scannen** → erkennt Ladepunkte und Live-Werte automatisch
  - Broker, Ladepunkt-ID(s), Name, VRM-Instanz, Max-Strom, Position einstellen
  - optionaler **Passwortschutz** (HTTP Basic Auth, Passwort nur als Hash gespeichert)
  - **Speichern** schreibt `config.ini` und startet den Treiber neu
- **Steuerung (optional, abschaltbar)**: Start/Stop, Ladestrom und Lademodus
  aus Venus OS zurück in die openWB (Modus *Sofortladen* / *Stop* / *PV*).

### So sieht es in Venus OS / VRM aus

<img src="docs/vrm-dashboard.jpg" alt="openWB als EVCS-Kachel in der VRM-App" width="360">

Die openWB erscheint als **EVCS-Kachel** neben Netz, Lasten, PV und Batterie
(hier mit 727 kWh Gesamtenergie).

## Voraussetzungen

- Venus-OS-Gerät (getestet gedacht für Cerbo GX) mit **root/SSH-Zugang**
- openWB **2.x** mit aktivem **MQTT-Broker** (Standard: Port 1883, Root-Topic `openWB`)

## Schnellinstallation (Endnutzer)

Per SSH auf dem Venus-OS-Gerät:

```bash
cd /tmp
wget -O dbus-openwb2.zip https://github.com/teesmokr/dbus-openwb2/archive/refs/heads/main.zip
unzip -o dbus-openwb2.zip
rm -rf /data/etc/dbus-openwb2
cp -R dbus-openwb2-main /data/etc/dbus-openwb2
bash /data/etc/dbus-openwb2/install.sh
```

Danach **`http://<venus-ip>:8088`** im Browser öffnen und konfigurieren.

## Installation via SetupHelper (Beta)

Wenn du den [SetupHelper](https://github.com/kwindrem/SetupHelper) von Kevin
Windrem installiert hast, kannst du `dbus-openwb2` über den **Package Manager**
verwalten (Download, Update, automatische Neuinstallation nach Firmware-Updates):

**GX-GUI → Settings → Package manager → Inactive packages → new** und eintragen:

| Feld | Wert |
|------|------|
| Package name | `dbus-openwb2` |
| GitHub user   | `teesmokr` |
| GitHub branch/tag | `main` (oder `v1.3.0`) |

Dann **Proceed / Install**. Das mitgelieferte [`setup`](setup)-Script installiert
`paho-mqtt`, legt die `config.ini` an und verlinkt beide Dienste.

> Beta: Die SetupHelper-Integration ist noch nicht auf Hardware getestet.
> Rückmeldungen willkommen. Ohne SetupHelper einfach die Schnellinstallation
> oder `install.sh` nutzen.

## Installation (manuell)

```bash
# 1. Projekt auf den Cerbo kopieren (per scp) nach:
#    /data/etc/dbus-openwb2/
# 2. Installer ausführen:
bash /data/etc/dbus-openwb2/install.sh
```

Der Installer setzt Rechte, installiert `paho-mqtt` (falls nötig), verlinkt beide
Dienste in `/service/` und trägt sich in `/data/rc.local` ein (übersteht
Firmware-Updates).

Danach im Browser: **`http://<venus-ip>:8088`** öffnen, openWB scannen,
Ladepunkt übernehmen, speichern. Fertig – die Wallbox erscheint in der Geräteliste.

## Aktualisieren (Update)

Deine `config.ini` bleibt bei allen Wegen erhalten (sie wird nie überschrieben,
nur angelegt, falls sie fehlt).

**Mit SetupHelper:** GX-GUI → **Settings → Package manager → Active packages →
dbus-openwb2 → Proceed** (bzw. „Check for updates" / „Update"). SetupHelper zieht
die neueste Version und reinstalliert automatisch.

**Manuell / Schnellinstallation:** einfach erneut ausführen – das Skript
überschreibt die Programmdateien und lässt die `config.ini` in Ruhe:

```bash
cd /tmp
wget -O dbus-openwb2.zip https://github.com/teesmokr/dbus-openwb2/archive/refs/heads/main.zip
unzip -o dbus-openwb2.zip
# Programmdateien aktualisieren, config.ini behalten:
cp -R dbus-openwb2-main/* /data/etc/dbus-openwb2/
bash /data/etc/dbus-openwb2/install.sh
```

`install.sh` erkennt eine vorhandene Installation, aktualisiert die Dienste und
startet sie neu. Danach im Browser neu laden (Strg+F5).

Aktuell installierte Version prüfen:

```bash
cat /data/etc/dbus-openwb2/version
```

## openWB vorbereiten (MQTT)

Der Treiber liest die Daten über MQTT. Es gibt zwei Wege – **Variante A ist der
Normalfall und braucht keine openWB-Einstellung.**

### Variante A – direkt mit dem openWB-Broker verbinden (empfohlen)

openWB 2.x betreibt intern einen MQTT-Broker, der im Heimnetz auf **Port 1883**
erreichbar ist. Es ist **keine Konfiguration an der openWB nötig** – einfach im
Web-Interface eintragen:

| Feld            | Wert                          |
|-----------------|-------------------------------|
| openWB IP       | IP der openWB (z. B. `192.168.1.50`) |
| MQTT-Port       | `1883`                        |
| Root-Topic      | `openWB`                      |
| Benutzer/Passwort | leer                        |

Dann **„openWB scannen"** klicken – die Ladepunkte erscheinen automatisch.

### Variante B – MQTT-Brücke (nur wenn A nicht geht)

Falls Port 1883 der openWB nicht erreichbar ist oder du die Daten bewusst an
einen **anderen** Broker (z. B. einen eigenen Mosquitto oder den auf dem Cerbo)
weiterleiten willst, richtest du in der openWB eine **MQTT-Brücke** ein:

**openWB-Menü:** `System → MQTT-Brücken → „+" (Neue Brücke)`

| Feld in der openWB      | Wert                                              |
|-------------------------|---------------------------------------------------|
| Bezeichnung             | z. B. `Venus`                                     |
| Brücke aktivieren       | **Ja**                                            |
| Entfernter Server       | IP des Ziel-Brokers (z. B. Cerbo/Mosquitto)       |
| Entfernter Port         | Port des Ziel-Brokers (Standard `1883`)           |
| Benutzername / Passwort | Login des Ziel-Brokers (falls gesetzt)            |
| Präfix                  | `openWB/`                                         |
| Client ID               | z. B. `openWB`                                     |
| MQTT Protokoll          | `v3.1.1`                                           |
| **Alle Statusdaten**    | **An**  ← wichtig, sonst kommen keine Ladepunkt-Werte |
| Datenserien für Diagramme | Aus (optional)                                  |
| Fernkonfiguration ermöglichen | nur **An**, wenn die Steuerung (Variante mit Set-Topics) über diesen Broker laufen soll |

Danach im Web-Interface als **openWB IP** die Adresse dieses **Ziel-Brokers**
eintragen (nicht die der openWB).

> ⚠️ Die openWB warnt zu Recht: Eine Brücke gibt alle weitergeleiteten Daten an
> jeden frei, der Zugriff auf den Ziel-Broker hat. Für Brücken zu externen
> Servern TLS + Login verwenden. Im rein lokalen Heimnetz (openWB → Cerbo) ist
> das meist unkritisch.

## Datenzuordnung (openWB 2.x → Venus)

| Venus-D-Bus-Pfad        | openWB-2-Topic                                    |
|-------------------------|---------------------------------------------------|
| `/Ac/Power`             | `chargepoint/<id>/get/power`                       |
| `/Ac/L{1,2,3}/Power`    | `get/powers` (bzw. `currents` × `voltages`)        |
| `/Ac/Energy/Forward`    | `get/imported` (Wh → kWh)                           |
| `/Ac/Voltage`           | `get/voltages` (Mittelwert)                         |
| `/Current`              | `get/currents` (Maximum)                            |
| `/SetCurrent`           | `get/evse_current`                                  |
| `/NrOfPhases`           | `get/phases_in_use`                                 |
| `/Ac/Frequency`         | `get/frequency`                                     |
| `/Soc`                  | `get/connected_vehicle/soc` → `soc`                 |
| `/Status`               | `get/plug_state` + `get/charge_state`               |
| `/Mode`                 | `get/connected_vehicle/config` → `chargemode`       |

## Steuerung (Venus → openWB)

Nur aktiv, wenn im Web-Interface **„Steuerung erlauben"** gesetzt ist
(`[CONTROL] enabled = 1`). Verwendete Set-Topics:

| Venus-Aktion       | openWB-2-Topic                                                                   |
|--------------------|----------------------------------------------------------------------------------|
| Start / Stop       | `set/vehicle/template/charge_template/<tpl>/chargemode/selected` = `instant_charging` / `stop` |
| Ladestrom setzen   | `set/vehicle/template/charge_template/<tpl>/chargemode/instant_charging/current`  |
| Modus              | `.../chargemode/selected` = `instant_charging` / `pv_charging` / `scheduled_charging` |

Die `charge_template`-ID (`<tpl>`) wird automatisch aus
`get/connected_vehicle/config` erkannt (im Web-Interface auf `0` = auto lassen).

## Name der Ladestation (EVCS-Kachel)

Der Gerätename wird im Web-Interface unter **„Gerätename"** gesetzt
(`[DEFAULT] device_name`) und landet auf dem D-Bus als `/CustomName`.

Wichtig zu wissen: Auf der **Übersichts-Kachel** (Dashboard/„Zuhause") zeigt
Victrons GUI **fest den Text „EVCS"** an – dieser Titel ist in `gui-v2`
hardcodiert (`components/widgets/EvcsWidget.qml`) und lässt sich vom Treiber aus
**nicht** ändern; das gilt für jede Ladestation, auch Victrons eigene.

Der konfigurierte Name (z. B. `openWB`) erscheint dafür an allen Stellen, die
`device.name` verwenden:

- in der **EVCS-Steuerkarte** (Kachel antippen)
- in **Einstellungen → Geräteliste**
- auf der **Gerätedetailseite**

Dort kann der Name auch direkt in der GX-Oberfläche geändert werden
(**Geräteliste → Ladestation → Name**); Venus speichert das dauerhaft.

## Betrieb

```bash
# Treiber-Log
tail -f /data/log/dbus-openwb2/current | tai64nlocal
# Web-Log
tail -f /data/log/dbus-openwb2-web/current | tai64nlocal
# Status
svstat /service/dbus-openwb2 /service/dbus-openwb2-web
# Neustart
bash /data/etc/dbus-openwb2/restart.sh
```

## Deinstallation

```bash
bash /data/etc/dbus-openwb2/uninstall.sh
```

## Hinweise / Haftung

Privates Projekt, keine Gewähr. Die openWB-2-Set-Topics können sich je nach
Version unterscheiden – die Steuerung ist bewusst standardmäßig **aus** und
sollte nach dem ersten Livetest kontrolliert werden. Die VRM-Instanz (`53`)
muss eindeutig sein; bei Konflikt eine andere Zahl wählen.

## Credits

- [gvzdus/dbus-mqtt-openwb](https://github.com/gvzdus/dbus-mqtt-openwb) – Vorlage (openWB 1.9)
- [mr-manuel/venus-os_dbus-mqtt-*](https://github.com/mr-manuel) – ursprüngliche MQTT-Treiber
- [Victron Energy](https://github.com/victronenergy/velib_python) – `velib_python`
- openWB-2-Topic-Referenz aus [a529987659852/openwb2mqtt](https://github.com/a529987659852/openwb2mqtt)

## Beitragen

Pull Requests willkommen – insbesondere getestete openWB-2-Set-Topics für die
Steuerung über verschiedene openWB-Versionen hinweg. Siehe [CONTRIBUTING.md](CONTRIBUTING.md).
