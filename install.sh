#!/bin/bash
# Installiert dbus-openwb2 (Treiber + Web-Interface) als Venus-OS-Dienste.
SCRIPT_DIR=$( cd -- "$( dirname -- "${BASH_SOURCE[0]}" )" &> /dev/null && pwd )

# Zeilenenden absichern (falls Dateien unter Windows bearbeitet wurden)
for f in "$SCRIPT_DIR/dbus-openwb2.py" "$SCRIPT_DIR/webconfig.py" \
         "$SCRIPT_DIR/service/run" "$SCRIPT_DIR/service/log/run" \
         "$SCRIPT_DIR/service-web/run" "$SCRIPT_DIR/service-web/log/run" \
         "$SCRIPT_DIR/install.sh" "$SCRIPT_DIR/uninstall.sh" "$SCRIPT_DIR/restart.sh"; do
    [ -f "$f" ] && sed -i 's/\r$//' "$f"
done

# config.ini anlegen, falls noch nicht vorhanden
if [ ! -f "$SCRIPT_DIR/config.ini" ]; then
    cp "$SCRIPT_DIR/config.sample.ini" "$SCRIPT_DIR/config.ini"
    echo "config.ini aus Vorlage erstellt - bitte im Web-Interface konfigurieren."
fi

# Rechte setzen
chmod 755 "$SCRIPT_DIR/dbus-openwb2.py" "$SCRIPT_DIR/webconfig.py"
chmod 755 "$SCRIPT_DIR/install.sh" "$SCRIPT_DIR/uninstall.sh" "$SCRIPT_DIR/restart.sh"
chmod 755 "$SCRIPT_DIR/service/run" "$SCRIPT_DIR/service/log/run"
chmod 755 "$SCRIPT_DIR/service-web/run" "$SCRIPT_DIR/service-web/log/run"

# Abhaengigkeit paho-mqtt pruefen/installieren
python -c "import paho.mqtt.client" 2>/dev/null
if [ $? -gt 0 ]; then
    python -m pip install paho-mqtt || { opkg update && opkg install python3-pip && python -m pip install paho-mqtt; }
fi

# Dienste im daemontools-Verzeichnis verlinken
ln -sfn "$SCRIPT_DIR/service"     /service/dbus-openwb2
ln -sfn "$SCRIPT_DIR/service-web" /service/dbus-openwb2-web

# Dienste neu starten, damit bei einem UPDATE die neuen Dateien sofort greifen
# (daemontools kurz Zeit geben, den Symlink zu erkennen)
sleep 3
svc -t /service/dbus-openwb2     2>/dev/null || true
svc -t /service/dbus-openwb2-web 2>/dev/null || true

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
echo "Treiber-Log:  tail -f /data/log/dbus-openwb2/current | tai64nlocal"
echo "----------------------------------------------------------------"
