#!/bin/bash
# Entfernt dbus-openwb2 (Treiber + Web-Interface).
SCRIPT_DIR=$( cd -- "$( dirname -- "${BASH_SOURCE[0]}" )" &> /dev/null && pwd )

# aus rc.local entfernen
sed -i "\#bash $SCRIPT_DIR/install.sh#d" /data/rc.local 2>/dev/null

# Dienste stoppen und Symlinks entfernen
for svc in dbus-openwb2 dbus-openwb2-web; do
    rm -f /service/$svc
    kill $(pgrep -f "supervise $svc") 2>/dev/null
done

# laufende Prozesse beenden
kill $(pgrep -f "dbus-openwb2.py") 2>/dev/null
kill $(pgrep -f "webconfig.py") 2>/dev/null

echo "dbus-openwb2 deinstalliert. Ordner $SCRIPT_DIR kann geloescht werden."
