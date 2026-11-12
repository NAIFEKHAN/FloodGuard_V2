const test = require("node:test");
const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");

const read = file => fs.readFileSync(path.join(__dirname, "..", file), "utf8");
const html = read("frontend/index.html");
const js = read("frontend/js/dashboard.js");
const css = read("frontend/css/dashboard.css");

test("shelter card exposes a Locate on Map action", () => {
  assert.match(html, /id="btn-locate-shelter"[^>]*class="btn-locate-shelter"/);
  assert.match(html, /<span>Locate on Map<\/span>/);
  assert.match(js, /btnLocateShelter\.onclick = locateSelectedShelterOnMap/);
});

test("shelter location closes drawers, reveals and focuses its marker popup", () => {
  const locateAction = js.match(/function locateSelectedShelterOnMap\(\) \{([\s\S]*?)\n  \}/)?.[1];
  assert.ok(locateAction, "locate action should be implemented");
  assert.match(locateAction, /closeAllPanels\(null\)/);
  assert.match(locateAction, /map\.addLayer\(shelterLayer\)/);
  assert.match(locateAction, /map\.flyTo\(\[shelter\.latitude, shelter\.longitude\]/);
  assert.match(locateAction, /marker\.openPopup\(\)/);
  assert.match(js, /createPane\("shelterMarkersPane"\)/);
  assert.match(js, /shelterMarkersPane\.style\.zIndex = "650"/);
});

test("overview counts and sensor details keep aligned responsive columns", () => {
  assert.match(css, /\.overview-counts-row\s*\{[^}]*grid-template-columns:\s*repeat\(3, minmax\(0, 1fr\)\)/s);
  assert.match(css, /\.bench-sensor-details > div\s*\{[^}]*display:\s*grid/s);
  assert.match(css, /@media \(max-width: 768px\)\s*\{[^}]*\.gmap-overview-card/s);
});

test("origin picker gives map clicks priority and restores layer interaction on exit", () => {
  assert.match(css, /#map\.map-picking-active \.leaflet-interactive\s*\{\s*pointer-events:\s*none\s*!important;/);
  assert.match(js, /function enterMapPickingMode\(\)\s*\{\s*if \(isPickingOriginOnMap\) return;\s*closeAllPanels\(null\);\s*if \(map\) map\.closePopup\(\);/);
  assert.match(js, /function exitMapPickingMode\(\)[\s\S]*?classList\.remove\("map-picking-active"\)/);
  assert.match(js, /setUserOrigin\(e\.latlng\.lat, e\.latlng\.lng/);
});

test("road route uses validated OSRM geometry and reports unavailable instead of drawing a straight line", () => {
  assert.match(js, /driving\/\$\{originLng\},\$\{originLat\};\$\{destLng\},\$\{destLat\}/);
  assert.match(js, /rawCoordinates\.map\(\(\[lng, lat\]\) => \[Number\(lat\), Number\(lng\)\]\)/);
  assert.match(js, /routeCoordinates\.at\(-1\)/);
  assert.match(js, /map\.fitBounds\(bounds, \{ paddingTopLeft, paddingBottomRight, maxZoom: 14 \}\)/);
  assert.match(js, /No valid road route is available for the selected origin and shelter/);
  assert.doesNotMatch(js, /Direct line representation \(road network fallback\)/);
});
