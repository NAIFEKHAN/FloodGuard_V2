#include <HTTPClient.h>
#include <WiFi.h>
#include <WiFiClient.h>

#include "secrets.h"

// Confirm ADC-capable pins against the exact ESP32 board and installed wiring.
constexpr int SOIL_MOISTURE_ADC_PIN = 34;
constexpr int RAIN_SENSOR_ADC_PIN = -1;  // Set only after confirming the connected ADC pin.

// Set measured, distinct calibration endpoints before enabling uploads.
constexpr int DRY_ADC_VALUE = -1;
constexpr int WET_ADC_VALUE = -1;

// Optional threshold from testing the actual rain module. Leave -1 for raw-only context.
constexpr int RAIN_WET_RAW_THRESHOLD = -1;
constexpr bool RAIN_IS_WET_BELOW_THRESHOLD = true;

constexpr unsigned long SEND_INTERVAL_MS = 10UL * 1000UL;
constexpr unsigned long WIFI_CONNECT_TIMEOUT_MS = 20UL * 1000UL;
unsigned long lastSendAt = 0;
bool wifiStarted = false;

bool configuredText(const char* value) {
  return value != nullptr && value[0] != '\0' &&
         String(value).indexOf("REPLACE_") != 0 &&
         String(value) != "WIFI_SSID" &&
         String(value) != "WIFI_PASSWORD" &&
         String(value) != "SENSOR_API_KEY" &&
         String(value) != "VILLAGE_LGD_CODE" &&
         String(value).indexOf("CURRENT_LAPTOP_WIFI_IPV4") < 0;
}

bool deviceConfigurationReady() {
  const bool credentialsReady =
      configuredText(WIFI_SSID) && configuredText(WIFI_PASSWORD) &&
      configuredText(SENSOR_API_KEY) && configuredText(SENSOR_ID) &&
      configuredText(VILLAGE_LGD_CODE) && configuredText(SERVER_URL);
  const bool calibrationReady =
      DRY_ADC_VALUE >= 0 && WET_ADC_VALUE >= 0 &&
      DRY_ADC_VALUE != WET_ADC_VALUE;
  if (!credentialsReady) {
    Serial.println("Configuration required: set Wi-Fi, API key, sensor ID, and actual village LGD code in secrets.h.");
  }
  if (!calibrationReady) {
    Serial.println("Calibration required: set measured, distinct DRY_ADC_VALUE and WET_ADC_VALUE. No readings will be submitted.");
  }
  return credentialsReady && calibrationReady;
}

float calibratedSoilPercent(int rawValue) {
  const float percent =
      (static_cast<float>(rawValue - DRY_ADC_VALUE) * 100.0f) /
      static_cast<float>(WET_ADC_VALUE - DRY_ADC_VALUE);
  return constrain(percent, 0.0f, 100.0f);
}

bool ensureWiFiConnected() {
  WiFi.mode(WIFI_STA);
  WiFi.setAutoReconnect(true);
  if (!wifiStarted) {
    WiFi.begin(WIFI_SSID, WIFI_PASSWORD);
    wifiStarted = true;
    Serial.printf("Connecting to Wi-Fi; server: %s\n", SERVER_URL);
  } else if (WiFi.status() != WL_CONNECTED) {
    Serial.println("Wi-Fi disconnected; reconnecting.");
    WiFi.reconnect();
  }

  const unsigned long startedAt = millis();
  while (WiFi.status() != WL_CONNECTED &&
         millis() - startedAt < WIFI_CONNECT_TIMEOUT_MS) {
    delay(250);
    Serial.print(".");
  }
  if (WiFi.status() != WL_CONNECTED) {
    Serial.println("\nWi-Fi connection failed; will retry at the next send interval.");
    return false;
  }

  Serial.printf("\nWi-Fi connected; ESP32 IP: %s\n", WiFi.localIP().toString().c_str());
  return true;
}

void sendReading() {
  const int soilRaw = analogRead(SOIL_MOISTURE_ADC_PIN);
  Serial.printf("Soil Raw: %d\n", soilRaw);

  int rainRaw = -1;
  bool rainStateAvailable = false;
  bool rainDetected = false;
  if (RAIN_SENSOR_ADC_PIN >= 0) {
    rainRaw = analogRead(RAIN_SENSOR_ADC_PIN);
    Serial.printf("Rain Raw: %d\n", rainRaw);
    if (RAIN_WET_RAW_THRESHOLD >= 0) {
      rainDetected = RAIN_IS_WET_BELOW_THRESHOLD
          ? rainRaw <= RAIN_WET_RAW_THRESHOLD
          : rainRaw >= RAIN_WET_RAW_THRESHOLD;
      rainStateAvailable = true;
      Serial.printf("Rain sensor state: %s\n", rainDetected ? "WET DETECTED" : "NO WET STATE DETECTED");
    } else {
      Serial.println("Rain wet/dry threshold not calibrated; sending raw context only.");
    }
  } else {
    Serial.println("Rain ADC pin not configured; rain observation omitted.");
  }

  if (DRY_ADC_VALUE < 0 || WET_ADC_VALUE < 0 ||
      DRY_ADC_VALUE == WET_ADC_VALUE) {
    Serial.println("Reading not submitted: soil-moisture calibration is incomplete.");
    return;
  }
  const float soilPercent = calibratedSoilPercent(soilRaw);
  Serial.printf("Soil Moisture: %.1f%%\n", soilPercent);
  if (!deviceConfigurationReady()) return;
  if (!ensureWiFiConnected()) return;

  String payload = "{\"sensor_id\":\"";
  payload += SENSOR_ID;
  payload += "\",\"village_lgd_code\":\"";
  payload += VILLAGE_LGD_CODE;
  payload += "\",\"soil_moisture_percent\":";
  payload += String(soilPercent, 1);
  payload += ",\"raw_value\":";
  payload += String(soilRaw);
  if (rainRaw >= 0) {
    payload += ",\"rain_raw\":";
    payload += String(rainRaw);
  }
  if (rainStateAvailable) {
    payload += ",\"rain_detected\":";
    payload += rainDetected ? "true" : "false";
  }
  payload += "}";

  WiFiClient client;
  HTTPClient http;
  if (!http.begin(client, SERVER_URL)) {
    Serial.println("Could not initialize HTTP connection.");
    return;
  }
  http.setTimeout(10000);
  http.addHeader("Content-Type", "application/json");
  http.addHeader("X-Sensor-Key", SENSOR_API_KEY);
  Serial.printf("Sending reading to %s\n", SERVER_URL);
  const int responseCode = http.POST(payload);
  Serial.printf("HTTP status: %d\n", responseCode);
  if (responseCode > 0) {
    Serial.printf("Backend response: %s\n", http.getString().c_str());
  } else {
    Serial.printf("HTTP error: %s\n", http.errorToString(responseCode).c_str());
  }
  http.end();
}

void setup() {
  Serial.begin(115200);
  analogReadResolution(12);
  Serial.printf("FloodGuard sensor endpoint: %s\n", SERVER_URL);
  if (deviceConfigurationReady()) {
    ensureWiFiConnected();
  }
  sendReading();
  lastSendAt = millis();
}

void loop() {
  if (millis() - lastSendAt >= SEND_INTERVAL_MS) {
    lastSendAt = millis();
    sendReading();
  }
}
