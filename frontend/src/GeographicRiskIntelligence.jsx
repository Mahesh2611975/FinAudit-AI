import React, { useEffect, useMemo, useRef, useState } from 'react';
import L from 'leaflet';
import 'leaflet/dist/leaflet.css';
import 'leaflet.markercluster';
import 'leaflet.markercluster/dist/MarkerCluster.css';
import 'leaflet.markercluster/dist/MarkerCluster.Default.css';
import {
  Activity,
  AlertTriangle,
  Building2,
  CircleDollarSign,
  Filter,
  Globe2,
  Layers,
  Loader2,
  MapPin,
  RefreshCw,
  Siren,
  TrendingUp,
  X
} from 'lucide-react';
import {
  Bar,
  BarChart,
  CartesianGrid,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis
} from 'recharts';
import './risk.css';

const LOCATION_UNAVAILABLE = 'Location unavailable';
const RISK_COLORS = {
  critical: '#dc2626',
  high: '#ef4444',
  medium: '#f59e0b',
  low: '#22c55e'
};
const MARKER_GROUPS = ['high', 'medium', 'low'];
const EMPTY_FILTERS = {
  start_date: '',
  end_date: '',
  country: '',
  risk_level: '',
  channel: '',
  min_amount: '',
  // Calculated risk follows persisted risk-analysis alerts by default.
  // "imported" uses the optional CSV risk label as a display classification
  // only; it never changes which transactions are flagged.
  risk_source: 'calculated'
};
const RISK_SOURCE_LABELS = {
  calculated: 'Calculated (from risk alerts)',
  imported: 'Imported CSV label (display only)'
};

const fmtNumber = (value) => new Intl.NumberFormat('en-IN').format(Number(value) || 0);
const fmtPercent = (value) => `${(Number(value) || 0).toFixed(1)}%`;

const fmtCurrency = (amount, currency = 'INR') => {
  const code = String(currency || 'INR').toUpperCase();
  const value = Number(amount) || 0;
  try {
    return new Intl.NumberFormat('en-IN', {
      style: 'currency',
      currency: /^[A-Z]{3}$/.test(code) ? code : 'INR',
      maximumFractionDigits: 2
    }).format(value);
  } catch {
    return `${code} ${value.toFixed(2)}`;
  }
};

const fmtDate = (value) => {
  if (!value) return '—';
  const date = new Date(value);
  return Number.isNaN(date.getTime()) ? value : date.toLocaleDateString();
};

const locationLabel = (item) =>
  [item?.city, item?.state, item?.country].filter(Boolean).join(', ') || LOCATION_UNAVAILABLE;

const locationSourceLabel = (source) => {
  switch (source) {
    case 'stored':
      return 'Stored transaction coordinates';
    case 'alert':
      return 'Risk alert coordinates';
    case 'geocode_cache':
      return 'Cached geocode (no new lookup)';
    case 'nominatim':
      return 'OpenStreetMap Nominatim lookup';
    default:
      return 'No coordinates stored';
  }
};

const locationStatusLabel = (status) => {
  switch (status) {
    case 'mapped':
      return 'Mapped with valid coordinates';
    case 'named_only':
      return 'Named location only · coordinates unavailable';
    case 'invalid_coordinates':
      return 'Invalid coordinate pair ignored';
    default:
      return 'Location unavailable';
  }
};

const channelSourceLabel = (item) => {
  const source = String(item?.channel_source || '').toLowerCase();
  if (source === 'inferred') return 'Inferred from description';
  if (source === 'explicit') return 'Explicit channel column';
  if (source === 'explicit_unmapped') return 'Unmapped explicit value';
  const counts = item?.source_counts;
  if (counts?.inferred && !counts?.explicit && !counts?.unknown) return 'Inferred from description';
  if (counts?.explicit && !counts?.inferred && !counts?.unknown) return 'Explicit channel column';
  if (counts?.inferred || counts?.explicit) return 'Mixed sources';
  return 'Unknown source';
};

async function geoApi(apiBase, path, options = {}) {
  const response = await fetch(`${apiBase}${path}`, {
    headers: { 'Content-Type': 'application/json', ...(options.headers || {}) },
    ...options
  });
  const data = await response.json().catch(() => ({}));
  if (!response.ok) {
    const detail = Array.isArray(data.detail) ? data.detail.join(' ') : data.detail;
    throw new Error(detail || 'Geographic risk request failed.');
  }
  return data;
}

function RiskPill({ level }) {
  const value = String(level || 'low').toLowerCase();
  return (
    <span className={`risk-badge risk-badge-${value}`}>
      <span className="risk-badge-dot" />
      {value}
    </span>
  );
}

function GeoKpi({ label, value, hint, icon: Icon, tone = 'blue' }) {
  return (
    <article className="risk-kpi">
      <div className="risk-kpi-top">
        <div>
          <span>{label}</span>
          <strong>{value}</strong>
        </div>
        <div className={`risk-kpi-icon risk-kpi-icon-${tone}`}><Icon size={20} /></div>
      </div>
      <small>{hint}</small>
    </article>
  );
}

function GeoEmpty({ icon: Icon = Globe2, title, detail }) {
  return (
    <div className="risk-empty">
      <div className="risk-empty-icon"><Icon size={24} /></div>
      <strong>{title}</strong>
      <span>{detail}</span>
    </div>
  );
}

function buildTooltipNode(marker, currency) {
  const wrapper = document.createElement('div');
  wrapper.className = 'geo-tooltip';
  const title = document.createElement('strong');
  title.textContent = marker.transaction_id || 'Transaction';
  wrapper.appendChild(title);

  const addLine = (text) => {
    const line = document.createElement('div');
    line.textContent = text;
    wrapper.appendChild(line);
  };

  addLine(`${fmtCurrency(marker.amount, marker.currency || currency)} · ${String(marker.risk_level || 'low').toUpperCase()}`);
  addLine(locationLabel(marker));
  addLine(`Risk score ${fmtNumber(marker.risk_score)} / 100`);
  return wrapper;
}

function MarkerDetail({ marker, onClose }) {
  if (!marker) {
    return (
      <aside className="geo-detail geo-detail-empty">
        <div className="geo-detail-empty-icon"><MapPin size={22} /></div>
        <strong>Select a marker</strong>
        <span>Click any red, amber or green marker to inspect the stored transaction, its risk explanation and source file.</span>
        <ul className="geo-detail-hints">
          <li><span className="geo-legend-dot geo-legend-high" /> Red · high or critical risk</li>
          <li><span className="geo-legend-dot geo-legend-medium" /> Amber · medium risk</li>
          <li><span className="geo-legend-dot geo-legend-low" /> Green · no linked anomaly alert</li>
        </ul>
      </aside>
    );
  }

  return (
    <aside className="geo-detail" aria-live="polite">
      <header className="geo-detail-header">
        <div>
          <div className="geo-detail-eyebrow">Transaction detail</div>
          <h3>{marker.transaction_id || `Record ${marker.id}`}</h3>
          <div className="geo-detail-badges">
            <RiskPill level={marker.risk_level} />
            <span className="risk-score-pill">Score {fmtNumber(marker.risk_score)}</span>
            {marker.risk_classification_source === 'imported' ? (
              <span className="geo-flag-chip geo-flag-chip-imported">Imported label</span>
            ) : null}
            {marker.flagged ? (
              <span className="geo-flag-chip"><Siren size={11} /> Alert linked</span>
            ) : (
              <span className="geo-flag-chip geo-flag-chip-clear">No alert</span>
            )}
          </div>
        </div>
        <button type="button" className="risk-icon-btn" onClick={onClose} aria-label="Close marker detail">
          <X size={17} />
        </button>
      </header>

      <div className="geo-detail-grid">
        <div><span>Date</span><strong>{fmtDate(marker.date)}</strong></div>
        <div><span>Amount</span><strong>{fmtCurrency(marker.amount, marker.currency)}</strong></div>
        <div><span>City</span><strong>{marker.city || LOCATION_UNAVAILABLE}</strong></div>
        <div><span>State</span><strong>{marker.state || LOCATION_UNAVAILABLE}</strong></div>
        <div><span>Country</span><strong>{marker.country || LOCATION_UNAVAILABLE}</strong></div>
        <div><span>Channel</span><strong>{marker.channel || 'Unknown'}</strong></div>
        <div><span>Original channel</span><strong>{marker.channel_original || '—'}</strong></div>
        <div><span>Channel source</span><strong>{channelSourceLabel(marker)}</strong></div>
        <div><span>Risk source</span><strong>{marker.risk_classification_source === 'imported' ? 'Imported CSV label' : 'Calculated from alerts'}</strong></div>
        <div><span>Calculated level</span><strong>{marker.calculated_risk_level || 'low'}</strong></div>
        <div><span>Imported label</span><strong>{marker.imported_risk_level || '—'}</strong></div>
        <div><span>Linked alerts</span><strong>{fmtNumber(marker.linked_alert_count || marker.alert_ids?.length || 0)}</strong></div>
        <div><span>Merchant</span><strong>{marker.merchant || '—'}</strong></div>
        <div><span>Source file</span><strong>{marker.source_file || '—'}</strong></div>
        <div><span>Source row</span><strong>{marker.source_row || '—'}</strong></div>
        <div><span>Location status</span><strong>{locationStatusLabel(marker.location_status)}</strong></div>
        <div><span>Coordinates</span><strong>{marker.latitude != null && marker.longitude != null ? `${Number(marker.latitude).toFixed(4)}, ${Number(marker.longitude).toFixed(4)}` : LOCATION_UNAVAILABLE}</strong></div>
        <div><span>Location source</span><strong>{locationSourceLabel(marker.location_source)}</strong></div>
      </div>

      <section className="geo-detail-section">
        <span className="geo-detail-label">Why this transaction is risk-rated</span>
        <p>{marker.reason || 'No anomaly explanation is stored for this transaction.'}</p>
      </section>

      <section className="geo-detail-section">
        <span className="geo-detail-label">Linked alerts</span>
        {marker.alert_ids?.length ? (
          <div className="geo-alert-tags">
            {marker.alert_ids.map((alertId) => <span key={alertId}>{alertId}</span>)}
          </div>
        ) : (
          <p className="risk-muted">No anomaly alert is linked to this transaction in the latest analysis.</p>
        )}
        <p className="risk-muted geo-detail-disclaimer">
          Alerts are investigation leads, not proof of fraud. Human review is required before any financial action.
        </p>
      </section>
    </aside>
  );
}

function CountryRiskPanel({ countries, currency }) {
  return (
    <section className="risk-card geo-panel">
      <div className="risk-card-header">
        <div>
          <h2>Country risk distribution</h2>
          <p>High-risk counts, risk mix and flagged value by mapped country.</p>
        </div>
        <span className="risk-meta-pill"><Globe2 size={13} /> {fmtNumber(countries.length)} countries</span>
      </div>
      {countries.length === 0 ? (
        <GeoEmpty title="No mapped country statistics" detail="Country statistics appear only when stored records have coordinates." />
      ) : (
        <div className="geo-country-list">
          {countries.map((country) => {
            const total = country.transaction_count || 0;
            const critical = country.critical_count || 0;
            const high = country.high_count || 0;
            const medium = country.medium_count || 0;
            const low = country.low_count || 0;
            const pct = (count) => (total ? (count / total) * 100 : 0);
            return (
              <article key={country.country} className="geo-country-row">
                <div className="geo-country-head">
                  <div>
                    <strong>{country.country}</strong>
                    <span>{fmtNumber(country.city_count)} {country.city_count === 1 ? 'city' : 'cities'} · avg score {country.average_risk_score}</span>
                  </div>
                  <div className="geo-country-numbers">
                    <b>{fmtNumber(country.high_risk_count)}</b>
                    <span>high-risk</span>
                  </div>
                </div>
                <div className="geo-distribution" title={`Critical ${critical} · High ${high} · Medium ${medium} · Low ${low}`}>
                  {critical > 0 && <span style={{ width: `${pct(critical)}%`, background: RISK_COLORS.critical }} />}
                  {high > 0 && <span style={{ width: `${pct(high)}%`, background: RISK_COLORS.high }} />}
                  {medium > 0 && <span style={{ width: `${pct(medium)}%`, background: RISK_COLORS.medium }} />}
                  {low > 0 && <span style={{ width: `${pct(low)}%`, background: RISK_COLORS.low }} />}
                </div>
                <div className="geo-country-meta">
                  <span>C {fmtNumber(critical)} · H {fmtNumber(high)} · M {fmtNumber(medium)} · L {fmtNumber(low)}</span>
                  <span>{fmtNumber(total)} txns</span>
                  <span>Flagged {fmtCurrency(country.flagged_value, currency)}</span>
                </div>
              </article>
            );
          })}
        </div>
      )}
    </section>
  );
}

function HighRiskCitiesPanel({ cities, markers, onSelect }) {
  return (
    <section className="risk-card geo-panel">
      <div className="risk-card-header">
        <div>
          <h2>Highest-risk cities</h2>
          <p>Ranked by high-risk transaction count, then flagged value.</p>
        </div>
        <span className="risk-meta-pill"><Building2 size={13} /> {fmtNumber(cities.length)} ranked</span>
      </div>
      {cities.length === 0 ? (
        <GeoEmpty title="No high-risk cities" detail="No mapped city currently has a linked high or critical alert for this filter." />
      ) : (
        <ol className="geo-ranked-list">
          {cities.slice(0, 8).map((city, index) => {
            const match = markers.find(
              (marker) =>
                marker.city === city.city &&
                marker.country === city.country &&
                (marker.risk_level === 'high' || marker.risk_level === 'critical')
            ) || markers.find(
              (marker) => marker.city === city.city && marker.country === city.country
            );
            return (
              <li key={`${city.city}-${city.state}-${city.country}`}>
                <button type="button" onClick={() => match && onSelect(match)} disabled={!match}>
                  <span className="geo-rank">{index + 1}</span>
                  <span className="geo-rank-name">
                    <strong>{city.city}</strong>
                    <span>{[city.state, city.country].filter(Boolean).join(', ') || LOCATION_UNAVAILABLE}</span>
                  </span>
                  <span className="geo-rank-stats">
                    <b>{fmtNumber(city.high_risk_count)} high</b>
                    <span>{fmtNumber(city.transaction_count)} txns</span>
                  </span>
                </button>
              </li>
            );
          })}
        </ol>
      )}
    </section>
  );
}

function UnusualLocationsPanel({ locations }) {
  return (
    <section className="risk-card geo-panel">
      <div className="risk-card-header">
        <div>
          <h2>Unusually active locations</h2>
          <p>Cities above the mapped-city activity threshold (mean + 2 standard deviations).</p>
        </div>
        <span className="risk-meta-pill"><Activity size={13} /> {fmtNumber(locations.length)} flagged</span>
      </div>
      {locations.length === 0 ? (
        <GeoEmpty
          icon={Activity}
          title="No unusual activity"
          detail="No mapped city exceeds the activity threshold for the current filter. This is an empty result, not a claim of normal activity."
        />
      ) : (
        <div className="geo-unusual-list">
          {locations.slice(0, 6).map((location) => (
            <div key={`${location.city}-${location.state}-${location.country}`}>
              <span className="geo-unusual-name"><MapPin size={13} /> {locationLabel(location)}</span>
              <div className="geo-unusual-meter">
                <span style={{ width: `${Math.min(100, (location.activity_index || 0) * 50)}%` }} />
              </div>
              <span className="geo-unusual-value">
                {fmtNumber(location.transaction_count)} txns · {location.activity_index}× mean
              </span>
            </div>
          ))}
          <p className="risk-muted">
            Threshold: {fmtNumber(locations[0]?.activity_threshold ?? 0)} transactions. Activity is not evidence of fraud.
          </p>
        </div>
      )}
    </section>
  );
}

function GeoChannelPanel({ analytics, currency }) {
  const channels = analytics?.channels || [];
  const observed = channels.filter((item) => item.transaction_count > 0);
  return (
    <section className="risk-card geo-panel">
      <div className="risk-card-header">
        <div>
          <h2>Channel analytics for this map filter</h2>
          <p>Counts and flagged values by payment channel, with source provenance.</p>
        </div>
        <span className="risk-meta-pill"><Layers size={13} /> {fmtNumber(analytics?.transactions_analyzed)} txns</span>
      </div>
      {channels.length === 0 ? (
        <GeoEmpty title="No channel analytics" detail="No transactions match the current geographic filter." />
      ) : (
        <>
          <div className="geo-channel-list">
            {channels.map((item) => (
              <div key={item.channel} className={item.transaction_count ? '' : 'geo-channel-empty'}>
                <div className="geo-channel-name">
                  <strong>{item.channel}</strong>
                  <small>{channelSourceLabel(item)}</small>
                </div>
                <div className="geo-channel-stats">
                  <span><b>{fmtNumber(item.transaction_count)}</b> txns</span>
                  <span><b>{fmtNumber(item.flagged_count)}</b> flagged</span>
                  <span className="geo-channel-value">{fmtCurrency(item.flagged_amount, currency)}</span>
                </div>
              </div>
            ))}
          </div>
          <p className="risk-muted">
            {fmtNumber(observed.length)} active channels · {fmtNumber(analytics?.inferred_count)} inferred from descriptions · Unknown remains listed.
          </p>
        </>
      )}
    </section>
  );
}

function GeoTrendTooltip({ active, payload, label }) {
  if (!active || !payload?.length) return null;
  const item = payload[0].payload;
  return (
    <div className="risk-tooltip">
      <strong>{label}</strong>
      <div>Mapped transactions: {fmtNumber(item.total)}</div>
      <div>Critical: {fmtNumber(item.critical)} · High: {fmtNumber(item.high)}</div>
      <div>Medium: {fmtNumber(item.medium)} · Low: {fmtNumber(item.low)}</div>
      <div>Flagged: {fmtNumber(item.flagged)}</div>
    </div>
  );
}

function GeoTrendPanel({ trend }) {
  return (
    <section className="risk-card geo-panel geo-trend-panel">
      <div className="risk-card-header">
        <div>
          <h2>Geographic activity trend</h2>
          <p>Mapped transactions by date and risk level from stored records.</p>
        </div>
        <span className="risk-meta-pill"><TrendingUp size={13} /> {fmtNumber(trend.length)} days</span>
      </div>
      {trend.length === 0 ? (
        <GeoEmpty icon={TrendingUp} title="No geographic trend" detail="No mapped transaction has a usable date for the current filter." />
      ) : (
        <div className="risk-chart">
          <ResponsiveContainer width="100%" height={240}>
            <BarChart data={trend} margin={{ top: 10, right: 10, left: -18, bottom: 0 }}>
              <CartesianGrid stroke="rgba(148,163,184,0.12)" vertical={false} />
              <XAxis dataKey="date" tick={{ fill: '#7f8da8', fontSize: 10 }} axisLine={false} tickLine={false} minTickGap={22} />
              <YAxis allowDecimals={false} tick={{ fill: '#7f8da8', fontSize: 11 }} axisLine={false} tickLine={false} />
              <Tooltip content={<GeoTrendTooltip />} cursor={{ fill: 'rgba(59,130,246,0.07)' }} />
              <Bar dataKey="critical" name="Critical" stackId="risk" fill={RISK_COLORS.critical} radius={[3, 3, 0, 0]} />
              <Bar dataKey="high" name="High" stackId="risk" fill={RISK_COLORS.high} />
              <Bar dataKey="medium" name="Medium" stackId="risk" fill={RISK_COLORS.medium} />
              <Bar dataKey="low" name="Low" stackId="risk" fill={RISK_COLORS.low} radius={[3, 3, 0, 0]} />
            </BarChart>
          </ResponsiveContainer>
        </div>
      )}
    </section>
  );
}

export default function GeographicRiskIntelligence({ apiBase = 'http://127.0.0.1:8000', refreshSignal = 0 }) {
  const [data, setData] = useState(null);
  const [filters, setFilters] = useState(EMPTY_FILTERS);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState('');
  const [selectedMarker, setSelectedMarker] = useState(null);
  const [localRefresh, setLocalRefresh] = useState(0);

  const mapContainerRef = useRef(null);
  const mapRef = useRef(null);
  const clusterRef = useRef(null);

  const markers = data?.markers || [];
  const summary = data?.summary || null;
  const countries = data?.countries || [];
  const cities = data?.high_risk_locations || [];
  const unusualLocations = data?.unusual_locations || [];
  const trend = data?.trend || [];
  const channelAnalytics = data?.channel_analytics || null;
  const options = data?.options || { countries: [], channels: [], risk_levels: [], risk_sources: [] };
  const geocoding = data?.geocoding || {};
  const available = Boolean(data?.available && markers.length > 0);

  const currency = useMemo(() => {
    const counts = new Map();
    markers.forEach((marker) => {
      const code = String(marker.currency || '').toUpperCase();
      if (/^[A-Z]{3}$/.test(code)) counts.set(code, (counts.get(code) || 0) + 1);
    });
    if (counts.size === 0) return 'INR';
    return [...counts.entries()].sort((a, b) => b[1] - a[1])[0][0];
  }, [markers]);

  const activeFilterCount = Object.entries(filters).filter(
    ([key, value]) => Boolean(value) && !(key === 'risk_source' && value === 'calculated')
  ).length;

  const updateFilter = (key, value) => {
    setFilters((current) => ({ ...current, [key]: value }));
  };

  const clearFilters = () => setFilters(EMPTY_FILTERS);

  useEffect(() => {
    let cancelled = false;
    const timer = setTimeout(async () => {
      setLoading(true);
      setError('');
      try {
        const params = new URLSearchParams();
        Object.entries(filters).forEach(([key, value]) => {
          if (value !== '' && value !== null && value !== undefined) params.set(key, value);
        });
        const query = params.toString();
        const payload = await geoApi(
          apiBase,
          `/api/risk/geography/summary${query ? `?${query}` : ''}`
        );
        if (cancelled) return;
        setData(payload);
        setSelectedMarker((current) => {
          if (!current) return null;
          return payload.markers?.find((marker) => marker.id === current.id) || null;
        });
      } catch (err) {
        if (!cancelled) setError(err.message);
      } finally {
        if (!cancelled) setLoading(false);
      }
    }, 280);

    return () => {
      cancelled = true;
      clearTimeout(timer);
    };
  }, [apiBase, filters, refreshSignal, localRefresh]);

  useEffect(() => {
    if (!mapContainerRef.current || mapRef.current) return undefined;

    const map = L.map(mapContainerRef.current, {
      center: [20, 10],
      zoom: 2,
      minZoom: 2,
      maxZoom: 12,
      zoomControl: true,
      worldCopyJump: true,
      attributionControl: true
    });

    // Standard OpenStreetMap tile layer: public, no API key required, and the
    // attribution below satisfies the OpenStreetMap tile usage policy.
    L.tileLayer('https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png', {
      subdomains: 'abc',
      maxZoom: 19,
      attribution: '&copy; <a href="https://www.openstreetmap.org/copyright">OpenStreetMap</a> contributors'
    }).addTo(map);

    const cluster = L.markerClusterGroup({
      showCoverageOnHover: false,
      spiderfyOnMaxZoom: true,
      removeOutsideVisibleBounds: true,
      maxClusterRadius: 52,
      iconCreateFunction: (item) => {
        const children = item.getAllChildMarkers();
        const group = children.some((child) => child.options.riskGroup === 'high')
          ? 'high'
          : children.some((child) => child.options.riskGroup === 'medium')
            ? 'medium'
            : 'low';
        const count = item.getChildCount();
        const size = count > 99 ? 54 : count > 9 ? 48 : 42;
        return L.divIcon({
          className: 'geo-cluster-wrap',
          html: `<span class="geo-cluster geo-cluster-${group}" style="width:${size}px;height:${size}px">${count}</span>`,
          iconSize: L.point(size, size)
        });
      }
    });

    map.addLayer(cluster);
    mapRef.current = map;
    clusterRef.current = cluster;

    const invalidate = () => map.invalidateSize();
    requestAnimationFrame(invalidate);
    window.addEventListener('resize', invalidate);

    return () => {
      window.removeEventListener('resize', invalidate);
      map.remove();
      mapRef.current = null;
      clusterRef.current = null;
    };
  }, []);

  useEffect(() => {
    const map = mapRef.current;
    const cluster = clusterRef.current;
    if (!map || !cluster) return;

    cluster.clearLayers();
    const maxAmount = Math.max(0, ...markers.map((marker) => Number(marker.amount) || 0));
    const bounds = L.latLngBounds([]);

    markers.forEach((marker) => {
      if (marker.latitude == null || marker.longitude == null) return;
      const amount = Number(marker.amount) || 0;
      const ratio = maxAmount > 0 ? Math.sqrt(amount / maxAmount) : 0;
      const size = Math.round(14 + ratio * 11);
      const group = MARKER_GROUPS.includes(marker.marker_group) ? marker.marker_group : 'low';
      const icon = L.divIcon({
        className: 'geo-marker-wrap',
        html: `<span class="geo-marker geo-marker-${group}" style="width:${size}px;height:${size}px"><span class="geo-marker-core"></span></span>`,
        iconSize: L.point(size, size),
        iconAnchor: L.point(size / 2, size / 2)
      });
      const point = L.marker([Number(marker.latitude), Number(marker.longitude)], {
        icon,
        riskGroup: group,
        title: marker.transaction_id || 'Transaction'
      });
      point.bindTooltip(() => buildTooltipNode(marker, currency), {
        direction: 'top',
        offset: L.point(0, -(size / 2) - 4),
        opacity: 1,
        className: 'geo-tooltip-shell'
      });
      point.on('click', () => setSelectedMarker(marker));
      cluster.addLayer(point);
      bounds.extend([Number(marker.latitude), Number(marker.longitude)]);
    });

    if (bounds.isValid()) {
      if (markers.length === 1) {
        map.setView(bounds.getCenter(), 6);
      } else {
        map.fitBounds(bounds, { padding: [36, 36], maxZoom: 6 });
      }
      requestAnimationFrame(() => map.invalidateSize());
    }
  }, [markers, currency]);

  const geocodingLabel = geocoding.enabled
    ? `Geocoding on · ${fmtNumber(geocoding.cache_hits || 0)} cached`
    : 'Geocoding off · cached + stored coordinates';

  return (
    <section className="geo-section">
      <div className="geo-heading">
        <div>
          <div className="geo-heading-title"><Globe2 size={20} /> Geographic risk intelligence</div>
          <p>
            Interactive world map of stored transactions. Marker colour shows risk; marker size reflects transaction value.
            No coordinates or locations are invented.
          </p>
        </div>
        <div className="geo-heading-meta">
          <span className={`geo-status-pill ${geocoding.enabled ? 'geo-status-on' : ''}`}>
            <Layers size={13} /> {geocodingLabel}
          </span>
          <button
            type="button"
            className="secondary-btn"
            onClick={() => setLocalRefresh((value) => value + 1)}
            disabled={loading}
          >
            <RefreshCw size={15} className={loading ? 'spin' : ''} /> Reload map
          </button>
        </div>
      </div>

      <div className="risk-filter-bar geo-filter-bar">
        <label>
          <span><Filter size={13} /> From date</span>
          <input type="date" value={filters.start_date} onChange={(event) => updateFilter('start_date', event.target.value)} />
        </label>
        <label>
          <span>To date</span>
          <input type="date" value={filters.end_date} onChange={(event) => updateFilter('end_date', event.target.value)} />
        </label>
        <label>
          <span>Country</span>
          <select value={filters.country} onChange={(event) => updateFilter('country', event.target.value)}>
            <option value="">All countries</option>
            {options.countries.map((country) => <option key={country} value={country}>{country}</option>)}
          </select>
        </label>
        <label>
          <span>Risk source</span>
          <select value={filters.risk_source} onChange={(event) => updateFilter('risk_source', event.target.value)}>
            {(options.risk_sources?.length ? options.risk_sources : ['calculated', 'imported']).map((source) => (
              <option key={source} value={source}>{RISK_SOURCE_LABELS[source] || source}</option>
            ))}
          </select>
        </label>
        <label>
          <span>Risk level</span>
          <select value={filters.risk_level} onChange={(event) => updateFilter('risk_level', event.target.value)}>
            <option value="">All risk levels</option>
            <option value="high">High &amp; critical</option>
            <option value="critical">Critical only</option>
            <option value="medium">Medium</option>
            <option value="low">Low</option>
          </select>
        </label>
        <label>
          <span>Channel</span>
          <select value={filters.channel} onChange={(event) => updateFilter('channel', event.target.value)}>
            <option value="">All channels</option>
            {options.channels.map((channel) => <option key={channel} value={channel}>{channel}</option>)}
          </select>
        </label>
        <label>
          <span>Minimum amount</span>
          <input
            type="number"
            min="0"
            step="0.01"
            placeholder="0"
            value={filters.min_amount}
            onChange={(event) => updateFilter('min_amount', event.target.value)}
          />
        </label>
        <button type="button" className="risk-text-btn geo-clear-filters" onClick={clearFilters} disabled={activeFilterCount === 0}>
          <X size={14} /> Clear filters
        </button>
      </div>

      {error && <div className="error-box">{error}</div>}

      <div className="risk-kpi-grid geo-kpi-grid">
        <GeoKpi
          label="Total transactions"
          value={summary ? fmtNumber(summary.total_transactions) : '—'}
          hint={summary ? `${fmtNumber(summary.transactions_with_location_information)} with location information` : 'Loading stored records'}
          icon={MapPin}
          tone="blue"
        />
        <GeoKpi
          label="With coordinates"
          value={summary ? fmtNumber(summary.transactions_with_coordinates) : '—'}
          hint={summary ? `${fmtNumber(summary.located_transactions)} plotted on the map` : 'Stored or resolved coordinates'}
          icon={Globe2}
          tone="cyan"
        />
        <GeoKpi
          label="Without coordinates"
          value={summary ? fmtNumber(summary.transactions_without_coordinates) : '—'}
          hint={summary ? `${fmtNumber(summary.unavailable_location_transactions)} unavailable · ${fmtNumber(summary.named_only_transactions)} named only` : 'No coordinates stored'}
          icon={MapPin}
          tone="blue"
        />
        <GeoKpi
          label="Linked risk alerts"
          value={summary ? fmtNumber(summary.transactions_with_linked_alerts) : '—'}
          hint={summary ? `${fmtNumber(summary.transactions_without_linked_alerts)} without linked alerts` : 'From persisted alert linking'}
          icon={Siren}
          tone="red"
        />
        <GeoKpi
          label="High-risk transactions"
          value={summary ? fmtNumber(summary.high_risk_transactions) : '—'}
          hint={summary ? `${fmtNumber(data?.high_risk_countries?.length || 0)} countries with high risk · source: ${summary.classification_source || 'calculated'}` : 'From linked alerts'}
          icon={Siren}
          tone="red"
        />
        <GeoKpi
          label="Flagged value"
          value={summary ? fmtCurrency(summary.flagged_value, currency) : '—'}
          hint="Mapped transactions with linked alerts · counted once each"
          icon={CircleDollarSign}
          tone="amber"
        />
        <GeoKpi
          label="High-risk value"
          value={summary ? fmtCurrency(summary.high_risk_value, currency) : '—'}
          hint="Mapped value rated high or critical"
          icon={AlertTriangle}
          tone="purple"
        />
        <GeoKpi
          label="Countries mapped"
          value={summary ? fmtNumber(summary.countries_count) : '—'}
          hint={summary ? `${fmtNumber(summary.cities_count)} cities with coordinates` : 'Stored coordinates only'}
          icon={Globe2}
          tone="cyan"
        />
        <GeoKpi
          label="Total mapped value"
          value={summary ? fmtCurrency(summary.total_mapped_value, currency) : '—'}
          hint={summary ? `${fmtNumber(summary.geocoded_transactions)} resolved by cached geocoder` : 'No fabricated totals'}
          icon={Building2}
          tone="green"
        />
      </div>

      <div className="geo-map-layout">
        <section className="risk-card geo-map-card">
          <div className="risk-card-header">
            <div>
              <h2>Transaction risk map</h2>
              <p>Click a marker for transaction ID, date, amount, risk reason, location and source file.</p>
            </div>
            <span className="risk-meta-pill">
              <MapPin size={13} /> {fmtNumber(markers.length)} plotted
            </span>
          </div>

          <div className="geo-map-shell">
            <div ref={mapContainerRef} className="geo-map" role="application" aria-label="Interactive geographic risk map" />
            <div className="geo-map-legend">
              <span><i className="geo-legend-dot geo-legend-high" /> High / critical</span>
              <span><i className="geo-legend-dot geo-legend-medium" /> Medium</span>
              <span><i className="geo-legend-dot geo-legend-low" /> Low</span>
              <small>Marker size = transaction value</small>
            </div>

            {(!data || loading) && (
              <div className="geo-map-overlay">
                <Loader2 size={24} className="spin" />
                <strong>{data ? 'Updating map…' : 'Loading geographic risk data…'}</strong>
                <span>Reading stored transactions and linked alerts.</span>
              </div>
            )}

            {error && !loading && !data && (
              <div className="geo-map-overlay geo-map-overlay-empty">
                <div className="risk-empty-icon"><AlertTriangle size={24} /></div>
                <strong>Map data unavailable</strong>
                <span>{error}</span>
              </div>
            )}

            {data && !loading && !available && (
              <div className="geo-map-overlay geo-map-overlay-empty">
                <div className="risk-empty-icon"><Globe2 size={24} /></div>
                <strong>{summary?.total_transactions_matched ? 'Location unavailable' : 'No transactions match these filters'}</strong>
                <span>{data.reason || 'No stored transaction has usable coordinates for this selection.'}</span>
                <span className="geo-overlay-hint">
                  Import latitude/longitude columns, or enable optional cached geocoding with
                  {' '}<code>FINAUDIT_GEOCODING_ENABLED=true</code>. No coordinates are inferred from IP addresses.
                </span>
              </div>
            )}
          </div>

          {data?.notice && <p className="geo-map-notice"><AlertTriangle size={13} /> {data.notice}</p>}
        </section>

        <MarkerDetail marker={selectedMarker} onClose={() => setSelectedMarker(null)} />
      </div>

      <div className="geo-panel-grid">
        <CountryRiskPanel countries={countries} currency={currency} />
        <HighRiskCitiesPanel cities={cities} markers={markers} onSelect={setSelectedMarker} />
      </div>

      <div className="geo-panel-grid">
        <GeoChannelPanel analytics={channelAnalytics} currency={currency} />
        <GeoTrendPanel trend={trend} />
      </div>

      <div className="geo-panel-grid">
        <UnusualLocationsPanel locations={unusualLocations} />
      </div>

      <div className="geo-footnote">
        <Globe2 size={14} />
        <span>
          {summary
            ? `${fmtNumber(summary.coordinate_transactions)} markers use stored or alert coordinates; ${fmtNumber(summary.geocoded_transactions)} use cached geocoding. ${fmtNumber(summary.unavailable_location_transactions)} unavailable, ${fmtNumber(summary.named_only_transactions)} named-only and ${fmtNumber(summary.invalid_coordinate_transactions)} invalid-coordinate transactions are reported without invented markers. ${fmtPercent(
              summary.transactions_with_location_information
                ? (summary.located_transactions / summary.transactions_with_location_information) * 100
                : 0
            )} of matched transactions with location information are plotted.`
            : 'Geographic statistics load from the backend; no demo values are shown.'}
        </span>
      </div>
    </section>
  );
}
