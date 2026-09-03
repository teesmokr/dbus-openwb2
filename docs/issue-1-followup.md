## Analyse

Der Melder (Kai9555) hat auf v2.0.2 aktualisiert und neu gestartet. Der zuerst
gemeldete Punkt 1 ("Ladezeit läuft, obwohl nicht geladen wird") sollte mit
v2.0.1 bereits behoben sein – die `/ChargingTime`-Logik in `dbus-openwb2.py`
zählt seitdem nachweislich nur noch, während `plug_state` **und**
`charge_state` gesetzt sind. Trotzdem beobachtet er weiterhin eine
hochlaufende Ladezeit und hat zusätzlich ein Log mitgeschickt.

Das Log zeigt den eigentlichen Fehler:

```
KeyError: "Can't register the object-path handler for '/': there is already a handler"
```

Dieser Fehler tritt beim Anlegen des **zweiten** `evcharger`-Dienstes
(`VeDbusService`) im selben Prozess auf. Ursache: `_build_service()` rief
bisher `VeDbusService(sn)` ohne eigene D-Bus-Verbindung auf. `vedbus.py`
verwendet dann `dbus.SystemBus()` – und `dbus.SystemBus()` liefert (anders als
`dbus.SystemBus(private=True)`) pro Prozess eine **gecachte, gemeinsame**
Verbindung zurück. Legt der Treiber mehrere Ladepunkte an (kommagetrennte
`chargepoint_id`, z. B. bei Standalone-Primary + openWB2-Secondary mit
mehreren gescannten Ladepunkten), versuchen also mehrere `ChargePoint`-Objekte
im selben Prozess, auf **derselben** Verbindung das Root-Objekt `/` zu
registrieren – das schlägt für den zweiten und jeden weiteren Ladepunkt fehl.
Der Prozess stürzt ab, der Supervisor startet ihn neu, und derselbe Fehler
tritt wieder auf (Absturzschleife, im Log gut sichtbar an den sich
wiederholenden "*** starting dbus-openwb2 ***"-Zeilen mit wachsendem
Abstand).

Das erklärt vermutlich auch den Eindruck aus dem ursprünglichen Bericht: In
einer Absturzschleife bekommt der Prozess nie einen stabilen, dauerhaften
Lauf – abhängig davon, welcher (Neu-)Start gerade "übersteht", können
Zustand und Anzeige in VRM unstimmig wirken bzw. sich Zähler anders verhalten
als erwartet. Die eigentliche `/ChargingTime`-Logik selbst ist nach Prüfung
des aktuellen Codes (Stand v2.0.2 / main) korrekt.

Der Fehler tritt **nur** auf, wenn mehr als eine `chargepoint_id`
konfiguriert ist (mehrere Ladepunkte in einem Prozess). Das passt zum
beschriebenen Setup: Standalone-Primary + openWB2-Secondary, Scan findet 3
Ladepunkte.

**Fix:** Jeder Ladepunkt bekommt jetzt eine eigene private D-Bus-Verbindung
(`dbus.SystemBus(private=True)` bzw. `dbus.SessionBus(private=True)`, falls
`DBUS_SESSION_BUS_ADDRESS` gesetzt ist), sodass sich mehrere `evcharger`-
Services im selben Prozess nicht mehr um das Root-Objekt `/` streiten.

Offen bleiben die von uns zuvor gestellten Rückfragen zur genauen
Ladepunkt-Zuordnung (welche `chargepoint_id`(s) in der Config, welche IDs der
Scan gefunden hat, an welchem Ladepunkt physisch der Tesla hängt) – die
lassen sich ohne weitere Angaben des Melders nicht abschließend klären. Der
jetzige Fix behebt aber unabhängig davon die Absturzursache, sodass der
Dienst bei mehreren konfigurierten Ladepunkten überhaupt stabil läuft.

## Antwort-Entwurf

Hallo,

danke für das Log – damit lässt sich der eigentliche Fehler klar erkennen:

```
KeyError: "Can't register the object-path handler for '/': there is already a handler"
```

Das ist kein Problem der `/ChargingTime`-Logik selbst (die ist seit v2.0.1
korrekt), sondern ein Absturz beim Start: Wenn mehr als ein Ladepunkt
konfiguriert ist (kommagetrennte `chargepoint_id`, bei dir also vermutlich
mehrere der 3 gefundenen Ladepunkte), hat der Treiber bisher versucht, alle
zugehörigen D-Bus-Dienste über **eine** gemeinsame D-Bus-Verbindung
anzulegen. Das funktioniert für den ersten Ladepunkt, scheitert aber beim
zweiten – der Prozess stürzt ab und wird ständig neu gestartet. Das erklärt
vermutlich auch, warum sich die Ladezeit-Anzeige für dich unstimmig verhält:
Der Dienst kam bei dir nie in einen sauberen Dauerbetrieb.

Ich habe das behoben: Jeder Ladepunkt bekommt jetzt eine eigene, private
D-Bus-Verbindung, sodass sich mehrere Ladepunkte im selben Prozess nicht mehr
gegenseitig blockieren. Der Fix ist als Pull Request vorbereitet und wird
mit der nächsten Version ausgeliefert.

Zur Ladepunkt-Zuordnung (Punkt 2, "Tesla" vs. "Gastfahrzeug" im VRM) bleiben
meine Rückfragen von vorhin bestehen, falls du dazu noch etwas sagen
kannst:
- Welche `chargepoint_id`(s) hast du aktuell in der Config eingetragen?
- Welche Ladepunkt-IDs hat der Scan im Web-Interface gefunden?
- An welchem Ladepunkt hängt physisch dein Tesla, und zeigt openWB dort den
  richtigen SoC an?

Sobald der Absturz nicht mehr auftritt, lässt sich das leichter beurteilen –
magst du nach dem Update kurz berichten, ob die Ladezeit sich jetzt korrekt
verhält?

## Codeaenderung

- `dbus-openwb2.py`: `_build_service()` übergibt `VeDbusService` jetzt eine
  eigene private D-Bus-Verbindung (`dbus.SystemBus(private=True)` bzw.
  `dbus.SessionBus(private=True)`) statt der ungenannten, prozessweit
  gecachten Standardverbindung. Damit können mehrere Ladepunkte
  (kommagetrennte `chargepoint_id`) im selben Prozess ihr jeweiliges
  Root-Objekt `/` registrieren, ohne zu kollidieren – das behebt die im Log
  sichtbare Absturzschleife (`KeyError: Can't register the object-path
  handler for '/': there is already a handler`).
- `CHANGELOG.md`: Eintrag unter „Unreleased" ergänzt.

Geprüft mit `python3 -m py_compile dbus-openwb2.py webconfig.py` und
`python3 -m pyflakes dbus-openwb2.py webconfig.py` (beide ohne Befund).
