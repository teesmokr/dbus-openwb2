## Analyse

Der neueste Kommentar des Melders (Kai9555) lautet:

> Perfekt - funktioniert 🙌

Das ist die Rückmeldung auf den vorherigen Kommentar
([#issuecomment-5521997861](https://github.com/teesmokr/dbus-openwb2/issues/1#issuecomment-5521997861)),
in dem der Melder nach dem Update auf v2.0.3 noch eine hochlaufende
„Ladezeit" beobachtet hatte, obwohl der Absturz-Loop (KeyError beim
zweiten Ladepunkt) bereits behoben war. Die Ursache dafür wurde
zwischenzeitlich identifiziert und mit **v2.0.4** behoben: Die VRM-GUI
zeigt die „Ladezeit" aus `/Session/Time`, nicht aus `/ChargingTime` –
gefixt wurde bis dahin aber nur Letzteres. Seit v2.0.4 werden beide
Felder korrekt nur während echten Ladens hochgezählt (siehe
`CHANGELOG.md`, Abschnitt „[2.0.4] – 2026-09-03").

Der aktuelle Kommentar bestätigt, dass nach dem Update auf v2.0.4 nun
beide gemeldeten Punkte des Issues gelöst sind:
1. Ladezeit zählt nicht mehr hoch, wenn nicht geladen wird (Fix v2.0.4).
2. Die Absturzschleife bei mehreren Ladepunkten ist behoben (Fix v2.0.3).

Der dritte, offene Nebenpunkt („Gastfahrzeug" statt „Tesla" in VRM) wurde
bereits als Plattform-Einschränkung erklärt (Victron-EVCS-Schnittstelle
hat kein Feld für einen Fahrzeugnamen, der SoC wird aber korrekt
übertragen) und vom Melder nicht weiter moniert.

Es ist **keine Codeänderung** erforderlich – lediglich eine bestätigende
Antwort und ggf. das Schließen des Issues durch den Maintainer.

## Antwort-Entwurf

Klasse, danke für die Rückmeldung! 🙌 Freut mich, dass jetzt beides passt:
kein Absturz mehr bei mehreren Ladepunkten (v2.0.3) und die Ladezeit in
VRM zählt nur noch während des tatsächlichen Ladens (v2.0.4).

Falls dir noch etwas an der SoC-/Ladepunkt-Zuordnung bei deinem Standalone-
Primary + openWB2-Secondary-Setup auffällt, meld dich gerne wieder – sonst
mache ich das Issue jetzt zu. Danke nochmal fürs geduldige Testen und die
guten Logs, die haben sehr geholfen!

## Codeaenderung

keine – reine Rückfrage/Antwort (Bestätigung des Melders, dass die Fixes
aus v2.0.3/v2.0.4 sein Problem gelöst haben; kein weiterer Handlungsbedarf
im Code).
