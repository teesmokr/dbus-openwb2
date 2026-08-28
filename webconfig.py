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
Alle Grafiken sind eingebettetes SVG -> keine externen Requests (offline-faehig).
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
    data = {sec: dict(vals) for sec, vals in DEFAULTS.items()}
    if os.path.exists(CONFIG_FILE):
        cfg.read(CONFIG_FILE)
        for sec in cfg.sections():
            data.setdefault(sec, {})
            data[sec].update(dict(cfg[sec]))
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
    def log_message(self, *a):
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
# HTML (inline, theme-aware, alle Grafiken als SVG eingebettet)
# --------------------------------------------------------------------------
PAGE = r"""<!doctype html>
<html lang="de">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>openWB 2.x - Venus OS</title>
<style>
  :root{
    --bg:#eef1f5; --card:#ffffff; --fg:#1c2430; --muted:#5b6774;
    --border:#e2e7ee; --accent:#1f6fb2; --accent2:#3aa93c;
    --accent-fg:#fff; --ok:#1a9e5f; --err:#d64545; --chip:#eef2f7;
    --shadow:0 6px 22px rgba(20,40,70,.08); --radius:14px;
    --owb:#3aa93c; --vic:#1f6fb2;
  }
  @media (prefers-color-scheme: dark){
    :root{ --bg:#0f141a; --card:#1a2029; --fg:#e6ebf1; --muted:#93a1b0;
      --border:#28313d; --accent:#4a90e2; --chip:#232b35;
      --shadow:0 6px 24px rgba(0,0,0,.35); --owb:#49c24b; --vic:#4a90e2; }
  }
  *{box-sizing:border-box}
  body{margin:0;background:var(--bg);color:var(--fg);
    font:15px/1.55 -apple-system,BlinkMacSystemFont,Segoe UI,Roboto,Helvetica,Arial,sans-serif}
  .wrap{max-width:840px;margin:0 auto;padding:0 16px 72px}

  /* ---------- Hero ---------- */
  .hero{margin:22px 0 8px;border-radius:var(--radius);overflow:hidden;
    box-shadow:var(--shadow);
    background:linear-gradient(120deg,var(--owb) 0%,#2f8f77 48%,var(--vic) 100%)}
  .hero .inner{padding:26px 24px 22px;color:#fff;text-align:center;
    background:rgba(6,20,32,.10)}
  .hero h1{margin:14px 0 2px;font-size:23px;font-weight:700;letter-spacing:.2px}
  .hero p{margin:0;opacity:.92;font-size:14px}
  .flow{width:100%;max-width:460px;height:110px;display:block;margin:0 auto}
  .brands{display:flex;justify-content:center;gap:10px;margin-top:14px;flex-wrap:wrap}
  .badge{display:inline-flex;align-items:center;gap:8px;background:rgba(255,255,255,.16);
    border:1px solid rgba(255,255,255,.28);padding:6px 12px;border-radius:999px;
    font-size:13px;font-weight:600;color:#fff;backdrop-filter:blur(2px)}
  .badge svg{display:block}

  /* ---------- Message ---------- */
  #msg{padding:11px 15px;border-radius:10px;margin:16px 0 4px;display:none;font-size:14px}
  .m-ok{background:rgba(26,158,95,.15);color:var(--ok)}
  .m-err{background:rgba(214,69,69,.15);color:var(--err)}
  .m-info{background:rgba(31,111,178,.14);color:var(--accent)}

  /* ---------- Cards ---------- */
  .card{background:var(--card);border:1px solid var(--border);border-radius:var(--radius);
    padding:20px 22px;margin-top:18px;box-shadow:var(--shadow)}
  .card h2{font-size:16px;margin:0 0 4px;display:flex;align-items:center;gap:11px}
  .card .hint{color:var(--muted);font-size:13px;margin:0 0 8px 39px}
  .ico{width:28px;height:28px;border-radius:8px;display:inline-flex;
    align-items:center;justify-content:center;flex:0 0 28px}
  .ico.g{background:rgba(58,169,60,.15);color:var(--owb)}
  .ico.b{background:rgba(31,111,178,.15);color:var(--vic)}
  .ico.o{background:rgba(217,138,0,.16);color:#d98a00}

  label{display:block;font-size:12.5px;color:var(--muted);margin:12px 0 5px;font-weight:500}
  input,select{width:100%;padding:10px 12px;border:1px solid var(--border);
    border-radius:9px;background:var(--bg);color:var(--fg);font-size:14px;transition:border-color .15s}
  input:focus,select:focus{outline:none;border-color:var(--accent);
    box-shadow:0 0 0 3px rgba(31,111,178,.15)}
  .row{display:flex;gap:14px;flex-wrap:wrap}
  .row>div{flex:1;min-width:150px}
  .switch{display:flex;align-items:center;gap:11px;margin:6px 0 2px}
  .switch input{width:20px;height:20px;accent-color:var(--accent2)}
  .switch label{margin:0;color:var(--fg);font-size:14px;font-weight:500}

  button{cursor:pointer;border:none;border-radius:10px;padding:11px 18px;
    font-size:14px;font-weight:600;transition:transform .05s,filter .15s}
  button:active{transform:translateY(1px)}
  .primary{background:linear-gradient(120deg,var(--accent2),var(--accent));color:#fff;
    box-shadow:0 4px 14px rgba(31,111,178,.28)}
  .primary:hover{filter:brightness(1.05)}
  .ghost{background:var(--chip);color:var(--fg)}
  .ghost:hover{filter:brightness(.97)}
  .bar{display:flex;gap:10px;flex-wrap:wrap;margin-top:20px}
  .note{font-size:13px;color:var(--muted);margin-top:10px}
  code{background:var(--chip);padding:1px 6px;border-radius:5px;font-size:12.5px}

  .cp{border:1px solid var(--border);border-radius:10px;padding:11px 13px;margin-top:9px;
    cursor:pointer;display:flex;justify-content:space-between;gap:10px;transition:.12s}
  .cp:hover{border-color:var(--accent);transform:translateY(-1px)}
  .cp.sel{border-color:var(--accent2);background:rgba(58,169,60,.08)}
  .cp small{color:var(--muted)}
  .status{font-size:12.5px;color:var(--muted);margin-top:12px;text-align:center}
  .foot{text-align:center;color:var(--muted);font-size:12px;margin-top:26px}
  .foot b{color:var(--fg)}
</style>
</head>
<body>
<div class="wrap">

  <div class="hero"><div class="inner">
    <svg class="flow" viewBox="0 0 460 110" xmlns="http://www.w3.org/2000/svg" aria-label="openWB zu Venus OS">
      <!-- openWB Wallbox -->
      <g transform="translate(40,18)">
        <rect x="0" y="0" width="58" height="80" rx="10" fill="#ffffff" opacity=".95"/>
        <rect x="10" y="10" width="38" height="26" rx="4" fill="#2f7d33"/>
        <circle cx="29" cy="23" r="7" fill="none" stroke="#eaffea" stroke-width="2.5"/>
        <rect x="14" y="46" width="30" height="6" rx="3" fill="#3aa93c"/>
        <rect x="14" y="57" width="22" height="6" rx="3" fill="#cfe9d0"/>
        <path d="M44 68 q12 0 12 12" fill="none" stroke="#3aa93c" stroke-width="3"/>
        <circle cx="56" cy="82" r="4" fill="#3aa93c"/>
      </g>
      <!-- Flusslinie mit wandernden Energie-Punkten -->
      <line x1="112" y1="58" x2="348" y2="58" stroke="#ffffff" stroke-opacity=".55" stroke-width="3" stroke-dasharray="2 8" stroke-linecap="round"/>
      <g fill="#ffffff">
        <circle r="4" cx="118" cy="58" opacity="0"><animate attributeName="cx" values="118;344" dur="2.2s" repeatCount="indefinite"/><animate attributeName="opacity" values="0;1;1;0" dur="2.2s" repeatCount="indefinite"/></circle>
        <circle r="4" cx="118" cy="58" opacity="0"><animate attributeName="cx" values="118;344" dur="2.2s" begin="0.73s" repeatCount="indefinite"/><animate attributeName="opacity" values="0;1;1;0" dur="2.2s" begin="0.73s" repeatCount="indefinite"/></circle>
        <circle r="4" cx="118" cy="58" opacity="0"><animate attributeName="cx" values="118;344" dur="2.2s" begin="1.46s" repeatCount="indefinite"/><animate attributeName="opacity" values="0;1;1;0" dur="2.2s" begin="1.46s" repeatCount="indefinite"/></circle>
      </g>
      <!-- Venus / Victron Energiespeicher -->
      <g transform="translate(360,20)">
        <rect x="0" y="6" width="60" height="70" rx="9" fill="#ffffff" opacity=".95"/>
        <rect x="22" y="0" width="16" height="9" rx="3" fill="#ffffff" opacity=".95"/>
        <path d="M33 16 L20 44 L30 44 L26 64 L42 34 L31 34 Z" fill="#1f6fb2"/>
      </g>
    </svg>
    <h1>openWB&nbsp;2.x&nbsp; &rarr; &nbsp;Venus&nbsp;OS</h1>
    <p>MQTT-Br&uuml;cke &amp; Konfiguration &middot; <code style="color:#fff;background:rgba(255,255,255,.18)">dbus-openwb2</code></p>
    <div class="brands">
      <span class="badge">
        <svg width="16" height="16" viewBox="0 0 16 16"><circle cx="8" cy="8" r="7" fill="#3aa93c"/><path d="M9 3 L5 9 H8 L7 13 L11 6 H8 Z" fill="#fff"/></svg>
        openWB
      </span>
      <span class="badge">
        <svg width="16" height="16" viewBox="0 0 16 16"><rect x="1" y="1" width="14" height="14" rx="3" fill="#1f6fb2"/><path d="M8 3 L4 9 H7 L6.5 13 L11 7 H8 Z" fill="#fff"/></svg>
        Victron&nbsp;Energy
      </span>
    </div>
  </div></div>

  <div id="msg"></div>

  <div class="card">
    <h2><span class="ico g">
      <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M4 20V6a2 2 0 0 1 2-2h6a2 2 0 0 1 2 2v14"/><path d="M2 20h14"/><path d="M14 9h2a2 2 0 0 1 2 2v5a1.5 1.5 0 0 0 3 0V9l-3-3"/></svg>
    </span>1 &middot; Verbindung zur openWB</h2>
    <p class="hint">MQTT-Broker der openWB (Port 1883) oder eines Ziel-Brokers einer MQTT-Br&uuml;cke.</p>
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
    <div class="bar"><button class="ghost" onclick="scan()">&#128246;&nbsp; openWB scannen</button></div>
    <div id="scanresult"></div>
  </div>

  <div class="card">
    <h2><span class="ico b">
      <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M18 3v4M6 3v4"/><rect x="4" y="7" width="16" height="14" rx="2"/><path d="M13 11l-3 4h4l-3 4"/></svg>
    </span>2 &middot; Ladepunkt &amp; Anzeige</h2>
    <div class="row">
      <div><label>Ladepunkt-ID</label><input id="chargepoint_id" value="1"></div>
      <div><label>Ger&auml;tename (in Venus)</label><input id="device_name" value="openWB"></div>
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
    <h2><span class="ico o">
      <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><line x1="4" y1="21" x2="4" y2="14"/><line x1="4" y1="10" x2="4" y2="3"/><line x1="12" y1="21" x2="12" y2="12"/><line x1="12" y1="8" x2="12" y2="3"/><line x1="20" y1="21" x2="20" y2="16"/><line x1="20" y1="12" x2="20" y2="3"/><line x1="1" y1="14" x2="7" y2="14"/><line x1="9" y1="8" x2="15" y2="8"/><line x1="17" y1="16" x2="23" y2="16"/></svg>
    </span>3 &middot; Steuerung (optional)</h2>
    <div class="switch">
      <input type="checkbox" id="control_enabled">
      <label for="control_enabled">Steuerung aus Venus OS erlauben (Start/Stop, Ladestrom, Modus)</label>
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
    <button class="primary" onclick="save()">&#128190;&nbsp; Speichern &amp; Treiber neu starten</button>
    <button class="ghost" onclick="restart()">&#8635;&nbsp; Nur neu starten</button>
  </div>
  <div class="status" id="drvstatus"></div>

  <div class="foot">
    <b>dbus-openwb2</b> &middot; openWB 2.x als <code>com.victronenergy.evcharger</code> in Venus OS<br>
    Marken &amp; Logos geh&ouml;ren ihren jeweiligen Eigent&uuml;mern.
  </div>
</div>

<script>
const $ = id => document.getElementById(id);
const FIELDS = {
  MQTT: ["broker_address","broker_port","mqtt_root","username","password"],
  WALLBOX: ["chargepoint_id","max_current","position"],
};
function msg(text, cls){ const m=$("msg"); m.textContent=text;
  m.className=cls; m.style.display="block"; window.scrollTo({top:0,behavior:"smooth"}); }

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
    let h="<p class='note'>Gefundene Ladepunkte (klicken zum &Uuml;bernehmen):</p>";
    for(const id of ids){ const d=cps[id];
      const plug=d.plug_state==="1"?"eingesteckt":"frei";
      const chg=d.charge_state==="1"?"l&auml;dt":"steht";
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
  msg("Ladepunkt "+id+" &uuml;bernommen.","m-info"); }
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
    $("drvstatus").textContent="Treiber-Status: "+r.driver; }catch(e){}
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
