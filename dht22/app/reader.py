import logging
import math
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
SAMPLE_INTERVAL_SECONDS = int(os.environ.get("SAMPLE_INTERVAL_SECONDS", "15"))
PUBLISH_INTERVAL_SECONDS = int(os.environ.get("PUBLISH_INTERVAL_SECONDS", "120"))
SAMPLES_PER_WINDOW = max(1, PUBLISH_INTERVAL_SECONDS // SAMPLE_INTERVAL_SECONDS)
MAX_RETRIES = int(os.environ.get("MAX_RETRIES", "5"))

# Alduchov-Eskridge (1996) refined Magnus-Tetens constants — accurate to ~0.35C
# across -40..50C, well inside the DHT22's own +-0.5C/+-2-5%RH sensor error budget.
MAGNUS_A = 17.625
MAGNUS_B = 243.04


def dew_point_celsius(temperature_c, relative_humidity_pct):
    gamma = math.log(relative_humidity_pct / 100.0) + (MAGNUS_A * temperature_c) / (MAGNUS_B + temperature_c)
    return (MAGNUS_B * gamma) / (MAGNUS_A - gamma)


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


def collect_window(sensor):
    samples = []
    for i in range(SAMPLES_PER_WINDOW):
        temperature_c, humidity = read_sensor(sensor)
        if temperature_c is not None and humidity is not None:
            samples.append((temperature_c, humidity))
        if i < SAMPLES_PER_WINDOW - 1:
            time.sleep(SAMPLE_INTERVAL_SECONDS)
    return samples


def main():
    sensor = adafruit_dht.DHT22(board.D4, use_pulseio=False)
    client = mqtt.Client(callback_api_version=mqtt.CallbackAPIVersion.VERSION2)
    client.connect(MQTT_HOST, MQTT_PORT)
    client.loop_start()

    try:
        while True:
            samples = collect_window(sensor)
            if samples:
                avg_temperature = sum(t for t, _ in samples) / len(samples)
                avg_humidity = sum(h for _, h in samples) / len(samples)
                dew_point = dew_point_celsius(avg_temperature, avg_humidity)

                client.publish(f"{TOPIC_PREFIX}/temperature", round(avg_temperature, 1), retain=True)
                client.publish(f"{TOPIC_PREFIX}/humidity", round(avg_humidity, 1), retain=True)
                client.publish(f"{TOPIC_PREFIX}/dew_point", round(dew_point, 1), retain=True)
                log.info(
                    "Published temperature=%.1fC humidity=%.1f%% dew_point=%.1fC (%d/%d samples)",
                    avg_temperature, avg_humidity, dew_point, len(samples), SAMPLES_PER_WINDOW,
                )
            else:
                log.error("All %d samples failed this window; skipping publish", SAMPLES_PER_WINDOW)
    finally:
        sensor.exit()
        client.loop_stop()
        client.disconnect()


if __name__ == "__main__":
    main()
