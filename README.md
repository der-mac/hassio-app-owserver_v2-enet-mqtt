# hassio-app-owserver_v2-enet-mqtt

Ein Home-Assistant-Add-on, das die `details.xml` eines **Embedded Data Systems
OW-SERVER v2-Enet** (bzw. kompatibler OW-SERVER) in festen Intervallen abruft und
die Daten via MQTT inklusive Home-Assistant-MQTT-Discovery bereitstellt.

## Installation als lokales Add-on-Repository

1. Dieses Repository nach `/addons/owserver-v2-enet-mqtt` auf deiner
   Home-Assistant-Installation kopieren (oder als eigenes GitHub-Repository
   veröffentlichen und unter **Einstellungen → Add-ons → Add-on-Shop →
   Repositorys** hinzufügen).
2. Home Assistant unter **Einstellungen → Add-ons** neu laden bzw. neu starten.
3. Das Add-on **OW-SERVER v2 Enet MQTT** installieren, konfigurieren und starten.

Die eigentliche Add-on-Struktur liegt unter `owserver_v2_enet_mqtt/`.

Weitere Details: [`owserver_v2_enet_mqtt/DOCS.md`](owserver_v2_enet_mqtt/DOCS.md).
