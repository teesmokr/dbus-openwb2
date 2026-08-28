#!/bin/bash
# Installiert dbus-openwb2 (Treiber + Web-Interface) als Venus-OS-Dienste.
SCRIPT_DIR=$( cd -- "$( dirname -- "${BASH_SOURCE[0]}" )" &> /dev/null && pwd )

# Zeilenenden absichern (nur schreiben, wenn wirklich CRLF vorhanden -> schont eMMC)
for f in "$SCRIPT_DIR/dbus-openwb2.py" "$SCRIPT_DIR/webconfig.py" \
         "$SCRIPT_DIR/service/run" "$SCRIPT_DIR/service/log/run" \
         "$SCRIPT_DIR/service-web/run" "$SCRIPT_DIR/service-web/log/run" \
         "$SCRIPT_DIR/install.sh" "$SCRIPT_DIR/uninstall.sh" "$SCRIPT_DIR/restart.sh"; do
    [ -f "$f" ] && grep -q $'\r' "$f" && sed -i 's/\r$//' "$f"
done

# config.ini anlegen, falls noch nicht vorhanden (Update: vorhandene wird NIE ueberschrieben)
if [ ! -f "$SCRIPT_DIR/config.ini" ]; then
    cp "$SCRIPT_DIR/config.sample.ini" "$SCRIPT_DIR/config.ini"
    echo "config.ini aus Vorlage erstellt - bitte im Web-Interface konfigurieren."
fi

# Rechte setzen
chmod 755 "$SCRIPT_DIR/dbus-openwb2.py" "$SCRIPT_DIR/webconfig.py"
chmod 755 "$SCRIPT_DIR/install.sh" "$SCRIPT_DIR/uninstall.sh" "$SCRIPT_DIR/restart.sh"
chmod 755 "$SCRIPT_DIR/service/run" "$SCRIPT_DIR/service/log/run"
chmod 755 "$SCRIPT_DIR/service-web/run" "$SCRIPT_DIR/service-web/log/run"

# Abhaengigkeit paho-mqtt: gebuendelte Kopie unter ext/ bevorzugen, sonst pip
if ! PYTHONPATH="$SCRIPT_DIR/ext/paho-mqtt" python3 -c "import paho.mqtt.client" 2>/dev/null; then
    python3 -m pip install paho-mqtt \
        || { opkg update && opkg install python3-pip && python3 -m pip install paho-mqtt; }
fi

# Dienste im daemontools-Verzeichnis verlinken
ln -sfn "$SCRIPT_DIR/service"     /service/dbus-openwb2
ln -sfn "$SCRIPT_DIR/service-web" /service/dbus-openwb2-web

# Auf supervise warten, dann Dienste (neu) starten -> bei einem UPDATE greifen die
# neuen Dateien sofort, bei einer Erstinstallation wird nichts unnoetig getoetet.
for s in dbus-openwb2 dbus-openwb2-web; do
    for _ in 1 2 3 4 5 6 7 8; do
        svok "/service/$s" 2>/dev/null && break
        sleep 1
    done
    svc -t "/service/$s" 2>/dev/null || true
done

# In rc.local eintragen, damit die Installation ein Firmware-Update ueberlebt
RC=/data/rc.local
if [ ! -f "$RC" ]; then
    echo "#!/bin/bash" > "$RC"
    echo >> "$RC"
    chmod 755 "$RC"
fi
grep -qxF "bash $SCRIPT_DIR/install.sh" "$RC" || echo "bash $SCRIPT_DIR/install.sh" >> "$RC"

echo "----------------------------------------------------------------"
echo "Fertig. Web-Interface:  http://<venus-ip>:8088"
echo "Treiber-Log:  tail -f /var/log/dbus-openwb2/current | tai64nlocal"
echo "----------------------------------------------------------------"
