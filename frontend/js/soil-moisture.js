(() => {
  "use strict";

  const SENSOR_API = "/api/sensors";
  const BENCH_SENSOR_ID = "FG-BENCH-001";
  const REFRESH_INTERVAL_MS = 5000;
  const HISTORY_LIMIT = 50;
  const SVG_NS = "http://www.w3.org/2000/svg";
  const { classifyMoisture, evaluateCurrentMoisture } = window.FloodGuardSoilAlerts;
  const $ = id => document.getElementById(id);

  const elements = {
    pickerWrap: $("sensor-picker-wrap"),
    picker: $("sensor-picker"),
    banner: $("alert-banner"),
    bannerTitle: $("alert-title"),
    bannerMessage: $("alert-message"),
    bannerReading: $("alert-reading"),
    status: $("sensor-status"),
    moisture: $("current-moisture"),
    gauge: $("moisture-gauge"),
    gaugeFill: $("gauge-fill"),
    currentNote: $("current-note"),
    condition: $("soil-condition"),
    sensorClass: $("sensor-class"),
    sensorId: $("detail-sensor-id"),
    deviceType: $("detail-device-type"),
    rawValue: $("detail-raw-value"),
    village: $("detail-village"),
    coordinates: $("detail-coordinates"),
    lastReading: $("detail-last-reading"),
    lastUpdate: $("detail-last-update"),
    benchNote: $("bench-note"),
    calibrationDry: $("calibration-dry"),
    calibrationWet: $("calibration-wet"),
    calibrationNote: $("calibration-note"),
    alertMessage: $("sensor-alert-message"),
    chart: $("moisture-chart"),
    chartWrap: $("chart-wrap"),
    chartEmpty: $("chart-empty"),
    chartCount: $("chart-count"),
    history: $("alert-history"),
    apiStatus: $("api-update-status")
  };

  let selectedSensorId = BENCH_SENSOR_ID;
  let lastKnownReading = null;
  let refreshTimer = null;
  let refreshing = false;

  function normalizedStatus(value) {
    const status = String(value || "").toLowerCase();
    return ["online", "stale", "offline"].includes(status) ? status : "no_data";
  }

  function formatMoisture(value) {
    return `${value.toFixed(1)}%`;
  }

  function formatElapsed(seconds, timestamp) {
    let age = seconds === null || seconds === undefined ? NaN : Number(seconds);
    if (!Number.isFinite(age) && timestamp) {
      const parsed = Date.parse(timestamp);
      if (Number.isFinite(parsed)) age = Math.max(0, (Date.now() - parsed) / 1000);
    }
    if (!Number.isFinite(age)) return "Time unavailable";
    const value = Math.max(0, Math.floor(age));
    if (value < 60) return `${value} sec ago`;
    if (value < 3600) return `${Math.floor(value / 60)} min ago`;
    if (value < 86400) return `${Math.floor(value / 3600)} hr ago`;
    return `${Math.floor(value / 86400)} days ago`;
  }

  function formatTimestamp(value) {
    if (!value) return "Not available";
    const parsed = new Date(value);
    return Number.isNaN(parsed.getTime())
      ? "Time unavailable"
      : parsed.toLocaleString();
  }

  async function fetchJson(path) {
    const response = await fetch(path, { signal: AbortSignal.timeout(4000) });
    if (!response.ok) throw new Error(`Sensor API returned HTTP ${response.status}`);
    return response.json();
  }

  function renderPicker(sensors) {
    const currentOptions = Array.from(elements.picker.options, option => option.value);
    const nextOptions = sensors.map(sensor => sensor.sensor_id);
    const changed = currentOptions.length !== nextOptions.length ||
      currentOptions.some((value, index) => value !== nextOptions[index]);
    if (changed) {
      elements.picker.replaceChildren();
      for (const sensor of sensors) {
        const option = document.createElement("option");
        option.value = sensor.sensor_id;
        option.textContent = `${sensor.sensor_id}${sensor.is_test ? " · TEST / BENCH" : ""}`;
        elements.picker.append(option);
      }
    }
    elements.pickerWrap.hidden = sensors.length < 2;
    if (sensors.length === 0) {
      selectedSensorId = BENCH_SENSOR_ID;
      return null;
    }
    const selected = sensors.find(sensor => sensor.sensor_id === selectedSensorId) ||
      sensors.find(sensor => sensor.sensor_id === BENCH_SENSOR_ID) ||
      sensors[0];
    selectedSensorId = selected.sensor_id;
    elements.picker.value = selectedSensorId;
    return selected;
  }

  function setStatus(status, text = null) {
    elements.status.className = `status-pill status-${status}`;
    elements.status.textContent = text || status.replace("_", " ").toUpperCase();
  }

  function setSensorAlertUnavailable(status, sensor, message = null) {
    const titleByStatus = {
      stale: "SENSOR STALE",
      offline: "SENSOR OFFLINE",
      no_data: "NO DATA",
      unavailable: "SENSOR DATA UNAVAILABLE"
    };
    const description = message || (status === "stale" || status === "offline"
      ? "Alert evaluation unavailable — reading is not current."
      : "Awaiting sensor data.");
    elements.banner.className = "alert-banner alert-unavailable";
    elements.bannerTitle.textContent = titleByStatus[status] || "SENSOR DATA UNAVAILABLE";
    elements.bannerMessage.textContent = description;
    elements.bannerReading.textContent = "";
    elements.condition.textContent = status === "no_data" ? "Awaiting data" : "Unavailable";
    elements.currentNote.textContent = status === "no_data"
      ? "Awaiting sensor data"
      : "Alert evaluation unavailable — reading is not current.";
    elements.alertMessage.textContent = status === "no_data"
      ? "Awaiting a current sensor reading."
      : "Alert evaluation unavailable — reading is not current.";
    elements.moisture.textContent = "—";
    elements.gaugeFill.style.width = "0%";
    elements.gauge.removeAttribute("aria-valuenow");
    elements.gauge.removeAttribute("aria-valuetext");
    setStatus(status, status === "unavailable" ? "DATA UNAVAILABLE" : null);
    if (lastKnownReading) {
      elements.currentNote.textContent = `Last known moisture: ${formatMoisture(lastKnownReading.moisture)} · ${formatElapsed(sensor?.age_seconds, lastKnownReading.received_at)}`;
    }
  }

  function updateSensorDetails(sensor) {
    const latest = sensor.latest || null;
    const isBench = sensor.sensor_id === BENCH_SENSOR_ID && sensor.is_test === true &&
      sensor.village_lgd_code == null;
    elements.sensorId.textContent = sensor.sensor_id || "—";
    elements.sensorClass.textContent = sensor.is_test ? "TEST / BENCH" : "PRODUCTION";
    elements.deviceType.textContent = sensor.sensor_type || "Not reported";
    elements.rawValue.textContent = latest?.raw_value === null || latest?.raw_value === undefined
      ? "Not reported"
      : String(latest.raw_value);
    elements.village.textContent = sensor.village?.village_name_en ||
      sensor.village_lgd_code || "Not assigned";
    const latitude = sensor.latitude;
    const longitude = sensor.longitude;
    elements.coordinates.textContent = latitude !== null && longitude !== null &&
      latitude !== undefined && longitude !== undefined
      ? `${latitude}, ${longitude}`
      : "Not assigned";
    const receivedAt = latest?.received_at || sensor.last_seen_at;
    elements.lastReading.textContent = latest
      ? formatElapsed(sensor.age_seconds, receivedAt)
      : "No reading received";
    elements.lastUpdate.textContent = latest ? formatTimestamp(receivedAt) : "Not available";
    elements.benchNote.hidden = !isBench;
    elements.calibrationDry.textContent = isBench ? "4095" : "Not published";
    elements.calibrationWet.textContent = isBench ? "2082" : "Not published";
    elements.calibrationNote.textContent = isBench
      ? "Displayed percentage is sensor-relative moisture based on the configured dry/wet calibration. This is not laboratory volumetric water-content calibration."
      : "Calibration values are not published by this sensor’s configuration.";
    return latest;
  }

  function renderCurrent(sensor) {
    const status = normalizedStatus(sensor.status);
    const latest = updateSensorDetails(sensor);
    const value = latest?.soil_moisture_percent;
    const validMoisture = typeof value === "number" && Number.isFinite(value) &&
      value >= 0 && value <= 100;

    if (status !== "online") {
      if (validMoisture && latest?.received_at) {
        lastKnownReading = {
          moisture: value,
          received_at: latest.received_at
        };
      }
      setSensorAlertUnavailable(status, sensor);
      if (lastKnownReading) {
        elements.bannerMessage.textContent =
          `Last known moisture: ${formatMoisture(lastKnownReading.moisture)} · ${formatElapsed(sensor.age_seconds, lastKnownReading.received_at)}. Alert evaluation unavailable — reading is not current.`;
      }
      return;
    }

    if (!validMoisture) {
      setSensorAlertUnavailable("no_data", sensor);
      return;
    }

    lastKnownReading = { moisture: value, received_at: latest.received_at };
    const classification = evaluateCurrentMoisture(status, value);
    if (!classification) {
      setSensorAlertUnavailable("no_data", sensor);
      return;
    }
    const display = formatMoisture(value);
    const bannerState = classification === "high" ? "high"
      : classification === "elevated" ? "elevated" : "normal";
    const label = classification === "high" ? "HIGH SOIL MOISTURE"
      : classification === "elevated" ? "ELEVATED SOIL MOISTURE" : "NORMAL";
    const alertText = classification === "high"
      ? "High soil moisture detected. Review current FloodGuard environmental conditions."
      : classification === "elevated"
        ? "Elevated soil moisture detected."
        : "Soil moisture is below the configured elevated threshold.";

    elements.banner.className = `alert-banner alert-${bannerState}`;
    elements.bannerTitle.textContent = label;
    elements.bannerMessage.textContent = alertText;
    elements.bannerReading.textContent = display;
    elements.moisture.textContent = display;
    elements.gaugeFill.style.width = `${value}%`;
    elements.gauge.setAttribute("aria-valuenow", String(value));
    elements.gauge.setAttribute("aria-valuetext", display);
    elements.currentNote.textContent = `Reading is current · ${formatElapsed(sensor.age_seconds, latest.received_at)}`;
    elements.condition.textContent = label;
    elements.alertMessage.textContent = alertText;
    setStatus("online");
  }

  function svgElement(name, attrs = {}, text = null) {
    const element = document.createElementNS(SVG_NS, name);
    for (const [key, value] of Object.entries(attrs)) {
      element.setAttribute(key, String(value));
    }
    if (text !== null) element.textContent = text;
    return element;
  }

  function renderChart(readings) {
    const points = readings
      .filter(reading => typeof reading.soil_moisture_percent === "number" &&
        Number.isFinite(reading.soil_moisture_percent) &&
        reading.soil_moisture_percent >= 0 && reading.soil_moisture_percent <= 100)
      .slice()
      .sort((a, b) => Date.parse(a.recorded_at || a.received_at) -
        Date.parse(b.recorded_at || b.received_at));
    elements.chart.replaceChildren();
    elements.chartWrap.classList.toggle("chart-has-data", points.length > 0);
    elements.chart.classList.toggle("chart-no-data", points.length === 0);
    elements.chartEmpty.textContent = points.length ? "" : "Awaiting sensor data";
    elements.chartCount.textContent = points.length
      ? `${points.length} real reading${points.length === 1 ? "" : "s"}`
      : "Waiting for readings";

    const left = 68;
    const right = 874;
    const top = 22;
    const bottom = 254;
    const y = value => bottom - (value / 100) * (bottom - top);
    const x = index => points.length < 2
      ? (left + right) / 2
      : left + (index / (points.length - 1)) * (right - left);

    for (const value of [0, 20, 40, 60, 80, 100]) {
      const axisY = y(value);
      elements.chart.append(
        svgElement("line", {
          x1: left,
          x2: right,
          y1: axisY,
          y2: axisY,
          class: value === 60 ? "chart-grid-line chart-threshold-elevated" :
            value === 80 ? "chart-grid-line chart-threshold-high" : "chart-grid-line"
        }),
        svgElement("text", {
          x: left - 10,
          y: axisY + 4,
          "text-anchor": "end",
          class: "chart-axis-label"
        }, `${value}`)
      );
    }
    elements.chart.append(
      svgElement("text", {
        x: 15,
        y: (top + bottom) / 2,
        transform: `rotate(-90 15 ${(top + bottom) / 2})`,
        "text-anchor": "middle",
        class: "chart-axis-label"
      }, "Soil moisture %"),
      svgElement("text", {
        x: right - 4,
        y: y(60) - 6,
        "text-anchor": "end",
        class: "chart-threshold-label",
        fill: "#b45309"
      }, "Elevated threshold · 60%"),
      svgElement("text", {
        x: right - 4,
        y: y(80) - 6,
        "text-anchor": "end",
        class: "chart-threshold-label",
        fill: "#b91c1c"
      }, "High threshold · 80%"),
      svgElement("text", { x: left, y: 286, class: "chart-axis-label" },
        formatChartTime(points[0])),
      svgElement("text", {
        x: right,
        y: 286,
        "text-anchor": "end",
        class: "chart-axis-label"
      }, formatChartTime(points[points.length - 1])),
      svgElement("text", {
        x: (left + right) / 2,
        y: 310,
        "text-anchor": "middle",
        class: "chart-axis-label"
      }, "Observation time")
    );

    if (points.length) {
      const path = points.map((reading, index) => {
        const value = reading.soil_moisture_percent;
        return `${index ? "L" : "M"} ${x(index).toFixed(2)} ${y(value).toFixed(2)}`;
      }).join(" ");
      elements.chart.append(svgElement("path", { d: path, class: "chart-series" }));
      const latestPoint = points[points.length - 1];
      elements.chart.append(svgElement("circle", {
        cx: x(points.length - 1),
        cy: y(latestPoint.soil_moisture_percent),
        r: 5,
        class: "chart-point"
      }));
    }
  }

  function formatChartTime(reading) {
    const timestamp = reading?.recorded_at || reading?.received_at;
    if (!timestamp) return "—";
    const date = new Date(timestamp);
    return Number.isNaN(date.getTime())
      ? "—"
      : date.toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" });
  }

  function renderHistory(readings) {
    const ordered = readings.slice().sort((a, b) =>
      Date.parse(a.recorded_at || a.received_at) -
      Date.parse(b.recorded_at || b.received_at));
    const transitions = [];
    let previous = null;
    for (const reading of ordered) {
      const value = reading.soil_moisture_percent;
      const state = classifyMoisture(value);
      if (!state || state === previous) continue;
      previous = state;
      transitions.push({ reading, state, value });
    }
    elements.history.replaceChildren();
    if (!transitions.length) {
      const empty = document.createElement("p");
      empty.className = "history-empty";
      empty.textContent = "No sensor readings are available.";
      elements.history.append(empty);
      return;
    }
    for (const { reading, state, value } of transitions.slice(-12).reverse()) {
      const row = document.createElement("div");
      row.className = "history-entry";
      const time = document.createElement("time");
      time.className = "history-time";
      time.dateTime = reading.recorded_at || reading.received_at || "";
      time.textContent = formatTimestamp(time.dateTime);
      const moisture = document.createElement("span");
      moisture.className = "history-moisture";
      moisture.textContent = formatMoisture(value);
      const level = document.createElement("strong");
      level.className = `history-level history-level-${state}`;
      level.textContent = state.toUpperCase();
      const message = document.createElement("span");
      message.className = "history-message";
      message.textContent = "Recorded sensor state transition";
      row.append(time, moisture, level, message);
      elements.history.append(row);
    }
  }

  function renderNoSensor() {
    elements.sensorId.textContent = selectedSensorId;
    elements.sensorClass.textContent = "Not registered";
    elements.deviceType.textContent = "—";
    elements.rawValue.textContent = "—";
    elements.village.textContent = "Not assigned";
    elements.coordinates.textContent = "Not assigned";
    elements.lastReading.textContent = "No reading received";
    elements.lastUpdate.textContent = "Not available";
    elements.benchNote.hidden = selectedSensorId !== BENCH_SENSOR_ID;
    elements.calibrationDry.textContent = "4095";
    elements.calibrationWet.textContent = "2082";
    lastKnownReading = null;
    setSensorAlertUnavailable("no_data", null, "This sensor is not registered in FloodGuard.");
    elements.chartCount.textContent = "Waiting for readings";
    renderChart([]);
    renderHistory([]);
  }

  function renderApiError(error) {
    setSensorAlertUnavailable("unavailable", null, "Could not refresh sensor data. Current alert evaluation is unavailable.");
    elements.bannerMessage.textContent = `Could not refresh sensor data: ${error.message}`;
    elements.apiStatus.textContent = "Sensor API unavailable";
    elements.apiStatus.className = "api-error";
    elements.currentNote.textContent = lastKnownReading
      ? `Last known moisture: ${formatMoisture(lastKnownReading.moisture)} · current value unavailable`
      : "Current sensor reading unavailable.";
  }

  async function refresh() {
    if (refreshing) return;
    refreshing = true;
    try {
      const inventory = await fetchJson(SENSOR_API);
      const sensors = Array.isArray(inventory.records)
        ? inventory.records.filter(sensor =>
          sensor.sensor_type === "soil_moisture" && typeof sensor.sensor_id === "string")
        : [];
      const sensor = renderPicker(sensors);
      if (!sensor) {
        renderNoSensor();
        elements.apiStatus.textContent = "Sensor not registered";
        elements.apiStatus.className = "";
        return;
      }

      renderCurrent(sensor);
      elements.apiStatus.textContent = `Updated ${new Date().toLocaleTimeString()}`;
      elements.apiStatus.className = "";

      try {
        const history = await fetchJson(
          `${SENSOR_API}/${encodeURIComponent(sensor.sensor_id)}/readings?limit=${HISTORY_LIMIT}`
        );
        const readings = Array.isArray(history.records) ? history.records : [];
        renderChart(readings);
        renderHistory(readings);
      } catch (error) {
        elements.chartCount.textContent = "Trend unavailable";
        elements.chartEmpty.textContent = `Could not load sensor history: ${error.message}`;
        elements.chartWrap.classList.remove("chart-has-data");
      }
    } catch (error) {
      renderApiError(error);
    } finally {
      refreshing = false;
      refreshTimer = window.setTimeout(refresh, REFRESH_INTERVAL_MS);
    }
  }

  elements.picker.addEventListener("change", () => {
    selectedSensorId = elements.picker.value;
    lastKnownReading = null;
    if (refreshTimer !== null) window.clearTimeout(refreshTimer);
    refresh();
  });

  refresh();
})();
