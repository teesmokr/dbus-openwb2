#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
openwb_http.py  --  Optionaler HTTP-Transport fuer die openWB-SimpleAPI.

Hintergrund: Der Standard-Transport dieses Treibers ist MQTT (openWB-Broker auf
Port 1883, anonym). Sobald in der openWB die **Benutzerverwaltung** aktiviert
wird, schaltet openWB den unverschluesselten MQTT-Listener auf 1883 ab -> der
Scan/Treiber bekommt "Connection refused". Die openWB bietet dieselbe SimpleAPI
aber zusaetzlich ueber **HTTP** an (muss in der openWB manuell aktiviert werden),
mit Authentifizierung ueber Bearer-Token oder Benutzer/Passwort. Dieser Transport
funktioniert damit auch bei aktiver Benutzerverwaltung.

Nur Python-Standardbibliothek (urllib/ssl/json) -> keine zusaetzliche Abhaengigkeit.

Endpoint (laut openWB-Wiki, 2.1.9/2.2.0):
  http(s)://<ip>/openWB/simpleAPI/simpleapi.php
  Lesen : GET  ?get_chargepoint_all=<id>            -> verschachteltes JSON
          GET  ?get_chargepoint_<feld>=<id>&raw=true -> Rohwert
  Setzen: POST set_chargemode=<mode>&chargepoint_nr=<id>
          POST chargecurrent=<A>&chargepoint_nr=<id>
  Auth  : Header 'Authorization: Bearer <token>'  ODER  Felder username/password

ACHTUNG (experimentell): Das genaue JSON-Schema von get_chargepoint_all ist in
der openWB-Doku nur als Auszug dokumentiert. Das Mapping unten ist defensiv
(mehrere moegliche Schluesselnamen, Einzel-Getter als Fallback) und sollte gegen
eine echte openWB mit aktivierter HTTP-SimpleAPI verifiziert werden.
"""

import ssl
import json
import socket
from urllib.parse import urlencode
from urllib.request import Request, urlopen
from urllib.error import URLError, HTTPError

DEFAULT_PATH = "/openWB/simpleAPI/simpleapi.php"

# Felder, die wir pro Ladepunkt brauchen -> SimpleAPI-GET-Parametername (Einzel-Getter)
_SCALAR_GETTERS = {
    "power":         "get_chargepoint_power",
    "imported":      "get_chargepoint_imported",
    "soc":           "get_chargepoint_soc",
    "plug_state":    "get_chargepoint_plug_state",
    "charge_state":  "get_chargepoint_charge_state",
    "phases_in_use": "get_chargepoint_phases_in_use",
    "evse_current":  "get_chargepoint_evse_current",
    "chargemode":    "get_chargepoint_chargemode",
}
# Per-Phase-Getter als Fallback, falls get_chargepoint_all keine Arrays liefert
_PHASE_GETTERS = {
    "currents": ("get_chargepoint_current_p1", "get_chargepoint_current_p2",
                 "get_chargepoint_current_p3"),
    "voltages": ("get_chargepoint_voltage_p1", "get_chargepoint_voltage_p2",
                 "get_chargepoint_voltage_p3"),
}
# Moegliche Schluesselnamen im get_chargepoint_all-JSON (defensiv)
_ALL_KEYS = {
    "power":         ("power",),
    "imported":      ("imported",),
    "currents":      ("currents",),
    "voltages":      ("voltages",),
    "soc":           ("soc",),
    "plug_state":    ("plug_state", "plugged"),
    "charge_state":  ("charge_state", "charging"),
    "phases_in_use": ("phases_in_use", "phases"),
    "evse_current":  ("evse_current",),
    "chargemode":    ("chargemode",),
    "state_str":     ("state_str",),
}


def _num(v, default=None):
    try:
        if isinstance(v, bool):
            return 1.0 if v else 0.0
        return float(v)
    except (ValueError, TypeError):
        return default


def truthy(v):
    """Robuste Wahrheitswert-Erkennung fuer bool/int/str aus der HTTP-API."""
    if isinstance(v, bool):
        return v
    if isinstance(v, (int, float)):
        return v != 0
    return str(v).strip().lower() in ("1", "true", "on", "yes")


class OpenWBHttp:
    def __init__(self, host, scheme="http", port=None, path=DEFAULT_PATH,
                 auth_mode="none", token="", username="", password="",
                 verify_tls=True, timeout=5.0):
        self.scheme = (scheme or "http").strip().lower()
        self.host = (host or "").strip().rstrip("/")
        # host darf bereits ein voller URL sein ("http://1.2.3.4") -> tolerieren
        for pre in ("https://", "http://"):
            if self.host.startswith(pre):
                self.scheme = pre[:-3]
                self.host = self.host[len(pre):].rstrip("/")
        self.port = int(port) if str(port or "").strip() else None
        self.path = path or DEFAULT_PATH
        if not self.path.startswith("/"):
            self.path = "/" + self.path
        self.auth_mode = (auth_mode or "none").strip().lower()
        self.token = token or ""
        self.username = username or ""
        self.password = password or ""
        self.verify_tls = bool(verify_tls)
        self.timeout = float(timeout)

    # ---- URL / HTTP ----
    def _base(self):
        netloc = self.host
        if self.port:
            netloc = "%s:%d" % (self.host, self.port)
        return "%s://%s%s" % (self.scheme, netloc, self.path)

    def _ctx(self):
        if self.scheme != "https":
            return None
        if self.verify_tls:
            return ssl.create_default_context()
        ctx = ssl.create_default_context()
        ctx.check_hostname = False
        ctx.verify_mode = ssl.CERT_NONE
        return ctx

    def _auth_headers(self):
        if self.auth_mode == "token" and self.token:
            return {"Authorization": "Bearer %s" % self.token}
        return {}

    def _auth_params(self):
        if self.auth_mode == "userpass" and (self.username or self.password):
            return {"username": self.username, "password": self.password}
        return {}

    def _get(self, params):
        q = dict(params)
        q.update(self._auth_params())
        url = self._base() + "?" + urlencode(q)
        req = Request(url, headers=self._auth_headers(), method="GET")
        with urlopen(req, timeout=self.timeout, context=self._ctx()) as r:
            return r.read().decode("utf-8", "ignore")

    def _post(self, data):
        d = dict(data)
        d.update(self._auth_params())
        body = urlencode(d).encode("utf-8")
        headers = {"Content-Type": "application/x-www-form-urlencoded"}
        headers.update(self._auth_headers())
        url = self._base()
        req = Request(url, data=body, headers=headers, method="POST")
        with urlopen(req, timeout=self.timeout, context=self._ctx()) as r:
            return r.read().decode("utf-8", "ignore")

    def _get_raw(self, getter, cp_id):
        """Einzelwert ueber &raw=true holen (fuer Fallback / Scan-Probe)."""
        txt = self._get({getter: cp_id, "raw": "true"}).strip()
        return txt

    # ---- Lesen ----
    def read_chargepoint(self, cp_id):
        """Liest einen Ladepunkt und liefert ein normalisiertes Dict.

        Strategie: ein get_chargepoint_all (1 Request), fehlende Felder per
        Einzel-Getter nachladen. Gibt None zurueck, wenn gar nichts lesbar war.
        """
        out = {k: None for k in _ALL_KEYS}
        got_any = False

        # 1) get_chargepoint_all -> verschachteltes JSON
        try:
            raw = self._get({"get_chargepoint_all": cp_id})
            data = json.loads(raw)
            inner = None
            if isinstance(data, dict):
                inner = data.get("chargepoint_%s" % cp_id)
                if inner is None:
                    # erstes dict-Value als Fallback (Schema evtl. anders)
                    for v in data.values():
                        if isinstance(v, dict):
                            inner = v
                            break
            if isinstance(inner, dict):
                for field, keys in _ALL_KEYS.items():
                    for k in keys:
                        if k in inner and inner[k] is not None:
                            out[field] = inner[k]
                            got_any = True
                            break
        except (URLError, HTTPError, ValueError, socket.timeout):
            pass

        # 2) Fehlende Skalare per Einzel-Getter (raw=true)
        for field, getter in _SCALAR_GETTERS.items():
            if out[field] is None:
                try:
                    val = self._get_raw(getter, cp_id)
                    if val not in (None, ""):   # leere Antwort = Feld/ID nicht vorhanden
                        out[field] = val
                        got_any = True
                except (URLError, HTTPError, ValueError, socket.timeout):
                    pass

        # 3) currents/voltages ggf. per Per-Phase-Getter
        for field, getters in _PHASE_GETTERS.items():
            if not isinstance(out[field], (list, tuple)):
                vals = []
                for g in getters:
                    try:
                        vals.append(_num(self._get_raw(g, cp_id), 0.0))
                    except (URLError, HTTPError, ValueError, socket.timeout):
                        vals.append(0.0)
                if any(v for v in vals):
                    out[field] = vals
                    got_any = True

        return out if got_any else None

    def scan(self, probe_ids=range(1, 9)):
        """Sucht vorhandene Ladepunkte, indem IDs 1..8 probiert werden.

        openWB kennt ueber HTTP keine 'liste alle IDs'-Abfrage; wir probieren
        daher eine kleine Menge IDs und melden die, die Daten liefern.
        """
        found = {}
        for cp_id in probe_ids:
            d = self.read_chargepoint(cp_id)
            if d is not None:
                found[str(cp_id)] = {
                    "power": d.get("power"),
                    "imported": d.get("imported"),
                    "plug_state": d.get("plug_state"),
                    "charge_state": d.get("charge_state"),
                    "evse_current": d.get("evse_current"),
                    "phases_in_use": d.get("phases_in_use"),
                    "chargemode": d.get("chargemode"),
                    "soc": _num(d.get("soc")),
                }
        return found

    # ---- Schreiben (Steuerung) ----
    def set_chargemode(self, cp_id, mode):
        return self._post({"set_chargemode": mode, "chargepoint_nr": cp_id})

    def set_current(self, cp_id, amps):
        return self._post({"chargecurrent": amps, "chargepoint_nr": cp_id})
