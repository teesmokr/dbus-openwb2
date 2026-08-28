#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
webconfig.py  --  Dediziertes Web-Interface fuer dbus-openwb2.

Laeuft als eigener Dienst auf dem Venus-OS-Geraet und stellt unter
http://<venus-ip>:8088 eine Konfigurationsseite bereit:

  * MQTT-Broker der openWB eintragen und live testen ("Scan")
  * erkannte Ladepunkte inkl. Live-Werten auswaehlen
  * Geraetename, VRM-Instanz, Max-Strom, Position einstellen
  * Steuerung (Start/Stop, Ladestrom) an-/abschalten
  * Speichern -> schreibt config.ini und startet den Treiber-Dienst neu

Nur Python-Standardbibliothek + paho-mqtt (fuer den Scan).
"""

import os
import json
import time
import configparser
import threading
import subprocess
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import paho.mqtt.client as mqtt


def make_mqtt_client(client_id):
    """paho-mqtt 1.x/2.x kompatibel."""
    try:
        from paho.mqtt.enums import CallbackAPIVersion  # paho >= 2.0
        return mqtt.Client(CallbackAPIVersion.VERSION1, client_id=client_id)
    except ImportError:
        return mqtt.Client(client_id)


HERE = os.path.dirname(os.path.realpath(__file__))
CONFIG_FILE = os.path.join(HERE, "config.ini")
SAMPLE_FILE = os.path.join(HERE, "config.sample.ini")
DRIVER_SERVICE = "dbus-openwb2"

DEFAULTS = {
    "DEFAULT": {"logging": "WARNING", "device_name": "openWB",
                "device_instance": "53", "timeout": "60"},
    "WALLBOX": {"chargepoint_id": "1", "max_current": "16",
                "position": "1", "nominal_voltage": "230"},
    "CONTROL": {"enabled": "0", "charge_template_id": "0"},
    "WEB":     {"port": "8088"},
    "MQTT":    {"broker_address": "IP_ADDR_OR_FQDN", "broker_port": "1883",
                "username": "", "password": "", "tls_enabled": "0",
                "mqtt_root": "openWB"},
}


# --------------------------------------------------------------------------
# Config lesen / schreiben
# --------------------------------------------------------------------------
def read_config():
    cfg = configparser.ConfigParser()
    # Defaults vorbelegen
    data = {sec: dict(vals) for sec, vals in DEFAULTS.items()}
    if os.path.exists(CONFIG_FILE):
        cfg.read(CONFIG_FILE)
        for sec in cfg.sections():
            data.setdefault(sec, {})
            data[sec].update(dict(cfg[sec]))
        # configparser haelt [DEFAULT] separat
        if cfg.defaults():
            data["DEFAULT"].update(dict(cfg.defaults()))
    return data


def write_config(data):
    cfg = configparser.ConfigParser()
    for key, val in data.get("DEFAULT", {}).items():
        cfg["DEFAULT"][key] = str(val)
    for sec in ("WALLBOX", "CONTROL", "WEB", "MQTT"):
        cfg[sec] = {k: str(v) for k, v in data.get(sec, {}).items()}
    with open(CONFIG_FILE, "w") as fh:
        fh.write("; erzeugt vom dbus-openwb2 Web-Interface\n")
        cfg.write(fh)


# --------------------------------------------------------------------------
# openWB live scannen
# --------------------------------------------------------------------------
def scan_openwb(broker, port, user, password, root, duration=4.0):
    """Verbindet kurz mit dem openWB-Broker und sammelt Ladepunkte + Werte."""
    result = {"ok": False, "error": None, "chargepoints": {}}
    root = (root or "openWB").rstrip("/")
    found = {}

    def _on_connect(cli, u, flags, rc):
        if rc == 0:
            cli.subscribe(root + "/chargepoint/#", qos=0)
        else:
            result["error"] = "Connect rc=%s (Auth/Netzwerk pruefen)" % rc

    def _on_message(cli, u, msg):
        parts = msg.topic.split("/")
        # .../chargepoint/<id>/get/<...>
        try:
            i = parts.index("chargepoint")
            cp = parts[i + 1]
            if not cp.isdigit():
                return
        except (ValueError, IndexError):
            return
        sub = "/".join(parts[i + 2:])
        payload = msg.payload.decode("utf-8", "ignore")
        d = found.setdefault(cp, {})
        if sub in ("get/power", "get/imported", "get/plug_state",
                   "get/charge_state", "get/evse_current", "get/phases_in_use",
                   "get/currents", "get/connected_vehicle/config"):
            d[sub] = payload

    try:
        cli = make_mqtt_client("openwb2-webscan-%d" % int(time.time()))
        cli.on_connect = _on_connect
        cli.on_message = _on_message
        if user and password:
            cli.username_pw_set(user, password)
        cli.connect(broker, int(port), keepalive=10)
        cli.loop_start()
        time.sleep(duration)
        cli.loop_stop()
        cli.disconnect()
    except Exception as e:  # noqa: BLE001
        result["error"] = str(e)
        return result

    # aufbereiten
    for cp, d in found.items():
        tpl = None
        cfg_raw = d.get("get/connected_vehicle/config")
        if cfg_raw:
            try:
                tpl = json.loads(cfg_raw).get("charge_template")
            except (ValueError, TypeError):
                pass
        result["chargepoints"][cp] = {
            "power": d.get("get/power"),
            "imported": d.get("get/imported"),
            "plug_state": d.get("get/plug_state"),
            "charge_state": d.get("get/charge_state"),
            "evse_current": d.get("get/evse_current"),
            "phases_in_use": d.get("get/phases_in_use"),
            "charge_template_id": tpl,
        }
    result["ok"] = result["error"] is None
    if result["ok"] and not result["chargepoints"]:
        result["error"] = ("Verbunden, aber keine chargepoint-Topics empfangen. "
                           "Stimmt das Root-Topic (Standard 'openWB')?")
        result["ok"] = False
    return result


# --------------------------------------------------------------------------
# Dienst-Steuerung
# --------------------------------------------------------------------------
def restart_driver():
    try:
        subprocess.run(["svc", "-t", "/service/" + DRIVER_SERVICE],
                       timeout=10, check=False)
        return True, "Treiber-Dienst neu gestartet."
    except Exception as e:  # noqa: BLE001
        return False, "Konnte Dienst nicht neu starten: %s" % e


def driver_status():
    try:
        out = subprocess.check_output(["svstat", "/service/" + DRIVER_SERVICE],
                                      timeout=10).decode()
        return out.strip()
    except Exception as e:  # noqa: BLE001
        return "Status unbekannt: %s" % e


# --------------------------------------------------------------------------
# HTTP
# --------------------------------------------------------------------------
class Handler(BaseHTTPRequestHandler):
    def log_message(self, *a):  # ruhiger
        pass

    def _send(self, code, body, ctype="application/json"):
        data = body.encode("utf-8") if isinstance(body, str) else body
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def _json_body(self):
        length = int(self.headers.get("Content-Length", 0))
        raw = self.rfile.read(length) if length else b"{}"
        return json.loads(raw or b"{}")

    def do_GET(self):
        if self.path in ("/", "/index.html"):
            self._send(200, PAGE, "text/html; charset=utf-8")
        elif self.path == "/api/config":
            self._send(200, json.dumps(read_config()))
        elif self.path == "/api/status":
            self._send(200, json.dumps({"driver": driver_status()}))
        else:
            self._send(404, json.dumps({"error": "not found"}))

    def do_POST(self):
        try:
            if self.path == "/api/scan":
                b = self._json_body()
                res = scan_openwb(b.get("broker_address"), b.get("broker_port", 1883),
                                  b.get("username"), b.get("password"),
                                  b.get("mqtt_root", "openWB"))
                self._send(200, json.dumps(res))
            elif self.path == "/api/save":
                data = self._json_body()
                write_config(data)
                ok, msg = restart_driver()
                self._send(200, json.dumps({"ok": ok, "message": msg}))
            elif self.path == "/api/restart":
                ok, msg = restart_driver()
                self._send(200, json.dumps({"ok": ok, "message": msg}))
            else:
                self._send(404, json.dumps({"error": "not found"}))
        except Exception as e:  # noqa: BLE001
            self._send(500, json.dumps({"error": str(e)}))


# --------------------------------------------------------------------------
# HTML (inline, theme-aware, ohne externe Abhaengigkeiten)
# --------------------------------------------------------------------------
PAGE = r"""<!doctype html>
<html lang="de">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>openWB 2.x - Venus OS</title>
<style>
  :root{
    --bg:#f4f6f8; --card:#fff; --fg:#1c2430; --muted:#5b6774;
    --border:#dde3ea; --accent:#2b7de9; --accent-fg:#fff;
    --ok:#1a9e5f; --warn:#d98a00; --err:#d64545; --chip:#eef2f7;
  }
  @media (prefers-color-scheme: dark){
    :root{ --bg:#12161c; --card:#1b212a; --fg:#e6ebf1; --muted:#93a1b0;
      --border:#2a323d; --accent:#4a90e2; --chip:#232b35; }
  }
  *{box-sizing:border-box}
  body{margin:0;background:var(--bg);color:var(--fg);
    font:15px/1.5 -apple-system,Segoe UI,Roboto,Helvetica,Arial,sans-serif}
  .wrap{max-width:820px;margin:0 auto;padding:24px 16px 64px}
  h1{font-size:22px;margin:0 0 4px} .sub{color:var(--muted);margin:0 0 24px}
  .card{background:var(--card);border:1px solid var(--border);border-radius:12px;
    padding:20px;margin-bottom:18px}
  .card h2{font-size:16px;margin:0 0 14px}
  label{display:block;font-size:13px;color:var(--muted);margin:12px 0 4px}
  input,select{width:100%;padding:9px 11px;border:1px solid var(--border);
    border-radius:8px;background:var(--bg);color:var(--fg);font-size:14px}
  .row{display:flex;gap:14px;flex-wrap:wrap}
  .row>div{flex:1;min-width:150px}
  .switch{display:flex;align-items:center;gap:10px;margin-top:12px}
  .switch input{width:auto}
  button{cursor:pointer;border:none;border-radius:8px;padding:10px 16px;
    font-size:14px;font-weight:600}
  .primary{background:var(--accent);color:var(--accent-fg)}
  .ghost{background:var(--chip);color:var(--fg)}
  .bar{display:flex;gap:10px;flex-wrap:wrap;margin-top:8px}
  .note{font-size:13px;color:var(--muted);margin-top:8px}
  .cp{border:1px solid var(--border);border-radius:8px;padding:10px 12px;
    margin-top:8px;cursor:pointer;display:flex;justify-content:space-between;gap:10px}
  .cp:hover{border-color:var(--accent)}
  .cp.sel{border-color:var(--accent);background:var(--chip)}
  .cp small{color:var(--muted)}
  #msg{padding:10px 14px;border-radius:8px;margin-bottom:16px;display:none}
  .m-ok{background:rgba(26,158,95,.15);color:var(--ok)}
  .m-err{background:rgba(214,69,69,.15);color:var(--err)}
  .m-info{background:rgba(43,125,233,.12);color:var(--accent)}
  code{background:var(--chip);padding:1px 6px;border-radius:5px}
  .status{font-size:13px;color:var(--muted);margin-top:6px}
</style>
</head>
<body>
<div class="wrap">
  <h1>openWB 2.x &rarr; Venus OS</h1>
  <p class="sub">Konfiguration des <code>dbus-openwb2</code>-Treibers</p>
  <div id="msg"></div>

  <div class="card">
    <h2>1 &middot; Verbindung zur openWB</h2>
    <div class="row">
      <div><label>openWB IP / Hostname</label>
        <input id="broker_address" placeholder="192.168.1.50"></div>
      <div><label>MQTT-Port</label><input id="broker_port" value="1883"></div>
      <div><label>Root-Topic</label><input id="mqtt_root" value="openWB"></div>
    </div>
    <div class="row">
      <div><label>Benutzer (optional)</label><input id="username"></div>
      <div><label>Passwort (optional)</label><input id="password" type="password"></div>
    </div>
    <div class="bar">
      <button class="ghost" onclick="scan()">openWB scannen</button>
    </div>
    <div id="scanresult"></div>
  </div>

  <div class="card">
    <h2>2 &middot; Ladepunkt &amp; Anzeige</h2>
    <div class="row">
      <div><label>Ladepunkt-ID</label><input id="chargepoint_id" value="1"></div>
      <div><label>Geraetename (in Venus)</label><input id="device_name" value="openWB"></div>
    </div>
    <div class="row">
      <div><label>VRM-Instanz</label><input id="device_instance" value="53"></div>
      <div><label>Max. Strom (A)</label><input id="max_current" value="16"></div>
      <div><label>Position</label>
        <select id="position">
          <option value="1">AC-Eingang 1</option>
          <option value="2">AC-Eingang 2</option>
          <option value="0">AC-Ausgang</option>
        </select></div>
    </div>
  </div>

  <div class="card">
    <h2>3 &middot; Steuerung (optional)</h2>
    <div class="switch">
      <input type="checkbox" id="control_enabled">
      <label for="control_enabled" style="margin:0;color:var(--fg)">
        Steuerung aus Venus OS erlauben (Start/Stop, Ladestrom, Modus)</label>
    </div>
    <p class="note">Bei aktiver Steuerung setzt Venus in der openWB den Lademodus
      <code>Sofortladen</code> bzw. <code>Stop</code> und den Ladestrom.
      Die <code>charge_template</code>-ID wird automatisch erkannt (0 = auto).</p>
    <div class="row">
      <div><label>charge_template-ID (0 = automatisch)</label>
        <input id="charge_template_id" value="0"></div>
      <div><label>Log-Level</label>
        <select id="logging">
          <option>WARNING</option><option>INFO</option>
          <option>DEBUG</option><option>ERROR</option></select></div>
    </div>
  </div>

  <div class="bar">
    <button class="primary" onclick="save()">Speichern &amp; Treiber neu starten</button>
    <button class="ghost" onclick="restart()">Nur neu starten</button>
  </div>
  <div class="status" id="drvstatus"></div>
</div>

<script>
const $ = id => document.getElementById(id);
const FIELDS = {
  MQTT: ["broker_address","broker_port","mqtt_root","username","password"],
  WALLBOX: ["chargepoint_id","max_current","position"],
  DEFAULT: ["device_name","device_instance","logging"],
};
function msg(text, cls){ const m=$("msg"); m.textContent=text;
  m.className=cls; m.style.display="block"; }

async function load(){
  const c = await (await fetch("/api/config")).json();
  const g=(s,k,d)=>((c[s]||{})[k]!==undefined?c[s][k]:d);
  for(const k of FIELDS.MQTT) $(k).value=g("MQTT",k,"");
  for(const k of FIELDS.WALLBOX) $(k).value=g("WALLBOX",k,"");
  $("device_name").value=g("DEFAULT","device_name","openWB");
  $("device_instance").value=g("DEFAULT","device_instance","53");
  $("logging").value=g("DEFAULT","logging","WARNING");
  $("control_enabled").checked=g("CONTROL","enabled","0")==="1";
  $("charge_template_id").value=g("CONTROL","charge_template_id","0");
  if($("broker_address").value==="IP_ADDR_OR_FQDN") $("broker_address").value="";
  status();
}
function collect(){
  return {
    DEFAULT:{logging:$("logging").value, device_name:$("device_name").value,
      device_instance:$("device_instance").value, timeout:"60"},
    WALLBOX:{chargepoint_id:$("chargepoint_id").value,
      max_current:$("max_current").value, position:$("position").value,
      nominal_voltage:"230"},
    CONTROL:{enabled:$("control_enabled").checked?"1":"0",
      charge_template_id:$("charge_template_id").value},
    WEB:{port:"8088"},
    MQTT:{broker_address:$("broker_address").value, broker_port:$("broker_port").value,
      username:$("username").value, password:$("password").value,
      tls_enabled:"0", mqtt_root:$("mqtt_root").value},
  };
}
async function scan(){
  msg("Scanne openWB (ca. 4 s) ...","m-info");
  const body={broker_address:$("broker_address").value,
    broker_port:$("broker_port").value, username:$("username").value,
    password:$("password").value, mqtt_root:$("mqtt_root").value};
  try{
    const r = await (await fetch("/api/scan",{method:"POST",
      body:JSON.stringify(body)})).json();
    if(!r.ok){ msg("Scan fehlgeschlagen: "+(r.error||"?"),"m-err");
      $("scanresult").innerHTML=""; return; }
    const cps=r.chargepoints; const ids=Object.keys(cps).sort();
    let h="<p class='note'>Gefundene Ladepunkte (klicken zum Uebernehmen):</p>";
    for(const id of ids){ const d=cps[id];
      const plug=d.plug_state==="1"?"eingesteckt":"frei";
      const chg=d.charge_state==="1"?"laedt":"steht";
      h+=`<div class="cp" onclick="pick('${id}','${d.charge_template_id}')">
        <div><b>Ladepunkt ${id}</b><br><small>${d.power||0} W &middot; ${plug} &middot; ${chg}
        &middot; Soll ${d.evse_current||0} A</small></div>
        <small>tpl ${d.charge_template_id ?? "?"}</small></div>`; }
    $("scanresult").innerHTML=h;
    msg("Scan ok: "+ids.length+" Ladepunkt(e) gefunden.","m-ok");
  }catch(e){ msg("Scan-Fehler: "+e,"m-err"); }
}
function pick(id,tpl){ $("chargepoint_id").value=id;
  if(tpl && tpl!=="null" && tpl!=="undefined") $("charge_template_id").value=tpl;
  document.querySelectorAll(".cp").forEach(e=>e.classList.remove("sel"));
  event.currentTarget.classList.add("sel");
  msg("Ladepunkt "+id+" uebernommen.","m-info"); }
async function save(){
  msg("Speichere ...","m-info");
  const r = await (await fetch("/api/save",{method:"POST",
    body:JSON.stringify(collect())})).json();
  msg(r.message||"Gespeichert.", r.ok?"m-ok":"m-err"); setTimeout(status,1500);
}
async function restart(){
  const r = await (await fetch("/api/restart",{method:"POST"})).json();
  msg(r.message, r.ok?"m-ok":"m-err"); setTimeout(status,1500);
}
async function status(){
  try{ const r=await (await fetch("/api/status")).json();
    $("drvstatus").textContent="Treiber: "+r.driver; }catch(e){}
}
load();
</script>
</body></html>
"""


def main():
    cfg = read_config()
    port = int(cfg.get("WEB", {}).get("port", "8088"))
    srv = ThreadingHTTPServer(("0.0.0.0", port), Handler)
    print("dbus-openwb2 Web-Interface auf Port %d" % port)
    srv.serve_forever()


if __name__ == "__main__":
    main()
