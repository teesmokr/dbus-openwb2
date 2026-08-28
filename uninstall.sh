#!/bin/bash
# Entfernt dbus-openwb2 (Treiber + Web-Interface).
SCRIPT_DIR=$( cd -- "$( dirname -- "${BASH_SOURCE[0]}" )" &> /dev/null && pwd )

# aus rc.local entfernen (aktueller und historischer Pfad)
sed -i "\#bash $SCRIPT_DIR/install.sh#d" /data/rc.local 2>/dev/null
sed -i "\#bash /data/etc/dbus-openwb2/install.sh#d" /data/rc.local 2>/dev/null

# Dienste inkl. Log-Runner sauber stoppen, dann Symlinks entfernen
for s in dbus-openwb2 dbus-openwb2-web; do
    svc -dx "/service/$s" "/service/$s/log" 2>/dev/null
    rm -f "/service/$s"
    kill "$(pgrep -f "supervise $s")" 2>/dev/null
done

# laufende Prozesse beenden (Treiber, Web-Interface, multilog)
kill "$(pgrep -f "dbus-openwb2.py")" 2>/dev/null
kill "$(pgrep -f "webconfig.py")" 2>/dev/null
kill "$(pgrep -f "multilog .* /var/log/dbus-openwb2")" 2>/dev/null

echo "dbus-openwb2 deinstalliert. Ordner $SCRIPT_DIR kann geloescht werden."
