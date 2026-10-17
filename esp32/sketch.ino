#include <WiFi.h>
#include <PubSubClient.h>
#include <ArduinoJson.h>

// ---- Config ----
const char* WIFI_SSID = "Wokwi-GUEST";
const char* WIFI_PASS = "";
const char* BROKER = "broker.hivemq.com";
const int   PORT = 1883;
const char* TOPIC_CMD    = "motiva/562881/comandos";
const char* TOPIC_STATUS = "motiva/562881/status";

const int PIN_R = 25, PIN_G = 26, PIN_B = 27, PIN_BUZ = 14;
const unsigned long HEARTBEAT_MS = 5000;   // heartbeat period
const unsigned long RECONNECT_MS = 3000;   // retry period
const unsigned long FAIL_GRACE_MS = 3000;  // offline this long -> FALHA

// ---- State ----
enum State { NORMAL, SUSPEITA, CONFIRMADO, FALHA };
const char* STATE_NAMES[] = {"NORMAL", "SUSPEITA", "CONFIRMADO", "FALHA"};
volatile State state = NORMAL;   // logical state (what the operator decided)
volatile bool commFail = true;   // overlay: FALHA shown while communication is lost
String currentEvent = "";

// Ring buffers for duplicate protection
const int N = 20;
String seenCmds[N];      int seenIdx = 0;
String closedEvents[N];  int closedIdx = 0;

WiFiClient espClient;
PubSubClient mqtt(espClient);
unsigned long lostSince = 0, lastTry = 0, lastHb = 0;
bool simOutage = false;  // test hook: type 'x' in the serial monitor

bool inList(String* list, const String& id) {
  for (int i = 0; i < N; i++) if (list[i] == id) return true;
  return false;
}
void addToList(String* list, int& idx, const String& id) {
  list[idx] = id;
  idx = (idx + 1) % N;
}

// ---- Outputs (own task, so blocking network calls never freeze the LED) ----
void setRGB(bool r, bool g, bool b) {
  digitalWrite(PIN_R, r); digitalWrite(PIN_G, g); digitalWrite(PIN_B, b);
}

void outputsTask(void*) {
  bool buzzing = false;
  for (;;) {
    unsigned long t = millis();
    State s = commFail ? FALHA : state;
    bool wantBuzz = false;
    switch (s) {
      case NORMAL:     setRGB(0, 1, 0); break;
      case SUSPEITA:   { bool on = (t / 500) % 2; setRGB(on, on, 0); break; }
      case CONFIRMADO: setRGB(1, 0, 0); wantBuzz = (t / 400) % 2; break;
      case FALHA:      { bool on = (t / 150) % 2; setRGB(0, 0, on); break; }
    }
    if (wantBuzz && !buzzing) tone(PIN_BUZ, 2000);
    if (!wantBuzz && buzzing) noTone(PIN_BUZ);
    buzzing = wantBuzz;
    vTaskDelay(20 / portTICK_PERIOD_MS);
  }
}

// ---- MQTT ----
void sendAck(const char* cid, const char* eid, bool applied, const char* why) {
  JsonDocument d;
  d["tipo"] = "ack";
  d["command_id"] = cid;
  d["event_id"] = eid;
  d["estado"] = STATE_NAMES[state];
  d["aplicado"] = applied;
  d["motivo"] = why;
  char buf[256];
  serializeJson(d, buf);
  mqtt.publish(TOPIC_STATUS, buf);
}

void onMessage(char* topic, byte* payload, unsigned int len) {
  Serial.println("onMessage called");
  JsonDocument doc;
  if (deserializeJson(doc, payload, len)) return;  // ignore malformed JSON
  String cid = doc["command_id"] | "";
  String eid = doc["event_id"] | "";
  String cmd = doc["comando"] | "";
  if (cid == "" || eid == "" || cmd == "") return;

  // Duplicate command: do NOT act again, but re-ack so the central can settle
  if (inList(seenCmds, cid)) {
    sendAck(cid.c_str(), eid.c_str(), false, "duplicado");
    return;
  }
  addToList(seenCmds, seenIdx, cid);

  bool closed = inList(closedEvents, eid);
  bool applied = false;
  const char* why = "";

  if (cmd == "SUSPEITA") {
    if (closed) why = "evento encerrado";
    else if (state == CONFIRMADO) why = "ocupado (ja confirmado)";  // never downgrade
    else { state = SUSPEITA; currentEvent = eid; applied = true; }
  } else if (cmd == "CONFIRMAR") {
    if (closed) why = "evento encerrado";
    else if (state == CONFIRMADO && currentEvent == eid) why = "ja confirmado";
    else { state = CONFIRMADO; currentEvent = eid; applied = true; }
  } else if (cmd == "REJEITAR" || cmd == "ENCERRAR") {
    if (closed) why = "evento ja encerrado";
    else {
      addToList(closedEvents, closedIdx, eid);  // can never be reopened
      if (currentEvent == eid) { state = NORMAL; currentEvent = ""; }
      applied = true;
    }
  } else {
    why = "comando desconhecido";
  }

  Serial.printf("CMD %s %s -> %s (%s)\n", cmd.c_str(), eid.c_str(),
                applied ? "aplicado" : "ignorado", why);
  sendAck(cid.c_str(), eid.c_str(), applied, why);
}

void tryConnect() {
  const char* id = "motiva-esp32-562881";
  const char* will = "{\"tipo\":\"lwt\",\"estado\":\"FALHA\"}";
  Serial.print("MQTT connecting... ");
  if (mqtt.connect(id, NULL, NULL, TOPIC_STATUS, 1, false, will, false)) {
    mqtt.subscribe(TOPIC_CMD, 1);
    lostSince = 0;
    commFail = false;
    lastHb = millis() - HEARTBEAT_MS;  // heartbeat immediately
    Serial.println("connected");
  } else {
    Serial.printf("failed rc=%d\n", mqtt.state());
  }
}

void handleSerial() {
  while (Serial.available()) {
    char c = Serial.read();
    if (c == 'x') {
      simOutage = !simOutage;
      if (simOutage) espClient.stop();  // abrupt drop: the broker publishes our last will
      Serial.println(simOutage ? "SIM: broker outage ON" : "SIM: broker outage OFF");
    }
  }
}

void setup() {
  Serial.begin(115200);
  Serial.println("MOTIVA ESP32 boot");
  pinMode(PIN_R, OUTPUT); pinMode(PIN_G, OUTPUT);
  pinMode(PIN_B, OUTPUT); pinMode(PIN_BUZ, OUTPUT);
  xTaskCreatePinnedToCore(outputsTask, "outputs", 4096, NULL, 1, NULL, 0);

  WiFi.begin(WIFI_SSID, WIFI_PASS, 6);
  mqtt.setServer(BROKER, PORT);
  mqtt.setCallback(onMessage);
  mqtt.setKeepAlive(15);        // detect a dead broker within seconds
  mqtt.setSocketTimeout(2);
  mqtt.setBufferSize(512);
  lostSince = millis();        // start in FALHA until the first connection
}

void loop() {
  handleSerial();

  if (mqtt.connected()) {
    mqtt.loop();
  } else {
    if (lostSince == 0) lostSince = millis();
    if (!simOutage && WiFi.status() == WL_CONNECTED && millis() - lastTry >= RECONNECT_MS) {
      lastTry = millis();
      tryConnect();
    }
  }
  if (lostSince != 0 && millis() - lostSince > FAIL_GRACE_MS) commFail = true;

  if (mqtt.connected() && millis() - lastHb >= HEARTBEAT_MS) {
    lastHb = millis();
    JsonDocument d;
    d["tipo"] = "heartbeat";
    d["estado"] = STATE_NAMES[state];
    d["evento"] = currentEvent;
    d["rssi"] = WiFi.RSSI();
    char buf[200];
    serializeJson(d, buf);
    mqtt.publish(TOPIC_STATUS, buf);
  }
}
