# Basis-Image für Home-Assistant-Add-ons.
# Das Argument BUILD_FROM wird durch die Datei build.yaml oder die
# Home-Assistant-Builder-Infrastruktur automatisch gesetzt.
ARG BUILD_FROM
FROM $BUILD_FROM

# Erforderliche Python-Bibliotheken installieren.
# ==
# paho-mqtt : MQTT-Client für die Kommunikation mit dem Broker
# requests  : HTTP-Client für den periodischen OW-SERVER-Abruf
# ==
RUN pip3 install --no-cache-dir \
    paho-mqtt==1.6.1 \
    requests==2.32.3

# Anwendungscode kopieren.
# Der Ordner "app/" muss sich im Build-Kontext (also neben diesem Dockerfile)
# befinden und die Datei "main.py" enthalten.
COPY app /app

# Startskript kopieren und ausführbar machen.
COPY run.sh /run.sh
RUN chmod a+x /run.sh

# Ausführungsrechte für das Python-Skript setzen.
# Dies ist erforderlich, damit das Startskript die Anwendung direkt
# via "exec /app/main.py" starten kann, ohne vorangestelltes "python3".
RUN chmod a+x /app/main.py

# Sicherstellen, dass alle Dateien unter /app für den Prozess lesbar sind.
RUN chmod -R a+rX /app

# Das Startskript wird als PID 1 des Containers ausgeführt.
# Es liest die Home-Assistant-Add-on-Konfiguration aus, exportiert die
# Werte als Umgebungsvariablen und startet anschließend die Python-
# Anwendung per "exec" (wichtig für korrektes Signalhandling bei Stop).
CMD [ "/run.sh" ]
