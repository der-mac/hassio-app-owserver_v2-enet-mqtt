# OW-SERVER v2 Enet MQTT – Dokumentation

## Funktion

Das Add-on lädt regelmäßig eine XML-Datei eines Embedded-Data-Systems-
OW-SERVERs. Standardmäßig wird folgende Adresse abgefragt:

```text
http://192.168.178.10/details.xml
```

Das Add-on verarbeitet:

- alle globalen Felder unterhalb von `Devices-Detail-Response`;
- alle Elemente, deren XML-Tag mit `owd_` beginnt;
- direkte und verschachtelte Felder dieser 1-Wire-Geräte;
- XML-Attribute, z. B. `Temperature Units="Centigrade"` als
  `temperature_units`;
- numerische Werte als numerische MQTT-Payloads;
- verständliche Zusatzwerte für bekannte 1-Wire-Family- und Health-Codes.

XML-Namespaces werden beim Parsen entfernt. Daher funktionieren XML-Dateien
mit oder ohne Namespace gleichermaßen.

## Feldnamen und Datentypen

XML-Feldnamen werden in `snake_case` umgewandelt:

| XML-Feld | MQTT-Feld |
|---|---|
| `VoltageChannel1` | `voltage_channel1` |
| `PrimaryValue` | `primary_value` |
| `ROMId` | `rom_id` |
| `UserByte1` | `user_byte1` |
| `MACAddress` | `mac_address` |

XML-Attribute werden an den Feldnamen angehängt. Das Beispiel:

```xml
<Temperature Units="Centigrade">23.5625</Temperature>
```

wird zu:

```text
temperature = 23.5625
temperature_units = Centigrade
```

Die Anwendung wandelt einfache Werte automatisch um:

| XML-Wert | Veröffentlichter Typ |
|---|---|
| `true` / `false` | Boolean |
| `0`, `7`, `28` | Ganzzahl |
| `4.65`, `23.5625` | Gleitkommazahl |
| `OW-SERVER`, `DS18B20` | Text |

Ein Spannungswert wird beispielsweise als folgende MQTT-Payload veröffentlicht:

```text
4.65
```

Nicht als Text mit Anführungszeichen:

```text
"4.65"
```

Dadurch kann Home Assistant numerische Messwerte korrekt erkennen und
Verlaufsgraphen erstellen.

## MQTT-Themen

Mit dem Standardwert:

```yaml
topic_prefix: "owserver"
```

werden folgende Topics verwendet:

| Inhalt | Topic |
|---|---|
| Add-on-Verfügbarkeit | `owserver/availability` |
| Globaler Statuswert | `owserver/status/<feld>` |
| Alle globalen Werte als JSON | `owserver/status/json` |
| Feld eines 1-Wire-Geräts | `owserver/<romid>/<feld>` |
| Alle Werte eines 1-Wire-Geräts als JSON | `owserver/<romid>/json` |

Für einen DS18B20 mit der ROMId `C200000B27986828` entstehen beispielsweise:

```text
owserver/c200000b27986828/temperature
owserver/c200000b27986828/temperature_units
owserver/c200000b27986828/family
owserver/c200000b27986828/family_name
owserver/c200000b27986828/health
owserver/c200000b27986828/health_name
owserver/c200000b27986828/resolution
owserver/c200000b27986828/primary_value
owserver/c200000b27986828/raw_data
owserver/c200000b27986828/json
```

Die ROMId dient als stabiler Teil des MQTT-Topics. Deshalb bleiben Topics und
Home-Assistant-Entities stabil, auch wenn sich der Name eines 1-Wire-Geräts
ändert.

## JSON-Snapshots

Neben den einzelnen MQTT-Topics veröffentlicht das Add-on vollständige
JSON-Snapshots.

### Globale Werte

Topic:

```text
owserver/status/json
```

Beispiel:

```json
{
  "poll_count": 37,
  "devices_connected": 1,
  "loop_time": 1.973,
  "voltage_channel1": 4.65,
  "voltage_channel2": 4.65,
  "voltage_channel3": 4.66,
  "voltage_power": 4.89,
  "device_name": "OWServer_v2-Enet",
  "host_name": "OW-SERVER",
  "mac_address": "00:00:00:00:00:00"
}
```

### Einzelnes 1-Wire-Gerät

Topic:

```text
owserver/<romid>/json
```

Der Snapshot enthält auch technische Werte, die in Home Assistant
standardmäßig deaktiviert sind, beispielsweise:

- `raw_data`;
- `user_byte1`, `user_byte2` und weitere `user_byte*`-Felder;
- `family`;
- `health`;
- `resolution`;
- `power_source`;
- XML-Attribute;
- `family_name`;
- `health_name`.

## Family-Codes

Der Family-Code identifiziert die Gerätefamilie eines 1-Wire-Geräts. Der
technische Originalwert bleibt als `family` erhalten. Zusätzlich erzeugt das
Add-on `family_name` mit einer verständlichen deutschen Bezeichnung.

Beispiel:

```text
family      = 28
family_name = Temperaturfühler (DS18B20)
```

Bekannte Family-Codes:

| Family-Code | Beschreibung |
|---:|---|
| `05` | Schaltgerät (DS2405) |
| `10` | Temperaturfühler (DS18S20) |
| `12` | Mehrkanal-Schaltgerät (DS2406) |
| `1D` | Zähler (DS2423) |
| `1F` | 1-Wire-Koppler (DS2409) |
| `22` | Temperaturfühler (DS1822) |
| `26` | Batterie-/Spannungsmonitor (DS2438) |
| `28` | Temperaturfühler (DS18B20) |
| `29` | Achtkanal-Schaltgerät (DS2408) |
| `30` | Batteriemonitor (DS2760) |
| `3A` | Zweikanal-Schaltgerät (DS2413) |
| `3B` | Temperaturfühler (DS1825) |
| `42` | Temperaturfühler (DS28EA00) |

Für unbekannte Codes wird keine möglicherweise falsche Zuordnung getroffen:

```text
family_name = Unbekannte 1-Wire-Familie (XX)
```

## Health-Codes

Der Health-Wert beschreibt den Kommunikationszustand eines 1-Wire-Geräts.
Der Rohwert bleibt unter `health` erhalten. Zusätzlich wird unter
`health_name` eine verständliche Bezeichnung veröffentlicht.

| Health-Code | `health_name` | Bedeutung |
|---:|---|---|
| `0` | `Getrennt` | Das Gerät antwortet nicht mehr und ist vom 1-Wire-Bus verschwunden. |
| `1` bis `6` | `Eingeschränkt` | Die Verbindung ist instabil, z. B. durch CRC-Fehler, Paketverluste oder Timing-Probleme. |
| `7` | `Gesund` | Das Gerät ist online und kommuniziert fehlerfrei mit dem Controller. |

Beispiel:

```text
health      = 7
health_name = Gesund
```

## MQTT und Home-Assistant-Discovery

Wenn `mqtt_host` leer ist, verwendet das Add-on den von Home Assistant
bereitgestellten MQTT-Service, üblicherweise das Mosquitto-Add-on.

Für einen externen Broker können `mqtt_host`, `mqtt_port`, `mqtt_username`,
`mqtt_password` und optional `mqtt_tls` gesetzt werden.

Die Discovery-Konfigurationen werden standardmäßig unter folgenden Topics
veröffentlicht:

```text
homeassistant/sensor/owserver_v2_enet/.../config
homeassistant/binary_sensor/owserver_v2_enet/.../config
```

Alle Entitäten werden einem gemeinsamen Home-Assistant-Gerät zugeordnet.

Der Gerätename wird in dieser Reihenfolge bestimmt:

1. konfigurierte Option `device_name`;
2. XML-Feld `HostName`;
3. XML-Feld `DeviceName`;
4. Fallback `OW-SERVER`.

Die Gerätebeschreibung enthält außerdem:

- Hersteller: `Embedded Data Systems`;
- Modell aus XML-`DeviceName`;
- eine stabile Kennung aus MAC-Adresse oder Hostname;
- die konfigurierte XML-URL als Konfigurationsadresse.

### Numerische Messwerte

Erkannte Messwerte erhalten passende Home-Assistant-Metadaten:

| Feld | Einheit | Device Class | State Class |
|---|---|---|---|
| `temperature` | `°C` oder `°F` | `temperature` | `measurement` |
| `voltage_channel1` | `V` | `voltage` | `measurement` |
| `voltage_channel2` | `V` | `voltage` | `measurement` |
| `voltage_channel3` | `V` | `voltage` | `measurement` |
| `voltage_power` | `V` | `voltage` | `measurement` |
| `loop_time` | `s` | `duration` | `measurement` |

Andere numerische Werte erhalten, sofern sie als Messwert erkannt werden,
mindestens:

```yaml
state_class: measurement
```

Dadurch können sie in Home Assistant historisiert und als Graph dargestellt
werden.

## Diagnose-Entities

Technische und diagnostische Werte werden weiterhin vollständig über MQTT und
in den JSON-Snapshots veröffentlicht.

In Home Assistant werden sie jedoch als Diagnose-Entities angelegt und
standardmäßig deaktiviert:

```yaml
entity_category: diagnostic
enabled_by_default: false
```

Damit bleibt das OW-SERVER-Gerät übersichtlich. Bei Bedarf können diese
Entities in Home Assistant manuell aktiviert werden.

Standardmäßig deaktiviert sind unter anderem:

- `user_byte1`, `user_byte2` und weitere `user_byte*`-Felder;
- `family`, `family_name`;
- `health`, `health_name`;
- `rom_id`;
- `raw_data`;
- `resolution`;
- `power_source`;
- `channel`;
- `primary_value`;
- `poll_count`;
- `devices_connected`;
- `data_errors_channel*`;
- `loop_time`;
- `device_name`, `host_name`, `mac_address`;
- `date_time`;
- `name`;
- `device_type`.

`raw_data` wird absichtlich **nicht** als einzelne Home-Assistant-Entity
angelegt. Es bleibt aber im regulären MQTT-Topic und im JSON-Snapshot
verfügbar.

## Retained-Nachrichten

State-, Discovery- und Availability-Nachrichten werden standardmäßig retained
veröffentlicht.

Das bedeutet:

- Der MQTT-Broker speichert jeweils den letzten Zustand.
- Home Assistant erhält beim Neustart sofort den letzten bekannten Wert.
- Discovery-Konfigurationen sind nach Add-on-Neustarts weiterhin vorhanden.

Die Funktion lässt sich deaktivieren:

```yaml
retained: false
```

Dann erscheinen Zustände erst nach einem erfolgreichen neuen Polling-Zyklus.

## Verfügbarkeit und MQTT Last Will

Das Availability-Topic lautet standardmäßig:

```text
owserver/availability
```

Mögliche Zustände:

```text
online
offline
```

Nach einer erfolgreichen Synchronisation veröffentlicht das Add-on:

```text
owserver/availability = online
```

Wenn ein vollständiger Polling-Zyklus fehlschlägt, veröffentlicht es:

```text
owserver/availability = offline
```

Zusätzlich werden alle zuvor bekannten State-Topics mit folgendem Wert
überschrieben:

```text
unavailable
```

Der MQTT-Client konfiguriert außerdem ein Last Will. Bricht die Verbindung
unerwartet ab, veröffentlicht der MQTT-Broker automatisch:

```text
owserver/availability = offline
```

## HTTP-Abfragen, Timeout und Retries

Jeder Polling-Zyklus ruft die konfigurierte XML-URL ab.

Standardwerte:

```yaml
poll_interval: 10
http_timeout: 10
http_retries: 3
```

`http_retries` ist die maximale Zahl vollständiger HTTP-Versuche je
Polling-Zyklus, **einschließlich** des ersten Versuchs.

Bei diesem Beispiel:

```yaml
http_retries: 3
```

führt das Add-on maximal folgende Abrufe aus:

1. erster HTTP-Versuch;
2. erster Wiederholungsversuch;
3. zweiter Wiederholungsversuch.

Wiederholungsversuche erfolgen bei:

- Verbindungsproblemen;
- Timeouts;
- Fehlern beim Lesen der Antwort;
- HTTP 429;
- HTTP 500;
- HTTP 502;
- HTTP 503;
- HTTP 504.

Sind alle Versuche erfolglos, läuft das Add-on weiter und startet nach dem
konfigurierten Abfrageintervall einen neuen Zyklus.

## Fehlerverhalten

### Ungültige Konfiguration

Beim Start werden unzulässige Konfigurationen abgewiesen, z. B.:

- leere OW-SERVER-URL;
- leere MQTT-Host-Adresse;
- Intervall oder Timeout kleiner als eine Sekunde;
- `http_retries` kleiner als eins;
- ungültiger MQTT-Port.

Das Add-on beendet sich in diesem Fall mit einem Fehler, damit die Ursache
direkt im Log sichtbar ist.

### HTTP-Fehler und leere Antworten

Nach dem letzten fehlgeschlagenen HTTP-Versuch wird ein Fehler geloggt,
`availability` auf `offline` gesetzt und bekannte Werte auf `unavailable`
gesetzt.

Eine leere HTTP-Antwort gilt ebenfalls als Fehler und wird nicht als gültige
XML-Datei verarbeitet.

### Ungültiges XML

XML-Parsefehler werden geloggt. Die Anwendung bleibt aktiv und versucht im
nächsten Polling-Zyklus erneut, Daten zu laden.

### Unerwartete Fehler

Unerwartete Fehler werden mit Stacktrace protokolliert. Die bekannten
Entitäten werden auf nicht verfügbar gesetzt; danach läuft die Anwendung
weiter.

### MQTT-Fehler

Fehler beim Einreihen einer MQTT-Nachricht werden geloggt. Der MQTT-Client
führt bei einem Verbindungsabbruch automatische Wiederverbindungsversuche aus.

## ROMId und Ersatzkennungen

Die ROMId wird bevorzugt unter folgenden Namen gesucht:

- `ROMId`;
- `rom_id`;
- `romid`;
- Varianten mit einem zusätzlichen XML-Präfix.

Die ROMId wird für den Topic-Pfad eines Geräts verwendet:

```text
owserver/<romid>/...
```

Wenn in der XML-Datei keine ROMId vorhanden ist, erzeugt das Add-on eine
Ersatzkennung auf Basis eines Hashes der Gerätedaten, zum Beispiel:

```text
ds18b20_f1656b54d156
```

In diesem Fall erscheint eine Warnung im Log. Eine echte ROMId ist
vorzuziehen, weil nur sie dauerhaft stabile Topics und Entity-IDs garantiert.

## Alte Discovery-Entities entfernen

Falls frühere Versionen wegen einer nicht gefundenen ROMId Ersatzkennungen
verwendet haben, können alte MQTT-Discovery-Entities im Broker verbleiben.
Der Grund sind retained Discovery-Nachrichten.

Das Löschen einer Entity in Home Assistant allein genügt dann nicht immer,
weil der Broker die alte Discovery-Konfiguration erneut liefern kann.

### Vorgehen

1. Add-on stoppen.
2. In Home Assistant **Entwicklerwerkzeuge → MQTT** öffnen.
3. Auf Discovery-Topics hören, beispielsweise:

   ```text
   homeassistant/sensor/owserver_v2_enet/#
   ```

4. Ein altes Topic mit der fehlerhaften Kennung ermitteln, zum Beispiel:

   ```text
   homeassistant/sensor/owserver_v2_enet/owserver_owserver_ds18b20_f1656b54d156_temperature/config
   ```

5. Auf exakt dieses `.../config`-Topic eine MQTT-Nachricht mit **leerem
   Payload** veröffentlichen.
6. **Retain** beim Veröffentlichen aktivieren.
7. Dies für jedes alte Discovery-`config`-Topic wiederholen.

Eine leere retained Nachricht löscht die gespeicherte Discovery-Konfiguration
aus dem Broker. Home Assistant entfernt die zugehörige MQTT-Entity danach
normalerweise automatisch.

Optional können auch die alten retained State-Topics entfernt werden:

```text
owserver/ds18b20_f1656b54d156/temperature
owserver/ds18b20_f1656b54d156/health
owserver/ds18b20_f1656b54d156/json
```

Auch dafür eine leere retained Nachricht auf das jeweilige Topic
veröffentlichen. Für das Entfernen der Home-Assistant-Entity sind jedoch vor
allem die Discovery-`config`-Topics entscheidend.

## Optionen

| Option | Standard | Bedeutung |
|---|---:|---|
| `url` | `http://192.168.178.10/details.xml` | URL der OW-SERVER-XML-Datei |
| `poll_interval` | `10` | Abfrageintervall in Sekunden; mindestens 1 |
| `http_timeout` | `10` | Timeout pro HTTP-Versuch in Sekunden |
| `http_retries` | `3` | Maximale Zahl der HTTP-Versuche je Polling-Zyklus, einschließlich Erstversuch |
| `http_username` | leer | Benutzername für optionale HTTP-Basic-Auth |
| `http_password` | leer | Passwort für optionale HTTP-Basic-Auth |
| `topic_prefix` | `owserver` | MQTT-Basis-Topic |
| `discovery_prefix` | `homeassistant` | Prefix für Home-Assistant-MQTT-Discovery |
| `mqtt_discovery` | `true` | MQTT-Discovery aktivieren oder deaktivieren |
| `retained` | `true` | State-, Discovery- und Availability-Nachrichten retained senden |
| `device_name` | leer | Überschreibt den aus XML ermittelten Gerätenamen |
| `mqtt_host` | leer | Externer MQTT-Broker; leer verwendet den HA-MQTT-Service |
| `mqtt_port` | `1883` | Port des MQTT-Brokers |
| `mqtt_username` | leer | Benutzername eines externen MQTT-Brokers |
| `mqtt_password` | leer | Passwort eines externen MQTT-Brokers |
| `mqtt_tls` | `false` | TLS für einen manuell konfigurierten MQTT-Broker aktivieren |
| `log_level` | `INFO` | `DEBUG`, `INFO`, `WARNING` oder `ERROR` |

## Beispielkonfiguration

```yaml
url: "http://192.168.178.10/details.xml"

poll_interval: 10
http_timeout: 10
http_retries: 3

http_username: ""
http_password: ""

topic_prefix: "owserver"
discovery_prefix: "homeassistant"
mqtt_discovery: true
retained: true

device_name: ""

mqtt_host: ""
mqtt_port: 1883
mqtt_username: ""
mqtt_password: ""
mqtt_tls: false

log_level: "INFO"
```

## MQTT-Service und externer Broker

Bleibt `mqtt_host` leer, verwendet das Add-on den Home-Assistant-MQTT-Service,
üblicherweise `core-mosquitto`.

Für einen externen MQTT-Broker:

```yaml
mqtt_host: "192.168.178.20"
mqtt_port: 1883
mqtt_username: "mqtt-benutzer"
mqtt_password: "passwort"
mqtt_tls: false
```

Für TLS:

```yaml
mqtt_tls: true
```

## Sicherheitshinweise

Bei einer XML-URL mit `http://` werden HTTP-Basic-Auth-Zugangsdaten
unverschlüsselt übertragen. HTTP-Basic-Auth sollte deshalb nur in einem
vertrauenswürdigen lokalen Netzwerk verwendet werden.

Für externe MQTT-Broker empfiehlt sich TLS:

```yaml
mqtt_tls: true
```

Passwörter sollten nicht in öffentlich zugänglichen Repositories gespeichert
werden.
