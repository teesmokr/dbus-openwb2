#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
dbus-openwb2  --  Integriert eine openWB 2.x (software2) Wallbox als
com.victronenergy.evcharger in Venus OS.

- Liest die openWB-Daten ueber MQTT (Broker laeuft auf der openWB, Port 1883).
- Published sie auf den D-Bus, damit die Wallbox in der Venus-GUI und im VRM
  als Ladestation erscheint.
- Unterstuetzt mehrere Ladepunkte (ein evcharger-Service je Ladepunkt).
- Optionale Steuerung (Start/Stop, Ladestrom, Modus) zurueck in die openWB,
  abschaltbar per config.ini ([CONTROL] enabled = 0/1).
- Schreibt eine status.json fuer die Live-Anzeige im Web-Interface.

Konfiguration bequem ueber das mitgelieferte Web-Interface (webconfig.py).
"""

import os
import sys
import json
import logging
import platform
import configparser
from time import time, sleep
from functools import partial

from gi.repository import GLib  # pyright: ignore[reportMissingImports]

HERE = os.path.dirname(os.path.realpath(__file__))

# paho-mqtt: System bevorzugen, sonst die gebuendelte Kopie unter ext/ nutzen
try:
    import paho.mqtt.client as mqtt
except ImportError:
    sys.path.insert(1, os.path.join(HERE, "ext", "paho-mqtt"))
    import paho.mqtt.client as mqtt

sys.path.insert(1, os.path.join(HERE, "ext", "velib_python"))
from vedbus import VeDbusService  # noqa: E402


def make_mqtt_client(client_id):
    """Erzeugt einen paho-Client kompatibel zu paho-mqtt 1.x und 2.x."""
    try:
        from paho.mqtt.enums import CallbackAPIVersion  # paho >= 2.0
        return mqtt.Client(CallbackAPIVersion.VERSION1, client_id=client_id)
    except ImportError:
        return mqtt.Client(client_id)


def _status_dir():
    """Verzeichnis fuer status.json — bevorzugt ein tmpfs (RAM), um eMMC-Flash zu schonen."""
    for base in ("/run", "/var/volatile/run", "/var/volatile", "/tmp"):
        if os.path.isdir(base):
            d = os.path.join(base, "dbus-openwb2")
            try:
                os.makedirs(d, exist_ok=True)
                return d
            except OSError:
                continue
    return HERE


# --------------------------------------------------------------------------
# Konfiguration laden
# --------------------------------------------------------------------------
CONFIG_FILE = os.path.join(HERE, "config.ini")
STATUS_FILE = os.path.join(_status_dir(), "status.json")


def _fatal(msg):
    print("ERROR: %s Neustart in 60 s." % msg)
    sleep(60)
    sys.exit(1)


def load_config():
    if not os.path.exists(CONFIG_FILE):
        _fatal("config.ini nicht gefunden. config.sample.ini kopieren oder das Web-Interface verwenden.")
    cfg = configparser.ConfigParser()
    try:
        cfg.read(CONFIG_FILE)
        broker = cfg["MQTT"]["broker_address"]
    except (configparser.Error, KeyError) as e:
        _fatal("config.ini ist defekt oder unvollstaendig (%s)." % e)
    if broker in ("", "IP_ADDR_OR_FQDN"):
        _fatal("Broker-Adresse ist noch nicht konfiguriert.")
    return cfg


config = load_config()

_levels = {"ERROR": logging.ERROR, "WARNING": logging.WARNING,
           "INFO": logging.INFO, "DEBUG": logging.DEBUG}
logging.basicConfig(
    level=_levels.get(config["DEFAULT"].get("logging", "WARNING"), logging.WARNING),
    format="%(asctime)s %(levelname)s %(message)s",
)
log = logging.getLogger("dbus-openwb2")


# --------------------------------------------------------------------------
# Konfig-Werte
# --------------------------------------------------------------------------
try:
    MQTT_ROOT      = config["MQTT"].get("mqtt_root", "openWB").rstrip("/")
    BROKER_ADDR    = config["MQTT"]["broker_address"]
    BROKER_PORT    = int(config["MQTT"].get("broker_port", "1883"))
    MQTT_USER      = config["MQTT"].get("username", "") or None
    MQTT_PASS      = config["MQTT"].get("password", "") or None
    TLS_ENABLED    = config["MQTT"].get("tls_enabled", "0") == "1"

    # Ladepunkt-IDs koennen als Liste angegeben werden: "1" oder "1,2"
    CP_IDS = [int(x) for x in str(config["WALLBOX"].get("chargepoint_id", "1")).replace(" ", "").split(",") if x]
    MAX_CURRENT    = int(config["WALLBOX"].get("max_current", "16"))
    POSITION       = int(config["WALLBOX"].get("position", "1"))
    NOM_VOLTAGE    = float(config["WALLBOX"].get("nominal_voltage", "230"))

    DEVICE_NAME    = config["DEFAULT"].get("device_name", "openWB")
    DEVICE_INST    = int(config["DEFAULT"].get("device_instance", "53"))
    TIMEOUT        = int(config["DEFAULT"].get("timeout", "60"))

    CONTROL_ENABLED = config["CONTROL"].get("enabled", "0") == "1" \
        if config.has_section("CONTROL") else False
    CFG_TEMPLATE_ID = int(config["CONTROL"].get("charge_template_id", "0")) \
        if config.has_section("CONTROL") else 0
except (ValueError, KeyError) as e:
    _fatal("config.ini enthaelt ungueltige Werte (%s)." % e)

if not CP_IDS:
    _fatal("Keine gueltige Ladepunkt-ID in [WALLBOX] chargepoint_id.")


# openWB chargemode  ->  Venus /Mode  (0=Manuell, 1=Auto, 2=Zeitplan)
CHARGEMODE_TO_MODE = {
    "instant_charging": 0, "stop": 0, "standby": 0,
    "pv_charging": 1, "eco_charging": 1,
    "scheduled_charging": 2, "time_charging": 2,
}

# formatting callbacks
_kwh = lambda p, v: "%.2f kWh" % v
_a   = lambda p, v: "%.1f A" % v
_w   = lambda p, v: "%.0f W" % v
_v   = lambda p, v: "%.1f V" % v
_hz  = lambda p, v: "%.1f Hz" % v
_pct = lambda p, v: "%.0f %%" % v
_s   = lambda p, v: "%d s" % v
_t   = lambda p, v: str(v)


# --------------------------------------------------------------------------
# Ein Ladepunkt = ein D-Bus evcharger-Service
# --------------------------------------------------------------------------
class ChargePoint:
    def __init__(self, cp_id, instance, name, client):
        self.id = cp_id
        self.instance = instance
        self.name = name
        self.client = client

        self.power = 0.0
        self.imported = 0.0
        self.currents = [0.0, 0.0, 0.0]
        self.voltages = [NOM_VOLTAGE] * 3
        self.powers = None
        self.phases = 1
        self.evse_current = 0.0
        self.plug = 0
        self.charge = 0
        self.frequency = 0.0
        self.chargemode = "stop"
        self.template_id = CFG_TEMPLATE_ID
        self.soc = None
        # Ladesitzung (seit Anstecken): Basiswerte fuer Session/Energy und /Time
        self.session_base_imported = None
        self.session_base_time = None
        self.last_msg = 0.0

        self.base = "%s/chargepoint/%d" % (MQTT_ROOT, cp_id)
        self.topics = {
            self.base + "/get/power":                     self._t_power,
            self.base + "/get/imported":                  self._t_imported,
            self.base + "/get/currents":                  self._t_currents,
            self.base + "/get/voltages":                  self._t_voltages,
            self.base + "/get/powers":                    self._t_powers,
            self.base + "/get/phases_in_use":             self._t_phases,
            self.base + "/get/evse_current":              self._t_evse,
            self.base + "/get/frequency":                 self._t_freq,
            self.base + "/get/plug_state":                self._t_plug,
            self.base + "/get/charge_state":              self._t_charge,
            self.base + "/get/connected_vehicle/config":  self._t_vehcfg,
            self.base + "/get/connected_vehicle/soc":     self._t_soc,
        }
        self.svc = self._build_service()

    # ---- Service-Aufbau ----
    def _build_service(self):
        sn = "com.victronenergy.evcharger.openwb2_%d" % self.instance
        svc = VeDbusService(sn)
        svc.add_path("/Mgmt/ProcessName", __file__)
        svc.add_path("/Mgmt/ProcessVersion", "1.5.0 auf Python " + platform.python_version())
        svc.add_path("/Mgmt/Connection", "MQTT openWB2 %s:%d" % (BROKER_ADDR, BROKER_PORT))
        svc.add_path("/DeviceInstance", self.instance)
        svc.add_path("/ProductId", 0xC024)
        svc.add_path("/ProductName", "openWB 2.x")
        svc.add_path("/CustomName", self.name, writeable=True)
        svc.add_path("/FirmwareVersion", "1.5")
        svc.add_path("/HardwareVersion", 2)
        svc.add_path("/Serial", "openwb2-cp%d" % self.id)
        svc.add_path("/Connected", 1)
        svc.add_path("/UpdateIndex", 0)
        svc.add_path("/Status", 0)

        cb = partial(_on_change, self)
        paths = {
            "/Ac/Power":          {"i": 0, "f": _w,   "w": False},
            "/Ac/L1/Power":       {"i": 0, "f": _w,   "w": False},
            "/Ac/L2/Power":       {"i": 0, "f": _w,   "w": False},
            "/Ac/L3/Power":       {"i": 0, "f": _w,   "w": False},
            "/Ac/Energy/Forward": {"i": 0, "f": _kwh, "w": False},
            "/Ac/Voltage":        {"i": 0, "f": _v,   "w": False},
            "/Ac/Frequency":      {"i": 0, "f": _hz,  "w": False},
            "/Current":           {"i": 0, "f": _a,   "w": False},
            "/ChargingTime":      {"i": 0, "f": _s,   "w": False},
            "/Session/Energy":    {"i": 0, "f": _kwh, "w": False},
            "/Session/Time":      {"i": 0, "f": _s,   "w": False},
            "/NrOfPhases":        {"i": 1, "f": _t,   "w": False},
            "/Soc":               {"i": 0, "f": _pct, "w": False},
            "/Position":          {"i": POSITION,    "f": _t, "w": True},
            "/MaxCurrent":        {"i": MAX_CURRENT, "f": _a, "w": True},
            "/SetCurrent":        {"i": 0, "f": _a,   "w": True},
            "/Mode":              {"i": 0, "f": _t,   "w": True},
            "/StartStop":         {"i": 0, "f": _t,   "w": True},
        }
        for path, s in paths.items():
            svc.add_path(path, s["i"], gettextcallback=s["f"], writeable=s["w"],
                         onchangecallback=(cb if s["w"] else None))
        return svc

    # ---- Message-Handler ----
    def handle(self, topic, payload):
        fn = self.topics.get(topic)
        if fn:
            self.last_msg = time()
            fn(payload)

    def _t_power(self, p):
        self.power = _f(p)
        self.svc["/Ac/Power"] = round(self.power, 1)
        self._phase_powers()

    def _t_imported(self, p):
        self.imported = _f(p)
        self.svc["/Ac/Energy/Forward"] = round(self.imported / 1000.0, 3)
        self._update_session()

    def _t_currents(self, p):
        a = _arr(p)
        if a and len(a) >= 3:
            self.currents = a[:3]
            self.svc["/Current"] = round(max(a[:3]), 1)
            self._phase_powers()

    def _t_voltages(self, p):
        a = _arr(p)
        if a and len(a) >= 3:
            self.voltages = a[:3]
            self.svc["/Ac/Voltage"] = round(sum(a[:3]) / 3.0, 1)

    def _t_powers(self, p):
        a = _arr(p)
        if a and len(a) >= 3:
            self.powers = a[:3]
            self._phase_powers()

    def _t_phases(self, p):
        self.phases = max(1, int(_f(p, 1)))
        self.svc["/NrOfPhases"] = self.phases

    def _t_evse(self, p):
        self.evse_current = _f(p)
        self.svc["/SetCurrent"] = round(self.evse_current, 1)

    def _t_freq(self, p):
        self.frequency = _f(p)
        self.svc["/Ac/Frequency"] = round(self.frequency, 1)

    def _t_plug(self, p):
        self.plug = _bool(p)
        self._status()

    def _t_charge(self, p):
        self.charge = _bool(p)
        self._status()

    def _t_vehcfg(self, p):
        try:
            cfg = json.loads(p)
        except (ValueError, TypeError):
            return
        if isinstance(cfg, dict):
            if CFG_TEMPLATE_ID == 0 and "charge_template" in cfg:
                self.template_id = int(cfg["charge_template"])
            mode = cfg.get("chargemode")
            if mode:
                self.chargemode = mode
                self.svc["/Mode"] = CHARGEMODE_TO_MODE.get(mode, 0)

    def _t_soc(self, p):
        # openWB sendet je nach Version JSON ({"soc": 42, ...}) oder eine Zahl
        txt = p.decode("utf-8", "ignore").strip()
        val = None
        try:
            j = json.loads(txt)
            if isinstance(j, dict):
                val = j.get("soc")
            elif isinstance(j, (int, float)):
                val = j
        except (ValueError, TypeError):
            try:
                val = float(txt)
            except ValueError:
                val = None
        if val is not None:
            self.soc = float(val)
            self.svc["/Soc"] = round(self.soc, 0)
            log.debug("LP%d SoC=%s", self.id, self.soc)

    def _phase_powers(self):
        pw = self.powers if (self.powers and len(self.powers) >= 3) \
            else [self.currents[i] * self.voltages[i] for i in range(3)]
        self.svc["/Ac/L1/Power"] = round(pw[0], 1)
        self.svc["/Ac/L2/Power"] = round(pw[1], 1)
        self.svc["/Ac/L3/Power"] = round(pw[2], 1)

    def _status(self):
        # Venus EVCS /Status: 0=getrennt, 1=verbunden, 2=laedt, 4=Warte auf Sonne
        if not self.plug:
            st = 0
        elif self.charge:
            st = 2
        elif self.chargemode in ("pv_charging", "eco_charging"):
            st = 4  # Waiting for sun (PV-Ueberschuss abwarten)
        else:
            st = 1
        self.svc["/Status"] = st
        self.svc["/StartStop"] = 1 if self.charge else 0
        self._update_session()

    def _update_session(self):
        """Sitzung = seit Anstecken. Speist /Session/Energy, /Session/Time, /ChargingTime."""
        if not self.plug:
            # getrennt -> keine Sitzung (Kachel zeigt "--")
            self.session_base_imported = None
            self.session_base_time = None
            self.svc["/Session/Energy"] = None
            self.svc["/Session/Time"] = None
            self.svc["/ChargingTime"] = 0
            return
        if self.session_base_imported is None:
            self.session_base_imported = self.imported
            self.session_base_time = time()
        kwh = max(0.0, self.imported - self.session_base_imported) / 1000.0
        secs = int(time() - self.session_base_time)
        self.svc["/Session/Energy"] = round(kwh, 3)
        self.svc["/Session/Time"] = secs
        self.svc["/ChargingTime"] = secs

    def tick(self):
        idx = (self.svc["/UpdateIndex"] + 1) % 256
        self.svc["/UpdateIndex"] = idx
        self._update_session()

    def snapshot(self):
        st = {0: "Getrennt", 1: "Verbunden", 2: "Laedt",
              4: "Warte auf Sonne"}.get(self.svc["/Status"], "?")
        sess = self.svc["/Session/Energy"]
        return {
            "id": self.id, "instance": self.instance, "name": self.name,
            "power": round(self.power, 1),
            "energy_kwh": round(self.imported / 1000.0, 2),
            "session_kwh": round(sess, 2) if sess is not None else None,
            "set_current": round(self.evse_current, 1),
            "phases": self.phases, "soc": self.soc,
            "status": st, "charging": bool(self.charge),
            "plugged": bool(self.plug), "chargemode": self.chargemode,
            "template_id": self.template_id,
            "age": round(time() - self.last_msg, 1) if self.last_msg else None,
        }

    # ---- Steuerung (Venus -> openWB) ----
    def publish_chargemode(self, mode):
        t = "%s/set/vehicle/template/charge_template/%d/chargemode/selected" % (MQTT_ROOT, self.template_id)
        self.client.publish(t, mode, qos=0, retain=False)
        log.info("STEUERUNG LP%d chargemode -> %s (tpl %d)", self.id, mode, self.template_id)

    def publish_current(self, amp):
        # auf sinnvollen Bereich begrenzen (6 A Minimum, Max aus Config)
        amp = max(6, min(int(round(float(amp))), MAX_CURRENT))
        t = "%s/set/vehicle/template/charge_template/%d/chargemode/instant_charging/current" % (MQTT_ROOT, self.template_id)
        self.client.publish(t, str(amp), qos=0, retain=False)
        log.info("STEUERUNG LP%d Sollstrom -> %d A (tpl %d)", self.id, amp, self.template_id)


# --------------------------------------------------------------------------
# Hilfsfunktionen / globale MQTT-Callbacks
# --------------------------------------------------------------------------
def _f(payload, default=0.0):
    try:
        return float(payload)
    except (ValueError, TypeError):
        return default


def _bool(payload):
    return 1 if payload.decode("utf-8", "ignore").strip().lower() in ("1", "true") else 0


def _arr(payload):
    try:
        a = json.loads(payload)
        return [float(x) for x in a] if isinstance(a, list) else None
    except (ValueError, TypeError):
        return None


chargepoints = {}   # cp_id -> ChargePoint
topic_index = {}    # topic -> ChargePoint
client = None
START_TS = 0.0      # Startzeitpunkt fuer den Watchdog


def _on_change(cp, path, value):
    if not CONTROL_ENABLED:
        log.warning("Steuerung deaktiviert - ignoriere %s=%s (LP%d)", path, value, cp.id)
        return True
    try:
        if path == "/StartStop":
            cp.publish_chargemode("instant_charging" if value == 1 else "stop")
        elif path == "/SetCurrent":
            cp.publish_current(value)
        elif path == "/MaxCurrent":
            pass  # nur lokales Limit, kein openWB-Ladebefehl
        elif path == "/Mode":
            cp.publish_chargemode({0: "instant_charging", 1: "pv_charging",
                                   2: "scheduled_charging"}.get(value, "instant_charging"))
    except Exception:  # noqa: BLE001
        et, eo, tb = sys.exc_info()
        log.error("Fehler in _on_change: %r (Zeile %s)", eo, tb.tb_lineno)
    return True


def on_connect(cli, userdata, flags, rc):
    if rc == 0:
        log.info("MQTT verbunden mit %s:%d", BROKER_ADDR, BROKER_PORT)
        cli.subscribe([(t, 0) for t in topic_index])
        log.info("Abonniert: %d Ladepunkt(e), %d Topics", len(chargepoints), len(topic_index))
    else:
        log.error("MQTT-Connect fehlgeschlagen, rc=%s", rc)


def on_disconnect(cli, userdata, rc):
    log.warning("MQTT getrennt (rc=%s), automatischer Reconnect laeuft.", rc)


def on_message(cli, userdata, msg):
    cp = topic_index.get(msg.topic)
    if cp is None:
        return
    try:
        cp.handle(msg.topic, msg.payload)
    except Exception:  # noqa: BLE001
        et, eo, tb = sys.exc_info()
        log.error("Fehler in on_message: %r (Zeile %s)", eo, tb.tb_lineno)


# --------------------------------------------------------------------------
# Periodische Aufgaben: UpdateIndex, Watchdog, status.json
# --------------------------------------------------------------------------
def write_status():
    try:
        data = {
            "updated": int(time()),
            "control_enabled": CONTROL_ENABLED,
            "broker": "%s:%d" % (BROKER_ADDR, BROKER_PORT),
            "chargepoints": [cp.snapshot() for cp in chargepoints.values()],
        }
        tmp = STATUS_FILE + ".tmp"
        with open(tmp, "w") as fh:
            json.dump(data, fh)
        os.replace(tmp, STATUS_FILE)
    except Exception as e:  # noqa: BLE001
        log.debug("status.json konnte nicht geschrieben werden: %s", e)


def periodic():
    try:
        newest = max((cp.last_msg for cp in chargepoints.values()), default=0)
        ref = newest if newest else START_TS
        if TIMEOUT != 0 and ref and (time() - ref) > TIMEOUT:
            log.error("Timeout: seit %d s keine MQTT-Nachricht. Beende (Neustart durch Dienst).", TIMEOUT)
            os._exit(1)  # zuverlaessiger Prozess-Abbruch aus dem GLib-Callback
        for cp in chargepoints.values():
            cp.tick()
        write_status()
    except Exception:  # noqa: BLE001
        et, eo, tb = sys.exc_info()
        log.error("Fehler in periodic(): %r (Zeile %s)", eo, tb.tb_lineno)
    return True  # Timer bleibt in jedem Fall aktiv


# --------------------------------------------------------------------------
# main
# --------------------------------------------------------------------------
def main():
    global client, START_TS
    START_TS = time()

    from dbus.mainloop.glib import DBusGMainLoop  # pyright: ignore[reportMissingImports]
    DBusGMainLoop(set_as_default=True)

    client = make_mqtt_client("dbus-openwb2-%d" % DEVICE_INST)
    client.on_connect = on_connect
    client.on_disconnect = on_disconnect
    client.on_message = on_message
    if MQTT_USER and MQTT_PASS:
        client.username_pw_set(MQTT_USER, MQTT_PASS)
    if TLS_ENABLED:
        client.tls_set(tls_version=2)

    # Ladepunkte anlegen (ein Service je ID, Instanz fortlaufend)
    for i, cp_id in enumerate(CP_IDS):
        inst = DEVICE_INST + i
        name = DEVICE_NAME if len(CP_IDS) == 1 else "%s LP%d" % (DEVICE_NAME, cp_id)
        cp = ChargePoint(cp_id, inst, name, client)
        chargepoints[cp_id] = cp
        for t in cp.topics:
            topic_index[t] = cp
        log.info("Ladepunkt %d -> Service openwb2_%d (%s)", cp_id, inst, name)

    log.info("Steuerung: %s", "AN" if CONTROL_ENABLED else "AUS")

    # connect_async + loop_start: paho verbindet selbststaendig mit Backoff und
    # blockiert/craeshet nicht, wenn die openWB beim Start (noch) nicht erreichbar ist
    try:
        client.reconnect_delay_set(min_delay=1, max_delay=60)
    except Exception:  # noqa: BLE001 (aeltere paho-Versionen)
        pass
    client.connect_async(BROKER_ADDR, BROKER_PORT, keepalive=60)
    client.loop_start()

    GLib.timeout_add_seconds(2, periodic)
    GLib.MainLoop().run()


if __name__ == "__main__":
    main()
