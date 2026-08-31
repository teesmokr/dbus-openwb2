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
import hmac
import base64
import hashlib
import configparser
import subprocess
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import uuid

HERE = os.path.dirname(os.path.realpath(__file__))

# paho-mqtt: System bevorzugen, sonst die gebuendelte Kopie unter ext/ nutzen
try:
    import paho.mqtt.client as mqtt
except ImportError:
    import sys
    sys.path.insert(1, os.path.join(HERE, "ext", "paho-mqtt"))
    import paho.mqtt.client as mqtt


def make_mqtt_client(client_id):
    """paho-mqtt 1.x/2.x kompatibel."""
    try:
        from paho.mqtt.enums import CallbackAPIVersion  # paho >= 2.0
        return mqtt.Client(CallbackAPIVersion.VERSION1, client_id=client_id)
    except ImportError:
        return mqtt.Client(client_id)


def _status_dir():
    """Muss mit dbus-openwb2.py uebereinstimmen: status.json liegt im tmpfs."""
    for base in ("/run", "/var/volatile/run", "/var/volatile", "/tmp"):
        if os.path.isdir(base):
            d = os.path.join(base, "dbus-openwb2")
            try:
                os.makedirs(d, exist_ok=True)
                return d
            except OSError:
                continue
    return HERE


CONFIG_FILE = os.path.join(HERE, "config.ini")
SAMPLE_FILE = os.path.join(HERE, "config.sample.ini")
STATUS_FILE = os.path.join(_status_dir(), "status.json")
DRIVER_SERVICE = "dbus-openwb2"
LOG_FILE = "/var/log/dbus-openwb2/current"
MAX_BODY = 256 * 1024  # 256 KiB Obergrenze fuer POST-Bodies

DEFAULTS = {
    "DEFAULT": {"logging": "WARNING", "device_name": "openWB",
                "device_instance": "53", "timeout": "60"},
    "WALLBOX": {"chargepoint_id": "1", "max_current": "16",
                "position": "1", "nominal_voltage": "230"},
    "CONTROL": {"enabled": "0", "charge_template_id": "0"},
    "WEB":     {"port": "8088", "username": "admin", "password_hash": ""},
    "MQTT":    {"broker_address": "IP_ADDR_OR_FQDN", "broker_port": "1883",
                "username": "", "password": "", "tls_enabled": "0",
                "mqtt_root": "openWB", "api_mode": "internal"},
}


# --------------------------------------------------------------------------
# Config lesen / schreiben
# --------------------------------------------------------------------------
def read_config():
    cfg = configparser.ConfigParser()
    data = {sec: dict(vals) for sec, vals in DEFAULTS.items()}
    if os.path.exists(CONFIG_FILE):
        try:
            cfg.read(CONFIG_FILE)
        except configparser.Error:
            # defekte config.ini: mit Defaults weiterarbeiten, damit das
            # Web-Interface der Rettungsanker bleibt
            return data
        if cfg.defaults():
            data["DEFAULT"].update(dict(cfg.defaults()))
        # Nur die sektions-eigenen Optionen uebernehmen (nicht die aus [DEFAULT]
        # geerbten), sonst dupliziert der Merge-Save die DEFAULT-Keys in jede Sektion.
        for sec in cfg.sections():
            data.setdefault(sec, {})
            data[sec].update(dict(cfg._sections.get(sec, {})))
    return data


def write_config(data):
    cfg = configparser.ConfigParser()
    for key, val in data.get("DEFAULT", {}).items():
        cfg["DEFAULT"][key] = str(val)
    for sec in ("WALLBOX", "CONTROL", "WEB", "MQTT"):
        cfg[sec] = {k: str(v) for k, v in data.get(sec, {}).items()}
    # atomar schreiben: tmp + fsync + rename
    tmp = CONFIG_FILE + ".tmp"
    with open(tmp, "w") as fh:
        fh.write("; erzeugt vom dbus-openwb2 Web-Interface\n")
        cfg.write(fh)
        fh.flush()
        os.fsync(fh.fileno())
    os.replace(tmp, CONFIG_FILE)
    # Passwort-Hash + MQTT-Zugangsdaten nicht world-readable
    try:
        os.chmod(CONFIG_FILE, 0o600)
    except OSError:
        pass


# --------------------------------------------------------------------------
# Passwortschutz (optional, HTTP Basic Auth)
# --------------------------------------------------------------------------
def hash_pw(plain):
    return hashlib.sha256(plain.encode("utf-8")).hexdigest()


def auth_settings():
    web = read_config().get("WEB", {})
    return web.get("username", "admin"), web.get("password_hash", "")


def check_credentials(user, pw):
    cfg_user, cfg_hash = auth_settings()
    if not cfg_hash:
        return True  # Schutz nicht aktiv
    ok_user = hmac.compare_digest(user or "", cfg_user or "")
    ok_pw = hmac.compare_digest(hash_pw(pw or ""), cfg_hash)
    return ok_user and ok_pw


# --------------------------------------------------------------------------
# openWB live scannen
# --------------------------------------------------------------------------
def scan_openwb(broker, port, user, password, root, duration=4.0):
    """Verbindet kurz mit dem openWB-Broker und sammelt Ladepunkte + Werte.
    Erkennt sowohl die stabile SimpleAPI (openWB/simpleAPI/chargepoint/...) als
    auch die internen get-Topics (openWB/chargepoint/.../get/...)."""
    result = {"ok": False, "error": None, "chargepoints": {}, "simple_available": False}
    root = (root or "openWB").rstrip("/")
    found = {}  # cp -> {"s:<sub>"/"i:<sub>": payload}

    def _on_connect(cli, u, flags, rc):
        if rc == 0:
            cli.subscribe([(root + "/chargepoint/#", 0),
                           (root + "/simpleAPI/chargepoint/#", 0)])
        else:
            result["error"] = "Connect rc=%s (Auth/Netzwerk pruefen)" % rc

    def _on_message(cli, u, msg):
        parts = msg.topic.split("/")
        is_simple = "simpleAPI" in parts
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
        d[("s:" if is_simple else "i:") + sub] = payload
        if is_simple:
            result["simple_available"] = True

    try:
        cli = make_mqtt_client("openwb2-webscan-%s" % uuid.uuid4().hex[:8])
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
        def pick(*keys):
            for k in keys:
                if d.get(k) is not None:
                    return d[k]
            return None

        # SoC: SimpleAPI = Zahl, intern = JSON {"soc": ..}
        soc = pick("s:soc")
        if soc is None:
            raw = pick("i:get/connected_vehicle/soc")
            if raw:
                try:
                    j = json.loads(raw)
                    soc = j.get("soc") if isinstance(j, dict) else j
                except (ValueError, TypeError):
                    try:
                        soc = float(raw)
                    except ValueError:
                        soc = None
        # charge_template nur bei den internen Topics
        tpl = None
        cfg_raw = pick("i:get/connected_vehicle/config")
        if cfg_raw:
            try:
                tpl = json.loads(cfg_raw).get("charge_template")
            except (ValueError, TypeError):
                pass

        result["chargepoints"][cp] = {
            "power": pick("s:power", "i:get/power"),
            "imported": pick("s:imported", "i:get/imported"),
            "plug_state": pick("s:plug_state", "i:get/plug_state"),
            "charge_state": pick("s:charge_state", "i:get/charge_state"),
            "evse_current": pick("s:evse_current", "i:get/evse_current"),
            "phases_in_use": pick("s:phases_in_use", "i:get/phases_in_use"),
            "charge_template_id": tpl,
            "soc": soc,
            "source": "simple" if any(k.startswith("s:") for k in d) else "internal",
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


def read_status():
    """Live-Werte, die der Treiber in status.json schreibt."""
    try:
        with open(STATUS_FILE) as fh:
            data = json.load(fh)
        data["stale"] = (time.time() - data.get("updated", 0)) > 15
        return data
    except Exception:  # noqa: BLE001
        return {"chargepoints": [], "error": "noch keine Live-Daten "
                "(laeuft der Treiber und ist er verbunden?)"}


def read_log(lines=120):
    """Letzte Zeilen des Treiber-Logs (mit tai64nlocal, falls vorhanden)."""
    if not os.path.exists(LOG_FILE):
        return "Logdatei %s nicht gefunden (nur auf dem Venus-Geraet vorhanden)." % LOG_FILE
    try:
        raw = subprocess.check_output(["tail", "-n", str(lines), LOG_FILE], timeout=10)
        try:
            p = subprocess.run(["tai64nlocal"], input=raw, stdout=subprocess.PIPE, timeout=10)
            raw = p.stdout or raw
        except Exception:  # noqa: BLE001
            pass
        return raw.decode("utf-8", "ignore")
    except Exception as e:  # noqa: BLE001
        return "Log konnte nicht gelesen werden: %s" % e


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
        self.send_header("Cache-Control", "no-store, must-revalidate")
        self.end_headers()
        self.wfile.write(data)

    def _json_body(self):
        length = int(self.headers.get("Content-Length", 0) or 0)
        if length < 0 or length > MAX_BODY:
            raise ValueError("Request-Body zu gross")
        raw = self.rfile.read(length) if length else b"{}"
        return json.loads(raw or b"{}")

    def _csrf_ok(self):
        """Blockt Simple-Request-CSRF: verlangt JSON-Content-Type + eigenen
        Header und prueft, falls vorhanden, Origin gegen Host."""
        ctype = (self.headers.get("Content-Type") or "").split(";")[0].strip().lower()
        if ctype != "application/json":
            return False
        if (self.headers.get("X-Requested-With") or "") != "dbus-openwb2":
            return False
        origin = self.headers.get("Origin")
        if origin:
            from urllib.parse import urlparse
            try:
                if urlparse(origin).netloc != (self.headers.get("Host") or ""):
                    return False
            except Exception:  # noqa: BLE001
                return False
        return True

    def _authorized(self):
        """True, wenn kein Schutz aktiv oder Basic-Auth stimmt. Sonst 401."""
        auth = self.headers.get("Authorization", "")
        user = pw = ""
        if auth.startswith("Basic "):
            try:
                user, _, pw = base64.b64decode(auth[6:]).decode("utf-8").partition(":")
            except Exception:  # noqa: BLE001
                user = pw = ""
        if check_credentials(user, pw):
            return True
        self.send_response(401)
        self.send_header("WWW-Authenticate", 'Basic realm="dbus-openwb2"')
        self.send_header("Content-Type", "application/json")
        self.end_headers()
        self.wfile.write(b'{"error":"Anmeldung erforderlich"}')
        return False

    def do_GET(self):
        if not self._authorized():
            return
        if self.path in ("/", "/index.html"):
            self._send(200, PAGE, "text/html; charset=utf-8")
        elif self.path == "/api/config":
            cfg = read_config()
            web = cfg.get("WEB", {})
            web["protected"] = bool(web.pop("password_hash", ""))   # Hash nie ausliefern
            mq = cfg.get("MQTT", {})
            mq["password_set"] = bool(mq.pop("password", ""))       # MQTT-Passwort nie ausliefern
            self._send(200, json.dumps(cfg))
        elif self.path == "/api/status":
            self._send(200, json.dumps({"driver": driver_status()}))
        elif self.path == "/api/live":
            self._send(200, json.dumps(read_status()))
        elif self.path == "/api/log":
            self._send(200, read_log(), "text/plain; charset=utf-8")
        else:
            self._send(404, json.dumps({"error": "not found"}))

    def do_POST(self):
        if not self._authorized():
            return
        if not self._csrf_ok():
            self._send(403, json.dumps({"error": "CSRF-Schutz: ungueltige Anfrage"}))
            return
        try:
            if self.path == "/api/scan":
                b = self._json_body()
                cur_mqtt = read_config().get("MQTT", {})
                user = b.get("username") or cur_mqtt.get("username", "")
                pw = b.get("password") or cur_mqtt.get("password", "")
                res = scan_openwb(b.get("broker_address"), b.get("broker_port", 1883),
                                  user, pw, b.get("mqtt_root", "openWB"))
                self._send(200, json.dumps(res))
            elif self.path == "/api/save":
                data = self._json_body()
                cur = read_config()
                # Merge: bestehende Config als Basis, nur gelieferte Felder ueberschreiben
                # -> per SSH gepflegte Werte (tls_enabled, timeout, ...) bleiben erhalten
                merged = {sec: dict(cur.get(sec, {}))
                          for sec in ("DEFAULT", "WALLBOX", "CONTROL", "WEB", "MQTT")}
                for sec in ("DEFAULT", "WALLBOX", "CONTROL", "MQTT"):
                    for k, v in (data.get(sec) or {}).items():
                        merged[sec][k] = v

                # WEB: Passwort-Hash-Logik (nur bei neuem Passwort aendern)
                cur_web = cur.get("WEB", {})
                web_in = data.get("WEB", {})
                if web_in.get("disable"):
                    new_hash = ""
                elif web_in.get("new_password"):
                    new_hash = hash_pw(web_in["new_password"])
                else:
                    new_hash = cur_web.get("password_hash", "")
                merged["WEB"]["username"] = (web_in.get("username")
                                             or cur_web.get("username") or "admin").strip()
                merged["WEB"]["password_hash"] = new_hash
                merged["WEB"].pop("protected", None)

                # MQTT-Passwort: leeres Feld = unveraendert (wird nie ans UI geliefert)
                if not (merged["MQTT"].get("password") or "").strip():
                    merged["MQTT"]["password"] = cur.get("MQTT", {}).get("password", "")
                merged["MQTT"].pop("password_set", None)

                write_config(merged)
                ok, msg = restart_driver()
                if new_hash and not cur_web.get("password_hash"):
                    msg += " Passwortschutz aktiv – bitte Seite neu laden und anmelden."
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

  /* Live-Status */
  .lp{border:1px solid var(--border);border-radius:10px;padding:12px 14px;margin-top:10px}
  .lp .lphead{display:flex;justify-content:space-between;align-items:center;margin-bottom:10px}
  .lp .lpname{font-weight:600}
  .pill{font-size:11.5px;font-weight:700;padding:3px 9px;border-radius:999px}
  .p-charging{background:rgba(58,169,60,.16);color:var(--accent2)}
  .p-connected{background:rgba(31,111,178,.16);color:var(--accent)}
  .p-idle{background:var(--chip);color:var(--muted)}
  .metrics{display:grid;grid-template-columns:repeat(auto-fit,minmax(90px,1fr));gap:10px}
  .metric{background:var(--bg);border-radius:9px;padding:9px 11px}
  .metric .v{font-size:18px;font-weight:700;line-height:1.1}
  .metric .k{font-size:11px;color:var(--muted);margin-top:2px}
  .stale{color:var(--err);font-size:12px;margin-top:8px}
  .logbox{background:#0d1117;color:#c8d2dc;border-radius:9px;padding:12px;
    font:12px/1.5 ui-monospace,SFMono-Regular,Menlo,Consolas,monospace;
    max-height:320px;overflow:auto;white-space:pre-wrap;margin-top:10px}
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

  <div class="card" id="livecard" style="display:none">
    <h2><span class="ico g">
      <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M22 12h-4l-3 9L9 3l-3 9H2"/></svg>
    </span>Live-Status</h2>
    <div id="live"></div>
  </div>

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
      <div><label>API</label>
        <select id="api_mode">
          <option value="simple">SimpleAPI (empfohlen)</option>
          <option value="internal">interne Topics</option>
        </select></div>
    </div>
    <p class="note">Die <b>SimpleAPI</b> (<code>openWB/simpleAPI/…</code>) ist von openWB
      versionsstabil und muss in der openWB unter <i>Einstellungen → System → SimpleAPI</i>
      aktiviert sein. „interne Topics" funktionieren ohne Aktivierung, können sich aber
      je openWB-Version ändern. Der Scan erkennt automatisch, was verfügbar ist.</p>
    <div class="bar"><button class="ghost" onclick="scan()">&#128246;&nbsp; openWB scannen</button></div>
    <div id="scanresult"></div>
  </div>

  <div class="card">
    <h2><span class="ico b">
      <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M18 3v4M6 3v4"/><rect x="4" y="7" width="16" height="14" rx="2"/><path d="M13 11l-3 4h4l-3 4"/></svg>
    </span>2 &middot; Ladepunkt &amp; Anzeige</h2>
    <div class="row">
      <div><label>Ladepunkt-ID (mehrere kommagetrennt: 1,2)</label><input id="chargepoint_id" value="1"></div>
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

  <div class="card">
    <h2><span class="ico b">
      <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><rect x="3" y="11" width="18" height="11" rx="2"/><path d="M7 11V7a5 5 0 0 1 10 0v4"/></svg>
    </span>4 &middot; Sicherheit (Web-Zugang)</h2>
    <p class="hint" id="secstate">&nbsp;</p>
    <div class="row">
      <div><label>Benutzername</label><input id="web_username" value="admin"></div>
      <div><label>Neues Passwort (leer = unver&auml;ndert)</label>
        <input id="web_new_password" type="password" placeholder="&bull;&bull;&bull;&bull;&bull;&bull;"></div>
    </div>
    <div class="switch">
      <input type="checkbox" id="web_disable">
      <label for="web_disable">Passwortschutz deaktivieren</label>
    </div>
    <p class="note">Sch&uuml;tzt dieses Web-Interface per Login (HTTP Basic Auth).
      Das Passwort wird nur als SHA-256-Hash gespeichert.
      Passwort vergessen? Per SSH in <code>config.ini</code> unter
      <code>[WEB] password_hash</code> leeren.</p>
  </div>

  <div class="bar">
    <button class="primary" onclick="save()">&#128190;&nbsp; Speichern &amp; Treiber neu starten</button>
    <button class="ghost" onclick="restart()">&#8635;&nbsp; Nur neu starten</button>
  </div>
  <div class="status" id="drvstatus"></div>

  <div class="card">
    <h2><span class="ico o">
      <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M4 17l6-6-6-6"/><line x1="12" y1="19" x2="20" y2="19"/></svg>
    </span>Treiber-Log</h2>
    <div class="bar" style="margin-top:4px">
      <button class="ghost" onclick="loadLog()">Log laden</button>
      <button class="ghost" onclick="$('logbox').textContent=''">Leeren</button>
    </div>
    <pre id="logbox" class="logbox"></pre>
  </div>

  <div class="foot">
    <b>dbus-openwb2</b> &middot; openWB 2.x als <code>com.victronenergy.evcharger</code> in Venus OS<br>
    Marken &amp; Logos geh&ouml;ren ihren jeweiligen Eigent&uuml;mern.
  </div>
</div>

<script>
const $ = id => document.getElementById(id);
// HTML-Escape gegen XSS aus untrusted MQTT-Payloads / config-Werten
function esc(s){ return String(s==null?"":s).replace(/[&<>"']/g,
  c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c])); }
// POST mit JSON-Content-Type + eigenem Header (erzwingt CORS-Preflight -> CSRF-Schutz)
async function postJSON(url, body){
  const r = await fetch(url, {method:"POST",
    headers:{"Content-Type":"application/json","X-Requested-With":"dbus-openwb2"},
    body: JSON.stringify(body||{})});
  return r.json();
}
const FIELDS = {
  MQTT: ["broker_address","broker_port","mqtt_root","username"],
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
  $("api_mode").value=g("MQTT","api_mode","internal");
  $("web_username").value=g("WEB","username","admin");
  // MQTT-Passwort wird nie ausgeliefert -> nur Platzhalter, leer = unveraendert
  $("password").value="";
  $("password").placeholder = g("MQTT","password_set",false)
    ? "•••••• (gesetzt · leer = unverändert)" : "";
  const prot=g("WEB","protected",false);
  $("secstate").textContent = prot
    ? "🔒 Passwortschutz ist aktiv."
    : "🔓 Kein Passwortschutz – Interface ist im Netzwerk offen zugänglich.";
  $("secstate").style.color = prot ? "var(--ok)" : "var(--muted)";
  if($("broker_address").value==="IP_ADDR_OR_FQDN") $("broker_address").value="";
  status();
}
function collect(){
  // Nur Felder senden, die das UI wirklich steuert. Alles andere (tls_enabled,
  // timeout, nominal_voltage, port, MQTT-Passwort) bleibt serverseitig erhalten.
  return {
    DEFAULT:{logging:$("logging").value, device_name:$("device_name").value,
      device_instance:$("device_instance").value},
    WALLBOX:{chargepoint_id:$("chargepoint_id").value,
      max_current:$("max_current").value, position:$("position").value},
    CONTROL:{enabled:$("control_enabled").checked?"1":"0",
      charge_template_id:$("charge_template_id").value},
    WEB:{username:$("web_username").value,
      new_password:$("web_new_password").value, disable:$("web_disable").checked},
    MQTT:{broker_address:$("broker_address").value, broker_port:$("broker_port").value,
      username:$("username").value, password:$("password").value,
      mqtt_root:$("mqtt_root").value, api_mode:$("api_mode").value},
  };
}
async function scan(){
  msg("Scanne openWB (ca. 4 s) ...","m-info");
  const body={broker_address:$("broker_address").value,
    broker_port:$("broker_port").value, username:$("username").value,
    password:$("password").value, mqtt_root:$("mqtt_root").value};
  try{
    const r = await postJSON("/api/scan", body);
    if(!r.ok){ msg("Scan fehlgeschlagen: "+(r.error||"?"),"m-err");
      $("scanresult").innerHTML=""; return; }
    // API automatisch auf SimpleAPI stellen, wenn erkannt (empfohlen)
    if(r.simple_available){ $("api_mode").value="simple"; }
    const isTrue = v => (v==="1"||v==="true"||v===true);
    const cps=r.chargepoints; const ids=Object.keys(cps).sort();
    const apiNote = r.simple_available
      ? "<b>SimpleAPI erkannt</b> – API auf „SimpleAPI" gesetzt."
      : "Nur interne Topics gefunden (SimpleAPI in der openWB nicht aktiv).";
    let h="<p class='note'>"+apiNote+" Gefundene Ladepunkte (klicken zum &Uuml;bernehmen):</p>";
    for(const id of ids){ const d=cps[id];
      const plug=isTrue(d.plug_state)?"eingesteckt":"frei";
      const chg=isTrue(d.charge_state)?"lädt":"steht";
      const soc = (d.soc!==null&&d.soc!==undefined)?`${Math.round(Number(d.soc)||0)} %`:"kein SoC";
      const tpl = (d.charge_template_id!==null&&d.charge_template_id!==undefined)?d.charge_template_id:"";
      const pw = Number(d.power)||0, cur = Number(d.evse_current)||0;
      const src = d.source==="simple"?"SimpleAPI":"intern";
      h+=`<div class="cp" data-id="${esc(id)}" data-tpl="${esc(tpl)}">
        <div><b>Ladepunkt ${esc(id)}</b> <small style="color:var(--muted)">(${src})</small><br>
        <small>${pw} W &middot; ${plug} &middot; ${chg} &middot; Soll ${cur} A &middot; SoC: ${soc}</small></div>
        <small>tpl ${esc(tpl||"?")}</small></div>`; }
    $("scanresult").innerHTML=h;
    $("scanresult").querySelectorAll(".cp").forEach(el =>
      el.addEventListener("click", () => pick(el.dataset.id, el.dataset.tpl, el)));
    msg("Scan ok: "+ids.length+" Ladepunkt(e) gefunden.","m-ok");
  }catch(e){ msg("Scan-Fehler: "+e,"m-err"); }
}
function pick(id,tpl,el){ $("chargepoint_id").value=id;
  if(tpl && tpl!=="null" && tpl!=="undefined" && tpl!=="") $("charge_template_id").value=tpl;
  document.querySelectorAll(".cp").forEach(e=>e.classList.remove("sel"));
  if(el) el.classList.add("sel");
  msg("Ladepunkt "+id+" übernommen.","m-info"); }
async function save(){
  msg("Speichere ...","m-info");
  const r = await postJSON("/api/save", collect());
  msg(r.message||"Gespeichert.", r.ok?"m-ok":"m-err"); setTimeout(status,1500);
}
async function restart(){
  const r = await postJSON("/api/restart", {});
  msg(r.message, r.ok?"m-ok":"m-err"); setTimeout(status,1500);
}
async function status(){
  try{ const r=await (await fetch("/api/status")).json();
    $("drvstatus").textContent="Treiber-Status: "+r.driver; }catch(e){}
}
function fmt(n){ return (n===null||n===undefined)?"–":n; }
async function pollLive(){
  try{
    const d = await (await fetch("/api/live")).json();
    const cps = d.chargepoints||[];
    if(!cps.length){ $("livecard").style.display="none"; return; }
    $("livecard").style.display="block";
    let h="";
    if(d.stale) h+="<div class='stale'>⚠ Live-Daten veraltet – Treiber getrennt?</div>";
    for(const c of cps){
      const cls = c.charging?"p-charging":(c.plugged?"p-connected":"p-idle");
      const soc = (c.soc!==null&&c.soc!==undefined)?`<div class="metric"><div class="v">${Math.round(Number(c.soc)||0)} %</div><div class="k">Fahrzeug-SoC</div></div>`:"";
      const sess = (c.session_kwh!==null&&c.session_kwh!==undefined)?`<div class="metric"><div class="v">${esc(c.session_kwh)}</div><div class="k">kWh Sitzung</div></div>`:"";
      h+=`<div class="lp">
        <div class="lphead"><span class="lpname">${esc(c.name)} <small style="color:var(--muted)">· LP ${esc(c.id)}</small></span>
          <span class="pill ${cls}">${esc(c.status)}</span></div>
        <div class="metrics">
          <div class="metric"><div class="v">${esc(fmt(c.power))} W</div><div class="k">Leistung</div></div>
          <div class="metric"><div class="v">${esc(fmt(c.set_current))} A</div><div class="k">Sollstrom</div></div>
          <div class="metric"><div class="v">${esc(fmt(c.phases))}</div><div class="k">Phasen</div></div>
          <div class="metric"><div class="v">${esc(fmt(c.energy_kwh))}</div><div class="k">kWh gesamt</div></div>
          ${sess}${soc}
        </div></div>`;
    }
    $("live").innerHTML=h;
  }catch(e){ /* Treiber evtl. aus */ }
}
async function loadLog(){
  $("logbox").textContent="lade …";
  try{ $("logbox").textContent = await (await fetch("/api/log")).text();
    $("logbox").scrollTop = $("logbox").scrollHeight; }
  catch(e){ $("logbox").textContent="Fehler: "+e; }
}
load();
pollLive(); setInterval(pollLive, 3000);
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
