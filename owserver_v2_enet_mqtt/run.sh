#!/usr/bin/with-contenv bashio
set -e

# Manual MQTT fields take precedence.  If mqtt_host is empty, use the
# Home Assistant MQTT service (normally supplied by the Mosquitto add-on).
if bashio::config.has_value 'mqtt_host'; then
    export MQTT_HOST="$(bashio::config 'mqtt_host')"
    export MQTT_PORT="$(bashio::config 'mqtt_port')"
    export MQTT_USERNAME="$(bashio::config 'mqtt_username')"
    export MQTT_PASSWORD="$(bashio::config 'mqtt_password')"
    export MQTT_TLS="$(bashio::config 'mqtt_tls')"
    bashio::log.info "Using manually configured MQTT broker: ${MQTT_HOST}:${MQTT_PORT}"
elif bashio::services.available 'mqtt'; then
    export MQTT_HOST="$(bashio::services 'mqtt' 'host')"
    export MQTT_PORT="$(bashio::services 'mqtt' 'port')"
    export MQTT_USERNAME="$(bashio::services 'mqtt' 'username')"
    export MQTT_PASSWORD="$(bashio::services 'mqtt' 'password')"
    export MQTT_TLS="false"
    bashio::log.info "Using Home Assistant MQTT service: ${MQTT_HOST}:${MQTT_PORT}"
else
    bashio::log.fatal "No MQTT service is available and mqtt_host is not configured."
    exit 1
fi

export OWSERVER_URL="$(bashio::config 'url')"
export OWSERVER_POLL_INTERVAL="$(bashio::config 'poll_interval')"
export OWSERVER_HTTP_TIMEOUT="$(bashio::config 'http_timeout')"
export OWSERVER_HTTP_USERNAME="$(bashio::config 'http_username')"
export OWSERVER_HTTP_PASSWORD="$(bashio::config 'http_password')"
export OWSERVER_TOPIC_PREFIX="$(bashio::config 'topic_prefix')"
export OWSERVER_DISCOVERY_PREFIX="$(bashio::config 'discovery_prefix')"
export OWSERVER_MQTT_DISCOVERY="$(bashio::config 'mqtt_discovery')"
export OWSERVER_RETAINED="$(bashio::config 'retained')"
export OWSERVER_DEVICE_NAME="$(bashio::config 'device_name')"
export OWSERVER_LOG_LEVEL="$(bashio::config 'log_level')"

exec python3 -u /app/main.py
