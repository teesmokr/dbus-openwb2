## Analyse

Kai9555 meldet in seinem neuesten Kommentar (2026-09-03T07:12:23Z,
https://github.com/teesmokr/dbus-openwb2/issues/1#issuecomment-5521997861):
Nach dem Update auf **v2.0.3** und einem Neustart ist der `KeyError`/Absturz-Loop
aus dem Log verschwunden (die Absturzschleife bei mehreren Ladepunkten ist also
tatsächlich behoben), **aber** die Ladezeit-Anzeige in VRM zählt weiterhin hoch,
obwohl openWB auf „Stop" steht. Er vermutet zusätzlich, das könnte eine
Einschränkung von VenusOS/VRM selbst sein (einfach hochzählen, sobald ein
Fahrzeug angesteckt ist).

**Diagnose:** Das ist keine VenusOS/VRM-Einschränkung, sondern ein Bug, der zum
Zeitpunkt seines Tests (Update auf v2.0.3) noch bestand. Der ursprüngliche Fix in
**v2.0.1** hatte nur `/ChargingTime` so geändert, dass es nur während echten
Ladens (`plug_state` **und** `charge_state`) hochzählt. Die Venus-EVCS-GUI
(Kachel und Detailseite, `gui-v2` `EvcsWidget`/`EvCharger`) liest die angezeigte
„Ladezeit" aber tatsächlich aus **`/Session/Time`** – und dieses Feld wurde nach
wie vor als reine „Dauer seit Anstecken" befüllt, unabhängig vom Lade-Status.
Deshalb lief die von Kai9555 beobachtete Anzeige in v2.0.1–v2.0.3 immer noch
hoch, sobald sein Tesla nur angesteckt war, ganz gleich ob `/ChargingTime`
selbst korrekt stand.

Dieser Root Cause wurde bereits identifiziert und in **v2.0.4** behoben (Commit
`410a190`, „fix: Ladezeit zaehlt nur noch waehrend echtem Laden (#1)", auf
`main` seit 2026-09-03 09:49 UTC – nach Kai9555s Testkommentar). Seit v2.0.4
speisen sich sowohl `/Session/Time` als auch `/ChargingTime` aus derselben
reinen Ladezeit-Berechnung (`ChargePoint._charging_time()` /
`_update_session()` in `dbus-openwb2.py`): Sie läuft nur während tatsächlich
geladen wird, pausiert bei Ladepausen (z. B. „Stop" oder PV-Wartezeit ohne
Sonne) und setzt beim Abstecken zurück. Für dieses konkrete Feedback ist damit
kein zusätzlicher Code-Fix nötig – der bereits vorhandene v2.0.4-Fix auf `main`
deckt genau das gemeldete Verhalten ab. Kai9555 muss lediglich von v2.0.3 auf
v2.0.4 aktualisieren, um den Effekt zu sehen.

## Antwort-Entwurf

Danke für den Test und die Rückmeldung! Gute Nachricht zuerst: Die
Absturzschleife ist laut deinem Log tatsächlich weg – der v2.0.3-Fix für
mehrere Ladepunkte greift.

Zur weiterhin hochzählenden Ladezeit: Das ist **keine** VenusOS/VRM-Einschränkung,
sondern ein zweiter, feinerer Bug, den ich zwischenzeitlich gefunden und in
**v2.0.4** behoben habe. Kurz erklärt: Der v2.0.1-Fix hatte `/ChargingTime`
korrigiert, aber die Venus-GUI zeigt die „Ladezeit" auf der Kachel/Detailseite
gar nicht aus `/ChargingTime` an, sondern aus einem anderen Feld –
`/Session/Time`. Genau dieses Feld lief bisher weiter einfach seit dem
Anstecken hoch, unabhängig vom Lade-Status. Deshalb hat sich bei dir trotz
v2.0.1/v2.0.3 nichts geändert.

In v2.0.4 speisen sich jetzt beide Felder aus derselben „nur während echtem
Laden"-Logik. Bitte aktualisiere auf v2.0.4:

```
cd /tmp
wget -O dbus-openwb2.zip https://github.com/teesmokr/dbus-openwb2/archive/refs/heads/main.zip
unzip -o dbus-openwb2.zip
cp -R dbus-openwb2-main/* /data/etc/dbus-openwb2/
bash /data/etc/dbus-openwb2/install.sh
```

Danach `cat /data/etc/dbus-openwb2/version` → sollte `v2.0.4` zeigen. Magst du
kurz testen, ob die Ladezeit jetzt stehen bleibt, wenn dein Tesla angesteckt,
aber openWB auf „Stop" ist?

## Codeaenderung

Keine zusätzliche Codeänderung in diesem PR nötig. Der Root-Cause-Fix für genau
dieses Feedback ist bereits auf `main` vorhanden: Commit `410a190` („fix:
Ladezeit zaehlt nur noch waehrend echtem Laden (#1)"), veröffentlicht als
**v2.0.4** (siehe `CHANGELOG.md`, Abschnitt „[2.0.4] – 2026-09-03"). Dieser
Commit lag zeitlich nach Kai9555s Testkommentar (v2.0.3), sodass sein Test das
Problem noch nicht abdecken konnte. Dieser PR dient ausschließlich dazu, die
Analyse und den Antwortentwurf für die Rückmeldung festzuhalten.
