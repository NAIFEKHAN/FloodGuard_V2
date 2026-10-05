# ESP32 soil-moisture integration

This is an optional, supplementary **observed current-condition** input. The
sensor API stores data locally and associates it with the canonical
`village_lgd_code`. Soil moisture is not a baseline XGBoost feature and does not
change susceptibility scores, warning thresholds, or rainfall-scenario results.
No readings are created by the application unless a device or an explicitly
labelled test command submits them.

## Architecture and storage

```text
ESP32 + calibrated analog sensor
  -> POST /api/sensors/readings (X-Sensor-Key)
  -> FastAPI validation and village/sensor match
  -> local SQLite (data/sensors.sqlite3 by default)
  -> sensor, village soil-moisture, and village-context APIs
  -> selected-village drawer and optional map marker
```

SQLite uses the Python standard library. Set `SENSOR_DATABASE_PATH` to move the
database elsewhere. SQLite files are local state and are ignored by Git.

The sensor record has `sensor_id` (unique), `sensor_name`,
`village_lgd_code`, optional latitude/longitude, `sensor_type` (currently only
`soil_moisture`), optional `installed_at`, server `registered_at`, and an
`is_test` flag. The reading record has a generated `reading_id`, sensor and LGD
IDs, `soil_moisture_percent`, optional soil ADC value (`raw_value`), optional
`rain_raw` and `rain_detected`, observation `recorded_at`, server-set
`received_at`, and `is_test`. Existing SQLite databases are migrated
additively; soil-only readings remain valid and rain fields stay null when not
sent. The API key is never stored in the database.

## Authentication and API

Writes are disabled until the backend process has a `SENSOR_API_KEY`. The same
prototype key protects both sensor registration and device readings. Generate
a local value in PowerShell and keep it out of source control:

```powershell
$env:SENSOR_API_KEY = python -c "import secrets; print(secrets.token_urlsafe(32))"
$env:SENSOR_API_KEY
```

The key must also be configured in the local, ignored `secrets.h` before
upload. This shared
key is a simple prototype mechanism, not per-device identity or production
credential management. Do not use the device on an untrusted network; rotate
the key if the device or firmware is exposed.

Register the device once using its real sensor ID and canonical village LGD
code. Coordinates are optional; they are only needed for a map marker.

```powershell
$headers = @{ "X-Sensor-Key" = $env:SENSOR_API_KEY }
$sensor = @{
  sensor_id = "FG-NIL-001"
  sensor_name = "Kodanad Soil Sensor 1"
  village_lgd_code = "635099"
  sensor_type = "soil_moisture"
  latitude = 11.4
  longitude = 76.8
}
Invoke-RestMethod -Method Post -Uri "http://127.0.0.1:8000/api/sensors" `
  -Headers $headers -ContentType "application/json" -Body ($sensor | ConvertTo-Json)
```

Reading contract:

```http
POST /api/sensors/readings
X-Sensor-Key: <configured key>
Content-Type: application/json
```

```json
{
  "sensor_id": "FG-NIL-001",
  "village_lgd_code": "635099",
  "soil_moisture_percent": 68.4,
  "raw_value": 1750,
  "rain_raw": 2310,
  "rain_detected": true
}
```

`soil_moisture_percent` must be finite and between 0 and 100. `raw_value` is
kept separately. `rain_raw` and `rain_detected` are optional local sensor
context; absent fields stay null. Send `rain_detected` only after testing a
threshold for the actual module. Rain ADC data is not rainfall depth, IMD
rainfall, or a rainfall-scenario multiplier, and it does not enter the model
or warning engine. `recorded_at` is optional; if omitted, the server time is
used for both timestamps. The server always sets `received_at`, which
determines freshness. Unknown sensors are rejected, and the submitted LGD code
must match the registered sensor. A reading cannot change the registration's
real/test classification.

For an authenticated bench device that is not installed in a supported
village, register it with `is_test: true` and a null/omitted
`village_lgd_code`. A reading from that registered test sensor may omit both
`village_lgd_code` and `is_test`; the server inherits its test designation from
the registration. Non-test registrations and readings remain village-bound.
Unassigned bench data appears only in the sensor inventory, labelled
`TEST / BENCH`; it is excluded from village context, village warnings, and
map markers.

Successful ingestion returns HTTP 202, for example:

```json
{
  "status": "accepted",
  "sensor_id": "FG-NIL-001",
  "soil_moisture_percent": 68.4,
  "rain_raw": null,
  "rain_detected": null,
  "recorded_at": "2026-10-05T05:00:00+00:00",
  "received_at": "2026-10-05T05:02:00+00:00",
  "sensor_status": "online",
  "is_test": false
}
```

Read APIs:

| Endpoint | Purpose |
| --- | --- |
| `POST /api/sensors` | Authenticated registration; a sensor ID is unique |
| `POST /api/sensors/readings` | Authenticated soil reading with optional local rain context |
| `GET /api/sensors` | Registered sensors, latest readings, freshness, village metadata |
| `GET /api/sensors/{sensor_id}` | One sensor and its latest reading/status |
| `GET /api/sensors/{sensor_id}/readings?limit=50` | Newest readings; limit is 1–500 |
| `GET /api/villages/{village_code}/soil-moisture` | Latest village reading and provenance, or unavailable/null |
| `GET /api/villages/{village_code}/context` | Existing context plus soil moisture and sensor metadata |

The API returns `online` for a server-received reading up to and including 10 minutes
old, `stale` for more than 10 and up to 30 minutes, and `offline` after 30
minutes. A registered sensor with no reading is `no_data`. Freshness is computed
by the backend from server-side `received_at`; the frontend does not reimplement
these cutoffs. Stale and offline readings remain visible as the **last reading**
but are not labelled current. No-sensor villages return unavailable status and
null reading data.

The data-source catalog reports the ESP32 source as `observed`, `planned` until
at least one non-test sensor is registered, and `available` after a real sensor
is configured. Availability means configured, not that a recent reading exists;
per-sensor freshness and missing data are reported separately. Provenance
includes sensor ID and recorded/received timestamps when there is a reading.

## Wiring overview

Use the sensor manufacturer's documentation and the exact ESP32 board
documentation. The known soil-moisture analog input is GPIO 34. The rain-sensor
ADC pin is not recorded in project configuration; leave
`RAIN_SENSOR_ADC_PIN = -1` until the actual wire/pin is confirmed. Generically,
connect sensor power and ground to compatible board power/ground, and the
analog output to a supported ADC input. Check that the sensor output voltage
never exceeds the board's ADC input limit. This project does not assume a
sensor module, supply voltage, rain wiring pin, or calibration scale.

## Calibration

Before readings are transmitted, the sketch requires distinct,
non-negative `DRY_ADC_VALUE` and `WET_ADC_VALUE` constants. Determine them
using the actual sensor/module and installation:

1. With the sensor in the agreed dry reference condition, record several
   stable ADC readings in the Serial Monitor and choose a representative
   `DRY_ADC_VALUE`.
2. Repeat in the agreed wet reference condition and choose a representative
   `WET_ADC_VALUE`.
3. Set both constants in the sketch. The formula supports either ADC direction:
   wet may produce a lower or a higher raw value.
4. Verify intermediate conditions and repeat the calibration if values are
   noisy or saturated.

These values convert the measured ADC range linearly to a percentage; that is
an instrument calibration, not a universal volumetric water-content
measurement. The UI labels Dry / Moderate / Wet / Very Wet are descriptive
display bands only (0–30, >30–60, >60–80, >80–100%). They are not scientific
landslide thresholds and are never used for warnings or risk calculations.

## Laptop and local-network setup (Windows)

1. Connect the laptop and ESP32 to the same Wi-Fi network. Guest Wi-Fi or
   client isolation may prevent devices from reaching one another.
2. Set `SENSOR_API_KEY` in the PowerShell session that will start FastAPI.
3. Find the laptop's Wi-Fi IPv4 address (not loopback):

   ```powershell
   Get-NetIPAddress -AddressFamily IPv4 |
     Where-Object { $_.IPAddress -notlike "127.*" }
   ```

4. Start the service from the repository root, listening on the LAN:

   ```powershell
   $env:SENSOR_API_KEY = "YOUR_SENSOR_API_KEY"
   python -m uvicorn backend.app.main:app --host 0.0.0.0 --port 8000 --reload
   ```

4. Verify `http://127.0.0.1:8000/docs` on the laptop. The previously supplied
   Wi-Fi IPv4 `192.168.13.207` was not assigned during the latest workspace
   check; the observed Wi-Fi address was `10.94.116.150`. Always confirm the
   current laptop Wi-Fi IPv4:

   ```powershell
   Get-NetIPAddress -AddressFamily IPv4 |
     Where-Object { $_.InterfaceAlias -eq "Wi-Fi" -and $_.AddressState -eq "Preferred" }
   ```

   From a phone/second device on the same Wi-Fi, open
   `http://<CURRENT_LAPTOP_WIFI_IPV4>:8000/docs`. Uvicorn must bind to
   `0.0.0.0`. If unreachable, check same-Wi-Fi/client isolation and allow
   Python/Uvicorn through Windows Firewall on the Private profile only; never
   disable the firewall globally.
5. Set `SERVER_URL` in local `secrets.h` to
   `http://<CURRENT_LAPTOP_WIFI_IPV4>:8000/api/sensors/readings`. Never use
   `127.0.0.1` from the ESP32.
6. If Windows Firewall prompts, allow Python inbound TCP port 8000 only on a
   trusted **Private** network. If a rule is needed, create it only for the
   private profile, for example from an elevated PowerShell:

   ```powershell
   New-NetFirewallRule -DisplayName "FloodGuard sensor API (Private)" `
     -Direction Inbound -Action Allow -Protocol TCP -LocalPort 8000 -Profile Private
   ```

Do not expose this prototype server or its API key to the public internet.

## Upload and verify the ESP32 sketch

Sketch: [hardware/esp32_soil_moisture/esp32_soil_moisture.ino](../hardware/esp32_soil_moisture/esp32_soil_moisture.ino).

1. Copy `hardware/esp32_soil_moisture/secrets.h.example` to the ignored local
   `secrets.h`; fill Wi-Fi credentials, API key, sensor ID, and the canonical
   village LGD code for the sensor's actual installation. Never commit
   `secrets.h`. A bench test in Chennai must not be registered against an
   unrelated Nilgiris village; wait for a valid deployment-village mapping
   before enabling sensor POSTs.
2. Coordinates are optional. Leave them null unless the actual installation
   location is known; village context works without a map marker.
3. Soil ADC is GPIO 34. Confirm board ADC capability, sensor voltage limits,
   and physical wiring. Rain ADC remains disabled (`-1`) until the connected
   pin is confirmed.
4. Set `DRY_ADC_VALUE` and `WET_ADC_VALUE` from physical calibration. Firmware
   prints raw ADC but will not POST until both are non-negative and distinct.
   Do not treat generic values as calibrated.
5. Optionally set the rain ADC pin after confirming wiring. `rain_raw` is only
   local context. To send `rain_detected`, determine the module-specific
   threshold and direction experimentally; otherwise keep its threshold at
   `-1`. It is not a calibrated rain gauge and must not be represented in mm
   or used by warnings/risk calculations.
6. Register the real sensor once before upload. Replace the village placeholder
   only with the supported LGD code for the real installation; omit coordinates
   unless known:

   ```powershell
   $headers = @{ "X-Sensor-Key" = $env:SENSOR_API_KEY }
   $sensor = @{
     sensor_id = "FG-NIL-001"
     sensor_name = "ESP32 soil-moisture sensor"
     village_lgd_code = "ACTUAL_SUPPORTED_VILLAGE_LGD_CODE"
     sensor_type = "soil_moisture"
     is_test = $false
   }
   Invoke-RestMethod -Method Post -Uri "http://127.0.0.1:8000/api/sensors" `
     -Headers $headers -ContentType "application/json" -Body ($sensor | ConvertTo-Json)
   ```

   HTTP 409 means the ID already exists. Do not create another registration to
   bypass it; inspect `GET /api/sensors/{sensor_id}` and confirm the existing
   village and type. The API does not update registration metadata.
7. Open the sketch in Arduino IDE and select the exact ESP32 board.
   `WiFi`, `HTTPClient`, and `WiFiClient` come from its Arduino core. Upload,
   open Serial Monitor at 115200 baud, and check Wi-Fi status/IP, URL, raw
   values, calibrated percentage, HTTP code, and backend response. The key is
   never printed. Hardware test interval is 10 seconds; use a configurable
   30–60 seconds for production/demo operation.
8. On HTTP 202, verify `GET /api/sensors`,
   `GET /api/villages/{village_code}/soil-moisture`, and
   `GET /api/villages/{village_code}/context`; confirm matching sensor/village,
   `is_test: false`, freshness, and rain fields/nulls. The dashboard displays
   backend data. A map marker requires registered coordinates.

## Software-only test data

This does not simulate a physical sensor. The utility uses a clearly synthetic
50.0% value by default (or the selected `--percent`) and creates/uses only a
sensor registered as `is_test: true`. Both sensor and reading remain labelled
`TEST DATA` in API output and the dashboard. It does not change real sensor
records.

In PowerShell, with the backend running and the key configured:

```powershell
$env:TEST_SENSOR_ID = "FG-TEST-001"
$env:TEST_VILLAGE_LGD_CODE = "635099"
python -m tools.send_test_sensor_reading
```

Use `--percent 68.4` to choose a different test-only value. Remove marked test
records locally with:

```powershell
python -m tools.clear_test_sensor_data
```

Confirm the prompt, or add `--yes` for a non-interactive cleanup. This deletes
only rows and sensor registrations explicitly marked as test data. It does not
provide a public delete API.

## Troubleshooting and limitations

- **503 on registration/ingestion:** start FastAPI in a process where
  `SENSOR_API_KEY` is set.
- **401:** check the exact key in the `X-Sensor-Key` header and the firmware.
- **404 unknown sensor/village:** register the device first and use the
  canonical `village_lgd_code` from `/api/villages`.
- **409 village mismatch:** the device's LGD code must equal its registered
  village.
- **No marker:** register both valid coordinates; the context and list APIs do
  not require coordinates.
- **No readings / no-data:** verify calibration, Wi-Fi reachability, private
  firewall access, URL, and the Serial Monitor HTTP status.
- **422:** inspect JSON field names and ranges; soil percentage must be finite
  and between 0 and 100.
- **500:** inspect the Uvicorn terminal instead of hiding the error.
- **Stale/offline:** status uses server receipt time, not device clock or the
  supplied measurement time.
- **Data persistence:** local SQLite survives service restarts on this host;
  back it up intentionally if the records need to be retained.
- The shared key is prototype-grade and is not stored per sensor. No TLS is
  configured on this local HTTP setup. Use a trusted private LAN only.
- Sensor readings are contextual evidence only. They do not affect the
  baseline model, warnings, risk tiers, or rainfall scenarios.
- Rain-sensor raw/state fields are local observations only; they are kept
  separate from historical/current official rainfall and rainfall scenarios.
