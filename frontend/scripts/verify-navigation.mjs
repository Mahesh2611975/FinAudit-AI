/**
 * Lightweight static contract checks for the two independent intelligence
 * pages. The project intentionally has no browser test runner, so these checks
 * verify that the routes, components, filters and map-selection wiring that
 * the production build ships are all present and separated.
 */
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { fileURLToPath } from 'node:url';
import path from 'node:path';

const here = path.dirname(fileURLToPath(import.meta.url));
const src = path.resolve(here, '..', 'src');
const read = (name) => readFileSync(path.join(src, name), 'utf8');

const main = read('main.jsx');
const risk = read('RiskIntelligence.jsx');
const geo = read('GeographicRiskIntelligence.jsx');
const css = read('risk.css');

const checks = [];
const check = (label, condition) => {
  assert.ok(condition, label);
  checks.push(label);
};

// --- Sidebar and routing -------------------------------------------------
check(
  'Risk Intelligence sidebar item maps to its own view',
  /navId:\s*'risk-intelligence',\s*view:\s*'risk-intelligence'/.test(main)
);
check(
  'Geographic Intelligence sidebar item maps to its own view',
  /navId:\s*'geographic-intelligence',\s*view:\s*'geographic-intelligence'/.test(main)
);
check(
  'Both views are routable',
  /ROUTABLE_VIEW_IDS\s*=\s*new Set\(ALL_NAV_ITEMS\.map/.test(main)
);
check(
  'Risk page renders only RiskIntelligence',
  /activeView === 'risk-intelligence'\s*&&\s*\(\s*<RiskIntelligence/.test(main)
);
check(
  'Geographic page renders only GeographicRiskIntelligence',
  /activeView === 'geographic-intelligence'\s*&&\s*\(\s*<GeographicRiskIntelligence/.test(main)
);
check(
  'Hash changes restore the active page',
  /addEventListener\('hashchange',\s*syncFromUrl\)/.test(main)
);
check(
  'Browser back/forward restores the active page',
  /addEventListener\('popstate',\s*syncFromUrl\)/.test(main)
);
check(
  'Status indicator uses the lightweight health endpoint',
  /fetch\(\s*`\$\{API\}\/api\/health/.test(main)
);

// --- Page separation -----------------------------------------------------
check('Risk page does not import Leaflet', !/from 'leaflet'/.test(risk));
check('Risk page does not call geography endpoints', !/geography\/summary/.test(risk));
check('Risk page does not render the geo map', !/geo-map/.test(risk));
check('Risk page does not render geographic panels', !/CountryRiskPanel|GeoTrendPanel|UnusualLocationsPanel/.test(risk));
check('Risk page keeps alert search/filter/review', /Risk alerts/.test(risk) && /risk-filter-bar/.test(risk));
check('Risk page keeps anomaly trends', /Alert trend/.test(risk));

// --- Geographic page instrumentation ------------------------------------
check('Geographic page owns the Leaflet map', /from 'leaflet'/.test(geo) && /L\.map\(/.test(geo));
check('Geographic page reads persisted backend records', /\/api\/risk\/geography\/summary/.test(geo));
check('Marker selection is wired to detail panel', /point\.on\('click',\s*\(\)\s*=>\s*setSelectedMarker\(marker\)\)/.test(geo));
check('Selected transaction detail panel is rendered', /<MarkerDetail\s+marker=\{selectedMarker\}/.test(geo));

for (const key of ['start_date', 'end_date', 'country', 'city', 'risk_level', 'channel', 'min_amount']) {
  check(`Geographic filter '${key}' is present`, new RegExp(`\\b${key}:`).test(geo));
}
check('Geographic refresh action is present', /geo-refresh-btn/.test(geo));
check('Geographic clear-filters action is present', /geo-clear-filters/.test(geo));

// --- Responsive layout ---------------------------------------------------
check('Two-column map/detail layout is styled', /\.geo-map-detail-grid\s*\{[^}]*grid-template-columns:\s*minmax\(0,\s*\d+fr\)\s+minmax\(0,\s*\d+fr\)/s.test(css));

console.log(`Navigation contract verified: ${checks.length} checks passed.`);
for (const label of checks) console.log(`  ✓ ${label}`);
