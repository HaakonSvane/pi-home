import logging
import os
import time

import adafruit_dht
import board
import paho.mqtt.client as mqtt

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger("dht22-reader")

MQTT_HOST = os.environ.get("MQTT_HOST", "mosquitto")
MQTT_PORT = int(os.environ.get("MQTT_PORT", "1883"))
TOPIC_PREFIX = os.environ.get("MQTT_TOPIC_PREFIX", "home/dht22")
READ_INTERVAL_SECONDS = int(os.environ.get("READ_INTERVAL_SECONDS", "30"))
MAX_RETRIES = int(os.environ.get("MAX_RETRIES", "5"))


def read_sensor(sensor):
    for attempt in range(1, MAX_RETRIES + 1):
        try:
            temperature_c = sensor.temperature
            humidity = sensor.humidity
            if temperature_c is not None and humidity is not None:
                return temperature_c, humidity
        except RuntimeError as error:
            # DHT22 checksum/timing failures are expected occasionally; retry rather than crash.
            log.warning("Read attempt %d/%d failed: %s", attempt, MAX_RETRIES, error)
        time.sleep(2)
    return None, None


def main():
    sensor = adafruit_dht.DHT22(board.D4, use_pulseio=False)
    client = mqtt.Client()
    client.connect(MQTT_HOST, MQTT_PORT)
    client.loop_start()

    try:
        while True:
            temperature_c, humidity = read_sensor(sensor)
            if temperature_c is not None and humidity is not None:
                client.publish(f"{TOPIC_PREFIX}/temperature", round(temperature_c, 1), retain=True)
                client.publish(f"{TOPIC_PREFIX}/humidity", round(humidity, 1), retain=True)
                log.info("Published temperature=%.1fC humidity=%.1f%%", temperature_c, humidity)
            else:
                log.error("Failed to read sensor after %d attempts", MAX_RETRIES)
            time.sleep(READ_INTERVAL_SECONDS)
    finally:
        sensor.exit()
        client.loop_stop()
        client.disconnect()


if __name__ == "__main__":
    main()
