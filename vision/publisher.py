import json
import os

import paho.mqtt.client as mqtt
from dotenv import load_dotenv

load_dotenv()
BROKER = os.getenv("MQTT_BROKER", "broker.hivemq.com")
PORT = int(os.getenv("MQTT_PORT", "1883"))
RM = os.getenv("RM", "562881")
TOPIC_EVENTS = f"motiva/{RM}/eventos"


class EventPublisher:
    def __init__(self):
        self.client = mqtt.Client(
            mqtt.CallbackAPIVersion.VERSION2,
            client_id=f"motiva-vision-{RM}-{os.getpid()}",
        )
        if os.getenv("MQTT_USER"):
            self.client.username_pw_set(os.getenv("MQTT_USER"), os.getenv("MQTT_PASS"))
        self.client.connect(BROKER, PORT, keepalive=30)
        self.client.loop_start()

    def publish(self, event):
        payload = json.dumps(event, ensure_ascii=False, default=float)
        info = self.client.publish(TOPIC_EVENTS, payload, qos=1, retain=False)
        info.wait_for_publish(timeout=5)

    def close(self):
        self.client.loop_stop()
        self.client.disconnect()
