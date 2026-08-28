# Beitragen zu dbus-openwb2

Danke für dein Interesse! Beiträge sind willkommen – Bugfixes, Features, Doku
und vor allem **auf Hardware getestete** Rückmeldungen.

## Entwicklung

Das Projekt ist bewusst schlank (nur Python-Standardbibliothek + `paho-mqtt`).

Vor einem Pull Request bitte lokal prüfen:

```bash
python -m py_compile dbus-openwb2.py webconfig.py
python -m pyflakes  dbus-openwb2.py webconfig.py
for f in install.sh uninstall.sh restart.sh setup; do bash -n "$f"; done
```

Die CI (GitHub Actions) führt genau diese Checks plus ShellCheck aus.

## Aufbau

- `dbus-openwb2.py` – der Treiber (MQTT → D-Bus `com.victronenergy.evcharger`),
  eine `ChargePoint`-Klasse je Ladepunkt.
- `webconfig.py` – das Web-Interface (stdlib HTTP-Server, Port 8088), liest
  `status.json` für die Live-Anzeige.
- `service*/` – daemontools-Runner. Zeilenenden müssen **LF** sein
  (`.gitattributes` erzwingt das).
- `ext/velib_python/` – gebündelte Victron-D-Bus-Bibliothek (nicht ändern).

## Besonders gesucht

Die Steuerung (Venus → openWB) nutzt die `charge_template`-Set-Topics, die je
openWB-2-Version leicht abweichen können. Getestete Werte und Verbesserungen
hier sind besonders wertvoll – bitte openWB-Version im PR nennen.

## Stil

- Kommentare/UI auf Deutsch (Projektsprache), Code-Bezeichner englisch.
- Keine neuen Laufzeit-Abhängigkeiten ohne guten Grund.
