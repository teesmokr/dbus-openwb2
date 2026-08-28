#!/bin/bash
# Startet beide Dienste neu.
svc -t /service/dbus-openwb2
svc -t /service/dbus-openwb2-web
echo "dbus-openwb2 und Web-Interface neu gestartet."
