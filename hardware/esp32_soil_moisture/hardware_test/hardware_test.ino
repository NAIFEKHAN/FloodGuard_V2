#include <LiquidCrystal_I2C.h>
#include <math.h>
#include <Wire.h>

constexpr int SOIL_MOISTURE_ADC_PIN = 34;
constexpr int DRY_ADC_VALUE = 4095;
constexpr int WET_ADC_VALUE = 2082;
constexpr int SAMPLE_COUNT = 10;
constexpr uint8_t LCD_I2C_ADDRESS = 0x27;

LiquidCrystal_I2C lcd(LCD_I2C_ADDRESS, 16, 2);
bool lcdDetected = false;

int readSoilRaw() {
  uint32_t total = 0;
  for (int sample = 0; sample < SAMPLE_COUNT; ++sample) {
    total += analogRead(SOIL_MOISTURE_ADC_PIN);
    delay(5);
  }
  return static_cast<int>(total / SAMPLE_COUNT);
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

void writeLcdLine(uint8_t row, const String& text) {
  lcd.setCursor(0, row);
  lcd.print(text.substring(0, 16));
  for (size_t column = text.length(); column < 16; ++column) {
    lcd.print(' ');
  }
}

void setup() {
  Serial.begin(115200);
  analogReadResolution(12);
  analogSetPinAttenuation(SOIL_MOISTURE_ADC_PIN, ADC_11db);

  Wire.begin(21, 22);
  Serial.println("I2C scan starting");
  for (uint8_t address = 1; address < 127; ++address) {
    Wire.beginTransmission(address);
    const uint8_t error = Wire.endTransmission();
    if (error == 0) {
      Serial.printf("I2C device found at 0x%02X\n", address);
      if (address == LCD_I2C_ADDRESS) lcdDetected = true;
    }
  }

  lcd.init();
  lcd.backlight();
  writeLcdLine(0, "FloodGuard");
  writeLcdLine(1, lcdDetected ? "LCD Working" : "LCD not found");

  Serial.printf("LCD 0x%02X: %s\n", LCD_I2C_ADDRESS,
                lcdDetected ? "detected" : "not detected");
  Serial.println("Hardware test ready; WiFi is not used.");
  delay(2000);
}

void loop() {
  const int rawValue = readSoilRaw();
  const float moisturePercent = calibratedSoilPercent(rawValue);
  const char* status = soilStatus(moisturePercent);

  Serial.printf("Raw ADC: %d\n", rawValue);
  Serial.printf("Moisture: %.1f%%\n", moisturePercent);
  Serial.printf("Status: %s\n", status);

  writeLcdLine(0, "Soil: " + String(static_cast<int>(roundf(moisturePercent))) + "%");
  writeLcdLine(1, String("Status: ") + status);
  delay(1000);
}
