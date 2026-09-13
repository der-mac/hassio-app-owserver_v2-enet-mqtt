#!/usr/bin/env python3
"""
OW-SERVER details.xml -> MQTT / Home-Assistant-MQTT-Discovery.

Die Anwendung liest regelmäßig eine details.xml-Datei eines
Embedded-Data-Systems-OW-SERVERs, wandelt die enthaltenen Werte in MQTT-
Nachrichten um und erzeugt passende MQTT-Discovery-Konfigurationen für
Home Assistant.

Funktionen:

* Abruf der XML-Datei per HTTP oder HTTPS
* optionale HTTP-Basic-Authentifizierung
* bis zu drei HTTP-Versuche pro Abfragezyklus
* robustes XML-Parsing mit und ohne XML-Namespace
* Verarbeitung globaler OW-SERVER-Werte
* generische Verarbeitung aller owd_*-Geräte
* Erkennung und Verwendung der ROMId als Gerätekennung
* sprechende Bezeichnungen für bekannte Family-Codes
* sprechende Bezeichnungen für Health-Codes
* MQTT State Topics
* MQTT JSON-Snapshots
* Home-Assistant-MQTT-Discovery
* Availability Topic und MQTT Last Will
* Setzen betroffener Werte auf "unavailable" bei Fehlern
* kontrolliertes Beenden bei SIGTERM und SIGINT
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import re
import signal
import sys
import time
from dataclasses import dataclass
from typing import Any, Dict, Tuple
from urllib.parse import urlparse
from xml.etree import ElementTree as ET

import paho.mqtt.client as mqtt
import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry


# -----------------------------------------------------------------------------
# Globale Konstanten
# -----------------------------------------------------------------------------

LOG = logging.getLogger("owserver_mqtt")
STOP = False


# Bekannte 1-Wire-Family-Codes.
#
# Der OW-SERVER liefert den Family-Code typischerweise als hexadezimale
# Kennung, zum Beispiel "28" für einen DS18B20.
#
# Die Werte werden nicht nur technisch, sondern bewusst mit einer
# verständlichen deutschen Beschreibung hinterlegt. Unbekannte Codes werden
# später als "Unbekannte 1-Wire-Familie (...)" dargestellt.
FAMILY_NAMES: Dict[str, str] = {
    "05": "Schaltgerät (DS2405)",
    "10": "Temperaturfühler (DS18S20)",
    "12": "Mehrkanal-Schaltgerät (DS2406)",
    "1D": "Zähler (DS2423)",
    "1F": "1-Wire-Koppler (DS2409)",
    "22": "Temperaturfühler (DS1822)",
    "26": "Batterie-/Spannungsmonitor (DS2438)",
    "28": "Temperaturfühler (DS18B20)",
    "29": "Achtkanal-Schaltgerät (DS2408)",
    "30": "Batteriemonitor (DS2760)",
    "3A": "Zweikanal-Schaltgerät (DS2413)",
    "3B": "Temperaturfühler (DS1825)",
    "42": "Temperaturfühler (DS28EA00)",
}


# Diese Felder werden weiterhin vollständig über MQTT und im JSON-Snapshot
# bereitgestellt. In Home Assistant werden sie jedoch standardmäßig als
# Diagnose-Entities angelegt und deaktiviert.
#
# Die Werte gehen dadurch nicht verloren. Sie können bei Bedarf in Home
# Assistant manuell aktiviert oder direkt über MQTT verwendet werden.
DIAGNOSTIC_FIELDS: set[str] = {
    "channel",
    "data_errors_channel1",
    "data_errors_channel2",
    "data_errors_channel3",
    "date_time",
    "description",
    "device_name",
    "device_type",
    "devices_connected",
    "devices_connected_channel1",
    "devices_connected_channel2",
    "devices_connected_channel3",
    "family",
    "health",
    "host_name",
    "loop_time",
    "mac_address",
    "name",
    "poll_count",
    "power_source",
    "primary_value",
    "raw_data",
    "resolution",
    "rom_id",
    "romid",
}


# Bekannte Namensbestandteile für echte Messwerte.
#
# Zahlenfelder, die nicht in DIAGNOSTIC_FIELDS stehen und keinen dieser
# Bestandteile enthalten, werden vorsichtshalber als Diagnosewerte behandelt.
MEASUREMENT_FIELD_HINTS: tuple[str, ...] = (
    "temperature",
    "humidity",
    "pressure",
    "voltage",
    "current",
    "illuminance",
    "light",
    "moisture",
    "wetness",
)


# -----------------------------------------------------------------------------
# Allgemeine Hilfsfunktionen
# -----------------------------------------------------------------------------


def env_bool(name: str, default: bool = False) -> bool:
    """
    Liest einen booleschen Wert aus einer Umgebungsvariable.

    Akzeptierte Werte für "wahr" sind:

    * 1
    * true
    * yes
    * on

    Alle anderen Werte gelten als "falsch". Falls die Variable nicht existiert,
    wird der angegebene Standardwert verwendet.
    """
    return os.getenv(name, str(default)).strip().lower() in {
        "1",
        "true",
        "yes",
        "on",
    }


def strip_namespace(tag: str) -> str:
    """
    Entfernt einen XML-Namespace aus einem Element- oder Attributnamen.

    Beispiel:

        {http://example.org/schema}Temperature -> Temperature
    """
    return tag.rsplit("}", 1)[-1]


def snake_case(value: str) -> str:
    """
    Wandelt einen XML-Namen in einen MQTT- und HA-tauglichen Feldnamen um.

    Beispiele:

    * VoltageChannel2 -> voltage_channel2
    * UserByte1 -> user_byte1
    * MACAddress -> mac_address
    """
    value = re.sub(r"([a-z0-9])([A-Z])", r"\1_\2", value)
    value = re.sub(r"[^A-Za-z0-9]+", "_", value)
    value = value.strip("_").lower()
    return value or "value"


def slug(value: str) -> str:
    """
    Erzeugt einen stabilen, einfachen Bezeichner für MQTT-Topics und IDs.

    Nicht erlaubte Zeichen werden durch Unterstriche ersetzt.
    """
    result = re.sub(r"[^a-zA-Z0-9_]+", "_", value.lower())
    return result.strip("_") or "unknown"


def topic_join(*parts: str) -> str:
    """
    Verbindet mehrere Topic-Bestandteile ohne doppelte Schrägstriche.
    """
    return "/".join(
        str(part).strip("/")
        for part in parts
        if str(part).strip("/")
    )


def parse_scalar(raw: Any) -> Any:
    """
    Wandelt einen XML-Textwert möglichst passend in einen Python-Datentyp um.

    Die Reihenfolge ist bewusst:

    * true/false werden zu bool
    * ganze Zahlen werden zu int
    * Dezimalzahlen und wissenschaftliche Schreibweisen werden zu float
    * alles andere bleibt ein bereinigter String

    Dadurch veröffentlicht MQTT beispielsweise 4.65 als numerischen Inhalt
    und nicht als JSON-String mit zusätzlichen Anführungszeichen.
    """
    value = str(raw or "").strip()

    if value.lower() in {"true", "false"}:
        return value.lower() == "true"

    if re.fullmatch(r"[+-]?\d+", value):
        try:
            return int(value)
        except ValueError:
            pass

    if re.fullmatch(
        r"[+-]?(?:\d+\.\d*|\d*\.\d+|\d+)(?:[eE][+-]?\d+)?",
        value,
    ):
        try:
            return float(value)
        except ValueError:
            pass

    return value


def json_payload(value: Any) -> str:
    """
    Wandelt einen einfachen Wert in eine MQTT-Payload um.

    Boolesche Werte werden als "true"/"false" übertragen. Andere Werte werden
    ohne JSON-Anführungszeichen übertragen, damit Home Assistant numerische
    MQTT-Zustände korrekt erkennen kann.
    """
    if isinstance(value, bool):
        return "true" if value else "false"

    return str(value)


def family_code_key(value: Any) -> str:
    """
    Normalisiert einen 1-Wire-Family-Code für die Zuordnung.

    Family-Codes werden üblicherweise hexadezimal angegeben. Der Code 28 wird
    daher als Kennung "28" behandelt und nicht mathematisch als Dezimalwert
    interpretiert.
    """
    code = str(value or "").strip().upper()

    if code.startswith("0X"):
        code = code[2:]

    return code.zfill(2)


def family_description(value: Any) -> str:
    """
    Liefert eine verständliche deutsche Beschreibung für einen Family-Code.

    Für unbekannte Family-Codes wird absichtlich keine möglicherweise falsche
    Gerätebezeichnung erfunden.
    """
    code = family_code_key(value)
    return FAMILY_NAMES.get(
        code,
        f"Unbekannte 1-Wire-Familie ({code})",
    )


def health_description(value: Any) -> str:
    """
    Übersetzt den Health-Code des OW-SERVERs in eine deutsche Beschreibung.

    Bekannte Zuordnung:

    * 0: Gerät getrennt
    * 1 bis 6: Verbindung eingeschränkt oder instabil
    * 7: Gerät gesund und vollständig funktionsfähig

    Andere Werte werden als unbekannt gemeldet.
    """
    try:
        health = int(value)
    except (TypeError, ValueError):
        return f"Unbekannter Gesundheitsstatus ({value})"

    if health == 0:
        return "Getrennt"

    if 1 <= health <= 6:
        return "Eingeschränkt"

    if health == 7:
        return "Gesund"

    return f"Unbekannter Gesundheitsstatus ({health})"


def flatten(
    element: ET.Element,
    prefix: str = "",
) -> Dict[str, Any]:
    """
    Macht einen XML-Teilbaum zu einem flachen Dictionary.

    XML-Attribute werden ebenfalls aufgenommen. Dabei wird der Attributname
    an den Elementnamen angehängt.

    Beispiel:

        <Temperature Units="Centigrade">23.5</Temperature>

    wird zu:

        {
            "temperature": 23.5,
            "temperature_units": "Centigrade"
        }

    Wiederholte gleichnamige Elemente erhalten einen numerischen Suffix.
    """
    result: Dict[str, Any] = {}
    base = snake_case(prefix) if prefix else ""

    for attr_name, attr_value in element.attrib.items():
        clean_attr = strip_namespace(attr_name)
        key = snake_case(
            f"{base}_{clean_attr}" if base else clean_attr
        )
        result[key] = parse_scalar(attr_value)

    children = list(element)

    if not children:
        if base:
            result[base] = parse_scalar(element.text or "")
        return result

    counts: Dict[str, int] = {}

    for child in children:
        child_name = snake_case(strip_namespace(child.tag))
        counts[child_name] = counts.get(child_name, 0) + 1

        child_prefix = (
            f"{base}_{child_name}"
            if base
            else child_name
        )

        if counts[child_name] > 1:
            child_prefix = f"{child_prefix}_{counts[child_name]}"

        result.update(flatten(child, child_prefix))

    return result


def find_value(
    values: Dict[str, Any],
    *names: str,
) -> Any:
    """
    Sucht einen Wert unter mehreren möglichen Feldnamen.

    Diese Funktion berücksichtigt, dass ältere oder angepasste Parser
    beispielsweise "ROMId", "rom_id", "romid" oder ein Feld mit Präfix liefern
    können.
    """
    for name in names:
        if name in values:
            return values[name]

    normalized_names = {
        snake_case(name)
        for name in names
    }

    for key, value in values.items():
        normalized_key = snake_case(str(key))

        if normalized_key in normalized_names:
            return value

        if any(
            normalized_key.endswith(f"_{candidate}")
            for candidate in normalized_names
        ):
            return value

    return None


# -----------------------------------------------------------------------------
# XML-Datenmodell und XML-Parser
# -----------------------------------------------------------------------------


@dataclass
class ParsedDocument:
    """
    Repräsentiert den verarbeiteten Inhalt einer details.xml-Datei.
    """

    global_values: Dict[str, Any]
    devices: list[Dict[str, Any]]
    host_name: str
    device_name: str
    mac_address: str


def parse_details_xml(xml_text: str) -> ParsedDocument:
    """
    Parst die OW-SERVER-XML-Datei.

    Globale XML-Elemente werden in global_values gespeichert. Alle Elemente,
    deren Name mit "owd_" beginnt, werden als eigenständige 1-Wire-Geräte
    behandelt.

    XML-Namespaces werden ignoriert, da sowohl die Standard-OW-SERVER-XML als
    auch mögliche Varianten robust verarbeitet werden sollen.
    """
    if not xml_text or not xml_text.strip():
        raise ValueError("Die empfangene XML-Antwort ist leer.")

    root = ET.fromstring(xml_text)

    global_values: Dict[str, Any] = {}
    devices: list[Dict[str, Any]] = []

    for child in root:
        tag = strip_namespace(child.tag)

        if tag.lower().startswith("owd_"):
            values = flatten(child)

            device_type = tag[4:] or tag
            values["device_type"] = device_type

            # Zusätzliche sprechende Werte werden direkt in den Datensatz
            # aufgenommen. Die Rohwerte wie family und health bleiben erhalten.
            family = find_value(values, "Family", "family")
            if family not in (None, ""):
                values["family_name"] = family_description(family)

            health = find_value(values, "Health", "health")
            if health not in (None, ""):
                values["health_name"] = health_description(health)

            devices.append(values)
        else:
            field_name = snake_case(tag)
            global_values.update(flatten(child, field_name))

    host_name = str(
        find_value(global_values, "HostName", "host_name") or ""
    )

    device_name = str(
        find_value(global_values, "DeviceName", "device_name") or ""
    )

    mac_address = str(
        find_value(global_values, "MACAddress", "mac_address") or ""
    )

    return ParsedDocument(
        global_values=global_values,
        devices=devices,
        host_name=host_name,
        device_name=device_name,
        mac_address=mac_address,
    )


# -----------------------------------------------------------------------------
# Konfiguration
# -----------------------------------------------------------------------------


@dataclass
class Settings:
    """
    Laufzeitkonfiguration der Anwendung.

    Die Werte werden aus Umgebungsvariablen gelesen. Diese werden normalerweise
    durch die Home-Assistant-App-Konfiguration beziehungsweise das Startskript
    gesetzt.
    """

    url: str
    interval: int
    http_timeout: int
    http_retries: int
    http_username: str
    http_password: str
    topic_prefix: str
    discovery_prefix: str
    discovery_enabled: bool
    retained: bool
    configured_device_name: str
    mqtt_host: str
    mqtt_port: int
    mqtt_username: str
    mqtt_password: str
    mqtt_tls: bool

    @classmethod
    def from_environment(cls) -> "Settings":
        """
        Erstellt die Konfiguration aus den Umgebungsvariablen.

        Pflichtvariablen:

        * OWSERVER_URL
        * MQTT_HOST

        Bei fehlerhaften Zahlenwerten wird bereits beim Start eine Exception
        ausgelöst. Dadurch startet die App nicht mit einer unklaren oder
        teilweise ungültigen Konfiguration.
        """
        url = os.getenv(
            "OWSERVER_URL",
            "http://192.168.178.10/details.xml",
        ).strip()

        mqtt_host = os.getenv(
            "MQTT_HOST",
            "core-mosquitto",
        ).strip()

        if not url:
            raise ValueError("OWSERVER_URL darf nicht leer sein.")

        if not mqtt_host:
            raise ValueError("MQTT_HOST darf nicht leer sein.")

        interval = int(os.getenv("OWSERVER_POLL_INTERVAL", "10"))
        timeout = int(os.getenv("OWSERVER_HTTP_TIMEOUT", "10"))
        retries = int(os.getenv("OWSERVER_HTTP_RETRIES", "3"))
        mqtt_port = int(os.getenv("MQTT_PORT", "1883"))

        if interval < 1:
            raise ValueError("OWSERVER_POLL_INTERVAL muss mindestens 1 sein.")

        if timeout < 1:
            raise ValueError("OWSERVER_HTTP_TIMEOUT muss mindestens 1 sein.")

        if retries < 1:
            raise ValueError("OWSERVER_HTTP_RETRIES muss mindestens 1 sein.")

        if not 1 <= mqtt_port <= 65535:
            raise ValueError("MQTT_PORT liegt außerhalb des gültigen Bereichs.")

        return cls(
            url=url,
            interval=interval,
            http_timeout=timeout,
            http_retries=retries,
            http_username=os.getenv(
                "OWSERVER_HTTP_USERNAME",
                "",
            ),
            http_password=os.getenv(
                "OWSERVER_HTTP_PASSWORD",
                "",
            ),
            topic_prefix=os.getenv(
                "OWSERVER_TOPIC_PREFIX",
                "owserver",
            ).strip("/"),
            discovery_prefix=os.getenv(
                "OWSERVER_DISCOVERY_PREFIX",
                "homeassistant",
            ).strip("/"),
            discovery_enabled=env_bool(
                "OWSERVER_MQTT_DISCOVERY",
                True,
            ),
            retained=env_bool(
                "OWSERVER_RETAINED",
                True,
            ),
            configured_device_name=os.getenv(
                "OWSERVER_DEVICE_NAME",
                "",
            ).strip(),
            mqtt_host=mqtt_host,
            mqtt_port=mqtt_port,
            mqtt_username=os.getenv(
                "MQTT_USERNAME",
                "",
            ),
            mqtt_password=os.getenv(
                "MQTT_PASSWORD",
                "",
            ),
            mqtt_tls=env_bool(
                "MQTT_TLS",
                False,
            ),
        )


# -----------------------------------------------------------------------------
# MQTT-/HTTP-Bridge
# -----------------------------------------------------------------------------


class Bridge:
    """
    Verantwortlich für HTTP-Abruf, XML-Verarbeitung und MQTT-Veröffentlichung.
    """

    def __init__(self, settings: Settings) -> None:
        """
        Initialisiert MQTT-Client, HTTP-Session und interne Zustandslisten.
        """
        self.s = settings

        self.client = mqtt.Client(
            client_id=f"owserver-v2-enet-{os.getpid()}",
            protocol=mqtt.MQTTv311,
        )

        if self.s.mqtt_username:
            self.client.username_pw_set(
                self.s.mqtt_username,
                self.s.mqtt_password,
            )

        if self.s.mqtt_tls:
            self.client.tls_set()

        self.client.reconnect_delay_set(
            min_delay=1,
            max_delay=30,
        )

        self.client.on_connect = self._on_connect
        self.client.on_disconnect = self._on_disconnect

        self.availability_topic = topic_join(
            self.s.topic_prefix,
            "availability",
        )

        # Der Last-Will wird automatisch vom MQTT-Broker veröffentlicht, falls
        # die Verbindung unerwartet abbricht.
        self.client.will_set(
            self.availability_topic,
            "offline",
            qos=1,
            retain=True,
        )

        # Bereits bekannte State-Topics werden bei Fehlern auf "unavailable"
        # gesetzt.
        self.known_state_topics: set[str] = set()

        # Verhindert unnötige Discovery-Veröffentlichungen innerhalb der
        # laufenden Anwendung.
        self.published_discovery: set[str] = set()

        # Wird zunächst aus dem Hostnamen der URL gebildet und später, sobald
        # die XML-Datei gelesen wurde, möglichst durch die MAC-Adresse ersetzt.
        self.identity = self._initial_identity()

        self.http = self._http_session()

    def _initial_identity(self) -> str:
        """
        Erzeugt eine vorläufige Gerätekennung aus dem Hostnamen der URL.
        """
        host = urlparse(self.s.url).hostname or "owserver"
        return slug(host)

    def _http_session(self) -> requests.Session:
        """
        Erstellt eine HTTP-Session mit konfigurierbaren Wiederholungsversuchen.

        HTTP-Fehler wie 500, 502, 503 und 504 sowie bestimmte Verbindungs- und
        Leseprobleme werden automatisch erneut versucht.
        """
        session = requests.Session()

        retry = Retry(
            total=max(0, self.s.http_retries - 1),
            connect=max(0, self.s.http_retries - 1),
            read=max(0, self.s.http_retries - 1),
            status=max(0, self.s.http_retries - 1),
            backoff_factor=0.4,
            status_forcelist=(429, 500, 502, 503, 504),
            allowed_methods=frozenset({"GET"}),
            raise_on_status=False,
        )

        adapter = HTTPAdapter(max_retries=retry)

        session.mount("http://", adapter)
        session.mount("https://", adapter)

        return session

    def _on_connect(
        self,
        _client: mqtt.Client,
        _userdata: Any,
        _flags: Dict[str, int],
        rc: int,
    ) -> None:
        """
        Verarbeitet das Ergebnis eines MQTT-Verbindungsaufbaus.
        """
        if rc == 0:
            LOG.info("Verbindung zum MQTT-Broker hergestellt.")
            self.publish_availability("online")
        else:
            LOG.error(
                "MQTT-Verbindung vom Broker abgelehnt; Rückgabecode %s.",
                rc,
            )

    def _on_disconnect(
        self,
        _client: mqtt.Client,
        _userdata: Any,
        rc: int,
    ) -> None:
        """
        Protokolliert unerwartete MQTT-Verbindungsabbrüche.

        Paho MQTT übernimmt anschließend den erneuten Verbindungsaufbau,
        sofern die Netzwerk-Schleife weiterläuft.
        """
        if rc:
            LOG.warning(
                "MQTT-Verbindung verloren; Rückgabecode %s. "
                "Neuer Verbindungsversuch wird durchgeführt.",
                rc,
            )

    def connect(self) -> None:
        """
        Startet die asynchrone MQTT-Verbindung und die Paho-Netzwerkschleife.
        """
        LOG.info(
            "Verbindung zum MQTT-Broker %s:%s wird hergestellt.",
            self.s.mqtt_host,
            self.s.mqtt_port,
        )

        self.client.connect_async(
            self.s.mqtt_host,
            self.s.mqtt_port,
            keepalive=60,
        )

        self.client.loop_start()

    def stop(self) -> None:
        """
        Veröffentlicht den Offline-Status und beendet MQTT kontrolliert.
        """
        try:
            self.publish_availability("offline")
            self.client.disconnect()
            self.client.loop_stop()
        except Exception:
            LOG.exception(
                "Fehler beim kontrollierten Beenden der MQTT-Verbindung."
            )

    def publish(
        self,
        topic: str,
        payload: Any,
        *,
        retain: bool | None = None,
    ) -> None:
        """
        Veröffentlicht einen einfachen MQTT-Wert.

        Der Rückgabecode wird geprüft. Die tatsächliche Zustellung erfolgt
        asynchron durch die Paho-Netzwerkschleife.
        """
        result = self.client.publish(
            topic,
            json_payload(payload),
            qos=1,
            retain=self.s.retained if retain is None else retain,
        )

        if result.rc != mqtt.MQTT_ERR_SUCCESS:
            LOG.warning(
                "MQTT-Wert konnte nicht veröffentlicht werden: %s "
                "(Rückgabecode %s).",
                topic,
                result.rc,
            )

    def publish_json(
        self,
        topic: str,
        payload: Any,
        *,
        retain: bool | None = None,
    ) -> None:
        """
        Veröffentlicht ein Objekt als kompaktes JSON.

        JSON-Snapshots sind besonders nützlich für Debugging und Anwendungen,
        die mehrere Werte gemeinsam verarbeiten möchten.
        """
        result = self.client.publish(
            topic,
            json.dumps(
                payload,
                ensure_ascii=False,
                separators=(",", ":"),
                default=str,
            ),
            qos=1,
            retain=self.s.retained if retain is None else retain,
        )

        if result.rc != mqtt.MQTT_ERR_SUCCESS:
            LOG.warning(
                "MQTT-JSON konnte nicht veröffentlicht werden: %s "
                "(Rückgabecode %s).",
                topic,
                result.rc,
            )

    def publish_availability(self, state: str) -> None:
        """
        Veröffentlicht den Verfügbarkeitsstatus des OW-SERVERs.
        """
        self.publish(
            self.availability_topic,
            state,
            retain=True,
        )

    def set_unavailable(self) -> None:
        """
        Markiert bei einem Polling-Fehler alle bekannten Sensorwerte als
        "unavailable".

        Die zuletzt bekannten retained Werte werden damit bewusst überschrieben,
        damit Home Assistant keine veralteten Messwerte als aktuell darstellt.
        """
        self.publish_availability("offline")

        for topic in self.known_state_topics:
            self.publish(
                topic,
                "unavailable",
                retain=True,
            )

    def _device_info(
        self,
        parsed: ParsedDocument,
    ) -> Dict[str, Any]:
        """
        Erzeugt die gemeinsame Home-Assistant-Gerätebeschreibung.

        Alle globalen und gerätebezogenen Entities werden einem einzigen
        Home-Assistant-Gerät zugeordnet.
        """
        raw_identity = (
            parsed.mac_address.replace(":", "").replace("-", "")
            or parsed.host_name
            or self.identity
        )

        self.identity = slug(raw_identity)

        name = (
            self.s.configured_device_name
            or parsed.host_name
            or parsed.device_name
            or "OW-SERVER"
        )

        model = parsed.device_name or "OW-SERVER v2-Enet"

        return {
            "identifiers": [
                f"owserver_v2_enet_{self.identity}",
            ],
            "name": name,
            "manufacturer": "Embedded Data Systems",
            "model": model,
            "configuration_url": self.s.url,
        }

    @staticmethod
    def _is_diagnostic_field(
        field: str,
        value: Any,
    ) -> bool:
        """
        Entscheidet, ob eine Entity als Diagnose-Entity markiert wird.

        Diagnose-Entities werden in Home Assistant mit
        "enabled_by_default": false angelegt. Sie bleiben per MQTT verfügbar
        und können in Home Assistant bei Bedarf manuell aktiviert werden.

        UserByte-Felder werden unabhängig von ihrer konkreten Nummer erkannt.
        """
        lower = field.lower()

        if lower.startswith("user_byte"):
            return True

        if lower in DIAGNOSTIC_FIELDS:
            return True

        if isinstance(value, str):
            return True

        if any(
            hint in lower
            for hint in MEASUREMENT_FIELD_HINTS
        ):
            return False

        return True

    @staticmethod
    def _entity_kind(
        value: Any,
        field: str,
    ) -> str:
        """
        Bestimmt den Home-Assistant-Entity-Typ.

        Boolesche Werte werden als binary_sensor veröffentlicht. Alle anderen
        geeigneten Werte werden als normale MQTT-Sensoren veröffentlicht.

        Sehr lange Textfelder und RawData werden nicht per Discovery als
        einzelne Entity angelegt, bleiben aber im MQTT-State-Topic und im
        JSON-Snapshot verfügbar.
        """
        if isinstance(value, bool):
            return "binary_sensor"

        if isinstance(value, str):
            if field == "raw_data" or len(value) > 255:
                return ""

        return "sensor"

    @staticmethod
    def _unit_and_class(
        field: str,
        values: Dict[str, Any],
    ) -> Tuple[str | None, str | None, str | None]:
        """
        Ermittelt Einheit, Device Class und State Class eines Messwerts.

        Rückgabe:

            (Einheit, device_class, state_class)

        Beispiele:

        * temperature -> °C, temperature, measurement
        * voltage_* -> V, voltage, measurement
        * loop_time -> s, duration, measurement
        * sonstige Zahlen -> keine Einheit, measurement
        """
        value = values.get(field)

        numeric = (
            isinstance(value, (int, float))
            and not isinstance(value, bool)
        )

        if not numeric:
            return None, None, None

        lower = field.lower()

        if (
            lower == "temperature"
            or lower.endswith("_temperature")
        ):
            units = str(
                values.get(f"{field}_units", "")
            ).lower()

            unit = "°F" if "fahrenheit" in units else "°C"

            return unit, "temperature", "measurement"

        if "voltage" in lower:
            return "V", "voltage", "measurement"

        if lower == "loop_time" or lower.endswith("_time"):
            return "s", "duration", "measurement"

        return None, None, "measurement"

    def _publish_discovery(
        self,
        object_key: str,
        field: str,
        display_name: str,
        topic: str,
        value: Any,
        values: Dict[str, Any],
        device: Dict[str, Any],
    ) -> None:
        """
        Veröffentlicht oder aktualisiert eine Home-Assistant-Discovery-Konfiguration.

        Der vollständige Feldname wird direkt als Parameter übergeben. Dadurch
        wird verhindert, dass Feldnamen wie "voltage_channel2" versehentlich
        auf nur "channel2" verkürzt werden.
        """
        if not self.s.discovery_enabled:
            return

        entity_kind = self._entity_kind(value, field)

        if not entity_kind:
            return

        unique_id = slug(
            f"owserver_{self.identity}_{object_key}"
        )

        config_topic = topic_join(
            self.s.discovery_prefix,
            entity_kind,
            "owserver_v2_enet",
            unique_id,
            "config",
        )

        fingerprint = (
            f"{config_topic}|{topic}|{display_name}|"
            f"{type(value).__name__}"
        )

        if fingerprint in self.published_discovery:
            return

        is_diagnostic = self._is_diagnostic_field(
            field,
            value,
        )

        config: Dict[str, Any] = {
            "name": display_name,
            "unique_id": unique_id,
            "state_topic": topic,
            "availability_topic": self.availability_topic,
            "payload_available": "online",
            "payload_not_available": "offline",
            "device": device,
            "entity_category": "diagnostic" if is_diagnostic else None,
            "enabled_by_default": False if is_diagnostic else None,
        }

        if entity_kind == "binary_sensor":
            config["payload_on"] = "true"
            config["payload_off"] = "false"
        else:
            unit, device_class, state_class = self._unit_and_class(
                field,
                values,
            )

            if unit:
                config["unit_of_measurement"] = unit

            if device_class:
                config["device_class"] = device_class

            if state_class:
                config["state_class"] = state_class

        config = {
            key: value
            for key, value in config.items()
            if value is not None
        }

        self.publish_json(
            config_topic,
            config,
            retain=True,
        )

        self.published_discovery.add(fingerprint)

    def _publish_values(
        self,
        prefix: str,
        values: Dict[str, Any],
        label_prefix: str,
        device: Dict[str, Any],
        key_prefix: str,
    ) -> None:
        """
        Veröffentlicht alle Werte eines globalen oder gerätebezogenen Bereichs.

        Jeder Wert erhält:

        * ein eigenes MQTT-State-Topic
        * optional eine Home-Assistant-Discovery-Entity

        Zusätzlich werden alle Werte zusammengefasst als JSON veröffentlicht.
        """
        for field, value in values.items():
            topic = topic_join(
                prefix,
                field,
            )

            self.known_state_topics.add(topic)

            self.publish(
                topic,
                value,
            )

            display_name = (
                f"{label_prefix} "
                f"{field.replace('_', ' ').title()}"
            )

            self._publish_discovery(
                f"{key_prefix}_{field}",
                field,
                display_name,
                topic,
                value,
                values,
                device,
            )

    def publish_document(
        self,
        parsed: ParsedDocument,
    ) -> None:
        """
        Veröffentlicht ein vollständig geparstes XML-Dokument.

        Globale Werte werden unter "owserver/status/..." veröffentlicht.
        Gerätewerte werden unter "owserver/<romid>/..." veröffentlicht.

        Die ROMId wird robust aus mehreren möglichen Schreibweisen gesucht.
        Dadurch funktionieren sowohl XML-Parserstände mit "ROMId" als auch
        bereits normalisierte Varianten wie "rom_id".
        """
        device = self._device_info(parsed)

        status_prefix = topic_join(
            self.s.topic_prefix,
            "status",
        )

        self._publish_values(
            status_prefix,
            parsed.global_values,
            "OW-SERVER",
            device,
            "status",
        )

        self.publish_json(
            topic_join(status_prefix, "json"),
            parsed.global_values,
        )

        seen_roms: set[str] = set()

        for index, values in enumerate(
            parsed.devices,
            start=1,
        ):
            device_type = str(
                find_value(
                    values,
                    "device_type",
                    "DeviceType",
                )
                or "1-Wire-Gerät"
            )

            rom = find_value(
                values,
                "ROMId",
                "rom_id",
                "romid",
            )

            if rom in (None, ""):
                digest = hashlib.sha1(
                    json.dumps(
                        values,
                        sort_keys=True,
                        default=str,
                    ).encode("utf-8")
                ).hexdigest()[:12]

                rom = f"{slug(device_type)}_{digest}"

                LOG.warning(
                    "%s besitzt keine ROMId. "
                    "Verwende ersatzweise die generierte Kennung %s.",
                    device_type,
                    rom,
                )

            rom_topic = slug(str(rom))

            if rom_topic in seen_roms:
                rom_topic = f"{rom_topic}_{index}"

                LOG.warning(
                    "Die ROMId %s kommt mehrfach vor. "
                    "Verwende für dieses Gerät das Topic-Suffix %s.",
                    rom,
                    rom_topic,
                )

            seen_roms.add(rom_topic)

            prefix = topic_join(
                self.s.topic_prefix,
                rom_topic,
            )

            self._publish_values(
                prefix,
                values,
                f"{device_type} {rom}",
                device,
                rom_topic,
            )

            self.publish_json(
                topic_join(prefix, "json"),
                values,
            )

        self.publish_availability("online")

    def fetch_and_publish(self) -> None:
        """
        Ruft die XML-Datei ab, validiert sie und veröffentlicht die Daten.

        requests und urllib3 übernehmen die konfigurierten Wiederholungsversuche.
        Bei HTTP-Fehlern, Zeitüberschreitungen oder ungültigem XML wird eine
        Exception an die Hauptschleife weitergegeben.
        """
        auth = None

        if self.s.http_username:
            auth = (
                self.s.http_username,
                self.s.http_password,
            )

        response = self.http.get(
            self.s.url,
            timeout=self.s.http_timeout,
            auth=auth,
        )

        response.raise_for_status()

        if not response.text.strip():
            raise ValueError(
                "Der OW-SERVER hat eine leere XML-Antwort geliefert."
            )

        parsed = parse_details_xml(response.text)

        self.publish_document(parsed)

        LOG.debug(
            "Synchronisation erfolgreich: %d globale Werte und "
            "%d 1-Wire-Gerät(e) veröffentlicht.",
            len(parsed.global_values),
            len(parsed.devices),
        )


# -----------------------------------------------------------------------------
# Signalbehandlung und Hauptroutine
# -----------------------------------------------------------------------------


def signal_handler(
    _signum: int,
    _frame: Any,
) -> None:
    """
    Fordert die Hauptschleife zum kontrollierten Beenden auf.
    """
    global STOP
    STOP = True


def main() -> int:
    """
    Startet die Anwendung und führt die zyklische Synchronisation aus.

    Fehler eines einzelnen Abfragezyklus beenden die Anwendung nicht. Stattdessen
    werden die bekannten Werte auf "unavailable" gesetzt und im nächsten Zyklus
    wird erneut versucht, den OW-SERVER zu erreichen.

    Nur Konfigurationsfehler beim Start oder ein unerwarteter schwerwiegender
    Fehler außerhalb des normalen Polling-Zyklus führen zum Prozessende.
    """
    level_name = os.getenv(
        "OWSERVER_LOG_LEVEL",
        "INFO",
    ).upper()

    logging.basicConfig(
        level=getattr(
            logging,
            level_name,
            logging.INFO,
        ),
        format="%(asctime)s %(levelname)s "
               "[%(name)s] %(message)s",
    )

    try:
        settings = Settings.from_environment()
    except (KeyError, ValueError) as error:
        LOG.critical(
            "Ungültige Konfiguration: %s",
            error,
        )
        return 2

    bridge = Bridge(settings)

    signal.signal(
        signal.SIGTERM,
        signal_handler,
    )

    signal.signal(
        signal.SIGINT,
        signal_handler,
    )

    try:
        bridge.connect()

        LOG.info(
            "OW-SERVER-XML-URL: %s; Abfrageintervall: %s Sekunden.",
            settings.url,
            settings.interval,
        )

        while not STOP:
            started = time.monotonic()

            try:
                bridge.fetch_and_publish()

            except (
                requests.RequestException,
                ET.ParseError,
                ValueError,
            ) as error:
                LOG.error(
                    "OW-SERVER-Abfrage fehlgeschlagen: %s",
                    error,
                )
                bridge.set_unavailable()

            except Exception:
                LOG.exception(
                    "Unerwarteter Fehler während der Synchronisation."
                )
                bridge.set_unavailable()

            elapsed = time.monotonic() - started
            remaining = max(
                0.0,
                settings.interval - elapsed,
            )

            # In kurzen Schritten schlafen, damit das Add-on bei einem
            # Stop-Signal nicht erst bis zum Ende des gesamten Intervalls
            # warten muss.
            while remaining > 0 and not STOP:
                sleep_time = min(
                    remaining,
                    0.5,
                )
                time.sleep(sleep_time)
                remaining -= sleep_time

    except Exception:
        LOG.exception(
            "Schwerwiegender Fehler außerhalb des normalen Polling-Zyklus."
        )
        return 1

    finally:
        bridge.stop()

    LOG.info("OW-SERVER-MQTT-Bridge wurde beendet.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
