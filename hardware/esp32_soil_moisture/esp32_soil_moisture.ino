#include <HTTPClient.h>
#include <LiquidCrystal_I2C.h>
#include <math.h>
#include <WiFi.h>
#include <WiFiClient.h>
#include <Wire.h>

#include "secrets.h"

constexpr int SOIL_MOISTURE_ADC_PIN = 34;
constexpr int DRY_ADC_VALUE = 4095;
constexpr int WET_ADC_VALUE = 2082;
constexpr int SAMPLE_COUNT = 10;
constexpr uint8_t LCD_I2C_ADDRESS = 0x27;
constexpr char BENCH_SENSOR_ID[] = "FG-BENCH-001";

constexpr unsigned long SEND_INTERVAL_MS = 10UL * 1000UL;
constexpr unsigned long WIFI_RECONNECT_INTERVAL_MS = 30UL * 1000UL;
constexpr unsigned long WIFI_CONNECT_TIMEOUT_MS = 20UL * 1000UL;

LiquidCrystal_I2C lcd(LCD_I2C_ADDRESS, 16, 2);
unsigned long lastReadingAt = 0;
unsigned long lastWiFiReconnectAt = 0;
unsigned long wifiStartedAt = 0;
bool wifiConfigured = false;
bool wifiConnected = false;

bool isConfigured(const char* value, const char* placeholder) {
  return value != nullptr && value[0] != '\0' && String(value) != placeholder;
}

float calibratedSoilPercent(int rawValue) {
  const float percent =
      (static_cast<float>(DRY_ADC_VALUE - rawValue) * 100.0f) /
      static_cast<float>(DRY_ADC_VALUE - WET_ADC_VALUE);
  return constrain(percent, 0.0f, 100.0f);
}

const char* soilStatus(float moisturePercent) {
  if (moisturePercent < 30.0f) return "DRY";
  if (moisturePercent < 70.0f) return "NORMAL";
  return "WET";
}

int readSoilRaw() {
  uint32_t total = 0;
  for (int sample = 0; sample < SAMPLE_COUNT; ++sample) {
    total += analogRead(SOIL_MOISTURE_ADC_PIN);
    delay(5);
  }
  return static_cast<int>(total / SAMPLE_COUNT);
}

void writeLcdLine(uint8_t row, const String& text) {
  lcd.setCursor(0, row);
  lcd.print(text.substring(0, 16));
  for (size_t column = text.length(); column < 16; ++column) {
    lcd.print(' ');
  }
}

void updateLocalDisplay(float moisturePercent) {
  writeLcdLine(0, "Soil: " + String(static_cast<int>(roundf(moisturePercent))) + "%");
  writeLcdLine(1, String("Status: ") + soilStatus(moisturePercent));
}

void maintainWiFi() {
  if (!wifiConfigured) return;

  const wl_status_t status = WiFi.status();
  if (status == WL_CONNECTED) {
    if (!wifiConnected) {
      Serial.println("WiFi connected");
      Serial.printf("ESP32 IP: %s\n", WiFi.localIP().toString().c_str());
    }
    wifiConnected = true;
    return;
  }

  if (wifiConnected) {
    Serial.println("WiFi disconnected");
    wifiConnected = false;
  }

  if (status == WL_DISCONNECTED &&
      millis() - lastWiFiReconnectAt >= WIFI_RECONNECT_INTERVAL_MS) {
    Serial.println("WiFi disconnected; requesting reconnect");
    WiFi.reconnect();
    lastWiFiReconnectAt = millis();
  }

  if (wifiStartedAt != 0 &&
      millis() - wifiStartedAt >= WIFI_CONNECT_TIMEOUT_MS &&
      status != WL_CONNECTED) {
    wifiStartedAt = millis();
    Serial.println("WiFi connection is still pending; waiting for station reconnect");
  }
}

void sendReading(float moisturePercent, int rawValue) {
  if (WiFi.status() != WL_CONNECTED) {
    Serial.println("Reading not uploaded: WiFi is not connected");
    return;
  }
  if (!isConfigured(SENSOR_API_KEY, "REPLACE_WITH_SENSOR_API_KEY")) {
    Serial.println("Reading not uploaded: configure SENSOR_API_KEY in secrets.h");
    return;
  }
  if (!isConfigured(SERVER_URL, "REPLACE_WITH_SERVER_URL")) {
    Serial.println("Reading not uploaded: configure SERVER_URL in secrets.h");
    return;
  }

  String payload = "{\"sensor_id\":\"";
  payload += BENCH_SENSOR_ID;
  payload += "\",\"soil_moisture_percent\":";
  payload += String(moisturePercent, 1);
  payload += ",\"raw_value\":";
  payload += String(rawValue);
  payload += "}";

  Serial.println("Payload:");
  Serial.println(payload);

  WiFiClient client;
  HTTPClient http;
  if (!http.begin(client, SERVER_URL)) {
    Serial.println("HTTP error: could not initialize connection");
    return;
  }

  http.setTimeout(10000);
  http.addHeader("Content-Type", "application/json");
  http.addHeader("X-Sensor-Key", SENSOR_API_KEY);
  const int responseCode = http.POST(payload);
  Serial.printf("HTTP status: %d\n", responseCode);
  if (responseCode > 0) {
    Serial.printf("Backend response: %s\n", http.getString().c_str());
  } else {
    Serial.printf("HTTP error: %s\n", http.errorToString(responseCode).c_str());
  }
  http.end();
}

void sampleAndDisplay() {
  const int rawValue = readSoilRaw();
  const float moisturePercent = calibratedSoilPercent(rawValue);
  const char* status = soilStatus(moisturePercent);

  updateLocalDisplay(moisturePercent);
  Serial.printf("Raw ADC: %d\n", rawValue);
  Serial.printf("Moisture: %.1f%%\n", moisturePercent);
  Serial.printf("Status: %s\n", status);

  sendReading(moisturePercent, rawValue);
}

void setup() {
  Serial.begin(115200);
  analogReadResolution(12);
  analogSetPinAttenuation(SOIL_MOISTURE_ADC_PIN, ADC_11db);

  Wire.begin(21, 22);
  lcd.init();
  lcd.backlight();
  writeLcdLine(0, "FloodGuard");
  writeLcdLine(1, "Starting...");

  wifiConfigured =
      isConfigured(WIFI_SSID, "WIFI_SSID") &&
      isConfigured(WIFI_PASSWORD, "WIFI_PASSWORD");
  if (wifiConfigured) {
    WiFi.mode(WIFI_STA);
    WiFi.setAutoReconnect(true);
    Serial.println("Connecting...");
    WiFi.begin(WIFI_SSID, WIFI_PASSWORD);
    lastWiFiReconnectAt = millis();
    wifiStartedAt = millis();
  } else {
    Serial.println("WiFi not configured; local soil readings remain available");
  }

  sampleAndDisplay();
  lastReadingAt = millis();
}

void loop() {
  maintainWiFi();
  if (millis() - lastReadingAt >= SEND_INTERVAL_MS) {
    lastReadingAt = millis();
    sampleAndDisplay();
  }
  delay(50);
}
