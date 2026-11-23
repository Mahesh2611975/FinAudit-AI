/**
 * Lightweight static contract checks for the public landing page, auth routing
 * and the independent intelligence pages. The project intentionally has no
 * browser test runner, so these checks verify that the routes, components,
 * filters and map-selection wiring shipped by the production build are present
 * and separated.
 */
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { fileURLToPath } from 'node:url';
import path from 'node:path';

const here = path.dirname(fileURLToPath(import.meta.url));
const src = path.resolve(here, '..', 'src');
const read = (name) => readFileSync(path.join(src, name), 'utf8');

const main = read('main.jsx');
const landing = read('LandingPage.jsx');
const dashboard = read('Dashboard.jsx');
const risk = read('RiskIntelligence.jsx');
const geo = read('GeographicRiskIntelligence.jsx');
const css = read('risk.css');
const landingCss = read('landing.css');
const apiClient = read('api.js');
const frontendEnv = readFileSync(path.join(here, '..', '.env.example'), 'utf8');
const backendAuth = readFileSync(path.resolve(here, '..', '..', 'backend', 'app', 'auth.py'), 'utf8');
const backendDemoTests = readFileSync(path.resolve(here, '..', '..', 'backend', 'tests', 'test_auth_rbac.py'), 'utf8');

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
  /apiFetch\(\s*`\/api\/health/.test(main)
);

// --- Authentication and role dashboards -----------------------------------
check('Login sends only username and password', /body: JSON\.stringify\(\{ username: username\.trim\(\), password \}\)/.test(main));
check('Registration sends account_type', /account_type:\s*accountType/.test(main));
check('Registration offers only User and Lender', /<option value="User">User<\/option>/.test(main) && /<option value="Lender">Lender<\/option>/.test(main) && !/<option value="Admin">/.test(main));
check('Backend dashboard response drives routing', /user\?\.dashboard && DASHBOARD_VIEWS\.has\(user\.dashboard\)/.test(main));
check('All three role dashboards are routable', /'admin-dashboard',\s*'lender-dashboard',\s*'user-dashboard'/.test(main));
check('Unauthorized views redirect to the role dashboard', /!isViewAllowed\(currentUser, activeView\)/.test(main));
check('Admin approval UI is present', /\/api\/auth\/users\/\$\{user\.id\}\/approval/.test(main));
check('Logout calls the backend revocation endpoint', /apiFetch\('\/api\/auth\/logout',\s*\{\s*method:\s*'POST'/.test(main));

// --- Public landing page and auth routing ----------------------------------
check('Landing page component and stylesheet are imported', /import LandingPage from '\.\/LandingPage'/.test(main) && /import '\.\/landing\.css'/.test(main));
check(
  'Unauthenticated visitors get the landing page before the auth screen',
  /if \(!currentUser\) \{[\s\S]*?if \(!demoMode && \(publicPage === 'login' \|\| publicPage === 'register'\)\)[\s\S]*?<AuthPage[\s\S]*?return \([\s\S]*?<LandingPage/.test(main)
);
check('Public hash routes support landing, login and register', /publicHashForPage[\s\S]*?'#\/login'[\s\S]*?'#\/register'[\s\S]*?return '#\/'/.test(main));
check('Open Dashboard is wired to the demo-aware handler', /onOpenDashboard=\{handleOpenDashboard\}/.test(main) && /const handleOpenDashboard = async \(\) =>/.test(main));
check('Successful login still routes to the backend dashboard', /setActiveView\(dashboardViewForUser\(user\), \{ replaceHash: true \}\)/.test(main));
check('Logout returns to the public landing page', /const handleLogout[\s\S]*?navigatePublicPage\('landing', \{ replaceHash: true \}\)/.test(main));
check('Auth screen can return to the homepage', /onBackHome=\{\(\) => navigatePublicPage\('landing'\)\}/.test(main));
check('Auth screen mode follows the public route', /initialMode=\{publicPage\}/.test(main));
check('Protected dashboard routes remain hash-driven', /syncHashForView\(activeView, true\)/.test(main));
check('Landing page never calls protected APIs', !/(apiFetch|apiJson|\/api\/)/.test(landing));

check('Landing page uses the exact brand, category and headline', /FinAudit AI/.test(landing) && /FINANCIAL INTELLIGENCE/.test(landing) && /Every transaction\.<br \/>\s*Every discrepancy\.<br \/>\s*<span>Accounted for\.<\/span>/.test(landing));
check(
  'Landing page uses the exact supporting copy',
  /FinAudit AI brings financial reconciliation, risk intelligence, invoice auditing and\s*AI-powered investigations into one evidence-driven workspace\./.test(landing)
);
check('Landing hero includes both required buttons', /Open Dashboard/.test(landing) && /Discover capabilities/.test(landing));
for (const label of [
  'Illustrative preview',
  'Sample data',
  'Financial reconciliation overview',
  'Total transactions',
  'Risk alerts',
  'Unresolved discrepancies',
  'Investigation status',
  'Evidence-backed review'
]) {
  check(`Dashboard preview shows '${label}'`, landing.includes(label));
}
for (const capability of [
  'Financial reconciliation',
  'Transaction intelligence',
  'Financial risk intelligence',
  'Vendor intelligence',
  'Invoice auditing',
  'Investigation case management',
  'AI investigation assistant',
  'Reports and analytics'
]) {
  check(`Capability '${capability}' is shown`, landing.includes(capability));
}
check('Planned functionality is labelled Upcoming', /Upcoming/.test(landing));
for (const section of [
  'THE PLATFORM',
  'CORE CAPABILITIES',
  'HOW IT WORKS',
  'AI SPECIALIST AGENTS',
  'EVIDENCE & HUMAN REVIEW',
  'GET STARTED'
]) {
  check(`Landing section '${section}' exists`, landing.includes(section));
}
check('Landing page has accessible navigation and a skip link', /aria-label="Public navigation"/.test(landing) && /lp-skip/.test(landing));
check('Landing styles use the warm off-white palette and separated CSS', /\.lp-root\s*\{[\s\S]*?--lp-bg:\s*#fbf9f5/.test(landingCss));
check('Landing styles include tablet and mobile breakpoints', /@media \(max-width: 980px\)/.test(landingCss) && /@media \(max-width: 680px\)/.test(landingCss));
check('Landing styles respect reduced motion', /@media \(prefers-reduced-motion: reduce\)/.test(landingCss));

// --- Local demonstration mode ---------------------------------------------
check('Local demo mode is opt-in and disabled by default', /VITE_LOCAL_DEMO_MODE=false/.test(frontendEnv) && /export const LOCAL_DEMO_MODE/.test(apiClient));
check('Demo status/session helpers call the backend', /export async function getDemoStatus/.test(apiClient) && /export async function requestDemoSession/.test(apiClient) && /\/api\/auth\/demo\/session/.test(apiClient));
check('Backend exposes gated demo endpoints', /@router\.get\("\/demo\/status"\)/.test(backendAuth) && /@router\.post\("\/demo\/session"/.test(backendAuth));
check('Backend demo mode refuses production-like environments', /def demo_mode_enabled[\s\S]*?return environment in _LOCAL_DEMO_ENVIRONMENTS/.test(backendAuth));
check('Demo sessions use signed JWTs, not a client-side auth bypass', /create_demo_token\(\)/.test(backendAuth) && /jwt\.encode\(payload, _jwt_secret\(\), algorithm=JWT_ALGORITHM\)/.test(backendAuth));
check('Protected routes still validate backend sessions', /apiFetch\('\/api\/auth\/me'\)/.test(main) && /enforce_route_permissions/.test(backendAuth));
check('Demo navigation skips the auth screens', /if \(!demoMode && \(publicPage === 'login' \|\| publicPage === 'register'\)\)/.test(main));
check('Demo Open Dashboard requests a backend demo session', /const handleOpenDashboard[\s\S]*?requestDemoSession\(\)[\s\S]*?setAuth\(data\)/.test(main));
check('Normal Open Dashboard still goes to sign in', /if \(!demoMode\) \{\s*navigatePublicPage\('login'\);/.test(main));
check('Demo sign-in links are hidden from landing navigation', /!demoMode && \(\s*<button className="lp-signin"/.test(landing) && /demoMode \? \(\s*<span className="lp-footer-demo"/.test(landing));
check('Demo auth hashes are redirected away from sign-in screens', /if \(demoMode && authPage\)/.test(main));
check('Workspace and landing are labelled DEMO MODE', /demo-mode-badge">DEMO MODE</.test(main) && /lp-demo-pill">DEMO MODE</.test(landing));
check('Demo logout skips backend revocation writes', /if \(!currentUser\?\.is_demo\) \{\s*try \{\s*await apiFetch\('\/api\/auth\/logout'/.test(main));
check('Backend tests prove demo mode cannot expose protected endpoints', /test_demo_mode_is_disabled_by_default/.test(backendDemoTests) && /test_demo_mode_is_refused_in_production/.test(backendDemoTests) && /test_demo_token_stops_working_when_mode_is_disabled/.test(backendDemoTests) && /test_local_demo_session_uses_real_jwt_rbac_without_database_writes/.test(backendDemoTests));

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

// --- Dashboard refresh contract ------------------------------------------
// There is no browser test runner in this project, so these static checks
// guard the refresh wiring that `npm run build` alone cannot verify.
check(
  'Dashboard reads every KPI, chart and activity row from the overview API',
  /apiFetch\('\/api\/dashboard\/overview'/.test(dashboard)
);
check(
  'Dashboard toolbar owns the single overview refresh action',
  /<div className="dash-toolbar">[\s\S]*?onClick=\{load\}[\s\S]*?Refresh/.test(dashboard)
);
check(
  'Duplicate dashboard Reload button is gone',
  !/Reload/.test(dashboard)
);
check(
  'Top-right workspace refresh is hidden on the overview view',
  /!DASHBOARD_REFRESH_VIEWS\.has\(activeView\) && \(\s*<button[\s\S]*?onClick=\{loadOverview\}/.test(main)
);
check(
  'Workspace refresh still loads audits and reconciliations for other pages',
  /const loadOverview[\s\S]*?\/api\/audits[\s\S]*?\/api\/reconciliations/.test(main)
);
check(
  'Dashboard blocks overlapping refresh requests',
  /if \(inFlight\.current\) return;/.test(dashboard)
);
check(
  'Refresh button is disabled and shows a spinner while refreshing',
  /disabled=\{refreshing \|\| loading\}/.test(dashboard) && /refreshing \? <Loader2 size=\{15\} className="spin"/.test(dashboard)
);
check(
  'Failed refresh keeps the last successful payload visible',
  !/if \(error\) return/.test(dashboard) && /Showing the last successful values/.test(dashboard) && !/setData\(null\)/.test(dashboard)
);
check(
  'Success indication is set only after a successful response',
  /setData\(payload\);[\s\S]*?setLastUpdated\(new Date\(\)\)/.test(dashboard) && /Updated \{lastUpdated\.toLocaleTimeString\(\)\}/.test(dashboard)
);
check(
  'Initial load error exposes a retry action',
  /if \(!data\) return[\s\S]*?role="alert"[\s\S]*?Retry\s*<\/button>/.test(dashboard)
);

// --- Dashboard data integrity display ------------------------------------
check(
  'Risk KPI renders all four severities from the overview distribution',
  /\['critical', 'high', 'medium', 'low'\]/.test(dashboard)
    && /severityCounts\[severity\]/.test(dashboard)
    && /detail: severityDetail/.test(dashboard)
    && !/high_risk_alerts/.test(dashboard)
);
check(
  'Reconciliation match rate is rendered with a percent unit',
  /reconciliation_match_rate[\s\S]*?toFixed\(2\)\}%/.test(dashboard)
    && /Match rate \$\{matchRateLabel\}/.test(dashboard)
);

// --- Responsive layout ---------------------------------------------------
check('Two-column map/detail layout is styled', /\.geo-map-detail-grid\s*\{[^}]*grid-template-columns:\s*minmax\(0,\s*\d+fr\)\s+minmax\(0,\s*\d+fr\)/s.test(css));

console.log(`Navigation contract verified: ${checks.length} checks passed.`);
for (const label of checks) console.log(`  ✓ ${label}`);
