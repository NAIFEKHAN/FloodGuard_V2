#include <HTTPClient.h>
#include <WiFi.h>
#include <WiFiClient.h>

// Replace every placeholder before uploading. Do not commit real credentials.
const char* WIFI_SSID = "WIFI_SSID";
const char* WIFI_PASSWORD = "WIFI_PASSWORD";
const char* SERVER_URL = "http://LAPTOP_LAN_IP:8000/api/sensors/readings";
const char* SENSOR_API_KEY = "SENSOR_API_KEY";
const char* SENSOR_ID = "SENSOR_ID";
const char* VILLAGE_LGD_CODE = "VILLAGE_LGD_CODE";

// Select an ADC-capable pin for the exact ESP32 board in use.
const int SOIL_MOISTURE_ADC_PIN = 34;

// Replace these with measured readings from the actual sensor/module.
const int DRY_ADC_VALUE = -1;
const int WET_ADC_VALUE = -1;

const unsigned long SEND_INTERVAL_MS = 60UL * 1000UL;
unsigned long lastSendAt = 0;

float calibratedSoilPercent(int rawValue) {
  const float percent =
      (static_cast<float>(rawValue - DRY_ADC_VALUE) * 100.0f) /
      static_cast<float>(WET_ADC_VALUE - DRY_ADC_VALUE);
  return constrain(percent, 0.0f, 100.0f);
}

void connectToWiFi() {
  WiFi.mode(WIFI_STA);
  WiFi.begin(WIFI_SSID, WIFI_PASSWORD);
  Serial.print("Connecting to Wi-Fi");
  while (WiFi.status() != WL_CONNECTED) {
    delay(500);
    Serial.print(".");
  }
  Serial.println();
  Serial.print("Connected; ESP32 IP: ");
  Serial.println(WiFi.localIP());
}

void sendReading() {
  if (WiFi.status() != WL_CONNECTED) {
    connectToWiFi();
  }
  if (DRY_ADC_VALUE < 0 || WET_ADC_VALUE < 0 ||
      DRY_ADC_VALUE == WET_ADC_VALUE) {
    Serial.println(
        "Calibration required: set distinct DRY_ADC_VALUE and WET_ADC_VALUE.");
    return;
  }

  const int rawValue = analogRead(SOIL_MOISTURE_ADC_PIN);
  const float percent = calibratedSoilPercent(rawValue);
  String payload = "{\"sensor_id\":\"";
  payload += SENSOR_ID;
  payload += "\",\"village_lgd_code\":\"";
  payload += VILLAGE_LGD_CODE;
  payload += "\",\"soil_moisture_percent\":";
  payload += String(percent, 1);
  payload += ",\"raw_value\":";
  payload += String(rawValue);
  payload += "}";

  WiFiClient client;
  HTTPClient http;
  if (!http.begin(client, SERVER_URL)) {
    Serial.println("Could not initialize HTTP connection.");
    return;
  }
  http.addHeader("Content-Type", "application/json");
  http.addHeader("X-Sensor-Key", SENSOR_API_KEY);
  const int responseCode = http.POST(payload);
  Serial.printf("ADC raw value: %d\n", rawValue);
  Serial.printf("Calibrated soil moisture: %.1f%%\n", percent);
  Serial.printf("FloodGuard HTTP status: %d\n", responseCode);
  if (responseCode > 0) {
    Serial.println(http.getString());
  } else {
    Serial.println(http.errorToString(responseCode));
  }
  http.end();
}

void setup() {
  Serial.begin(115200);
  analogReadResolution(12);
  connectToWiFi();
  sendReading();
  lastSendAt = millis();
}

void loop() {
  if (millis() - lastSendAt >= SEND_INTERVAL_MS) {
    lastSendAt = millis();
    sendReading();
  }
}
