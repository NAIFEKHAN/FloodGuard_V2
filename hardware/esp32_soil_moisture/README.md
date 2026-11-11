# FloodGuard ESP32 bench soil-moisture sensor

This optional hardware integration reports only the measured soil-moisture percentage and ADC value. `FG-BENCH-001` is a Chennai bench sensor with no village assignment or map coordinates; it is not a Nilgiris sensor and does not supply rainfall data.

## Wiring

| Device | ESP32 DevKit V1 |
| --- | --- |
| Soil sensor VCC | 3V3 |
| Soil sensor GND | GND |
| Soil sensor AO | GPIO34 |
| LCD1602 I2C VCC | VIN / 5V |
| LCD1602 I2C GND | GND |
| LCD SDA | GPIO21 |
| LCD SCL | GPIO22 |

Leave the soil sensor DO disconnected. The LCD backpack address is `0x27`.

## Arduino setup

Use the ESP32 board support package and select **ESP32 Dev Module**. Install a `LiquidCrystal_I2C` library that supports `LiquidCrystal_I2C(address, columns, rows)`, `init()`, and `backlight()`.

### 1. Verify the hardware without Wi-Fi

Open `hardware_test\hardware_test.ino` in Arduino IDE, upload it, and open Serial Monitor at **115200 baud**. The startup scan should report `I2C device found at 0x27`; the LCD shows `FloodGuard` / `LCD Working` before live soil readings begin.

With the probe dry, compare `Raw ADC` with the measured dry endpoint (about 4095). Insert the probe into wet soil and check that the raw ADC falls significantly. The sketch averages ten samples and prints the calibrated percentage and `DRY`, `NORMAL`, or `WET` status. Calibration maps dry 4095 to 0% and wet 2082 to 100%; readings are clamped to 0–100%. Those endpoints are bench calibration values, not universal sensor constants.

### 2. Configure and upload the Wi-Fi integration

Copy `secrets.h.example` to `secrets.h` in this directory. Set the local Wi-Fi SSID/password, the API key configured for the backend, and the server URL. Do not commit `secrets.h`; it is already ignored by Git. Never put either credential in frontend files.

Determine the laptop's current Wi-Fi address with `ipconfig`, then set:

```text
http://<CURRENT_LAPTOP_WIFI_IPV4>:8000/api/sensors/readings
```

Do not reuse an old address. Run the backend on `0.0.0.0:8000`, allow inbound TCP port 8000 through the laptop firewall on the private network, and open `http://<CURRENT_LAPTOP_WIFI_IPV4>:8000/docs` from another device on the same Wi-Fi before testing uploads.

Upload `esp32_soil_moisture.ino`. The sketch keeps the local LCD and Serial readings active independently of Wi-Fi. It averages ten ADC samples and sends one reading every ten seconds when the station is connected. It calls `WiFi.begin` once at startup; later reconnection requests are throttled and only issued when the station reports disconnected.

The only uploaded fields are `sensor_id`, `soil_moisture_percent`, and `raw_value`. The request uses `Content-Type: application/json` and `X-Sensor-Key`; a successful backend ingestion returns HTTP 202. A negative HTTP status indicates a connectivity/server issue; 401/403 indicates an API-key issue; 404 indicates a route or registration issue; 422 indicates a payload/schema issue.

### 3. Register the village-less bench sensor

With the backend running and its API key set locally, register this exact record once through `POST /api/sensors` using the `X-Sensor-Key` header:

```json
{
  "sensor_id": "FG-BENCH-001",
  "sensor_name": "FloodGuard Chennai Bench Sensor",
  "sensor_type": "soil_moisture",
  "is_test": true,
  "village_lgd_code": null,
  "latitude": null,
  "longitude": null
}
```

The endpoint returns 201 for registration. If it returns 409, check `GET /api/sensors` before deciding whether the existing registration is the intended bench record. Do not assign a village or coordinates to make the test sensor appear on the map.

## Verification and limits

After a real device upload, check `GET /api/sensors` for the latest reading, `is_test: true`, and freshness status. The dashboard's **LIVE BENCH SENSOR** panel reads that inventory endpoint and displays no measurement until an actual reading has been stored. The bench sensor is not geolocated, is not part of a village context, and must not be treated as a rainfall observation or a village warning input.

Hardware-dependent checks (LCD detection, live ADC range, Wi-Fi association, POSTs, and wet/dry response) require the board, probe, LCD, and same-Wi-Fi second device. Do not substitute sample readings for those physical checks.
