#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
dbus-openwb2  --  Integriert eine openWB 2.x (software2) Wallbox als
com.victronenergy.evcharger in Venus OS.

- Liest die openWB-Daten ueber MQTT (Broker laeuft auf der openWB, Port 1883).
- Published sie auf den D-Bus, damit die Wallbox in der Venus-GUI und im VRM
  als Ladestation erscheint.
- Optionale Steuerung (Start/Stop, Ladestrom, Modus) zurueck in die openWB,
  abschaltbar per config.ini ([CONTROL] enabled = 0/1).

Konfiguration bequem ueber das mitgelieferte Web-Interface (webconfig.py).

Abgeleitet von der Idee von gvzdus/dbus-mqtt-openwb (fuer openWB 1.9),
neu geschrieben fuer die openWB-2-Topic-Struktur.
"""

import os
import sys
import json
import logging
import platform
import configparser
from time import time, sleep

from gi.repository import GLib  # pyright: ignore[reportMissingImports]
import paho.mqtt.client as mqtt

sys.path.insert(1, os.path.join(os.path.dirname(__file__), "ext", "velib_python"))
from vedbus import VeDbusService  # noqa: E402


def make_mqtt_client(client_id):
    """Erzeugt einen paho-Client kompatibel zu paho-mqtt 1.x und 2.x."""
    try:
        from paho.mqtt.enums import CallbackAPIVersion  # paho >= 2.0
        return mqtt.Client(CallbackAPIVersion.VERSION1, client_id=client_id)
    except ImportError:
        return mqtt.Client(client_id)


# --------------------------------------------------------------------------
# Konfiguration laden
# --------------------------------------------------------------------------
CONFIG_FILE = os.path.join(os.path.dirname(os.path.realpath(__file__)), "config.ini")


def load_config():
    if not os.path.exists(CONFIG_FILE):
        print("ERROR: config.ini nicht gefunden. config.sample.ini kopieren "
              "oder das Web-Interface verwenden. Neustart in 60 s.")
        sleep(60)
        sys.exit(1)
    cfg = configparser.ConfigParser()
    cfg.read(CONFIG_FILE)
    if cfg["MQTT"]["broker_address"] in ("", "IP_ADDR_OR_FQDN"):
        print("ERROR: Broker-Adresse ist noch nicht konfiguriert. Neustart in 60 s.")
        sleep(60)
        sys.exit(1)
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
MQTT_ROOT      = config["MQTT"].get("mqtt_root", "openWB").rstrip("/")
BROKER_ADDR    = config["MQTT"]["broker_address"]
BROKER_PORT    = int(config["MQTT"].get("broker_port", "1883"))
MQTT_USER      = config["MQTT"].get("username", "") or None
MQTT_PASS      = config["MQTT"].get("password", "") or None
TLS_ENABLED    = config["MQTT"].get("tls_enabled", "0") == "1"

CP_ID          = int(config["WALLBOX"].get("chargepoint_id", "1"))
MAX_CURRENT    = int(config["WALLBOX"].get("max_current", "16"))
POSITION       = int(config["WALLBOX"].get("position", "1"))
NOM_VOLTAGE    = float(config["WALLBOX"].get("nominal_voltage", "230"))

DEVICE_NAME    = config["DEFAULT"].get("device_name", "openWB")
DEVICE_INST    = int(config["DEFAULT"].get("device_instance", "53"))
TIMEOUT        = int(config["DEFAULT"].get("timeout", "60"))

CONTROL_ENABLED = config["CONTROL"].get("enabled", "0") == "1" \
    if config.has_section("CONTROL") else False
# 0 = automatisch aus connected_vehicle/config erkennen
CFG_TEMPLATE_ID = int(config["CONTROL"].get("charge_template_id", "0")) \
    if config.has_section("CONTROL") else 0


# --------------------------------------------------------------------------
# openWB-2 Topics
# --------------------------------------------------------------------------
CP_BASE = "%s/chargepoint/%d" % (MQTT_ROOT, CP_ID)

T_POWER       = CP_BASE + "/get/power"
T_IMPORTED    = CP_BASE + "/get/imported"
T_CURRENTS    = CP_BASE + "/get/currents"
T_VOLTAGES    = CP_BASE + "/get/voltages"
T_POWERS      = CP_BASE + "/get/powers"
T_PHASES      = CP_BASE + "/get/phases_in_use"
T_EVSE_CUR    = CP_BASE + "/get/evse_current"
T_PLUG        = CP_BASE + "/get/plug_state"
T_CHARGE      = CP_BASE + "/get/charge_state"
T_FREQ        = CP_BASE + "/get/frequency"
T_VEH_CONFIG  = CP_BASE + "/get/connected_vehicle/config"
T_VEH_SOC     = CP_BASE + "/get/connected_vehicle/soc"

SUBSCRIPTIONS = [
    T_POWER, T_IMPORTED, T_CURRENTS, T_VOLTAGES, T_POWERS, T_PHASES,
    T_EVSE_CUR, T_PLUG, T_CHARGE, T_FREQ, T_VEH_CONFIG, T_VEH_SOC,
]

# openWB chargemode  ->  Venus /Mode  (0=Manuell, 1=Auto, 2=Zeitplan)
CHARGEMODE_TO_MODE = {
    "instant_charging": 0,
    "stop": 0,
    "standby": 0,
    "pv_charging": 1,
    "eco_charging": 1,
    "scheduled_charging": 2,
    "time_charging": 2,
}


# --------------------------------------------------------------------------
# Laufzeit-State
# --------------------------------------------------------------------------
class State:
    def __init__(self):
        self.power = 0.0
        self.imported = 0.0
        self.currents = [0.0, 0.0, 0.0]
        self.voltages = [NOM_VOLTAGE, NOM_VOLTAGE, NOM_VOLTAGE]
        self.powers = None
        self.phases = 1
        self.evse_current = 0.0
        self.plug = 0
        self.charge = 0
        self.frequency = 0.0
        self.chargemode = "stop"
        self.template_id = CFG_TEMPLATE_ID
        self.soc = None
        self.start_of_charge = None
        self.last_msg = 0.0


state = State()
client = None
dbus_service = None


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


# --------------------------------------------------------------------------
# MQTT
# --------------------------------------------------------------------------
def on_connect(cli, userdata, flags, rc):
    if rc == 0:
        log.info("MQTT verbunden mit %s:%d", BROKER_ADDR, BROKER_PORT)
        cli.subscribe([(t, 0) for t in SUBSCRIPTIONS])
        log.info("Abonniert: Ladepunkt %d (%d Topics)", CP_ID, len(SUBSCRIPTIONS))
    else:
        log.error("MQTT-Connect fehlgeschlagen, rc=%s", rc)


def on_disconnect(cli, userdata, rc):
    log.warning("MQTT getrennt (rc=%s), automatischer Reconnect laeuft.", rc)


def on_message(cli, userdata, msg):
    if dbus_service is None:
        return
    try:
        t = msg.topic
        p = msg.payload
        state.last_msg = time()

        if t == T_POWER:
            new = _f(p)
            if new > 1000 and state.power <= 1000:
                state.start_of_charge = time()
            elif new <= 1000:
                state.start_of_charge = None
            state.power = new
            dbus_service["/Ac/Power"] = round(new, 1)

        elif t == T_IMPORTED:
            state.imported = _f(p)
            dbus_service["/Ac/Energy/Forward"] = round(state.imported / 1000.0, 3)

        elif t == T_CURRENTS:
            a = _arr(p)
            if a and len(a) >= 3:
                state.currents = a[:3]
                dbus_service["/Current"] = round(max(a[:3]), 1)

        elif t == T_VOLTAGES:
            a = _arr(p)
            if a and len(a) >= 3:
                state.voltages = a[:3]
                dbus_service["/Ac/Voltage"] = round(sum(a[:3]) / 3.0, 1)

        elif t == T_POWERS:
            a = _arr(p)
            if a and len(a) >= 3:
                state.powers = a[:3]

        elif t == T_PHASES:
            state.phases = max(1, int(_f(p, 1)))
            dbus_service["/NrOfPhases"] = state.phases

        elif t == T_EVSE_CUR:
            state.evse_current = _f(p)
            dbus_service["/SetCurrent"] = round(state.evse_current, 1)

        elif t == T_FREQ:
            state.frequency = _f(p)

        elif t in (T_PLUG, T_CHARGE):
            if t == T_PLUG:
                state.plug = _bool(p)
            else:
                state.charge = _bool(p)
            _update_status()

        elif t == T_VEH_CONFIG:
            _parse_vehicle_config(p)

        elif t == T_VEH_SOC:
            _parse_soc(p)

        _update_phase_powers()

    except Exception:  # noqa: BLE001
        et, eo, tb = sys.exc_info()
        log.error("Fehler in on_message: %r (Zeile %s)", eo, tb.tb_lineno)


def _update_phase_powers():
    if state.powers and len(state.powers) >= 3:
        pw = state.powers
    else:
        pw = [state.currents[i] * state.voltages[i] for i in range(3)]
    dbus_service["/Ac/L1/Power"] = round(pw[0], 1)
    dbus_service["/Ac/L2/Power"] = round(pw[1], 1)
    dbus_service["/Ac/L3/Power"] = round(pw[2], 1)


def _update_status():
    # Venus EVCS /Status: 0=getrennt, 1=verbunden, 2=laedt
    if not state.plug:
        st = 0
    elif state.charge:
        st = 2
    else:
        st = 1
    dbus_service["/Status"] = st
    dbus_service["/StartStop"] = 1 if state.charge else 0
    if state.start_of_charge:
        dbus_service["/ChargingTime"] = int(time() - state.start_of_charge)
    else:
        dbus_service["/ChargingTime"] = 0


def _parse_vehicle_config(payload):
    try:
        cfg = json.loads(payload)
    except (ValueError, TypeError):
        return
    if isinstance(cfg, dict):
        if CFG_TEMPLATE_ID == 0 and "charge_template" in cfg:
            state.template_id = int(cfg["charge_template"])
        mode = cfg.get("chargemode")
        if mode:
            state.chargemode = mode
            dbus_service["/Mode"] = CHARGEMODE_TO_MODE.get(mode, 0)


def _parse_soc(payload):
    try:
        cfg = json.loads(payload)
        if isinstance(cfg, dict) and "soc" in cfg:
            state.soc = float(cfg["soc"])
    except (ValueError, TypeError):
        pass


# --------------------------------------------------------------------------
# Steuerung  (Venus -> openWB), nur wenn CONTROL_ENABLED
# --------------------------------------------------------------------------
def _publish_chargemode(mode):
    tpl = state.template_id
    topic = "%s/set/vehicle/template/charge_template/%d/chargemode/selected" % (MQTT_ROOT, tpl)
    client.publish(topic, mode, qos=0, retain=False)
    log.info("STEUERUNG chargemode -> %s (template %d)", mode, tpl)


def _publish_current(amp):
    tpl = state.template_id
    topic = "%s/set/vehicle/template/charge_template/%d/chargemode/instant_charging/current" % (MQTT_ROOT, tpl)
    client.publish(topic, str(int(amp)), qos=0, retain=False)
    log.info("STEUERUNG Sollstrom -> %d A (template %d)", int(amp), tpl)


def handle_changed_value(path, value):
    if not CONTROL_ENABLED:
        log.warning("Steuerung deaktiviert - ignoriere Aenderung %s=%s", path, value)
        return True
    if client is None:
        return True
    try:
        if path == "/StartStop":
            _publish_chargemode("instant_charging" if value == 1 else "stop")
        elif path == "/SetCurrent":
            _publish_current(value)
        elif path == "/MaxCurrent":
            _publish_current(value)
        elif path == "/Mode":
            if value == 0:
                _publish_chargemode("instant_charging")
            elif value == 1:
                _publish_chargemode("pv_charging")
            elif value == 2:
                _publish_chargemode("scheduled_charging")
    except Exception:  # noqa: BLE001
        et, eo, tb = sys.exc_info()
        log.error("Fehler in handle_changed_value: %r (Zeile %s)", eo, tb.tb_lineno)
    return True


# --------------------------------------------------------------------------
# D-Bus Service
# --------------------------------------------------------------------------
def build_service():
    _kwh = lambda p, v: "%.2f kWh" % v
    _a   = lambda p, v: "%.1f A" % v
    _w   = lambda p, v: "%.0f W" % v
    _v   = lambda p, v: "%.1f V" % v
    _s   = lambda p, v: "%d s" % v
    _t   = lambda p, v: str(v)

    servicename = "com.victronenergy.evcharger.openwb2_%d" % DEVICE_INST
    svc = VeDbusService(servicename)

    svc.add_path("/Mgmt/ProcessName", __file__)
    svc.add_path("/Mgmt/ProcessVersion",
                 "1.0 auf Python " + platform.python_version())
    svc.add_path("/Mgmt/Connection", "MQTT openWB2 %s:%d" % (BROKER_ADDR, BROKER_PORT))

    svc.add_path("/DeviceInstance", DEVICE_INST)
    svc.add_path("/ProductId", 0xC024)
    svc.add_path("/ProductName", "openWB 2.x")
    svc.add_path("/CustomName", DEVICE_NAME, writeable=True)
    svc.add_path("/FirmwareVersion", "1.0")
    svc.add_path("/HardwareVersion", 2)
    svc.add_path("/Serial", "openwb2-cp%d" % CP_ID)
    svc.add_path("/Connected", 1)
    svc.add_path("/UpdateIndex", 0)

    svc.add_path("/Status", 0)

    paths = {
        "/Ac/Power":            {"init": 0, "fmt": _w,   "w": False},
        "/Ac/L1/Power":         {"init": 0, "fmt": _w,   "w": False},
        "/Ac/L2/Power":         {"init": 0, "fmt": _w,   "w": False},
        "/Ac/L3/Power":         {"init": 0, "fmt": _w,   "w": False},
        "/Ac/Energy/Forward":   {"init": 0, "fmt": _kwh, "w": False},
        "/Ac/Voltage":          {"init": 0, "fmt": _v,   "w": False},
        "/Current":             {"init": 0, "fmt": _a,   "w": False},
        "/ChargingTime":        {"init": 0, "fmt": _s,   "w": False},
        "/NrOfPhases":          {"init": 1, "fmt": _t,   "w": False},
        "/Position":            {"init": POSITION,    "fmt": _t, "w": True},
        "/MaxCurrent":          {"init": MAX_CURRENT, "fmt": _a, "w": True},
        "/SetCurrent":          {"init": 0, "fmt": _a,   "w": True},
        "/Mode":                {"init": 0, "fmt": _t,   "w": True},
        "/StartStop":           {"init": 0, "fmt": _t,   "w": True},
    }
    for path, s in paths.items():
        svc.add_path(path, s["init"], gettextcallback=s["fmt"],
                     writeable=s["w"],
                     onchangecallback=handle_changed_value if s["w"] else None)
    return svc


# --------------------------------------------------------------------------
# Watchdog / UpdateIndex
# --------------------------------------------------------------------------
def periodic():
    if TIMEOUT != 0 and state.last_msg and (time() - state.last_msg) > TIMEOUT:
        log.error("Timeout: seit %d s keine MQTT-Nachricht. Beende (Neustart durch Dienst).",
                  TIMEOUT)
        sys.exit(1)
    idx = (dbus_service["/UpdateIndex"] + 1) % 256
    dbus_service["/UpdateIndex"] = idx
    if state.start_of_charge:
        dbus_service["/ChargingTime"] = int(time() - state.start_of_charge)
    return True


# --------------------------------------------------------------------------
# main
# --------------------------------------------------------------------------
def main():
    global client, dbus_service

    from dbus.mainloop.glib import DBusGMainLoop  # pyright: ignore[reportMissingImports]
    DBusGMainLoop(set_as_default=True)

    dbus_service = build_service()
    log.info("D-Bus Service angelegt: com.victronenergy.evcharger.openwb2_%d "
             "(Steuerung: %s)", DEVICE_INST, "AN" if CONTROL_ENABLED else "AUS")

    client = make_mqtt_client("dbus-openwb2-%d" % DEVICE_INST)
    client.on_connect = on_connect
    client.on_disconnect = on_disconnect
    client.on_message = on_message
    if MQTT_USER and MQTT_PASS:
        client.username_pw_set(MQTT_USER, MQTT_PASS)
    if TLS_ENABLED:
        client.tls_set(tls_version=2)

    client.connect(BROKER_ADDR, BROKER_PORT, keepalive=60)
    client.loop_start()

    GLib.timeout_add_seconds(2, periodic)
    GLib.MainLoop().run()


if __name__ == "__main__":
    main()
