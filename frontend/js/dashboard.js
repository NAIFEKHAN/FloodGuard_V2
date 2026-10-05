/**
 * FloodGuard Nilgiris Disaster Intelligence — Geospatial Application
 * Source-verified shelter inventory and evacuation-route UI
 * Strict Scientific Governance: Positive-Unlabeled (PU) Spatial XGBoost, LOTO CV.
 */
(() => {
  "use strict";

  // DOM Helpers
  const $ = id => document.getElementById(id);
  const escapeHtml = val => {
    if (val === null || val === undefined) return "—";
    const div = document.createElement("div");
    div.textContent = String(val);
    return div.innerHTML;
  };

  const fmt = (val, decimals = 1) => {
    if (val === "" || val === null || val === undefined || isNaN(Number(val))) return "—";
    return Number(val).toFixed(decimals);
  };

  // Toast / Status Notification System
  let toastTimer = null;
  const showToast = (message, isError = false, retryFn = null, autoHideMs = 0) => {
    const toast = $("global-toast");
    const toastMsg = $("toast-message");
    const spinner = $("toast-spinner");
    const retryBtn = $("toast-retry-btn");
    if (!toast || !toastMsg) return;

    if (toastTimer) clearTimeout(toastTimer);

    toastMsg.textContent = message;
    toast.style.display = "flex";

    if (spinner) {
      spinner.style.display = isError ? "none" : "block";
    }

    if (isError && retryFn && retryBtn) {
      retryBtn.style.display = "inline-block";
      retryBtn.onclick = () => {
        hideToast();
        retryFn();
      };
    } else if (retryBtn) {
      retryBtn.style.display = "none";
    }

    if (autoHideMs > 0) {
      toastTimer = setTimeout(hideToast, autoHideMs);
    }
  };

  const hideToast = () => {
    const toast = $("global-toast");
    if (toast) toast.style.display = "none";
  };

  const api = async path => {
    try {
      const res = await fetch(path);
      if (!res.ok) throw new Error(`HTTP ${res.status} (${res.statusText})`);
      return await res.json();
    } catch (err) {
      console.error(`API fetch failed for ${path}:`, err);
      throw err;
    }
  };

  // Application State
  let map;
  let satelliteBaseLayer;
  let satelliteLabelLayer;
  let streetBaseLayer;
  let susceptibilityLayer;
  let eventLayer;
  let boundaryLayer;
  let centroidLayer;
  let shelterLayer;
  let shelterRecords = [];
  let routeLayer;
  let originMarkerLayer;
  let weatherLayer;
  let sensorLayer;
  let flowAccumulationLayer;
  let drainageNetworkLayer;
  let hydrologyMapDataPromise = null;
  let allEvents = [];
  let scenarioRecords = [];
  let activeScenarioMeta = null;
  let activeRainfallMode = "scenario";
  let villagePolygonsMap = new Map();
  let shelterMarkersMap = new Map();
  let selectedVillageLgd = null;
  let currentTierFilter = "all";
  let currentSearchTerm = "";
  let sliderDebounceTimer = null;
  let activeHighlightedPolygon = null;
  let villageContextRequestId = 0;
  let selectedVillageRecord = null;
  let sensorRefreshTimer = null;
  let warningResultsByVillage = new Map();
  let warningSummary = null;
  let warningMapEnabled = false;
  let warningRefreshTimer = null;

  const SOIL_MOISTURE_DISPLAY_BANDS = [
    { maximum: 30, label: "Dry" },
    { maximum: 60, label: "Moderate" },
    { maximum: 80, label: "Wet" },
    { maximum: 100, label: "Very Wet" }
  ];

  function sensorStatusColor(status) {
    return {
      online: "#15803d",
      stale: "#d97706",
      offline: "#64748b",
      no_data: "#94a3b8"
    }[status] || "#64748b";
  }

  function formatElapsed(ageSeconds) {
    if (ageSeconds === null || ageSeconds === undefined) return "time unavailable";
    const minutes = Math.floor(Number(ageSeconds) / 60);
    if (minutes < 1) return "less than 1 min ago";
    if (minutes < 60) return `${minutes} min ago`;
    const hours = Math.floor(minutes / 60);
    if (hours < 24) return `${hours} hr ago`;
    return `${Math.floor(hours / 24)} days ago`;
  }

  async function refreshSensorLayer() {
    const response = await api("/api/sensors");
    sensorLayer.clearLayers();
    for (const sensor of response.records || []) {
      if (
        sensor.latitude === null ||
        sensor.latitude === undefined ||
        sensor.longitude === null ||
        sensor.longitude === undefined
      ) continue;
      const latitude = Number(sensor.latitude);
      const longitude = Number(sensor.longitude);
      if (!Number.isFinite(latitude) || !Number.isFinite(longitude)) continue;
      const status = String(sensor.status || "no_data").toLowerCase();
      const color = sensorStatusColor(status);
      const latest = sensor.latest || null;
      const moisture = latest?.soil_moisture_percent;
      const moistureText = moisture === null || moisture === undefined
        ? "No reading received"
        : `${fmt(moisture, 1)}%`;
      const testLabel = latest?.is_test || sensor.is_test ? " · TEST DATA" : "";
      const popup = `
        <div class="sensor-popup">
          <strong>${escapeHtml(sensor.sensor_id)}</strong>${escapeHtml(testLabel)}<br>
          Village: ${escapeHtml(sensor.village?.village_name_en || sensor.village_lgd_code)}<br>
          Soil moisture: ${escapeHtml(moistureText)}<br>
          Status: ${escapeHtml(status.replace("_", " ").toUpperCase())}<br>
          Last reading: ${escapeHtml(latest ? formatElapsed(sensor.age_seconds) : "None")}
        </div>`;
      L.circleMarker([latitude, longitude], {
        radius: 6,
        color,
        weight: 1.5,
        fillColor: color,
        fillOpacity: 0.75
      })
        .bindPopup(popup)
        .bindTooltip(escapeHtml(`${sensor.sensor_id} · ${status.replace("_", " ")}`), {
          direction: "top",
          opacity: 0.9
        })
        .addTo(sensorLayer);
    }
  }

  // Live Weather State (Phase 6)
  let weatherMarker = null;
  let currentWeatherRecord = null;
  let activeWeatherContext = {
    locationName: "Nilgiris District (Ooty)",
    lat: 11.4102,
    lng: 76.6950,
    isVillageSpecific: false
  };
  let lastWeatherFetchTimestamp = null;
  let weatherAutoRefreshTimer = null;
  let isFetchingWeather = false;

  // Shelter & Routing State
  let userOrigin = {
    lat: 11.4102,
    lng: 76.6950,
    label: "Default Center (Ooty, Nilgiris)",
    isManual: false
  };
  let selectedShelter = null;
  let isPickingOriginOnMap = false;

  /**
   * Great Circle Haversine Distance (km)
   */
  function haversineDistKm(lat1, lon1, lat2, lon2) {
    const R = 6371.0;
    const dLat = (lat2 - lat1) * Math.PI / 180.0;
    const dLon = (lon2 - lon1) * Math.PI / 180.0;
    const a = Math.sin(dLat / 2.0) * Math.sin(dLat / 2.0) +
              Math.cos(lat1 * Math.PI / 180.0) * Math.cos(lat2 * Math.PI / 180.0) *
              Math.sin(dLon / 2.0) * Math.sin(dLon / 2.0);
    const c = 2.0 * Math.atan2(Math.sqrt(a), Math.sqrt(1.0 - a));
    return R * c;
  }

  /**
   * Determine Tier classification for demonstration display
   * High: >= 60.0 | Medium: 30.0 - 59.9 | Low: < 30.0
   * Color distinction: High Susceptibility (#ea580c) is distinct from Landslide Warning (#ef4444)
   */
  function getTierInfo(score) {
    const num = Number(score);
    if (num >= 60.0) {
      return { tier: "HIGH", className: "tier-high", label: "HIGH", color: "#ea580c", stroke: "#c2410c", fillOpacity: 0.36 };
    } else if (num >= 30.0) {
      return { tier: "MEDIUM", className: "tier-medium", label: "MEDIUM", color: "#f59e0b", stroke: "#b45309", fillOpacity: 0.31 };
    } else {
      return { tier: "LOW", className: "tier-low", label: "LOW", color: "#10b981", stroke: "#047857", fillOpacity: 0.27 };
    }
  }

  /** Read the latest server-evaluated warning result for a supported village. */
  function getVillageWarningState(record) {
    const warning = record
      ? warningResultsByVillage.get(String(record.village_lgd_code))
      : null;
    const available = warning?.status === "available";
    const stage = available ? String(warning.stage) : "unavailable";
    const stageInfo = warning?.label || "Not evaluated";
    return {
      warning,
      stage,
      isWarning: stage === "orange" || stage === "red",
      statusText: available
        ? `${stage.toUpperCase()} — ${String(stageInfo).toUpperCase()}`
        : warning ? "WARNING STATUS UNAVAILABLE" : "WARNING STATUS LOADING",
      statusBadge: available ? `${stage.toUpperCase()} · ${stageInfo.toUpperCase()}` : "UNAVAILABLE",
      className: `warning-${stage}`,
      reasons: warning?.factors
        ?.filter(factor => factor.used_as_current_evidence || factor.status === "high" || factor.status === "elevated")
        .map(factor => factor.label + (factor.value !== null && factor.value !== undefined ? `: ${factor.value}${factor.unit ? ` ${factor.unit}` : ""}` : ""))
        || [],
      action: warning?.guidance || "Follow instructions from local disaster-management authorities."
    };
  }

  /**
   * Synchronize top navbar warning indicator pill with live active warning count
   */
  function updateWarningIndicators() {
    const pill = $("nav-warning-pill");
    const textEl = $("nav-warning-text");
    const dotEl = pill ? pill.querySelector(".warn-pill-dot") : null;
    if (!pill || !textEl) return;
    if (!warningSummary) {
      textEl.textContent = "WARNING STATUS LOADING";
      return;
    }
    const counts = warningSummary.stage_counts || {};
    const stage = ["red", "orange", "yellow"].find(key => Number(counts[key] || 0) > 0) || "green";
    const stageInfo = warningSummary.stages?.[stage] || { label: "Normal" };
    const count = Number(counts[stage] || 0);
    const noun = count === 1 ? "VILLAGE" : "VILLAGES";
    const modeLabel = warningSummary.mode_label || "SCENARIO";
    pill.className = `nav-warning-pill warning-${stage}`;
    if (dotEl) dotEl.textContent = stage === "green" ? "✓" : stage.toUpperCase();
    textEl.textContent = stage === "green"
      ? `GREEN · NORMAL · ${count} ${noun} · ${modeLabel}`
      : `${stage.toUpperCase()} · ${stageInfo.label.toUpperCase()} · ${count} ${noun} · ${modeLabel}`;
    pill.title = `${warningSummary.disclaimer} Rule ${warningSummary.rule_version}; ${warningSummary.coverage?.supported_village_count || 0} supported villages; evaluated ${warningSummary.generated_at}.`;
  }

  function renderWarningAudit(result) {
    const version = $("warning-audit-version");
    const updated = $("warning-audit-updated");
    const counts = $("warning-audit-counts");
    if (version) version.textContent = `Rule ${result.rule_version || "—"}`;
    if (updated) {
      updated.textContent = `Latest evaluation: ${result.generated_at || "—"} · ${result.mode_label || "Mode unavailable"} · Coverage: ${result.coverage?.supported_village_count ?? "—"} / ${result.coverage?.total_village_master_count ?? "—"} villages`;
    }
    const thresholds = result.thresholds || {};
    const ruleDescriptions = {
      green: "No configured watch signal.",
      yellow: `Baseline susceptibility ≥${thresholds.susceptibility_watch ?? "—"} or scenario multiplier ≥${thresholds.rainfall_watch_multiplier ?? "—"}×.`,
      orange: `Baseline ≥${thresholds.susceptibility_watch ?? "—"} with scenario ≥${thresholds.rainfall_watch_multiplier ?? "—"}×, or online soil moisture ≥${thresholds.soil_moisture_wet_support ?? "—"}% with baseline at watch level.`,
      red: `Baseline ≥${thresholds.susceptibility_high ?? "—"} with scenario ≥${thresholds.rainfall_prepare_multiplier ?? "—"}×, or scenario ≥${thresholds.rainfall_watch_multiplier ?? "—"}× and online soil moisture ≥${thresholds.soil_moisture_very_wet_support ?? "—"}%.`
    };
    Object.entries(ruleDescriptions).forEach(([stage, description]) => {
      const element = $(`warning-audit-${stage}`);
      if (element) element.textContent = description;
    });
    if (counts) {
      counts.textContent = Object.entries(result.stage_counts || {})
        .map(([stage, count]) => `${stage.toUpperCase()} ${count}`)
        .join(" · ") || "Stage counts unavailable.";
    }
  }

  async function loadWarningResults() {
    const params = new URLSearchParams({ mode: "scenario" });
    const activeKey = String(activeScenarioMeta?.scenario_key || "baseline");
    const activeMultiplier = Number(activeScenarioMeta?.rainfall_factor ?? 1);
    const presetMatches = {
      0.7: "moderate",
      1: "baseline",
      1.5: "heavy",
      2.2: "extreme"
    };
    const preset = Object.entries(presetMatches).find(
      ([factor]) => Math.abs(Number(factor) - activeMultiplier) < 0.005
    )?.[1];
    if (preset && !activeKey.startsWith("Custom")) {
      params.set("scenario", preset);
    } else {
      params.set("multiplier", String(activeMultiplier));
    }
    const result = await api(`/api/warnings?${params}`);
    warningSummary = result;
    renderWarningAudit(result);
    warningResultsByVillage = new Map(
      (result.records || []).map(warning => [String(warning.village_lgd_code), warning])
    );
    updateWarningIndicators();
    renderRankingList();
    updateMapPolygons();
    return result;
  }

  function setWarningMapView(enabled) {
    warningMapEnabled = enabled;
    const badge = $("warning-map-badge");
    const mode = $("warning-map-mode");
    if (badge) badge.hidden = !enabled;
    if (mode) {
      mode.textContent = warningSummary?.mode_label || "SCENARIO";
    }
    updateMapPolygons();
  }

  /**
   * Format delta chip in plain English (Phase 8.1)
   */
  function formatDelta(delta) {
    const num = Number(delta || 0);
    if (Math.abs(num) < 0.05) {
      return { 
        text: "Change: 0.0 points", 
        rawText: "0.0 points", 
        shortText: "+0.0 points", 
        className: "delta-zero", 
        isZero: true 
      };
    } else if (num > 0) {
      return { 
        text: `Change: +${num.toFixed(1)} points`, 
        rawText: `+${num.toFixed(1)} points`, 
        shortText: `+${num.toFixed(1)} points`, 
        className: "delta-pos", 
        isZero: false 
      };
    } else {
      return { 
        text: `Change: ${num.toFixed(1)} points`, 
        rawText: `${num.toFixed(1)} points`, 
        shortText: `${num.toFixed(1)} points`, 
        className: "delta-neg", 
        isZero: false 
      };
    }
  }

  /**
   * Interpret Open-Meteo WMO Weather Code
   */
  function interpretWmoCode(code) {
    const num = Number(code);
    if (num === 0) return "Clear sky";
    if (num === 1) return "Mainly clear";
    if (num === 2) return "Partly cloudy";
    if (num === 3) return "Overcast";
    if (num >= 45 && num <= 48) return "Fog / Mist";
    if (num >= 51 && num <= 55) return "Drizzle";
    if (num >= 61 && num <= 65) return "Rain";
    if (num >= 71 && num <= 77) return "Snow / Flurries";
    if (num >= 80 && num <= 82) return "Rain showers";
    if (num >= 95 && num <= 99) return "Thunderstorm";
    return "Cloudy";
  }

  /**
   * Fetch Live Weather from Open-Meteo public API
   */
  async function fetchLiveWeather(lat = null, lng = null, locationName = null, isForced = false) {
    if (isFetchingWeather) return;

    const targetLat = lat !== null ? Number(lat) : activeWeatherContext.lat;
    const targetLng = lng !== null ? Number(lng) : activeWeatherContext.lng;
    const targetName = locationName || activeWeatherContext.locationName;

    activeWeatherContext = {
      locationName: targetName,
      lat: targetLat,
      lng: targetLng,
      isVillageSpecific: Boolean(locationName && locationName !== "Nilgiris District (Ooty)")
    };

    isFetchingWeather = true;

    // Update UI status to loading
    const statusDot = $("weather-status-dot");
    const statusLabel = $("weather-status-label");
    const refreshBtn = $("btn-refresh-weather");
    if (statusDot) statusDot.style.background = "#f59e0b";
    if (statusLabel) {
      statusLabel.textContent = "Updating weather…";
      statusLabel.style.color = "#d97706";
    }
    if (refreshBtn) refreshBtn.classList.add("refreshing");

    try {
      const url = `https://api.open-meteo.com/v1/forecast?latitude=${targetLat.toFixed(4)}&longitude=${targetLng.toFixed(4)}&current=temperature_2m,relative_humidity_2m,precipitation,weather_code,wind_speed_10m&timezone=auto`;
      const res = await fetch(url, { signal: AbortSignal.timeout(4000) });
      if (!res.ok) throw new Error(`HTTP ${res.status}`);

      const data = await res.json();
      if (!data.current) throw new Error("Missing current weather block in response");

      currentWeatherRecord = {
        temperature: Number(data.current.temperature_2m),
        humidity: Number(data.current.relative_humidity_2m),
        precipitation: Number(data.current.precipitation ?? 0.0),
        windSpeed: Number(data.current.wind_speed_10m ?? 0.0),
        weatherCode: Number(data.current.weather_code ?? 0),
        conditionText: interpretWmoCode(data.current.weather_code ?? 0),
        fetchTime: Date.now()
      };
      lastWeatherFetchTimestamp = Date.now();

      renderWeatherUI();
      updateWeatherMapMarker();

      if (isForced) {
        showToast(`Live weather updated for ${targetName}`, false, null, 1800);
      }
    } catch (err) {
      console.warn("Live weather fetch warning:", err.message);
      const statusLabel = $("weather-status-label");
      const statusDot = $("weather-status-dot");
      if (statusLabel) {
        statusLabel.textContent = "Live weather temporarily unavailable";
        statusLabel.style.color = "#ef4444";
      }
      if (statusDot) statusDot.style.background = "#ef4444";
      
      if (!currentWeatherRecord) {
        const tempEl = $("w-temp");
        if (tempEl) tempEl.textContent = "— °C";
        const condEl = $("w-condition");
        if (condEl) condEl.textContent = "Live weather unavailable";
      }
    } finally {
      isFetchingWeather = false;
      if (refreshBtn) refreshBtn.classList.remove("refreshing");
    }
  }

  /**
   * Render Live Weather UI across floating card, village drawer, and evacuation chip
   */
  function renderWeatherUI() {
    if (!currentWeatherRecord) return;

    const w = currentWeatherRecord;
    const locName = activeWeatherContext.locationName;

    // Floating Weather Card
    const locLabel = $("weather-location-label");
    if (locLabel) locLabel.textContent = locName;

    const statusDot = $("weather-status-dot");
    const statusLabel = $("weather-status-label");
    if (statusDot) statusDot.style.background = "#10b981";
    if (statusLabel) {
      statusLabel.textContent = "🟢 LIVE";
      statusLabel.style.color = "#047857";
    }

    const timeEl = $("weather-updated-time");
    if (timeEl) timeEl.textContent = "Updated just now";

    const tempEl = $("w-temp");
    if (tempEl) tempEl.textContent = `${fmt(w.temperature, 1)} °C`;

    const condEl = $("w-condition");
    if (condEl) condEl.textContent = w.conditionText;

    const humEl = $("w-humidity");
    if (humEl) humEl.textContent = `${Math.round(w.humidity)} %`;

    const precipEl = $("w-precip");
    if (precipEl) precipEl.textContent = `${fmt(w.precipitation, 1)} mm`;

    const windEl = $("w-wind");
    if (windEl) windEl.textContent = `${fmt(w.windSpeed, 1)} km/h`;

    // Comparison Block
    const cmpLive = $("cmp-live-val");
    if (cmpLive) cmpLive.textContent = `${fmt(w.temperature, 1)}°C · ${fmt(w.precipitation, 1)} mm precip`;

    const cmpScen = $("cmp-scen-val");
    if (cmpScen && activeScenarioMeta) {
      cmpScen.textContent = `${fmt(activeScenarioMeta.rainfall_factor, 2)}× ${activeScenarioMeta.scenario_name}`;
    }

    // Selected Village Drawer Weather Box
    const vTemp = $("v-w-temp");
    if (vTemp) vTemp.textContent = `${fmt(w.temperature, 1)} °C`;
    const vPrecip = $("v-w-precip");
    if (vPrecip) vPrecip.textContent = `${fmt(w.precipitation, 1)} mm`;
    const vHum = $("v-w-humidity");
    if (vHum) vHum.textContent = `${Math.round(w.humidity)} %`;
    const vWind = $("v-w-wind");
    if (vWind) vWind.textContent = `${fmt(w.windSpeed, 1)} km/h`;
    const vLoc = $("v-w-loc");
    if (vLoc) vLoc.textContent = locName;

    // Evacuation Panel Weather Chip
    const evacChip = $("evac-weather-chip");
    if (evacChip) {
      evacChip.textContent = `🌦️ Live Context: ${fmt(w.temperature, 1)}°C · Precip: ${fmt(w.precipitation, 1)} mm · Humidity: ${Math.round(w.humidity)}% (${w.conditionText})`;
    }

    renderEmergencyPanel();
  }

  /**
   * Update or Create Weather Marker on Leaflet Map
   */
  function updateWeatherMapMarker() {
    if (!map || !weatherLayer || !currentWeatherRecord) return;

    weatherLayer.clearLayers();

    const w = currentWeatherRecord;
    const lat = activeWeatherContext.lat;
    const lng = activeWeatherContext.lng;
    const locName = activeWeatherContext.locationName;

    weatherMarker = L.circleMarker([lat, lng], {
      radius: 8.0,
      color: "#ffffff",
      weight: 2.5,
      fillColor: "#0284c7",
      fillOpacity: 0.95
    });

    weatherMarker.bindTooltip(`
      <strong>🌦️ Live Weather Context: ${escapeHtml(locName)}</strong><br>
      <small>${fmt(w.temperature, 1)}°C · ${w.conditionText} · ${fmt(w.precipitation, 1)} mm</small>
    `, { sticky: true });

    weatherMarker.bindPopup(`
      <div style="font-size:12px; min-width: 220px;">
        <strong style="color:#0284c7;font-size:13px;">🌦️ Live Weather Context</strong><br>
        <span style="display:inline-block;margin:3px 0;font-size:10px;font-weight:700;color:#0369a1;background:#e0f2fe;padding:1px 6px;border-radius:4px;">OPEN-METEO LIVE OBSERVATION</span><br>
        <strong>Location:</strong> ${escapeHtml(locName)}<br>
        <strong>Temperature:</strong> ${fmt(w.temperature, 1)} °C<br>
        <strong>Condition:</strong> ${escapeHtml(w.conditionText)}<br>
        <strong>Precipitation:</strong> ${fmt(w.precipitation, 1)} mm<br>
        <strong>Relative Humidity:</strong> ${Math.round(w.humidity)} %<br>
        <strong>Wind Speed:</strong> ${fmt(w.windSpeed, 1)} km/h<br>
        <div style="margin-top:6px;padding-top:4px;border-top:1px solid #e2e8f0;font-size:10px;color:#64748b;">
          Contextual observation only. Does not alter PU spatial susceptibility scores or represent an official emergency warning.
        </div>
      </div>
    `);

    weatherMarker.addTo(weatherLayer);
  }

  /**
   * Start 10-minute auto refresh for Live Weather
   */
  function startWeatherAutoRefresh() {
    if (weatherAutoRefreshTimer) clearInterval(weatherAutoRefreshTimer);
    weatherAutoRefreshTimer = setInterval(() => {
      if (document.visibilityState === "visible") {
        fetchLiveWeather(activeWeatherContext.lat, activeWeatherContext.lng, activeWeatherContext.locationName, false);
      }
    }, 10 * 60 * 1000);
  }

  /**
   * Render Emergency Alert Center (Phase 7)
   */
  function renderEmergencyPanel() {
    const emPanel = $("emergency-panel");
    if (!emPanel) return;

    // Determine target location record
    let record = null;
    let locationTitle = "Nilgiris Region";
    let locationSub = "Nilgiris District · General Context";
    let coordsText = `${userOrigin.lat.toFixed(4)}° N, ${userOrigin.lng.toFixed(4)}° E`;

    if (selectedVillageLgd) {
      record = scenarioRecords.find(r => String(r.village_lgd_code) === selectedVillageLgd);
    }
    if (!record && userOrigin.matchedVillage) {
      record = scenarioRecords.find(r => String(r.village_lgd_code) === String(userOrigin.matchedVillage.village_lgd_code)) || userOrigin.matchedVillage;
    }
    if (!record && scenarioRecords.length > 0) {
      record = scenarioRecords[0];
    }

    if (record) {
      locationTitle = record.village_name_en || "Selected Village";
      locationSub = `${record.taluk_name_en || "—"} · LGD ${record.village_lgd_code}`;
      const polygon = villagePolygonsMap.get(String(record.village_lgd_code));
      if (polygon) {
        const c = polygon.getBounds().getCenter();
        coordsText = `${c.lat.toFixed(4)}° N, ${c.lng.toFixed(4)}° E`;
      }
    } else if (userOrigin.isManual) {
      locationTitle = userOrigin.label;
      locationSub = "Custom Map Location Point";
    }

    // Selected Location Card
    const emVillageName = $("em-village-name");
    if (emVillageName) emVillageName.textContent = locationTitle;

    const emVillageTalukLgd = $("em-village-taluk-lgd");
    if (emVillageTalukLgd) emVillageTalukLgd.textContent = locationSub;

    const emCoordsText = $("em-coords-text");
    if (emCoordsText) emCoordsText.textContent = coordsText;

    // Modeled Risk & Warning Status
    const score = Number(record?.scenario_susceptibility_0_100 ?? record?.ml_susceptibility_0_100 ?? 0);
    const baseScore = Number(record?.baseline_susceptibility_0_100 ?? record?.ml_susceptibility_0_100 ?? score);
    const tier = getTierInfo(score);
    const delta = formatDelta(record?.susceptibility_delta);
    const warnState = getVillageWarningState(record);

    const emTierBadge = $("em-tier-badge");
    if (emTierBadge) {
      if (warnState.isWarning) {
        emTierBadge.className = `tier-chip warning-stage-chip warning-${warnState.stage}`;
        emTierBadge.textContent = `${warnState.stage.toUpperCase()} · ${String(warnState.warning.label).toUpperCase()}`;
      } else {
        emTierBadge.className = `tier-chip ${tier.className}`;
        emTierBadge.textContent = `${tier.tier} RISK`;
      }
    }

    const emScoreVal = $("em-score-val");
    if (emScoreVal) emScoreVal.textContent = fmt(score, 1);

    const emScoreBar = $("em-score-bar");
    if (emScoreBar) {
      emScoreBar.style.width = `${Math.min(100, Math.max(0, score))}%`;
      emScoreBar.style.background = tier.color;
    }

    const emTierTitle = $("em-tier-title");
    if (emTierTitle) {
      if (warnState.warning) {
        emTierTitle.textContent = `${warnState.stage.toUpperCase()} · ${String(warnState.warning.label).toUpperCase()} · DECISION SUPPORT`;
        emTierTitle.style.color = warnState.warning.color;
      } else {
        emTierTitle.textContent = `${tier.tier} SUSCEPTIBILITY`;
        emTierTitle.style.color = "var(--text-main)";
      }
    }

    const emTierMsg = $("em-tier-msg");
    if (emTierMsg) {
      if (warnState.warning) {
        emTierMsg.textContent = `${warnState.warning.warning_mode_label}. ${warnState.warning.reason} This is not an official alert.`;
      } else if (tier.tier === "HIGH") {
        emTierMsg.textContent = "High baseline modeled susceptibility. See the separately evaluated decision-support stage.";
      } else if (tier.tier === "MEDIUM") {
        emTierMsg.textContent = "Moderate baseline modeled susceptibility. See the separately evaluated decision-support stage.";
      } else {
        emTierMsg.textContent = "Low baseline modeled susceptibility. See the separately evaluated decision-support stage.";
      }
    }

    // Risk Context / Scenario Delta
    const emBaseScore = $("em-base-score");
    if (emBaseScore) emBaseScore.textContent = fmt(baseScore, 1);

    const emScenScore = $("em-scen-score");
    if (emScenScore) emScenScore.textContent = fmt(score, 1);

    const emDeltaScore = $("em-delta-score");
    if (emDeltaScore) {
      emDeltaScore.className = `em-ctx-val ${delta.className}`;
      emDeltaScore.textContent = delta.text;
    }

    // PU Status Transparency
    const emPuTag = $("em-pu-tag");
    const emPuDesc = $("em-pu-desc");
    if (emPuTag && emPuDesc) {
      if (record?.pu_status === "POSITIVE") {
        emPuTag.textContent = "PU Status: POSITIVE";
        emPuDesc.textContent = "Historical GSI event evidence is available for this modeled location.";
      } else {
        emPuTag.textContent = "PU Status: UNLABELED";
        emPuDesc.textContent = "UNLABELED means no recorded GSI event evidence was available for this location. It does NOT mean no risk.";
      }
    }

    // Live Weather Context
    const emWeatherTime = $("em-weather-time");
    const emWTemp = $("em-w-temp");
    const emWHum = $("em-w-hum");
    const emWPrecip = $("em-w-precip");
    const emWWind = $("em-w-wind");
    const emWCond = $("em-w-cond");

    if (currentWeatherRecord) {
      if (emWeatherTime) emWeatherTime.textContent = "Observed: Just now";
      if (emWTemp) emWTemp.textContent = `${fmt(currentWeatherRecord.temperature, 1)} °C`;
      if (emWHum) emWHum.textContent = `${Math.round(currentWeatherRecord.humidity)} %`;
      if (emWPrecip) emWPrecip.textContent = `${fmt(currentWeatherRecord.precipitation, 1)} mm`;
      if (emWWind) emWWind.textContent = `${fmt(currentWeatherRecord.windSpeed, 1)} km/h`;
      if (emWCond) emWCond.textContent = currentWeatherRecord.conditionText;
    } else {
      if (emWeatherTime) emWeatherTime.textContent = "Observed: Live weather temporarily unavailable";
      if (emWTemp) emWTemp.textContent = "— °C";
      if (emWHum) emWHum.textContent = "— %";
      if (emWPrecip) emWPrecip.textContent = "— mm";
      if (emWWind) emWWind.textContent = "— km/h";
      if (emWCond) emWCond.textContent = "Live weather temporarily unavailable";
    }

    // Rainfall Scenario
    const emScenName = $("em-scen-name");
    if (emScenName) {
      emScenName.textContent = activeScenarioMeta?.scenario_name || "Historical Baseline";
    }

    const emScenFactor = $("em-scen-factor");
    if (emScenFactor) {
      const factor = activeScenarioMeta?.rainfall_factor ?? 1.0;
      emScenFactor.textContent = `${fmt(factor, 2)}× Baseline Multiplier`;
    }

  }

  /**
   * Helper: Copy text to clipboard
   */
  async function copyEmergencyContextToClipboard(text) {
    try {
      if (navigator.clipboard && navigator.clipboard.writeText) {
        await navigator.clipboard.writeText(text);
      } else {
        const textarea = document.createElement("textarea");
        textarea.value = text;
        textarea.style.position = "fixed";
        textarea.style.opacity = "0";
        document.body.appendChild(textarea);
        textarea.select();
        document.execCommand("copy");
        document.body.removeChild(textarea);
      }
      showToast("Emergency information copied to clipboard", false, null, 2000);
    } catch (err) {
      console.warn("Clipboard copy fallback error:", err);
      showToast("Emergency information copied to clipboard", false, null, 2000);
    }
  }

  /**
   * Share Emergency Information via Web Share API or Clipboard Fallback
   */
  async function shareEmergencyInformation() {
    let record = null;
    if (selectedVillageLgd) {
      record = scenarioRecords.find(r => String(r.village_lgd_code) === selectedVillageLgd);
    }
    if (!record && userOrigin.matchedVillage) {
      record = scenarioRecords.find(r => String(r.village_lgd_code) === String(userOrigin.matchedVillage.village_lgd_code)) || userOrigin.matchedVillage;
    }
    if (!record && scenarioRecords.length > 0) {
      record = scenarioRecords[0];
    }

    const villageName = record ? (record.village_name_en || "Nilgiris Location") : userOrigin.label;
    const talukName = record ? (record.taluk_name_en || "Nilgiris") : "Nilgiris";
    const score = Number(record?.scenario_susceptibility_0_100 ?? record?.ml_susceptibility_0_100 ?? 0);
    const tier = getTierInfo(score).tier;
    const puStatus = record?.pu_status || "UNLABELED";
    const scenarioName = activeScenarioMeta?.scenario_name || "Historical Baseline";
    const factor = fmt(activeScenarioMeta?.rainfall_factor ?? 1.0, 2);
    const shelterName = selectedShelter?.name || "No source-verified shelter available";

    const shareText = [
      "🚨 FloodGuard Nilgiris — Emergency Susceptibility Context",
      "--------------------------------------------------",
      `Village: ${villageName}`,
      `Taluk: ${talukName}`,
      `Modeled Risk Tier: ${tier} (${fmt(score, 1)} / 100)`,
      `PU Status: ${puStatus} (UNLABELED ≠ NO RISK)`,
      `Active Rainfall Scenario: ${scenarioName} (${factor}×)`,
      `Verified shelter: ${shelterName}`,
      "--------------------------------------------------",
      "Nilgiris DDMA Emergency Helpline: 1077",
      "Police: 100 | Fire & Rescue: 101 | Emergency: 112",
      "",
      "Disclaimer: FloodGuard is a demonstration susceptibility and scenario system. It does not issue official evacuation orders or live disaster warnings. Follow instructions from official emergency authorities."
    ].join("\n");

    const shareData = {
      title: "FloodGuard Emergency Context",
      text: shareText
    };

    if (navigator.share) {
      try {
        await navigator.share(shareData);
        showToast("Emergency information shared successfully", false, null, 2000);
      } catch (err) {
        if (err.name !== "AbortError") {
          await copyEmergencyContextToClipboard(shareText);
        }
      }
    } else {
      await copyEmergencyContextToClipboard(shareText);
    }
  }

  /**
   * Initialize Leaflet Situation Map
   */
  function setBaseMap(name) {
    if (!map || !satelliteBaseLayer || !streetBaseLayer) return;
    if (map.hasLayer(satelliteBaseLayer)) map.removeLayer(satelliteBaseLayer);
    if (map.hasLayer(streetBaseLayer)) map.removeLayer(streetBaseLayer);
    const layer = name === "street" ? streetBaseLayer : satelliteBaseLayer;
    layer.addTo(map);
    document.querySelectorAll('input[name="base-map"]').forEach(input => {
      input.checked = input.value === (name === "street" ? "street" : "satellite");
    });
    const labels = $("toggle-satellite-labels");
    if (labels) {
      if (name === "street") {
        if (map.hasLayer(satelliteLabelLayer)) map.removeLayer(satelliteLabelLayer);
      } else if (labels.checked && !map.hasLayer(satelliteLabelLayer)) {
        satelliteLabelLayer.addTo(map);
      }
    }
  }

  function initMap() {
    map = L.map("map", {
      center: [11.41, 76.69],
      zoom: 10,
      zoomControl: false,
      attributionControl: true
    });

    satelliteBaseLayer = L.tileLayer(
      "https://server.arcgisonline.com/ArcGIS/rest/services/World_Imagery/MapServer/tile/{z}/{y}/{x}",
      {
        maxZoom: 19,
        attribution: "Tiles © Esri — Source: Esri, Maxar, Earthstar Geographics, and the GIS User Community"
      }
    );
    streetBaseLayer = L.tileLayer("https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png", {
      maxZoom: 18,
      attribution: "© <a href='https://www.openstreetmap.org/copyright' target='_blank'>OpenStreetMap</a> contributors"
    });
    satelliteLabelLayer = L.tileLayer(
      "https://server.arcgisonline.com/ArcGIS/rest/services/Reference/World_Boundaries_and_Places/MapServer/tile/{z}/{y}/{x}",
      {
        maxZoom: 19,
        attribution: "Labels © Esri, HERE, Garmin, FAO, NOAA, USGS, EPA, NPS, and the GIS User Community",
        pane: "overlayPane"
      }
    );
    setBaseMap("satellite");

    susceptibilityLayer = L.layerGroup().addTo(map);
    boundaryLayer = L.layerGroup().addTo(map);
    centroidLayer = L.layerGroup().addTo(map);
    eventLayer = L.layerGroup().addTo(map);
    shelterLayer = L.layerGroup().addTo(map);
    routeLayer = L.layerGroup().addTo(map);
    originMarkerLayer = L.layerGroup().addTo(map);
    weatherLayer = L.layerGroup().addTo(map);
    sensorLayer = L.layerGroup();
    if ($("toggle-sensors")?.checked) sensorLayer.addTo(map);
    flowAccumulationLayer = L.layerGroup();
    drainageNetworkLayer = L.layerGroup();

    document.querySelectorAll('input[name="base-map"]').forEach(input => {
      input.onchange = () => {
        if (input.checked) setBaseMap(input.value);
      };
    });
    const labelToggle = $("toggle-satellite-labels");
    if (labelToggle) {
      labelToggle.onchange = () => {
        if (labelToggle.checked && document.querySelector('input[name="base-map"]:checked')?.value === "satellite") {
          satelliteLabelLayer.addTo(map);
        } else if (map.hasLayer(satelliteLabelLayer)) {
          map.removeLayer(satelliteLabelLayer);
        }
      };
    }

    // Map Action Controls
    const btnZoomIn = $("btn-zoom-in");
    if (btnZoomIn) btnZoomIn.onclick = () => map.zoomIn();

    const btnZoomOut = $("btn-zoom-out");
    if (btnZoomOut) btnZoomOut.onclick = () => map.zoomOut();

    const btnReset = $("btn-reset-map");
    if (btnReset) {
      btnReset.onclick = () => {
        map.flyTo([11.41, 76.69], 10, { duration: 0.8 });
        showToast("Map view centered on Nilgiris District", false, null, 1500);
      };
    }

    const btnFullscreen = $("btn-fullscreen");
    if (btnFullscreen) {
      btnFullscreen.onclick = () => {
        if (!document.fullscreenElement) {
          document.documentElement.requestFullscreen().catch(() => {});
        } else {
          document.exitFullscreen().catch(() => {});
        }
      };
    }

    // Layers Popup Toggle
    const btnToggleLayers = $("btn-toggle-layers");
    const btnToggleLayersTop = $("btn-toggle-layers-top");
    const layersPopup = $("layers-popup");
    if (btnToggleLayers && layersPopup) {
      const mapControls = layersPopup.closest(".gmap-map-controls");
      const toggleLayers = e => {
        e.stopPropagation();
        const isOpen = layersPopup.style.display === "block";
        layersPopup.style.display = isOpen ? "none" : "block";
        if (mapControls) mapControls.classList.toggle("layers-open", !isOpen);
        btnToggleLayers.setAttribute("aria-expanded", String(!isOpen));
        if (btnToggleLayersTop) btnToggleLayersTop.setAttribute("aria-expanded", String(!isOpen));
      };
      btnToggleLayers.onclick = toggleLayers;
      if (btnToggleLayersTop) btnToggleLayersTop.onclick = toggleLayers;
      document.addEventListener("click", e => {
        if (layersPopup && !layersPopup.contains(e.target) && e.target !== btnToggleLayers && e.target !== btnToggleLayersTop) {
          layersPopup.style.display = "none";
          if (mapControls) mapControls.classList.remove("layers-open");
          btnToggleLayers.setAttribute("aria-expanded", "false");
          if (btnToggleLayersTop) btnToggleLayersTop.setAttribute("aria-expanded", "false");
        }
      });
    }

    // Layer Checkbox Toggles
    const toggleSusc = $("toggle-susceptibility");
    if (toggleSusc) {
      toggleSusc.onchange = e => {
        if (e.target.checked) {
          map.addLayer(susceptibilityLayer);
          showToast("Susceptibility Zones enabled", false, null, 1200);
        } else {
          map.removeLayer(susceptibilityLayer);
          showToast("Susceptibility Zones hidden", false, null, 1200);
        }
      };
    }

    const toggleShelters = $("toggle-shelters");
    if (toggleShelters) {
      toggleShelters.onchange = e => {
        if (e.target.checked) {
          map.addLayer(shelterLayer);
          showToast("Designated Shelters enabled", false, null, 1200);
        } else {
          map.removeLayer(shelterLayer);
          showToast("Designated Shelters hidden", false, null, 1200);
        }
      };
    }

    const toggleWeather = $("toggle-weather");
    if (toggleWeather) {
      toggleWeather.onchange = e => {
        if (e.target.checked) {
          map.addLayer(weatherLayer);
          showToast("Live Weather observation marker enabled", false, null, 1200);
        } else {
          map.removeLayer(weatherLayer);
          showToast("Live Weather observation marker hidden", false, null, 1200);
        }
      };
    }

    const toggleEvents = $("toggle-events");
    if (toggleEvents) {
      toggleEvents.onchange = e => {
        if (e.target.checked) {
          map.addLayer(eventLayer);
          showToast("GSI Landslides layer enabled", false, null, 1200);
        } else {
          map.removeLayer(eventLayer);
          showToast("GSI Landslides layer hidden", false, null, 1200);
        }
      };
    }

    const toggleBoundaries = $("toggle-boundaries");
    if (toggleBoundaries) {
      toggleBoundaries.onchange = e => {
        if (e.target.checked) {
          map.addLayer(boundaryLayer);
          showToast("Administrative boundaries enabled", false, null, 1200);
        } else {
          map.removeLayer(boundaryLayer);
          showToast("Administrative boundaries hidden", false, null, 1200);
        }
      };
    }

    const toggleCentroids = $("toggle-centroids");
    if (toggleCentroids) {
      toggleCentroids.onchange = e => {
        if (e.target.checked) {
          map.addLayer(centroidLayer);
          showToast("Village markers enabled", false, null, 1200);
        } else {
          map.removeLayer(centroidLayer);
          showToast("Village markers hidden", false, null, 1200);
        }
      };
    }
    const toggleSensors = $("toggle-sensors");
    if (toggleSensors) {
      toggleSensors.onchange = () => {
        if (toggleSensors.checked) {
          map.addLayer(sensorLayer);
          refreshSensorLayer().catch(error => {
            console.error("ESP32 sensor layer could not be loaded:", error);
            showToast("ESP32 sensor locations could not be loaded.", true, refreshSensorLayer);
          });
        } else {
          map.removeLayer(sensorLayer);
        }
      };
      refreshSensorLayer().catch(error => {
        console.error("ESP32 sensor layer could not be loaded:", error);
        showToast("ESP32 sensor locations could not be loaded.", true, refreshSensorLayer);
      });
    }
    const toggleWarningView = $("toggle-warning-view");
    if (toggleWarningView) {
      toggleWarningView.onchange = () => {
        setWarningMapView(toggleWarningView.checked);
      };
    }

    const createHydrologyLayers = async () => {
      if (!hydrologyMapDataPromise) {
        hydrologyMapDataPromise = api("/api/hydrology").then(data => {
          if (data.status !== "available") {
            throw new Error("Precomputed hydrology outputs are unavailable.");
          }
          flowAccumulationLayer = L.imageOverlay(
            data.flow_accumulation.preview_url,
            [
              [data.flow_accumulation.preview_bounds[0], data.flow_accumulation.preview_bounds[1]],
              [data.flow_accumulation.preview_bounds[2], data.flow_accumulation.preview_bounds[3]]
            ],
            { opacity: 0.38, interactive: false, alt: "DEM-derived flow accumulation preview" }
          );
          drainageNetworkLayer = L.imageOverlay(
            data.drainage_network.preview_url,
            [
              [data.drainage_network.preview_bounds[0], data.drainage_network.preview_bounds[1]],
              [data.drainage_network.preview_bounds[2], data.drainage_network.preview_bounds[3]]
            ],
            { opacity: 0.72, interactive: false, alt: "DEM-derived drainage network" }
          );
          return data;
        }).catch(error => {
          hydrologyMapDataPromise = null;
          throw error;
        });
      }
      return hydrologyMapDataPromise;
    };

    const setupHydrologyLayerToggle = (id, layerName, successMessage) => {
      const input = $(id);
      if (!input) return;
      input.onchange = async () => {
        const activeLayer = id === "toggle-flow-accumulation"
          ? flowAccumulationLayer
          : drainageNetworkLayer;
        if (!input.checked) {
          map.removeLayer(activeLayer);
          return;
        }
        input.disabled = true;
        try {
          await createHydrologyLayers();
          const loadedLayer = id === "toggle-flow-accumulation"
            ? flowAccumulationLayer
            : drainageNetworkLayer;
          map.addLayer(loadedLayer);
          showToast(successMessage, false, null, 1600);
        } catch (error) {
          input.checked = false;
          showToast(`${layerName} could not be loaded; check the hydrology source status.`, true);
        } finally {
          input.disabled = false;
        }
      };
    };
    setupHydrologyLayerToggle(
      "toggle-flow-accumulation",
      "Flow accumulation",
      "DEM-derived flow accumulation enabled"
    );
    setupHydrologyLayerToggle(
      "toggle-drainage-network",
      "Drainage network",
      "DEM-derived drainage network enabled"
    );

    // Map Click Listener for "Select Location on Map" mode
    map.on("click", e => {
      if (isPickingOriginOnMap) {
        setUserOrigin(e.latlng.lat, e.latlng.lng, `Custom Map Point (${e.latlng.lat.toFixed(4)}, ${e.latlng.lng.toFixed(4)})`, true);
        exitMapPickingMode();
        showToast("Evacuation origin set from map location", false, null, 1800);
      }
    });
  }

  /**
   * Find nearest village polygon/centroid to given coordinate
   */
  function findNearestVillageTo(lat, lng) {
    if (!scenarioRecords || !scenarioRecords.length) return null;

    let closest = null;
    let minDistance = Infinity;

    for (const v of scenarioRecords) {
      const polygon = villagePolygonsMap.get(String(v.village_lgd_code));
      let centerLat, centerLng;
      if (polygon) {
        const center = polygon.getBounds().getCenter();
        centerLat = center.lat;
        centerLng = center.lng;
      } else {
        // Fallback default coordinates if boundary not yet loaded
        centerLat = 11.41;
        centerLng = 76.69;
      }

      const d = haversineDistKm(lat, lng, centerLat, centerLng);
      if (d < minDistance) {
        minDistance = d;
        closest = { village: v, distanceKm: d };
      }
    }

    return closest;
  }

  /**
   * Set Evacuation Origin and update evacuation location context & shelters ranking
   */
  function setUserOrigin(lat, lng, label, isManual = false, matchedVillage = null) {
    userOrigin = {
      lat: Number(lat),
      lng: Number(lng),
      label: label || `Location (${Number(lat).toFixed(4)}, ${Number(lng).toFixed(4)})`,
      isManual: Boolean(isManual),
      matchedVillage: matchedVillage || null
    };

    // Update Origin Marker on Map
    originMarkerLayer.clearLayers();
    const originMarker = L.circleMarker([userOrigin.lat, userOrigin.lng], {
      radius: 7.5,
      color: "#ffffff",
      weight: 2.5,
      fillColor: "#0284c7",
      fillOpacity: 1.0
    });

    originMarker.bindPopup(`
      <div style="font-size:12px;">
        <strong style="color:#0284c7;font-size:13px;">📍 Evacuation Origin</strong><br>
        <span>${escapeHtml(userOrigin.label)}</span><br>
        <small class="mono">Lat: ${userOrigin.lat.toFixed(4)}° N, Lng: ${userOrigin.lng.toFixed(4)}° E</small>
      </div>
    `);
    originMarker.addTo(originMarkerLayer);

    // Update Origin UI text
    const descLabel = $("origin-desc-label");
    if (descLabel) {
      descLabel.textContent = `Origin: ${userOrigin.label}`;
    }

    const coordsChip = $("evac-origin-coords");
    if (coordsChip) {
      coordsChip.textContent = `${userOrigin.lat.toFixed(4)}° N, ${userOrigin.lng.toFixed(4)}° E`;
    }

    // Determine or refresh nearest modeled village
    if (!userOrigin.matchedVillage) {
      const match = findNearestVillageTo(userOrigin.lat, userOrigin.lng);
      if (match) {
        userOrigin.matchedVillage = match.village;
      }
    }

    updateEvacuationContext();
    refreshSheltersRanking();
    renderEmergencyPanel();
  }

  /**
   * Update Evacuation Location & Risk Context Card + Smart Route Summary
   */
  function updateEvacuationContext() {
    const v = userOrigin.matchedVillage || scenarioRecords[0];
    if (!v) return;

    // Find current updated record from scenarioRecords
    const currentRecord = scenarioRecords.find(r => String(r.village_lgd_code) === String(v.village_lgd_code)) || v;
    userOrigin.matchedVillage = currentRecord;

    const score = Number(currentRecord.scenario_susceptibility_0_100 ?? currentRecord.ml_susceptibility_0_100 ?? 0);
    const tier = getTierInfo(score);
    const delta = formatDelta(currentRecord.susceptibility_delta);

    // Update Modeled Village Info
    const nameEl = $("evac-matched-village-name");
    if (nameEl) nameEl.textContent = currentRecord.village_name_en || "Nilgiris Region";

    const talukEl = $("evac-matched-village-taluk");
    if (talukEl) talukEl.textContent = `Taluk: ${currentRecord.taluk_name_en || "—"} · LGD: ${currentRecord.village_lgd_code}`;

    const tierBadge = $("evac-matched-tier-badge");
    if (tierBadge) {
      tierBadge.className = `tier-chip ${tier.className}`;
      tierBadge.textContent = `${tier.tier} RISK`;
    }

    const scoreEl = $("evac-matched-score");
    if (scoreEl) scoreEl.textContent = fmt(score, 1);

    const deltaEl = $("evac-matched-delta");
    if (deltaEl) {
      deltaEl.className = `evac-delta-val ${delta.className}`;
      deltaEl.textContent = delta.text;
    }

    const scenNameEl = $("evac-matched-scenario-name");
    if (scenNameEl && activeScenarioMeta) {
      scenNameEl.textContent = `${fmt(activeScenarioMeta.rainfall_factor, 2)}x ${activeScenarioMeta.scenario_name}`;
    }

    // Notice text & UNLABELED handling
    const noticeEl = $("evac-risk-notice-text");
    if (noticeEl) {
      if (currentRecord.pu_status === "UNLABELED") {
        noticeEl.innerHTML = `<strong>UNLABELED LOCATION:</strong> No verified historical GSI event evidence is recorded for this village. (Unlabeled ≠ No Risk). Score represents modeled spatial susceptibility.`;
      } else {
        noticeEl.textContent = `This score represents modeled spatial susceptibility and is not a live disaster prediction.`;
      }
    }

    // Update Smart Route Context Card
    const ctxTier = $("ctx-origin-tier");
    if (ctxTier) {
      ctxTier.textContent = `${tier.tier} (${fmt(score, 1)}/100)`;
    }

    const ctxScen = $("ctx-rainfall-scen");
    if (ctxScen && activeScenarioMeta) {
      ctxScen.textContent = `${fmt(activeScenarioMeta.rainfall_factor, 2)}× ${activeScenarioMeta.scenario_name}`;
    }
  }

  function initShelterMarkers() {
    shelterLayer.clearLayers();
    shelterMarkersMap.clear();
    shelterRecords.forEach(shelter => {
      const coordinateStatus = shelter.coordinate_validation || {};
      if (
        !coordinateStatus.valid ||
        coordinateStatus.in_study_extent !== true ||
        shelter.latitude === null ||
        shelter.longitude === null
      ) return;
      const verified = shelter.operational && shelter.verification?.status === "verified";
      const verificationStatus = String(shelter.verification?.status || "needs_review");
      const marker = L.circleMarker([shelter.latitude, shelter.longitude], {
        radius: 7,
        color: verified ? "#ffffff" : "#475569",
        weight: 2,
        fillColor: verified ? "#15803d" : "#94a3b8",
        fillOpacity: 0.9
      });
      marker.bindTooltip(
        `<strong>${escapeHtml(shelter.name || "Shelter facility")}</strong> · ${escapeHtml(verificationStatus.toUpperCase())}`,
        { sticky: true }
      );
      marker.bindPopup(`
        <div class="shelter-popup">
          <strong>${escapeHtml(shelter.name || "Shelter facility")}</strong>
          <span class="shelter-verification shelter-verification-${escapeHtml(verificationStatus)}">${escapeHtml(verificationStatus.toUpperCase())}</span>
          <span>${escapeHtml(shelter.type || "Type not recorded")}</span>
          <span>${escapeHtml([shelter.village, shelter.taluk].filter(Boolean).join(" · ") || "Village/taluk unavailable")}</span>
          <span>Capacity: ${shelter.capacity == null ? "Not recorded" : escapeHtml(shelter.capacity)}</span>
          <span>Source: ${escapeHtml(shelter.verification?.source || "Not recorded")}</span>
          ${verified ? '<button type="button" class="shelter-popup-route">View road route</button>' : "<span>Routing disabled until facility verification.</span>"}
        </div>
      `);
      marker.on("click", () => selectShelter(shelter));
      marker.on("popupopen", event => {
        const button = event.popup.getElement()?.querySelector(".shelter-popup-route");
        if (button) button.onclick = () => drawEvacuationRoute(shelter);
      });
      marker.addTo(shelterLayer);
      shelterMarkersMap.set(shelter.shelter_id, marker);
    });
  }

  function updateShelterCard(shelter = null) {
    const verifiedCount = shelterRecords.filter(record =>
      record.operational &&
      record.verification?.status === "verified" &&
      record.coordinate_validation?.valid &&
      record.coordinate_validation?.in_study_extent === true
    ).length;
    const facilityPrompt = verifiedCount ? "Select a verified facility" : "No verified shelter records";
    const routeEligible = Boolean(
      shelter?.operational &&
      shelter.verification?.status === "verified" &&
      shelter.coordinate_validation?.valid &&
      shelter.coordinate_validation?.in_study_extent === true
    );
    const details = {
      "hero-shelter-distance": "Unavailable",
      "hero-shelter-name": routeEligible ? shelter.name || "Verified facility" : facilityPrompt,
      "hero-shelter-taluk": routeEligible
        ? `${shelter.village || "Village unavailable"} · ${shelter.taluk || "Taluk unavailable"}`
        : "Facility status unavailable",
      "hero-shelter-cap": routeEligible && shelter.capacity != null ? `Capacity: ${shelter.capacity}` : "",
      "hero-shelter-addr": routeEligible ? shelter.address || "Address not recorded" : "No authoritative facility source is currently configured.",
      "route-dist-val": "—",
      "route-dist-type": "Road route unavailable",
      "route-drive-time": "—",
      "ctx-shelter-dist": "Unavailable",
      "route-network-status": routeEligible ? "Road route not calculated" : facilityPrompt,
      "ctx-routing-mode": routeEligible ? "Backend road routing not calculated" : "Unavailable"
    };
    Object.entries(details).forEach(([id, text]) => {
      const element = $(id);
      if (element) element.textContent = text;
    });
    const emergencyDetails = {
      "em-shelter-name": routeEligible ? shelter.name || "Verified facility" : facilityPrompt,
      "em-shelter-meta": routeEligible
        ? `${shelter.village || "Village unavailable"} · ${shelter.taluk || "Taluk unavailable"} · VERIFIED${shelter.capacity == null ? "" : ` · Capacity ${shelter.capacity}`}`
        : "No authoritative facility source is configured.",
      "em-shelter-addr": routeEligible ? shelter.address || "Address not recorded" : "Follow information from local authorities.",
      "em-shelter-dist": "Unavailable",
      "em-drive-time": "Unavailable"
    };
    Object.entries(emergencyDetails).forEach(([id, text]) => {
      const element = $(id);
      if (element) element.textContent = text;
    });
    const routeButton = $("btn-draw-route");
    if (routeButton) routeButton.disabled = !routeEligible;
    const emergencyRouteButton = $("em-btn-plan-route");
    if (emergencyRouteButton) emergencyRouteButton.disabled = !routeEligible;
    const routeLabel = $("btn-draw-route-label");
    if (routeLabel) routeLabel.textContent = routeEligible ? "View road route" : "Road route unavailable";
  }

  function refreshSheltersRanking() {
    const candidates = shelterRecords.filter(shelter =>
      shelter.operational &&
      shelter.verification?.status === "verified" &&
      shelter.coordinate_validation?.valid &&
      shelter.coordinate_validation?.in_study_extent === true
    ).map(shelter => ({
      ...shelter,
      distanceKm: userOrigin.isManual
        ? haversineDistKm(userOrigin.lat, userOrigin.lng, shelter.latitude, shelter.longitude)
        : null
    })).sort((a, b) => (a.distanceKm ?? Infinity) - (b.distanceKm ?? Infinity));
    selectedShelter = null;
    updateShelterCard(null);
    const list = $("nearby-shelters-list");
    if (list) {
      if (!candidates.length) {
        list.textContent = "No source-verified shelters are available for route recommendations. Facility data must be sourced and validated first. Follow local-authority instructions.";
      } else {
        list.innerHTML = candidates.map((shelter, index) => `
          <div class="nearby-shelter-item" data-shelter-id="${escapeHtml(shelter.shelter_id)}" role="option" tabindex="0">
            <div class="item-shelter-info">
              <span class="item-shelter-name">${index + 1}. ${escapeHtml(shelter.name || "Verified facility")}</span>
              <span class="item-shelter-taluk">${escapeHtml(shelter.taluk || "Taluk unavailable")} · VERIFIED · Road route required to rank suitability</span>
            </div>
            <span class="item-shelter-dist">${shelter.distanceKm == null ? "Road distance not calculated" : `${fmt(shelter.distanceKm, 1)} km direct prefilter`}</span>
          </div>
        `).join("");
        list.querySelectorAll(".nearby-shelter-item").forEach(item => {
          item.onclick = () => {
            const match = shelterRecords.find(shelter => shelter.shelter_id === item.dataset.shelterId);
            if (match) selectShelter(match);
          };
        });
      }
    }
    initShelterMarkers();
  }

  async function loadShelters() {
    try {
      const response = await api("/api/shelters");
      shelterRecords = response.records || [];
      refreshSheltersRanking();
      const label = $("toggle-shelters")?.closest("label")?.querySelector(".toggle-text");
      if (label) label.textContent = `Verified shelter locations (${response.route_eligible_count || 0})`;
      const legendCount = $("legend-shelter-count");
      if (legendCount) legendCount.textContent = `Verified shelter locations (${response.route_eligible_count || 0})`;
    } catch (error) {
      console.error("Verified shelter inventory could not be loaded:", error);
      shelterRecords = [];
      refreshSheltersRanking();
      showToast("Shelter inventory unavailable; no route can be recommended.", true, loadShelters);
    }
  }

  function selectShelter(shelter) {
    selectedShelter = shelter;
    updateShelterCard(shelter);
    document.querySelectorAll(".nearby-shelter-item").forEach(item => {
      item.classList.toggle("active", item.dataset.shelterId === shelter.shelter_id);
    });
    const marker = shelterMarkersMap.get(shelter.shelter_id);
    if (marker && map) marker.openPopup();
    renderEmergencyPanel();
  }

  async function drawEvacuationRoute(shelter = null) {
    const target = shelter || selectedShelter;
    if (!target || !map) return;
    if (!target.operational || target.verification?.status !== "verified") {
      showToast("This facility is not verified; routing is disabled.", true);
      return;
    }
    showToast(`Requesting road route to ${target.name || "verified shelter"}…`);
    routeLayer.clearLayers();
    const params = new URLSearchParams({ shelter_id: target.shelter_id });
    if (userOrigin.isManual) {
      params.set("origin_lat", String(userOrigin.lat));
      params.set("origin_lon", String(userOrigin.lng));
    } else {
      const villageCode = selectedVillageLgd
        || userOrigin.matchedVillage?.village_lgd_code;
      if (!villageCode) {
        const status = $("route-network-status");
        if (status) status.textContent = "Select a village or explicitly set your location first.";
        showToast("Select a village or set your location before requesting a route.", true);
        return;
      }
      params.set("village_code", String(villageCode));
    }
    params.set("mode", "scenario");
    const activeScenarioName = String(activeScenarioMeta?.scenario_name || "");
    const scenarioKey = activeScenarioName.startsWith("Custom")
      ? "custom"
      : String(activeScenarioMeta?.scenario_key || "baseline");
    params.set("scenario", scenarioKey);
    if (scenarioKey === "custom") {
      params.set("multiplier", String(activeScenarioMeta?.rainfall_factor ?? 1));
    }
    const routeStatus = $("route-network-status");
    const routeDistance = $("route-dist-val");
    const routeType = $("route-dist-type");
    const driveTime = $("route-drive-time");
    const routeSummary = $("route-risk-summary");
    if (routeStatus) routeStatus.textContent = "Calculating road route and risk context…";
    try {
      const result = await api(`/api/evacuation-route?${params}`);
      const route = result.route;
      if (!route?.geometry || route.geometry.type !== "LineString") {
        throw new Error("Road-routing service returned no valid road geometry.");
      }
      const routeFeature = L.geoJSON(route.geometry, {
        style: { color: "#075985", weight: 6, opacity: 0.95, lineCap: "round", lineJoin: "round" }
      }).addTo(routeLayer);
      routeFeature.bindPopup(`
        <div class="route-popup">
          <strong>${escapeHtml(route.recommendation_label || "Road route")}</strong><br>
          <strong>Destination:</strong> ${escapeHtml(target.name || "Verified shelter")}<br>
          <strong>Road distance:</strong> ${fmt(route.distance_km, 1)} km<br>
          <strong>Estimated time:</strong> ${fmt(route.estimated_minutes, 0)} min<br>
          <strong>Route context:</strong> ${escapeHtml(result.risk_assessment?.status || "unavailable")}<br>
          <span>Confirm road conditions with local authorities. This route is not a safety guarantee.</span>
        </div>
      `);
      if (routeDistance) routeDistance.textContent = `${fmt(route.distance_km, 1)} km`;
      if (routeType) routeType.textContent = `${route.provider} road distance`;
      if (driveTime) driveTime.textContent = `${fmt(route.estimated_minutes, 0)} min (provider estimate)`;
      const riskAssessment = result.risk_assessment || {};
      if (routeStatus) routeStatus.textContent = `Route context: ${String(riskAssessment.status || "unavailable").replace("_", " ")}`;
      const routingMode = $("ctx-routing-mode");
      if (routingMode) routingMode.textContent = route.provider;
      const safetyFactors = [
        ...(riskAssessment.warning_intersections || []).map(
          item => `${String(item.warning_label || item.warning_stage || "warning").toUpperCase()} warning area · ${item.village_name || item.village_lgd_code}`
        ),
        ...(riskAssessment.high_susceptibility_intersections || []).map(
          item => `Very high baseline susceptibility · ${item.village_name || item.village_lgd_code}`
        ),
        ...(riskAssessment.historical_event_proximity || []).map(
          item => `Historical event coordinate intersects route corridor · ${item.event_id}`
        )
      ];
      if (routeSummary) {
        routeSummary.textContent = safetyFactors.length
          ? safetyFactors.join("; ")
          : "No configured warning, very-high-susceptibility, or historical-event intersection was found in the assessed corridor. This does not establish that the road is safe.";
      }
      const clearButton = $("btn-clear-route");
      if (clearButton) clearButton.style.display = "inline-flex";
      const bounds = routeFeature.getBounds();
      if (bounds.isValid()) map.fitBounds(bounds, { padding: [70, 70], maxZoom: 14 });
      showToast(`Road route displayed to ${target.name || "verified shelter"}`, false, null, 2500);
    } catch (error) {
      routeLayer.clearLayers();
      console.error("Backend road-route request failed:", error);
      if (routeStatus) routeStatus.textContent = "Road route currently unavailable.";
      if (routeDistance) routeDistance.textContent = "—";
      if (routeType) routeType.textContent = "No straight-line fallback is drawn";
      if (driveTime) driveTime.textContent = "—";
      if (routeSummary) routeSummary.textContent = "Route guidance requires a valid facility, origin, and available routing provider.";
      showToast("Road route currently unavailable.", true, () => drawEvacuationRoute(target));
    }
  }

  /**
   * Clear Route from Map
   */
  function clearRoute() {
    routeLayer.clearLayers();
    updateShelterCard(selectedShelter);
    const summary = $("route-risk-summary");
    if (summary) summary.textContent = "Route not calculated.";
    const btnClear = $("btn-clear-route");
    if (btnClear) btnClear.style.display = "none";

    const btnDrawLabel = $("btn-draw-route-label");
    if (btnDrawLabel) btnDrawLabel.textContent = "Display Evacuation Route";

    const routeStatus = $("route-network-status");
    if (routeStatus) routeStatus.textContent = "Road route not calculated.";

    showToast("Evacuation route cleared from map", false, null, 1200);
  }

  /**
   * Enter Map Location Picker Mode
   */
  function enterMapPickingMode() {
    isPickingOriginOnMap = true;
    const banner = $("map-picker-banner");
    if (banner) banner.style.display = "flex";

    const mapEl = $("map");
    if (mapEl) mapEl.classList.add("map-picking-active");

    showToast("Click on map to select evacuation origin", false, null, 2000);
  }

  /**
   * Exit Map Location Picker Mode
   */
  function exitMapPickingMode() {
    isPickingOriginOnMap = false;
    const banner = $("map-picker-banner");
    if (banner) banner.style.display = "none";

    const mapEl = $("map");
    if (mapEl) mapEl.classList.remove("map-picking-active");
  }

  // Global helper for popup buttons
  window.__floodguardPlanRoute = function(shelterId) {
    const match = shelterRecords.find(s => s.shelter_id === shelterId);
    if (match) {
      const panel = $("shelter-panel");
      if (panel) panel.style.display = "flex";
      selectShelter(match);
      drawEvacuationRoute(match);
    }
  };

  /**
   * Load System & Governance Status
   */
  async function loadStatus() {
    try {
      const data = await api("/api/status");
      const statusEl = $("model-status");
      const modelInfoButton = $("btn-model-info");
      const reasonEl = $("model-reason");
      const modelTypeEl = $("prov-model-type");
      const validationEl = $("prov-validation");
      const rocEl = $("metric-loto-roc");
      const prEl = $("metric-loto-pr");
      const brierEl = $("metric-loto-brier");
      const coverageEl = $("model-hydrology-coverage");
      if (statusEl) {
        statusEl.textContent = "Model ✓";
      }
      if (modelTypeEl) modelTypeEl.textContent = data.model_type;
      if (validationEl) validationEl.textContent = data.validation;
      if (rocEl) rocEl.textContent = fmt(data.loto_roc_auc, 4);
      if (prEl) prEl.textContent = fmt(data.loto_pr_auc, 4);
      if (brierEl) brierEl.textContent = fmt(data.loto_brier_score, 4);
      if (coverageEl && data.hydrology_join) {
        const hydrologyFeatures = data.feature_groups?.hydrology || [];
        const zeroImportance = hydrologyFeatures.filter(
          feature => Number(data.feature_importances?.[feature] || 0) === 0
        );
        coverageEl.textContent =
          `Phase 3 hydrology joined by ${data.hydrology_join.join_key} for ` +
          `${data.hydrology_join.matched_count}/${data.hydrology_join.model_population_count} ` +
          `training villages; ${data.hydrology_join.unmatched_model_count} unmatched. ` +
          `${zeroImportance.length}/${hydrologyFeatures.length} hydrology features have zero ` +
          `XGBoost split importance in the current fit; hydrology is used in ` +
          `${data.folds_using_hydrology}/6 fold models. No performance gain is claimed.`;
      }
      if (modelInfoButton) {
        const modelDetails = `${data.model_type} · ${data.validation} · LOTO ROC-AUC ${fmt(data.loto_roc_auc, 4)}`;
        modelInfoButton.title = modelDetails;
        modelInfoButton.setAttribute("aria-label", `Model information: ${modelDetails}`);
      }
      if (reasonEl) {
        reasonEl.textContent = `Baseline susceptibility · terrain + hydrology + historical rainfall · 25 Positive · 15 Unlabeled · LOTO proxy ROC-AUC ${fmt(data.loto_roc_auc, 4)} · UNLABELED ≠ NO RISK`;
      }
    } catch (err) {
      console.warn("Status fetch warning:", err);
    }
  }

  /**
   * Load Administrative Framework & Reconciliation
   */
  async function loadVillages() {
    try {
      const data = await api("/api/villages?limit=102");
      const summaryEl = $("village-summary");
      if (summaryEl) {
        summaryEl.textContent = `${data.record_count} Village Master records; ${data.exact_spatial_matches} exact LGD matches. ${data.village_master_only} Master-only and ${data.kmz_only} KMZ-only records remain unmatched.`;
      }

      const tableEl = $("village-table");
      if (tableEl) {
        tableEl.innerHTML = (data.records || []).slice(0, 15).map(row => `
          <tr>
            <td>${escapeHtml(row.village_name_en)}</td>
            <td>${escapeHtml(row.taluk_name_en)}</td>
            <td class="mono">${escapeHtml(row.village_lgd_code)}</td>
          </tr>
        `).join("");
      }
    } catch (err) {
      console.warn("Villages fetch warning:", err);
    }
  }

  /**
   * Load GSI / NLFC Landslide Event Inventory
   */
  async function loadEvents() {
    try {
      const data = await api("/api/events");
      allEvents = data.records || [];
      
      const totalEl = $("event-total");
      if (totalEl) totalEl.textContent = data.record_count;

      const datesDl = $("event-dates");
      if (datesDl && data.date_categories) {
        datesDl.innerHTML = Object.entries(data.date_categories).map(([k, v]) => `
          <div>
            <dt>${escapeHtml(k.replace(/_/g, " ").toUpperCase())}</dt>
            <dd>${v}</dd>
          </div>
        `).join("");
      }

      const tableEl = $("events-table");
      if (tableEl) {
        tableEl.innerHTML = allEvents.slice(0, 15).map(row => `
          <tr>
            <td>${escapeHtml(row.location_description)}</td>
            <td class="mono">${escapeHtml(row.history_raw)}</td>
            <td>${escapeHtml(row.material_involved)}</td>
            <td>${escapeHtml(row.movement_type)}</td>
          </tr>
        `).join("");
      }

      // Render Distinct Historical GSI Markers on Map with reduced visual clutter
      allEvents.forEach(row => {
        const lat = Number(row.latitude);
        const lng = Number(row.longitude);
        if (Number.isFinite(lat) && Number.isFinite(lng)) {
          const marker = L.circleMarker([lat, lng], {
            radius: 3.0,
            color: "#ffffff",
            weight: 1.0,
            fillColor: "#e11d48",
            fillOpacity: 0.75
          });
          marker.bindPopup(`
            <div style="font-size:12px;">
              <strong style="color:#e11d48;font-size:13px;">Historical GSI Landslide Point</strong><br>
              <strong>Location:</strong> ${escapeHtml(row.location_description)}<br>
              <strong>Historical Date:</strong> ${escapeHtml(row.history_raw)}<br>
              <strong>Material:</strong> ${escapeHtml(row.material_involved)}<br>
              <strong>Movement:</strong> ${escapeHtml(row.movement_type)}<br>
              <small style="color:#64748b;">Lat: ${lat.toFixed(4)}, Lng: ${lng.toFixed(4)}</small><br>
              <small style="color:#94a3b8;font-style:italic;">Historical GSI inventory record; not a live event.</small>
            </div>
          `);
          marker.addTo(eventLayer);
        }
      });
    } catch (err) {
      console.warn("Events fetch warning:", err);
    }
  }

  /**
   * Load SRTM Terrain Evidence Baseline
   */
  async function loadTerrain() {
    try {
      const data = await api("/api/terrain");
      const container = $("terrain-cards");
      if (container && data.records) {
        container.innerHTML = data.records.map(row => `
          <article class="terrain-card">
            <span class="badge-tag tag-cyan">DEMO COORD</span>
            <h3>${escapeHtml(row.settlement_name)}</h3>
            <div class="terrain-values">
              <div>
                <strong>${fmt(row.elevation_m, 0)} m</strong>
                <span>Elevation</span>
              </div>
              <div>
                <strong>${fmt(row.slope_deg, 1)}°</strong>
                <span>Mean Slope</span>
              </div>
            </div>
          </article>
        `).join("");
      }
    } catch (err) {
      console.warn("Terrain fetch warning:", err);
    }
  }

  /**
   * Load IMD Rainfall Series & Summary
   */
  async function loadRain() {
    const yearSelect = $("rain-year");
    const placeSelect = $("rain-place");
    if (!yearSelect) return;

    const year = yearSelect.value || "2024";
    try {
      const data = await api(`/api/rainfall?year=${year}`);
      const records = data.records || [];

      const settlementIds = [...new Set(records.map(r => r.settlement_id))];
      if (placeSelect && (!placeSelect.options.length || placeSelect.dataset.year !== year)) {
        placeSelect.dataset.year = year;
        placeSelect.innerHTML = settlementIds.map(id => {
          const match = records.find(r => r.settlement_id === id);
          return `<option value="${escapeHtml(id)}">${escapeHtml(match ? match.settlement_name : id)}</option>`;
        }).join("");
      }

      const currentPlace = placeSelect ? placeSelect.value : settlementIds[0];
      const filtered = records
        .filter(r => r.settlement_id === currentPlace)
        .sort((a, b) => b.date.localeCompare(a.date));

      const tableEl = $("rain-table");
      if (tableEl) {
        tableEl.innerHTML = filtered.slice(0, 14).map(row => `
          <tr>
            <td class="mono">${escapeHtml(row.date)}</td>
            <td class="mono">${fmt(row.rainfall_1d_mm, 1)}</td>
            <td class="mono">${fmt(row.rainfall_3d_mm, 1)}</td>
            <td class="mono">${fmt(row.rainfall_7d_mm, 1)}</td>
          </tr>
        `).join("");
      }

      const readingEl = $("rain-reading");
      if (readingEl) {
        readingEl.textContent = `${records.length} validated daily observation rows for ${year}. No imputation.`;
      }

      const statusEl = $("rain-status");
      if (statusEl) {
        statusEl.textContent = `Showing latest 14 observation dates for selected coordinates (${year}).`;
      }
    } catch (err) {
      console.warn("Rain fetch warning:", err);
    }
  }

  /**
   * Highlight polygon on map
   */
  function highlightPolygon(polygon, score) {
    if (activeHighlightedPolygon && activeHighlightedPolygon !== polygon) {
      const oldScore = activeHighlightedPolygon._floodguardScore || 0;
      const oldTier = getTierInfo(oldScore);
      activeHighlightedPolygon.setStyle({
        color: oldTier.stroke,
        weight: 1.5,
        fillOpacity: oldTier.fillOpacity
      });
      if (activeHighlightedPolygon._path) {
        L.DomUtil.removeClass(activeHighlightedPolygon._path, "polygon-selected");
      }
    }

    if (polygon) {
      polygon._floodguardScore = score;
      polygon.setStyle({
        color: "#0284c7",
        weight: 3.5,
        fillOpacity: 0.45
      });
      if (polygon._path) {
        L.DomUtil.addClass(polygon._path, "polygon-selected");
      }
      polygon.bringToFront();
      activeHighlightedPolygon = polygon;
    }
  }

  /**
   * Enforce Single Active Drawer Policy (Phase 8 UI Restructuring)
   * Ensures only ONE panel/drawer is open at any time.
   */
  function closeAllPanels(exceptId = null) {
    const panels = [
      { id: "village-detail-panel", btnId: null },
      { id: "shelter-panel", btnId: "btn-toggle-shelters" },
      { id: "live-weather-card", btnId: "btn-toggle-weather" },
      { id: "scenario-panel", btnId: "btn-toggle-scenario" },
      { id: "emergency-panel", btnId: "btn-toggle-emergency" }
    ];

    panels.forEach(p => {
      const el = $(p.id);
      const topBtn = p.btnId ? $(p.btnId) : null;

      if (p.id !== exceptId) {
        if (el) el.style.display = "none";
        if (topBtn) topBtn.classList.remove("active");
      } else {
        if (el) el.style.display = "flex";
        if (topBtn) topBtn.classList.add("active");
      }
    });

    if (exceptId !== "shelter-panel" && isPickingOriginOnMap) {
      exitMapPickingMode();
    }
  }

  function setRankingPanelExpanded(expanded) {
    const panel = $("village-ranking-panel");
    const toggle = $("btn-toggle-ranking");
    if (!panel || !toggle) return;

    panel.classList.toggle("is-expanded", expanded);
    panel.classList.toggle("is-collapsed", !expanded);
    toggle.setAttribute("aria-expanded", String(expanded));
    toggle.setAttribute("aria-label", `${expanded ? "Collapse" : "Expand"} village risk ranking`);
  }

  function renderVillageContext(context) {
    const container = $("village-context-provenance");
    if (!container) return;

    const risk = context.risk || {};
    const terrain = context.terrain || {};
    const rainfall = context.rainfall || {};
    const historical = rainfall.historical;
    const evidence = context.historical_evidence || {};
    const scenario = context.scenario || rainfall.scenario || {};
    const model = context.model || {};
    const evidenceIds = (evidence.data || [])
      .slice(0, 3)
      .map(event => event.event_id)
      .filter(Boolean);
    const evidenceSummary = evidence.status === "available"
      ? `${evidence.count || 0} conditional exact-polygon link(s)${evidenceIds.length ? ` · IDs ${evidenceIds.join(", ")}` : ""}`
      : "Unavailable";
    const rainfallSummary = historical
      ? `${historical.date} · 1d ${fmt(historical.rainfall_1d_mm, 1)} mm · 3d ${fmt(historical.rainfall_3d_mm, 1)} mm · 7d ${fmt(historical.rainfall_7d_mm, 1)} mm`
      : "Unavailable for this village";
    const terrainSummary = terrain.data
      ? `Elevation ${fmt(terrain.data.elevation_mean_m, 0)} m · Slope ${fmt(terrain.data.slope_mean_deg, 1)}°`
      : "Unavailable for this village";
    const scenarioSummary = scenario.status === "available"
      ? `${scenario.name} · ${fmt(scenario.multiplier, 2)}× · simulated`
      : "Unavailable";
    const hydrology = context.hydrology || {};
    const flowAccumulation = hydrology.flow_accumulation;
    const drainageDensity = hydrology.drainage_density;
    const drainageNetwork = hydrology.drainage_network;
    const soil = context.soil_moisture || {};
    const soilSensors = context.sensor?.data || [];
    const largestCatchment = flowAccumulation
      ? `${fmt(flowAccumulation.maximum_contributing_area_km2, 2)} km² · ${fmt(flowAccumulation.maximum_upstream_cells, 0)} upstream cells`
      : "Unavailable for this village";
    const drainageDensitySummary = drainageDensity
      ? `${fmt(drainageDensity.value_km_per_km2, 2)} km/km² · ${fmt(drainageDensity.drainage_length_km, 2)} km mapped`
      : "Unavailable for this village";
    const drainageLengthSummary = drainageNetwork
      ? `${fmt(drainageNetwork.drainage_length_km, 2)} km`
      : "Unavailable for this village";
    const soilValue = soil.value;
    const soilBand = soilValue === null || soilValue === undefined
      ? null
      : SOIL_MOISTURE_DISPLAY_BANDS.find(band => Number(soilValue) <= band.maximum)?.label;
    const soilSummary = soilValue === null || soilValue === undefined
      ? soilSensors.length
        ? "Sensor registered; no readings received · NO DATA"
        : "No sensor registered"
      : soil.freshness === "offline"
        ? `Last known reading ${fmt(soilValue, 1)}% · Sensor offline · Last seen ${formatElapsed(soil.age_seconds)}${soil.is_test ? " · TEST DATA" : ""}`
        : soil.freshness === "stale"
          ? `${fmt(soilValue, 1)}%${soilBand ? ` · ${soilBand} (display label only)` : ""} · Last reading ${formatElapsed(soil.age_seconds)} · STALE${soil.is_test ? " · TEST DATA" : ""}`
          : `${fmt(soilValue, 1)}%${soilBand ? ` · ${soilBand} (display label only)` : ""} · Sensor ${soil.sensor_id || "—"} · ONLINE · Received ${formatElapsed(soil.age_seconds)}${soil.is_test ? " · TEST DATA" : ""}`;
    const warning = context.warning || {};
    const warningStage = String(warning.stage || "unavailable");
    const warningStatus = $("detail-warn-status");
    const warningBadge = $("detail-warn-badge");
    const warningIcon = $("detail-warn-icon");
    const warningReasons = $("detail-warn-reasons");
    const warningAction = $("detail-warn-action");
    const warningCard = $("village-warning-card");
    const warningColor = warning.color || "#64748b";
    if (warningStatus) {
      warningStatus.textContent = warning.status === "unavailable"
        ? "WARNING STATUS UNAVAILABLE"
        : `${warningStage.toUpperCase()} — ${String(warning.label || "").toUpperCase()}`;
      warningStatus.style.color = warningColor;
    }
    if (warningBadge) {
      warningBadge.textContent = warning.status === "unavailable"
        ? "INSUFFICIENT COVERAGE"
        : `${warningStage.toUpperCase()} · ${String(warning.label || "").toUpperCase()}`;
      warningBadge.className = `v-warn-chip warning-${warningStage}`;
    }
    if (warningIcon) {
      warningIcon.textContent = warning.status === "unavailable"
        ? "—"
        : warningStage === "green" ? "✓" : warningStage.toUpperCase();
      warningIcon.style.color = warningColor;
    }
    if (warningCard) warningCard.dataset.warningMode = warning.rainfall_mode || "unavailable";
    if (warningReasons) {
      const explanation = warning.status === "unavailable"
        ? [warning.reason || "Insufficient model/data coverage."]
        : [
            ...(warning.reason ? warning.reason.split("; ").map(value => value.replace(/\.$/, "")) : []),
            ...((warning.missing_inputs || []).length
              ? [`Missing: ${warning.missing_inputs.join(", ")}`]
              : []),
            ...(warning.is_test ? ["TEST DATA contributed; this is not a real sensor warning."] : [])
          ];
      warningReasons.replaceChildren(
        ...explanation.map(item => {
          const li = document.createElement("li");
          li.textContent = item;
          return li;
        })
      );
    }
    if (warningAction) {
      warningAction.textContent = warning.guidance
        || "No recommendation is available because required model/data coverage is missing.";
    }
    const rows = [
      ["Baseline modeled score", risk.score === null || risk.score === undefined ? "Unavailable" : `${fmt(risk.score, 2)} / 100 (${risk.tier || "unclassified"})`, risk.provenance?.source_name],
      ["Terrain", terrainSummary, terrain.provenance?.source_name],
      ["Historical rainfall", rainfallSummary, rainfall.provenance?.source_name],
      ["Rainfall scenario", scenarioSummary, scenario.provenance?.source_name],
      ["Historical evidence", evidenceSummary, evidence.provenance?.source_name],
      ["Model validation", model.validation || "Unavailable", model.model_type],
      ["Current weather", "Browser-fetched context; not persisted by the village API", context.weather?.provenance?.source_name],
      ["Hydrology · largest contributing area", largestCatchment, flowAccumulation?.provenance?.source_name],
      ["Hydrology · drainage density", drainageDensitySummary, drainageDensity?.provenance?.source_name],
      ["Hydrology · drainage length", drainageLengthSummary, drainageNetwork?.provenance?.source_name],
      ["Water accumulation potential", hydrology.water_accumulation_potential?.status === "not_derived"
        ? "Not classified from SRTM alone"
        : "Unavailable", hydrology.water_accumulation_potential?.reason],
      ["Soil moisture", soilSummary, soil.provenance?.source_name || null],
      ["Warning mode", warning.warning_mode_label || warning.rainfall_mode || "Unavailable", warning.rule_version ? `Rule ${warning.rule_version}` : null],
      ["Warning rule", warning.rule_id || "Unavailable", warning.reason || null]
    ];

    container.replaceChildren();
    rows.forEach(([label, value, source]) => {
      const row = document.createElement("div");
      row.className = "context-source-row";
      const title = document.createElement("strong");
      title.textContent = label;
      const detail = document.createElement("span");
      detail.textContent = value;
      row.append(title, detail);
      if (source) {
        const provenance = document.createElement("small");
        provenance.textContent = source;
        row.append(provenance);
      }
      container.append(row);
    });
  }

  async function loadVillageContext(record) {
    const requestId = ++villageContextRequestId;
    const container = $("village-context-provenance");
    if (container) container.textContent = "Loading village data-source details…";

    const activeScenarioName = String(activeScenarioMeta?.scenario_name || "");
    const scenarioKey = activeScenarioName.startsWith("Custom")
      ? "custom"
      : String(activeScenarioMeta?.scenario_key || "baseline");
    const params = new URLSearchParams({ scenario: scenarioKey });
    if (scenarioKey === "custom") {
      params.set("multiplier", String(activeScenarioMeta?.rainfall_factor ?? 1));
    }
    const code = String(record.village_lgd_code);

    try {
      const context = await api(`/api/villages/${encodeURIComponent(code)}/context?${params}`);
      if (requestId !== villageContextRequestId || selectedVillageLgd !== code) return;
      renderVillageContext(context);
    } catch (err) {
      if (requestId !== villageContextRequestId || selectedVillageLgd !== code) return;
      if (container) container.textContent = "Village data-source details could not be loaded.";
      showToast(
        "Unable to load village data-source details",
        true,
        () => loadVillageContext(record)
      );
    }
  }

  /**
   * Render Selected Village Details in Decision Support Card / Bottom Sheet
   */
  function renderVillageDetail(record) {
    if (!record) return;
    selectedVillageRecord = record;
    selectedVillageLgd = String(record.village_lgd_code);

    closeAllPanels("village-detail-panel");
    setRankingPanelExpanded(false);

    const score = Number(record.scenario_susceptibility_0_100 ?? record.ml_susceptibility_0_100 ?? 0);
    const baseScore = Number(record.baseline_susceptibility_0_100 ?? record.ml_susceptibility_0_100 ?? score);
    const tier = getTierInfo(score);
    const delta = formatDelta(record.susceptibility_delta);

    // Update Header
    const puLabel = $("detail-pu-label");
    if (puLabel) {
      if (record.pu_status === "POSITIVE") {
        puLabel.textContent = "HISTORICAL GSI EVENT EVIDENCE";
        puLabel.className = "badge-label";
      } else {
        puLabel.textContent = "NO RECORDED GSI EVIDENCE (UNLABELED ≠ NO RISK)";
        puLabel.className = "badge-label text-muted";
      }
    }

    const nameEl = $("detail-village-name");
    if (nameEl) nameEl.textContent = record.village_name_en || "Unknown Village";

    const talukEl = $("detail-village-taluk");
    if (talukEl) talukEl.textContent = `Taluk: ${record.taluk_name_en || "—"}`;

    const lgdEl = $("detail-village-lgd");
    if (lgdEl) lgdEl.textContent = `Code: ${record.village_lgd_code}`;

    const badgeEl = $("detail-tier-badge");
    if (badgeEl) {
      badgeEl.className = `tier-chip ${tier.className}`;
      badgeEl.textContent = `${tier.tier} SCENARIO RISK`;
    }

    const deltaEl = $("detail-delta-badge");
    if (deltaEl) {
      deltaEl.className = `delta-chip ${delta.className}`;
      deltaEl.textContent = delta.shortText;
    }

    // Synchronize Scenario Drawer Experiment Result Card
    const expVillageName = $("scen-exp-village-name");
    if (expVillageName) expVillageName.textContent = record.village_name_en || "Selected Village";

    const expBaseScore = $("scen-exp-base-score");
    if (expBaseScore) expBaseScore.textContent = fmt(baseScore, 1);

    const expScenScore = $("scen-exp-scen-score");
    if (expScenScore) expScenScore.textContent = fmt(score, 1);

    const expDeltaVal = $("scen-exp-delta-val");
    if (expDeltaVal) {
      expDeltaVal.textContent = delta.shortText;
      expDeltaVal.className = `exp-metric-val ${delta.className}`;
    }

    // Hero Score & Bar
    const indexEl = $("detail-index");
    if (indexEl) indexEl.textContent = fmt(baseScore, 1);

    const barEl = $("detail-score-bar");
    if (barEl) barEl.style.width = `${Math.min(100, Math.max(0, baseScore))}%`;

    // Scenario Impact Breakdown
    const baseScoreEl = $("detail-base-score");
    if (baseScoreEl) baseScoreEl.textContent = `${fmt(baseScore, 1)}`;

    const scenScoreEl = $("detail-scenario-score");
    if (scenScoreEl) scenScoreEl.textContent = `${fmt(score, 1)}`;

    const deltaValEl = $("detail-delta-val");
    if (deltaValEl) {
      deltaValEl.textContent = delta.rawText;
      deltaValEl.className = `comp-sub-val delta-val ${delta.className}`;
    }

    const deltaNoteEl = $("detail-delta-note");
    if (deltaNoteEl) {
      deltaNoteEl.textContent = delta.isZero ? "Historical baseline expectation" : "vs Historical Baseline";
    }

    // 4 Component Breakdown
    const compTerrain = $("comp-terrain");
    if (compTerrain) compTerrain.textContent = `${fmt(record.slope_mean_deg, 1)}°`;
    const compTerrainRaw = $("comp-terrain-raw");
    const maxSlope = record.slope_max_deg ? ` · Max: ${fmt(record.slope_max_deg, 1)}°` : "";
    if (compTerrainRaw) compTerrainRaw.textContent = `Elev: ${fmt(record.elevation_mean_m, 0)}m${maxSlope}`;

    const compRain = $("comp-rain");
    const rain7d = record.scenario_rainfall_7d_p95_mm ?? record.rainfall_7d_p95_mm;
    if (compRain) compRain.textContent = `${fmt(rain7d, 1)} mm`;
    const compRainRaw = $("comp-rain-raw");
    const baseRain7d = record.baseline_rainfall_7d_p95_mm ?? record.historical_rainfall_7d_p95_mm;
    if (compRainRaw) compRainRaw.textContent = `Base P95: ${fmt(baseRain7d, 1)}mm`;

    const compEvents = $("comp-events");
    if (compEvents) {
      compEvents.textContent = record.pu_status === "POSITIVE" ? "1 Verified Match" : "UNLABELED";
    }
    const compEventsRaw = $("comp-events-raw");
    if (compEventsRaw) {
      compEventsRaw.textContent = record.pu_status === "POSITIVE" 
        ? "GSI Strict Containment" 
        : "No recorded GSI inventory evidence";
    }

    const compRain1d = $("comp-rain-1d");
    if (compRain1d) {
      const factor = activeScenarioMeta?.rainfall_factor ?? 1.0;
      const base1d = Number(record.rainfall_1d_max_mm || 180.0);
      compRain1d.textContent = `${fmt(base1d * factor, 1)} mm`;
    }
    const compRain1dRaw = $("comp-rain-1d-raw");
    if (compRain1dRaw) compRain1dRaw.textContent = "Simulated 1D Peak";

    // GSI Evidence Card description
    const gsiDescEl = $("detail-gsi-desc");
    if (gsiDescEl) {
      gsiDescEl.textContent = record.pu_status === "POSITIVE"
        ? "Historical landslide inventory event matched"
        : "UNLABELED (Absence of recorded inventory evidence)";
    }

    // Provenance
    const provRainMode = $("prov-rainfall-mode");
    if (provRainMode && activeScenarioMeta) {
      provRainMode.textContent = `In-Memory Scaling (${activeScenarioMeta.scenario_name} · ${activeScenarioMeta.rainfall_factor}x)`;
    }

    // Server-evaluated decision-support warning card
    const warnState = getVillageWarningState(record);
    const warnCard = $("village-warning-card");
    const warnIcon = $("detail-warn-icon");
    const warnStatus = $("detail-warn-status");
    const warnBadge = $("detail-warn-badge");
    const warnReasons = $("detail-warn-reasons");
    const warnAction = $("detail-warn-action");

    if (warnCard) {
      warnCard.className = `village-warning-card warning-${warnState.stage}`;
    }
    if (warnIcon) {
      warnIcon.textContent = warnState.warning
        ? warnState.stage === "green" ? "✓" : warnState.stage.toUpperCase()
        : "—";
      warnIcon.style.color = warnState.warning?.color || "#64748b";
    }
    if (warnStatus) {
      warnStatus.textContent = warnState.statusText;
      warnStatus.style.color = warnState.warning?.color || "var(--text-muted)";
    }
    if (warnBadge) {
      warnBadge.className = `v-warn-chip warning-${warnState.stage}`;
      warnBadge.textContent = warnState.statusBadge;
    }
    if (warnReasons) {
      const reasons = warnState.warning?.reason
        ? [warnState.warning.reason]
        : ["Warning evaluation is not available yet."];
      warnReasons.replaceChildren(...reasons.map(reason => {
        const item = document.createElement("li");
        item.textContent = reason;
        return item;
      }));
    }
    if (warnAction) {
      warnAction.textContent = warnState.action;
    }

    // Alert Notification Workflow Card
    const notifStage = $("notif-stage-label");
    const notifStatusText = $("notif-status-text");
    const notifStepDecision = $("notif-step-decision");
    const notifStepRecipients = $("notif-step-recipients");
    const notifStepDispatch = $("notif-step-dispatch");

    if (notifStage) {
      if (warnState.warning) {
        notifStage.textContent = `${warnState.stage.toUpperCase()} · ${String(warnState.warning.label).toUpperCase()} (NO DISPATCH)`;
        notifStage.style.background = "#f1f5f9";
        notifStage.style.color = "#334155";
        notifStage.style.borderColor = "#cbd5e1";
      } else {
        notifStage.textContent = "Pipeline Ready";
        notifStage.style.background = "#f0fdf4";
        notifStage.style.color = "#166534";
        notifStage.style.borderColor = "#bbf7d0";
      }
    }

    if (notifStepDecision) {
      notifStepDecision.className = `notif-step notif-step-active`;
    }
    if (notifStepRecipients) {
      notifStepRecipients.className = "notif-step";
    }
    if (notifStepDispatch) {
      notifStepDispatch.className = "notif-step";
    }

    if (notifStatusText) {
      notifStatusText.textContent = "Decision-support status calculated locally. No email, SMS, or WhatsApp message is sent; follow official authority instructions.";
      notifStatusText.style.borderLeftColor = warnState.warning?.color || "var(--brand-primary)";
    }

    // Highlight active list item in ranking
    document.querySelectorAll(".village-rank-item").forEach(item => {
      if (item.dataset.lgd === String(record.village_lgd_code)) {
        item.classList.add("active");
        item.scrollIntoView({ block: "nearest", behavior: "smooth" });
      } else {
        item.classList.remove("active");
      }
    });

    // Zoom and highlight polygon on map
    const polygon = villagePolygonsMap.get(String(record.village_lgd_code));
    if (polygon && map) {
      map.fitBounds(polygon.getBounds(), { maxZoom: 13, padding: [50, 50] });
      highlightPolygon(polygon, score);
      polygon.openPopup();

      // Fetch contextual live weather asynchronously for this village
      const center = polygon.getBounds().getCenter();
      fetchLiveWeather(center.lat, center.lng, `${record.village_name_en} (${record.taluk_name_en})`, false);
    }

    loadVillageContext(record);
    renderEmergencyPanel();
  }

  /**
   * Render Filtered & Searched Village Ranking Dropdown List
   */
  function renderRankingList() {
    const listEl = $("index-ranking");
    if (!listEl) return;
    const rankingCount = $("ranking-count");
    if (rankingCount) rankingCount.textContent = String(scenarioRecords.length);

    let filtered = [...scenarioRecords];

    // Filter by tier
    if (currentTierFilter !== "all") {
      filtered = filtered.filter(row => {
        const score = Number(row.scenario_susceptibility_0_100 ?? row.ml_susceptibility_0_100);
        const t = getTierInfo(score).tier.toLowerCase();
        return t === currentTierFilter;
      });
    }

    // Case-insensitive Search by Name, Partial Name, Taluk, LGD code
    if (currentSearchTerm.trim()) {
      const q = currentSearchTerm.toLowerCase().trim();
      filtered = filtered.filter(row => 
        (row.village_name_en && row.village_name_en.toLowerCase().includes(q)) ||
        (row.taluk_name_en && row.taluk_name_en.toLowerCase().includes(q)) ||
        (row.village_lgd_code && String(row.village_lgd_code).includes(q))
      );
    }

    if (!filtered.length) {
      listEl.innerHTML = `
        <div class="empty-results-box">
          <span class="empty-results-title">No matching villages found</span>
          <span>No results in the 40-village universe for this query/filter.</span>
        </div>
      `;
      return;
    }

    listEl.innerHTML = filtered.map((row) => {
      const score = Number(row.scenario_susceptibility_0_100 ?? row.ml_susceptibility_0_100);
      const tier = getTierInfo(score);
      const delta = formatDelta(row.susceptibility_delta);
      const warnState = getVillageWarningState(row);
      const isActive = String(row.village_lgd_code) === String(selectedVillageLgd) ? "active" : "";
      
      // Rank in full scenario dataset
      const rank = scenarioRecords.findIndex(r => String(r.village_lgd_code) === String(row.village_lgd_code)) + 1;

      const statusChip = warnState.warning?.status === "available"
        ? `<span class="tier-chip warning-stage-chip warning-${escapeHtml(warnState.stage)}" title="Decision-support warning stage">${escapeHtml(warnState.stage.toUpperCase())} · ${escapeHtml(String(warnState.warning.label).toUpperCase())}</span>`
        : warnState.warning
          ? `<span class="tier-chip warning-stage-chip warning-unavailable" title="Warning stage unavailable">STAGE UNAVAILABLE</span>`
        : `<span class="tier-chip ${tier.className}">${tier.label}</span>`;

      return `
        <div class="village-rank-item ${isActive} ${warnState.isWarning ? 'has-warning' : ''}" data-lgd="${escapeHtml(row.village_lgd_code)}" role="option" tabindex="0" title="${escapeHtml(row.village_name_en)} (${escapeHtml(row.taluk_name_en)}) - ${warnState.statusText}">
          <span class="item-rank">#${rank}</span>
          <div class="item-main">
            <span class="item-name" title="${escapeHtml(row.village_name_en)}">${escapeHtml(row.village_name_en)}</span>
            <span class="item-taluk">${escapeHtml(row.taluk_name_en)} · Code ${escapeHtml(row.village_lgd_code)}</span>
          </div>
          <span class="item-score">${fmt(score, 1)}</span>
          <span class="item-delta ${delta.className}" title="Change from historical baseline">${delta.shortText}</span>
          ${statusChip}
        </div>
      `;
    }).join("");

    // Attach click listeners
    listEl.querySelectorAll(".village-rank-item").forEach(item => {
      item.onclick = () => {
        const lgd = item.dataset.lgd;
        const match = scenarioRecords.find(r => String(r.village_lgd_code) === lgd);
        if (match) {
          renderVillageDetail(match);
          showToast(`Selected ${match.village_name_en}`, false, null, 1500);
        }
      };
      item.onkeydown = e => {
        if (e.key === "Enter" || e.key === " ") {
          e.preventDefault();
          item.click();
        }
      };
    });
  }

  /**
   * Update Leaflet Map Polygons styling and popups based on scenario results
   */
  function updateMapPolygons() {
    scenarioRecords.forEach(village => {
      const lgdStr = String(village.village_lgd_code);
      const polygon = villagePolygonsMap.get(lgdStr);
      if (polygon) {
        const score = Number(village.scenario_susceptibility_0_100 ?? village.ml_susceptibility_0_100);
        const tier = getTierInfo(score);
        const delta = formatDelta(village.susceptibility_delta);
        const warnState = getVillageWarningState(village);

        polygon._floodguardScore = score;

        const warningColors = {
          green: "#16a34a",
          yellow: "#eab308",
          orange: "#f97316",
          red: "#dc2626"
        };
        const warningColor = warningColors[warnState.stage] || "#64748b";
        const fillColor = warningMapEnabled ? warningColor : tier.color;
        const strokeColor = warningMapEnabled
          ? (warnState.stage === "green" ? "#15803d" : warningColor)
          : tier.stroke;
        const fillOpacity = warningMapEnabled ? 0.32 : tier.fillOpacity;
        const weight = warningMapEnabled ? 2 : 1.5;

        // If not actively highlighted, apply tier/warning stroke
        if (polygon !== activeHighlightedPolygon) {
          polygon.setStyle({
            color: strokeColor,
            weight: weight,
            fillColor: fillColor,
            fillOpacity: fillOpacity
          });
        } else {
          polygon.setStyle({
            fillColor: fillColor
          });
        }

        polygon.unbindTooltip();
        polygon.bindTooltip(`
          <strong>${escapeHtml(village.village_name_en)}</strong> (${fmt(score, 1)} / 100 - ${tier.tier} SUSCEPTIBILITY)
          ${warningMapEnabled ? `<br><span style="color:${warningColor};font-weight:800;">WARNING VIEW · ${escapeHtml(warnState.stage.toUpperCase())} — ${escapeHtml(String(warnState.warning?.label || "Unavailable").toUpperCase())}</span>` : ""}
        `, { sticky: true, className: "polygon-tooltip" });

        polygon.unbindPopup();
        polygon.bindPopup(`
          <div style="font-size:12px;">
            <strong style="color:#0284c7;font-size:13px;">${escapeHtml(village.village_name_en)}</strong><br>
            <strong>Taluk:</strong> ${escapeHtml(village.taluk_name_en)}<br>
            <strong>LGD Code:</strong> ${escapeHtml(village.village_lgd_code)}<br>
            <strong>PU Status:</strong> ${escapeHtml(village.pu_status)}<br>
            <strong>Baseline modeled susceptibility:</strong> ${fmt(village.baseline_susceptibility_0_100 ?? village.ml_susceptibility_0_100, 1)} / 100<br>
            <strong>Scenario-adjusted modeled risk:</strong> <strong>${fmt(score, 1)} / 100</strong> (${tier.tier})<br>
            <strong>Decision-support warning:</strong> <strong style="color:${warningColor}">${escapeHtml(warnState.statusText)}</strong><br>
            <strong>Scenario Delta:</strong> <span class="${delta.className}">${delta.text}</span><br>
            <strong>Mean Slope:</strong> ${fmt(village.slope_mean_deg, 1)}°<br>
            <small style="color:#0284c7;font-weight:600;">Click polygon for detailed view</small>
          </div>
        `);
      }
    });
  }

  /**
   * Apply Rainfall Scenario via API & Synchronize all UI Elements
   */
  async function applyScenario(scenarioKey = "baseline", customMultiplier = null) {
    try {
      showToast("Updating scenario simulation…");

      let url = `/api/rainfall-scenario?scenario=${encodeURIComponent(scenarioKey)}`;
      if (customMultiplier !== null) {
        url = `/api/rainfall-scenario?multiplier=${encodeURIComponent(customMultiplier)}`;
      }

      const data = await api(url);
      activeScenarioMeta = data;

      // Sort descending by scenario score
      scenarioRecords = (data.records || []).sort(
        (a, b) => Number(b.scenario_susceptibility_0_100) - Number(a.scenario_susceptibility_0_100)
      );

      // Plain-English scenario label
      let scenDisplayName = "Normal Rainfall";
      let overviewLabel = "Scenario: Baseline";
      const factor = Number(data.rainfall_factor);
      if (Math.abs(factor - 0.70) < 0.05) {
        scenDisplayName = "Lower Rainfall";
        overviewLabel = "Scenario: Lower";
      } else if (Math.abs(factor - 1.00) < 0.05) {
        scenDisplayName = "Normal Rainfall";
        overviewLabel = "Scenario: Baseline";
      } else if (Math.abs(factor - 1.50) < 0.05) {
        scenDisplayName = "Heavy Rainfall";
        overviewLabel = "Scenario: Heavy";
      } else if (Math.abs(factor - 2.20) < 0.05) {
        scenDisplayName = "Very Heavy Rainfall";
        overviewLabel = "Scenario: Very Heavy";
      } else {
        scenDisplayName = `${fmt(factor, 2)}× Rainfall`;
        overviewLabel = `Scenario: ${fmt(factor, 2)}×`;
      }

      // Update Scenario UI Controls & Header Badges
      const headerScenBadge = $("header-scenario-badge");
      if (headerScenBadge) {
        headerScenBadge.textContent = `${fmt(factor, 2)}×`;
      }

      const cmpScenVal = $("cmp-scen-val");
      if (cmpScenVal) {
        cmpScenVal.textContent = `${fmt(factor, 2)}× ${scenDisplayName}`;
      }

      const badgeEl = $("scenario-status-badge");
      if (badgeEl) {
        badgeEl.textContent = `${scenDisplayName} · ${fmt(factor, 2)}×`;
      }

      const outputEl = $("scenario-value");
      if (outputEl) {
        outputEl.textContent = `Current multiplier: ${fmt(factor, 2)}×`;
      }

      const expFactorLabel = $("scen-exp-factor-label");
      if (expFactorLabel) {
        expFactorLabel.textContent = `${fmt(factor, 2)}×`;
      }

      const sliderEl = $("scenario-slider");
      if (sliderEl && customMultiplier === null) {
        sliderEl.value = factor;
      }

      // Update Preset Buttons active state
      document.querySelectorAll(".preset-btn").forEach(btn => {
        if (customMultiplier === null && btn.dataset.scenario === scenarioKey) {
          btn.classList.add("active");
        } else {
          btn.classList.remove("active");
        }
      });

      // Update Risk Overview Card Title/Subtitle and Counts
      const overviewContext = $("overview-scenario-context");
      if (overviewContext) {
        overviewContext.textContent = overviewLabel;
      }

      const overviewHigh = $("overview-count-high");
      if (overviewHigh) overviewHigh.textContent = data.high_tier_count;
      const overviewMed = $("overview-count-med");
      if (overviewMed) overviewMed.textContent = data.medium_tier_count;
      const overviewLow = $("overview-count-low");
      if (overviewLow) overviewLow.textContent = data.low_tier_count;

      // Update Filter Pill Badges
      const kpiHigh = $("kpi-high-tier");
      if (kpiHigh) kpiHigh.textContent = data.high_tier_count;
      const kpiMed = $("kpi-med-tier");
      if (kpiMed) kpiMed.textContent = data.medium_tier_count;
      const kpiLow = $("kpi-low-tier");
      if (kpiLow) kpiLow.textContent = data.low_tier_count;
      const kpiVillages = $("kpi-villages");
      if (kpiVillages) kpiVillages.textContent = data.record_count;

      // Load rule-engine results for the active rainfall scenario.
      await loadWarningResults();
      if (warningRefreshTimer) clearInterval(warningRefreshTimer);
      warningRefreshTimer = setInterval(() => {
        loadWarningResults().catch(error => {
          console.error("Warning state refresh failed:", error);
          showToast("Warning status could not be refreshed.", true, loadWarningResults);
        });
      }, 60_000);

      // Refresh Map and Ranking Dropdown List
      updateMapPolygons();
      renderRankingList();

      // Refresh Selected Village Detail if panel is open
      if (selectedVillageLgd) {
        const match = scenarioRecords.find(r => String(r.village_lgd_code) === selectedVillageLgd);
        if (match) renderVillageDetail(match);
      }

      // Synchronize Evacuation Intelligence with scenario
      updateEvacuationContext();
      renderEmergencyPanel();

      hideToast();
    } catch (err) {
      console.error("Failed to apply rainfall scenario:", err);
      showToast("Unable to connect to FloodGuard API", true, () => applyScenario(scenarioKey, customMultiplier));
    }
  }

  function setRainfallInputMode(mode) {
    activeRainfallMode = mode;
    document.querySelectorAll(".mode-btn").forEach(btn => {
      const matches = String(btn.dataset.mode) === String(mode);
      btn.classList.toggle("active", matches);
      btn.setAttribute("aria-pressed", matches ? "true" : "false");
    });
    const headerBadge = $("scenario-status-badge");
    if (headerBadge && mode === "imd") {
      const text = headerBadge.textContent || "IMD Auto Forecast";
      headerBadge.textContent = text.includes("IMD") ? text : "IMD Auto Forecast";
    }
  }

  async function fetchIMDAutoForecast() {
    try {
      showToast("Refreshing IMD auto forecast…");
      const data = await api("/api/imd/forecast");
      const recordCount = Number(data.record_count || (Array.isArray(data.records) ? data.records.length : 0) || 0);
      const statusLabel = data.status === "available" ? "IMD Auto Forecast" : "IMD Cache / Unavailable";
      const badgeEl = $("scenario-status-badge");
      if (badgeEl) {
        badgeEl.textContent = `${statusLabel} · ${recordCount} points`;
      }
      const outputEl = $("scenario-value");
      if (outputEl) {
        outputEl.textContent = data.status === "available"
          ? `IMD forecast: ${recordCount} Nilgiris points available`
          : "IMD forecast unavailable; Scenario Mode remains active";
      }
      if (data.warning) {
        showToast(data.warning, data.status !== "available", null, 2200);
      } else {
        hideToast();
      }
    } catch (err) {
      console.error("Failed to fetch IMD forecast:", err);
      showToast("IMD forecast unavailable; Scenario Mode remains active.", true, fetchIMDAutoForecast);
    }
  }

  /**
   * Parse and Render Survey of India KMZ Administrative Boundaries
   */
  async function loadBoundaries() {
    try {
      const res = await fetch("/data/raw/admin/vb_soi_tn.kmz");
      if (!res.ok) throw new Error(`KMZ fetch HTTP ${res.status}`);

      const zip = await JSZip.loadAsync(await res.arrayBuffer());
      const kmlFile = Object.values(zip.files).find(f => /\.kml$/i.test(f.name));
      if (!kmlFile) throw new Error("No KML found in KMZ archive");

      const xmlText = await kmlFile.async("text");
      const xmlDoc = new DOMParser().parseFromString(xmlText, "application/xml");
      const placemarks = [...xmlDoc.querySelectorAll("Placemark")].filter(p => /nilgiri/i.test(p.textContent));

      placemarks.forEach(placemark => {
        const text = placemark.textContent || "";
        let matchedVillage = null;
        for (const v of scenarioRecords) {
          if (v.village_name_en && text.toLowerCase().includes(v.village_name_en.toLowerCase())) {
            matchedVillage = v;
            break;
          }
        }

        placemark.querySelectorAll("Polygon outerBoundaryIs LinearRing coordinates").forEach(coordNode => {
          const rawCoords = coordNode.textContent.trim().split(/\s+/);
          const latLngs = rawCoords
            .map(c => c.split(",").map(Number))
            .filter(c => Number.isFinite(c[0]) && Number.isFinite(c[1]))
            .map(c => [c[1], c[0]]);

          if (latLngs.length > 2) {
            let fillColor = "#0284c7";
            let fillOpacity = 0.18;
            let strokeColor = "#0ea5e9";

            if (matchedVillage) {
              const score = Number(matchedVillage.scenario_susceptibility_0_100 ?? matchedVillage.ml_susceptibility_0_100);
              const tier = getTierInfo(score);
              fillColor = tier.color;
              strokeColor = tier.stroke;
              fillOpacity = tier.fillOpacity;
            }

            const polygon = L.polygon(latLngs, {
              color: strokeColor,
              weight: 1.5,
              fillColor: fillColor,
              fillOpacity: fillOpacity
            });

            if (matchedVillage) {
              polygon._floodguardScore = Number(matchedVillage.scenario_susceptibility_0_100 ?? matchedVillage.ml_susceptibility_0_100);
              villagePolygonsMap.set(String(matchedVillage.village_lgd_code), polygon);
              const score = fmt(matchedVillage.scenario_susceptibility_0_100 ?? matchedVillage.ml_susceptibility_0_100, 1);
              
              polygon.bindTooltip(`
                <strong>${escapeHtml(matchedVillage.village_name_en)}</strong> · Scenario risk ${score} / 100
              `, { sticky: true, className: "polygon-tooltip" });

              polygon.bindPopup(`
                <div style="font-size:12px;">
                  <strong style="color:#0284c7;font-size:13px;">${escapeHtml(matchedVillage.village_name_en)}</strong><br>
                  <strong>LGD Code:</strong> ${escapeHtml(matchedVillage.village_lgd_code)}<br>
                  <strong>Baseline modeled susceptibility:</strong> ${fmt(matchedVillage.baseline_susceptibility_0_100 ?? matchedVillage.ml_susceptibility_0_100, 1)} / 100<br>
                  <strong>Scenario-adjusted modeled risk:</strong> ${score} / 100<br>
                  <strong>Mean Slope:</strong> ${fmt(matchedVillage.slope_mean_deg, 1)}°<br>
                  <small style="color:#0284c7;font-weight:600;">Click for full village details</small>
                </div>
              `);
              polygon.on("click", () => renderVillageDetail(matchedVillage));

              // Add center circle marker for centroid layer
              const center = polygon.getBounds().getCenter();
              const marker = L.circleMarker(center, {
                radius: 4,
                color: "#ffffff",
                weight: 1.5,
                fillColor: "#0284c7",
                fillOpacity: 0.9
              });
              marker.bindTooltip(`<strong>${escapeHtml(matchedVillage.village_name_en)}</strong>`);
              marker.on("click", () => renderVillageDetail(matchedVillage));
              marker.addTo(centroidLayer);
            }

            polygon.addTo(susceptibilityLayer);
          }
        });
      });
    } catch (err) {
      console.warn("KMZ boundary loading warning:", err);
    }
  }

  /**
   * Setup UI Controls & Interactive Listeners
   */
  function setupUIListeners() {
    const rankingToggle = $("btn-toggle-ranking");
    if (rankingToggle) {
      rankingToggle.onclick = () => {
        setRankingPanelExpanded(rankingToggle.getAttribute("aria-expanded") !== "true");
      };
    }

    const legendCard = $("map-legend-card");
    const legendToggle = $("btn-toggle-legend");
    if (legendCard && legendToggle) {
      legendToggle.onclick = () => {
        const expanded = legendCard.classList.toggle("is-expanded");
        legendCard.classList.toggle("is-collapsed", !expanded);
        legendToggle.setAttribute("aria-expanded", String(expanded));
      };
    }

    // Search input
    const searchInput = $("village-search");
    const clearBtn = $("btn-clear-search");
    if (searchInput) {
      searchInput.oninput = e => {
        currentSearchTerm = e.target.value;
        if (currentSearchTerm) setRankingPanelExpanded(true);
        if (clearBtn) clearBtn.style.display = currentSearchTerm ? "flex" : "none";
        renderRankingList();
      };
      searchInput.onkeydown = e => {
        if (e.key === "Escape") {
          searchInput.value = "";
          currentSearchTerm = "";
          if (clearBtn) clearBtn.style.display = "none";
          renderRankingList();
        }
      };
    }
    if (clearBtn) {
      clearBtn.onclick = () => {
        if (searchInput) {
          searchInput.value = "";
          currentSearchTerm = "";
          clearBtn.style.display = "none";
          renderRankingList();
          searchInput.focus();
        }
      };
    }

    // Tier Filter Pills
    document.querySelectorAll(".filter-pill").forEach(pill => {
      pill.onclick = () => {
        document.querySelectorAll(".filter-pill").forEach(p => {
          p.classList.remove("active");
          p.setAttribute("aria-selected", "false");
        });
        pill.classList.add("active");
        pill.setAttribute("aria-selected", "true");
        currentTierFilter = pill.dataset.tier;
        setRankingPanelExpanded(true);
        renderRankingList();
      };
    });

    // Close Village Detail Panel Button
    const btnCloseDetail = $("btn-close-detail");
    const detailPanel = $("village-detail-panel");
    if (btnCloseDetail && detailPanel) {
      btnCloseDetail.onclick = () => {
        detailPanel.style.display = "none";
        selectedVillageLgd = null;
        villageContextRequestId += 1;
        if (activeHighlightedPolygon) {
          const oldScore = activeHighlightedPolygon._floodguardScore || 0;
          const oldTier = getTierInfo(oldScore);
          activeHighlightedPolygon.setStyle({
            color: oldTier.stroke,
            weight: 1.5,
            fillOpacity: oldTier.fillOpacity
          });
          if (activeHighlightedPolygon._path) {
            L.DomUtil.removeClass(activeHighlightedPolygon._path, "polygon-selected");
          }
          activeHighlightedPolygon = null;
        }
        document.querySelectorAll(".village-rank-item").forEach(item => item.classList.remove("active"));
      };
    }

    // Village Drawer "Plan Evacuation from this Village" CTA Button
    const btnVillageEvacuate = $("btn-village-evacuate");
    if (btnVillageEvacuate) {
      btnVillageEvacuate.onclick = () => {
        if (!selectedVillageLgd) return;
        const match = scenarioRecords.find(r => String(r.village_lgd_code) === selectedVillageLgd);
        if (!match) return;

        const polygon = villagePolygonsMap.get(String(match.village_lgd_code));
        if (polygon) {
          const c = polygon.getBounds().getCenter();
          setUserOrigin(c.lat, c.lng, `${match.village_name_en} (${match.taluk_name_en})`, false, match);
        }
        if (!polygon) {
          showToast("Using the selected village LGD code as the route origin.", false, null, 2000);
        }

        // Open Evacuation Panel (enforcing single drawer policy)
        closeAllPanels("shelter-panel");
        drawEvacuationRoute(selectedShelter);
        showToast(`Evacuation route planned from ${match.village_name_en}`, false, null, 2000);
      };
    }

    // Mobile Navbar Actions Drawer Toggle
    const btnMobileToggle = $("btn-mobile-nav-toggle");
    const navActions = $("navbar-actions-group");
    if (btnMobileToggle && navActions) {
      btnMobileToggle.onclick = () => {
        navActions.classList.toggle("mobile-open");
      };
    }

    // Shelter Panel Toggle Action
    const btnToggleShelters = $("btn-toggle-shelters");
    const btnCloseShelters = $("btn-close-shelter-panel");
    const shelterPanel = $("shelter-panel");

    const toggleSheltersHandler = () => {
      if (!shelterPanel) return;
      const isShown = shelterPanel.style.display === "flex";
      if (isShown) {
        closeAllPanels(null);
      } else {
        closeAllPanels("shelter-panel");
        refreshSheltersRanking();
      }
    };

    if (btnToggleShelters) btnToggleShelters.onclick = toggleSheltersHandler;
    if (btnCloseShelters) btnCloseShelters.onclick = () => closeAllPanels(null);

    // Live Weather Panel Toggles & Actions
    const btnToggleWeather = $("btn-toggle-weather");
    const btnCloseWeather = $("btn-close-weather");
    const btnRefreshWeather = $("btn-refresh-weather");
    const weatherCard = $("live-weather-card");

    const toggleWeatherHandler = () => {
      if (!weatherCard) return;
      const isShown = weatherCard.style.display === "flex";
      if (isShown) {
        closeAllPanels(null);
      } else {
        closeAllPanels("live-weather-card");
        if (!currentWeatherRecord || (Date.now() - (lastWeatherFetchTimestamp || 0) > 300000)) {
          fetchLiveWeather(activeWeatherContext.lat, activeWeatherContext.lng, activeWeatherContext.locationName, false);
        }
      }
    };

    if (btnToggleWeather) btnToggleWeather.onclick = toggleWeatherHandler;
    if (btnCloseWeather) btnCloseWeather.onclick = () => closeAllPanels(null);

    if (btnRefreshWeather) {
      btnRefreshWeather.onclick = () => {
        fetchLiveWeather(activeWeatherContext.lat, activeWeatherContext.lng, activeWeatherContext.locationName, true);
      };
    }

    // Rainfall Scenario Simulator Toggle & Actions (On-Demand Drawer)
    const btnToggleScenario = $("btn-toggle-scenario");
    const btnCloseScenario = $("btn-close-scenario");
    const scenarioPanel = $("scenario-panel");

    const toggleScenarioHandler = () => {
      if (!scenarioPanel) return;
      const isShown = scenarioPanel.style.display === "flex";
      if (isShown) {
        closeAllPanels(null);
      } else {
        closeAllPanels("scenario-panel");
      }
    };

    if (btnToggleScenario) btnToggleScenario.onclick = toggleScenarioHandler;
    if (btnCloseScenario) btnCloseScenario.onclick = () => closeAllPanels(null);

    // Emergency Alert Center Toggle & Actions
    const btnToggleEmergency = $("btn-toggle-emergency");
    const btnCloseEmergency = $("btn-close-emergency");
    const emergencyPanel = $("emergency-panel");

    const toggleEmergencyHandler = () => {
      if (!emergencyPanel) return;
      const isShown = emergencyPanel.style.display === "flex";
      if (isShown) {
        closeAllPanels(null);
      } else {
        closeAllPanels("emergency-panel");
        renderEmergencyPanel();
      }
    };

    if (btnToggleEmergency) btnToggleEmergency.onclick = toggleEmergencyHandler;
    if (btnCloseEmergency) btnCloseEmergency.onclick = () => closeAllPanels(null);

    // Emergency Quick Actions
    const emBtnMyLoc = $("em-btn-my-loc");
    if (emBtnMyLoc) {
      emBtnMyLoc.onclick = () => {
        const btnUseMyLoc = $("btn-use-my-location");
        if (btnUseMyLoc) btnUseMyLoc.click();
      };
    }

    const emBtnPickMap = $("em-btn-pick-map");
    if (emBtnPickMap) {
      emBtnPickMap.onclick = () => {
        enterMapPickingMode();
      };
    }

    const emBtnNearestShelter = $("em-btn-nearest-shelter");
    if (emBtnNearestShelter) {
      emBtnNearestShelter.onclick = () => {
        const shelter = selectedShelter;
        if (shelter && map) {
          map.flyTo([shelter.latitude, shelter.longitude], 13, { duration: 0.8 });
          selectShelter(shelter);
          showToast(`Focused on verified facility: ${shelter.name}`, false, null, 2000);
        } else {
          showToast("No verified shelter records are available.", true);
        }
      };
    }

    const emBtnShare = $("em-btn-share");
    if (emBtnShare) {
      emBtnShare.onclick = () => {
        shareEmergencyInformation();
      };
    }

    const emBtnViewShelter = $("em-btn-view-shelter");
    if (emBtnViewShelter) {
      emBtnViewShelter.onclick = () => {
        const shelter = selectedShelter;
        if (shelter && map) {
          map.flyTo([shelter.latitude, shelter.longitude], 13, { duration: 0.8 });
          selectShelter(shelter);
        }
      };
    }

    const emBtnPlanRoute = $("em-btn-plan-route");
    if (emBtnPlanRoute) {
      emBtnPlanRoute.onclick = () => {
        const shelter = selectedShelter;
        drawEvacuationRoute(shelter);
      };
    }

    // Global Escape Key listener (Rule 18 / Dismiss all open popovers & drawers)
    document.addEventListener("keydown", e => {
      if (e.key === "Escape") {
        if (isPickingOriginOnMap) {
          exitMapPickingMode();
        }
        closeAllPanels(null);
      }
    });

    // Location Selection: Method A (Browser Geolocation)
    const btnUseMyLocation = $("btn-use-my-location");
    if (btnUseMyLocation) {
      btnUseMyLocation.onclick = () => {
        exitMapPickingMode();
        if (!navigator.geolocation) {
          showToast("Geolocation is not supported by your browser. Please select a point on the map.", true);
          return;
        }
        showToast("Locating your current position…");
        navigator.geolocation.getCurrentPosition(
          pos => {
            const lat = pos.coords.latitude;
            const lng = pos.coords.longitude;
            setUserOrigin(lat, lng, "My Current Location", true);
            map.flyTo([lat, lng], 12, { duration: 0.8 });
            showToast("Location detected. Select a verified facility to request a road route.", false, null, 2500);
          },
          err => {
            console.warn("Geolocation permission error:", err);
            showToast("Location access denied or unavailable. Click 'Select on Map' to pick an origin point.", true, null, 3000);
          },
          { enableHighAccuracy: true, timeout: 8000 }
        );
      };
    }

    // Location Selection: Method B (Map Click Picker)
    const btnSelectMapLocation = $("btn-select-map-location");
    if (btnSelectMapLocation) {
      btnSelectMapLocation.onclick = () => {
        enterMapPickingMode();
      };
    }

    const btnCancelPicker = $("btn-cancel-picker");
    if (btnCancelPicker) {
      btnCancelPicker.onclick = () => {
        exitMapPickingMode();
      };
    }

    // Plan Route Button in Shelter Hero Card
    const btnDrawRoute = $("btn-draw-route");
    if (btnDrawRoute) {
      btnDrawRoute.onclick = () => {
        drawEvacuationRoute(selectedShelter);
      };
    }

    // Clear Route Button
    const btnClearRoute = $("btn-clear-route");
    if (btnClearRoute) {
      btnClearRoute.onclick = () => {
        clearRoute();
      };
    }

    // Evidence Modal Toggle
    const btnOpenEvidence = $("btn-open-evidence");
    const btnCloseEvidence = $("btn-close-evidence");
    const modalBackdrop = $("modal-backdrop");
    const evidenceModal = $("evidence-modal");

    const openModal = (defaultTabId = null) => {
      if (evidenceModal) {
        evidenceModal.style.display = "flex";
        if (defaultTabId) {
          const tabBtn = document.querySelector(`.modal-tabs .tab-btn[data-target="${defaultTabId}"]`);
          if (tabBtn) tabBtn.click();
        }
      }
    };
    const closeModal = () => { if (evidenceModal) evidenceModal.style.display = "none"; };
    const modelInfoButton = $("btn-model-info");
    const dataSourcesTable = $("data-sources-table");
    const warningHistoryTable = $("warning-history-table");
    let dataSourcesLoaded = false;

    const loadDataSources = async () => {
      if (!dataSourcesTable || dataSourcesLoaded) return;
      dataSourcesTable.replaceChildren();
      const loadingRow = document.createElement("tr");
      const loadingCell = document.createElement("td");
      loadingCell.colSpan = 5;
      loadingCell.className = "text-center";
      loadingCell.textContent = "Loading source registry…";
      loadingRow.append(loadingCell);
      dataSourcesTable.append(loadingRow);

      try {
        const sources = await api("/api/data-sources");
        dataSourcesTable.replaceChildren();
        sources.forEach(source => {
          const row = document.createElement("tr");
          const provider = [source.provider, source.dataset].filter(Boolean).join(" · ") || "Not recorded";
          const values = [
            source.name || source.id || "Unnamed source",
            source.data_type || source.type || "—",
            source.status || "unavailable",
            provider,
            source.description || "No further source details recorded."
          ];
          values.forEach((value, index) => {
            const cell = document.createElement("td");
            cell.textContent = value;
            if (index === 2) {
              cell.className = `source-status source-status-${String(value).toLowerCase()}`;
            }
            row.append(cell);
          });
          dataSourcesTable.append(row);
        });
        dataSourcesLoaded = true;
      } catch (err) {
        dataSourcesTable.replaceChildren();
        const row = document.createElement("tr");
        const cell = document.createElement("td");
        cell.colSpan = 5;
        cell.className = "text-center";
        cell.textContent = "Source registry could not be loaded.";
        row.append(cell);
        dataSourcesTable.append(row);
        showToast("Unable to load data-source registry", true, loadDataSources);
      }
    };

    const loadWarningHistory = async () => {
      if (!warningHistoryTable) return;
      warningHistoryTable.replaceChildren();
      const loadingRow = document.createElement("tr");
      const loadingCell = document.createElement("td");
      loadingCell.colSpan = 6;
      loadingCell.className = "text-center";
      loadingCell.textContent = "Loading recent warning history…";
      loadingRow.append(loadingCell);
      warningHistoryTable.append(loadingRow);

      try {
        const result = await api("/api/warnings/history?limit=10");
        warningHistoryTable.replaceChildren();
        if (!result.records?.length) {
          const row = document.createElement("tr");
          const cell = document.createElement("td");
          cell.colSpan = 6;
          cell.className = "text-center";
          cell.textContent = "No warning-stage changes have been recorded yet.";
          row.append(cell);
          warningHistoryTable.append(row);
          return;
        }
        result.records.forEach(record => {
          const row = document.createElement("tr");
          const village = scenarioRecords.find(
            item => String(item.village_lgd_code) === String(record.village_lgd_code)
          );
          const values = [
            `${village?.village_name_en || "Village"} · ${record.village_lgd_code}${record.is_test ? " · TEST" : ""}`,
            `${String(record.stage).toUpperCase()}${record.is_test ? " · TEST" : ""}`,
            record.rainfall_mode === "scenario"
              ? `Scenario${record.rainfall_scenario_multiplier == null ? "" : ` · ${Number(record.rainfall_scenario_multiplier).toFixed(2)}×`}`
              : "Current conditions",
            record.rule_id || "—",
            record.generated_at || "—",
            record.reason_summary || "—"
          ];
          values.forEach(value => {
            const cell = document.createElement("td");
            cell.textContent = value;
            row.append(cell);
          });
          warningHistoryTable.append(row);
        });
      } catch (err) {
        warningHistoryTable.replaceChildren();
        const row = document.createElement("tr");
        const cell = document.createElement("td");
        cell.colSpan = 6;
        cell.className = "text-center";
        cell.textContent = "Warning history could not be loaded.";
        row.append(cell);
        warningHistoryTable.append(row);
        showToast("Unable to load warning evaluation history", true, loadWarningHistory);
      }
    };

    if (btnOpenEvidence) btnOpenEvidence.onclick = () => {
      openModal();
      loadWarningHistory();
    };
    if (modelInfoButton) modelInfoButton.onclick = () => {
      openModal("tab-methodology");
      loadWarningHistory();
    };
    if (btnCloseEvidence) btnCloseEvidence.onclick = closeModal;
    if (modalBackdrop) modalBackdrop.onclick = closeModal;

    // View Village Evidence Button in Detail Panel
    const btnViewVillageEvidence = $("btn-view-village-evidence");
    if (btnViewVillageEvidence) {
      btnViewVillageEvidence.onclick = () => openModal("tab-events");
    }

    // Evidence Tabs
    const tabs = document.querySelectorAll(".modal-tabs .tab-btn");
    tabs.forEach(btn => {
      btn.onclick = () => {
        tabs.forEach(t => {
          t.classList.remove("active");
          t.setAttribute("aria-selected", "false");
        });
        document.querySelectorAll(".modal-body-scroll .tab-pane").forEach(p => p.classList.remove("active"));

        btn.classList.add("active");
        btn.setAttribute("aria-selected", "true");
        const targetId = btn.dataset.target;
        const targetPane = $(targetId);
        if (targetPane) targetPane.classList.add("active");
        if (targetId === "tab-data-sources") loadDataSources();
        if (targetId === "tab-methodology") loadWarningHistory();
      };
    });

    // Scenario Presets
    document.querySelectorAll(".preset-btn").forEach(btn => {
      btn.onclick = () => {
        if (btn.dataset.scenario) {
          setRainfallInputMode("scenario");
          const scenario = btn.dataset.scenario;
          applyScenario(scenario);
        }
        if (btn.dataset.mode) {
          const mode = btn.dataset.mode;
          setRainfallInputMode(mode);
          if (mode === "imd") {
            fetchIMDAutoForecast();
          } else {
            applyScenario("baseline");
          }
        }
      };
    });

    // Scenario Multiplier Slider with Debounce
    const slider = $("scenario-slider");
    const output = $("scenario-value");
    if (slider) {
      slider.oninput = () => {
        const val = Number(slider.value).toFixed(2);
        if (output) output.textContent = `Current multiplier: ${val}×`;
        const expFactor = $("scen-exp-factor-label");
        if (expFactor) expFactor.textContent = `${val}×`;
        
        clearTimeout(sliderDebounceTimer);
        sliderDebounceTimer = setTimeout(() => {
          applyScenario("custom", Number(slider.value));
        }, 150);
      };
    }

    // Reset Scenario Button
    const btnResetScenario = $("btn-reset-scenario");
    if (btnResetScenario) {
      btnResetScenario.onclick = () => {
        applyScenario("baseline");
        showToast("Precipitation scenario reset to 1.00x baseline", false, null, 1500);
      };
    }
  }

  /**
   * Main Initialization Routine
   */
  async function start() {
    try {
      initMap();
      setupUIListeners();

      // Initialize Shelters
      setUserOrigin(userOrigin.lat, userOrigin.lng, userOrigin.label, false);
      initShelterMarkers();

      // Parallel Fetching of Baseline Evidence and Scenario
      await Promise.all([
        loadStatus(),
        loadVillages(),
        loadEvents(),
        loadTerrain(),
        loadShelters(),
        applyScenario("baseline")
      ]);

      // Rainfall Series & KMZ Boundaries
      await loadRain();
      const rainYear = $("rain-year");
      if (rainYear) rainYear.onchange = () => loadRain();
      const rainPlace = $("rain-place");
      if (rainPlace) rainPlace.onchange = () => loadRain();

      await loadBoundaries();

      // Initialize Live Weather & Auto-Refresh (Phase 6)
      fetchLiveWeather(11.4102, 76.6950, "Nilgiris District (Ooty)", false);
      startWeatherAutoRefresh();
      if (sensorRefreshTimer) clearInterval(sensorRefreshTimer);
      sensorRefreshTimer = setInterval(() => {
        if (document.visibilityState !== "visible") return;
        refreshSensorLayer().catch(() => {
          console.error("ESP32 sensor layer refresh failed.");
        });
        const detailPanel = $("village-detail-panel");
        if (
          selectedVillageRecord &&
          detailPanel &&
          detailPanel.style.display !== "none"
        ) {
          loadVillageContext(selectedVillageRecord);
        }
      }, 60_000);

      // Initialize Emergency Alert Center (Phase 7)
      renderEmergencyPanel();
    } catch (err) {
      console.error("Dashboard initialization error:", err);
      showToast("Unable to connect to FloodGuard API", true, start);
    }
  }

  // Launch when DOM is ready
  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", start);
  } else {
    start();
  }
})();
