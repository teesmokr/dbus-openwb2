# dbus-openwb2

Integriert eine **openWB 2.x** (software2) Wallbox als Ladestation
(`com.victronenergy.evcharger`) in **Venus OS** – inklusive **dediziertem
Web-Interface** zur Konfiguration und **optionaler Steuerung** aus Venus OS.

Inspiriert von [gvzdus/dbus-mqtt-openwb](https://github.com/gvzdus/dbus-mqtt-openwb)
(nur openWB 1.9), neu geschrieben für die openWB-2-Topic-Struktur.

---

## Was es kann

- **Anzeige** der openWB in Venus-GUI und VRM: Leistung (gesamt + pro Phase),
  Energie, Ladestrom, Status (getrennt/verbunden/lädt), Phasenzahl.
- **Web-Interface** unter `http://<venus-ip>:8088`:
  - openWB live **scannen** → erkennt Ladepunkte und Live-Werte automatisch
  - Broker, Ladepunkt-ID, Name, VRM-Instanz, Max-Strom, Position einstellen
  - **Speichern** schreibt `config.ini` und startet den Treiber neu
- **Steuerung (optional, abschaltbar)**: Start/Stop, Ladestrom und Lademodus
  aus Venus OS zurück in die openWB (Modus *Sofortladen* / *Stop* / *PV*).

## Voraussetzungen

- Venus-OS-Gerät (getestet gedacht für Cerbo GX) mit **root/SSH-Zugang**
- openWB **2.x** mit aktivem **MQTT-Broker** (Standard: Port 1883, Root-Topic `openWB`)

## Schnellinstallation (Endnutzer)

Per SSH auf dem Venus-OS-Gerät (`GITHUBUSER` durch den echten Namen ersetzen):

```bash
cd /tmp
wget -O dbus-openwb2.zip https://github.com/GITHUBUSER/dbus-openwb2/archive/refs/heads/main.zip
unzip -o dbus-openwb2.zip
rm -rf /data/etc/dbus-openwb2
cp -R dbus-openwb2-main /data/etc/dbus-openwb2
bash /data/etc/dbus-openwb2/install.sh
```

Danach **`http://<venus-ip>:8088`** im Browser öffnen und konfigurieren.

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
Steuerung über verschiedene openWB-Versionen hinweg.
