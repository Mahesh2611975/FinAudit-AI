import React, { useEffect, useMemo, useState } from 'react';
import {
  AlertOctagon,
  AlertTriangle,
  ArrowLeft,
  ArrowRight,
  BrainCircuit,
  CheckCircle2,
  ChevronDown,
  ChevronUp,
  CircleDollarSign,
  FileSearch,
  Filter,
  Loader2,
  MessageSquare,
  Radar,
  RefreshCw,
  Search,
  ShieldAlert,
  Siren,
  TrendingUp,
  UserCheck,
  X
} from 'lucide-react';
import {
  Area,
  AreaChart,
  Bar,
  BarChart,
  CartesianGrid,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis
} from 'recharts';
import GeographicRiskIntelligence from './GeographicRiskIntelligence';
import './risk.css';

const SEVERITY_ORDER = ['critical', 'high', 'medium', 'low'];
const SEVERITY_COLORS = {
  critical: '#dc2626',
  high: '#f97316',
  medium: '#f59e0b',
  low: '#22c55e'
};

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

const fmtNumber = (value) => new Intl.NumberFormat('en-IN').format(Number(value) || 0);

const fmtPercent = (value) => `${(Number(value) || 0).toFixed(1)}%`;

const fmtDate = (value) => {
  if (!value) return '—';
  const date = new Date(value);
  return Number.isNaN(date.getTime()) ? value : date.toLocaleString();
};

async function riskApi(apiBase, path, options = {}) {
  const response = await fetch(`${apiBase}${path}`, {
    headers: { 'Content-Type': 'application/json', ...(options.headers || {}) },
    ...options
  });
  const data = await response.json().catch(() => ({}));
  if (!response.ok) {
    const detail = Array.isArray(data.detail) ? data.detail.join(' ') : data.detail;
    const message = detail && typeof detail === 'object'
      ? detail.message || detail.errors?.join(' ') || JSON.stringify(detail)
      : detail;
    throw new Error(message || 'Risk intelligence request failed.');
  }
  return data;
}

function SeverityBadge({ severity }) {
  const value = String(severity || 'low').toLowerCase();
  return (
    <span className={`risk-badge risk-badge-${value}`}>
      <span className="risk-badge-dot" />
      {value}
    </span>
  );
}

function RiskBadge({ status }) {
  const label = String(status || 'open').replaceAll('_', ' ');
  const tone = status === 'resolved' ? 'low' : status === 'investigating' ? 'medium' : status === 'open' ? 'critical' : 'neutral';
  return <span className={`risk-status risk-status-${tone}`}>{label}</span>;
}

function Kpi({ label, value, hint, icon: Icon, tone = 'blue' }) {
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

function EmptyState({ icon: Icon = FileSearch, title, detail }) {
  return (
    <div className="risk-empty">
      <div className="risk-empty-icon"><Icon size={24} /></div>
      <strong>{title}</strong>
      <span>{detail}</span>
    </div>
  );
}

function ChannelTooltip({ active, payload }) {
  if (!active || !payload?.length) return null;
  const item = payload[0].payload;
  return (
    <div className="risk-tooltip">
      <strong>{item.channel}</strong>
      <div>Transactions: {fmtNumber(item.transaction_count)}</div>
      <div>Flagged: {fmtNumber(item.flagged_count)} ({fmtPercent(item.risk_percentage)})</div>
      <div>Total value: {fmtCurrency(item.total_amount)}</div>
    </div>
  );
}

function AlertDetail({
  alert,
  apiBase,
  onClose,
  onUpdated,
  onReviewSaved
}) {
  const [reviewStatus, setReviewStatus] = useState(alert.review_status || 'open');
  const [assignedTo, setAssignedTo] = useState(alert.assigned_to || '');
  const [reviewerName, setReviewerName] = useState('Risk Analyst');
  const [comment, setComment] = useState(alert.reviewer_comments || '');
  const [saving, setSaving] = useState(false);
  const [investigating, setInvestigating] = useState(false);
  const [investigation, setInvestigation] = useState(null);
  const [error, setError] = useState('');
  const [showEvidence, setShowEvidence] = useState(false);

  useEffect(() => {
    setReviewStatus(alert.review_status || 'open');
    setAssignedTo(alert.assigned_to || '');
    setComment(alert.reviewer_comments || '');
    setInvestigation(null);
    setError('');
    setShowEvidence(false);
  }, [alert.id]);

  const saveReview = async () => {
    if (!reviewerName.trim()) {
      setError('Reviewer name is required.');
      return;
    }
    setSaving(true);
    setError('');
    try {
      const action = reviewStatus === 'resolved'
        ? 'resolve'
        : reviewStatus === 'open'
          ? 'reopen'
          : reviewStatus === 'investigating'
            ? 'investigate'
            : 'status';
      const updated = await riskApi(apiBase, `/api/risk/alerts/${alert.id}/review`, {
        method: 'POST',
        body: JSON.stringify({
          action,
          review_status: reviewStatus,
          assigned_to: assignedTo,
          reviewer_name: reviewerName,
          comment
        })
      });
      onUpdated(updated);
      onReviewSaved();
    } catch (err) {
      setError(err.message);
    } finally {
      setSaving(false);
    }
  };

  const runInvestigation = async () => {
    setInvestigating(true);
    setError('');
    try {
      setInvestigation(await riskApi(apiBase, '/api/risk/investigate', {
        method: 'POST',
        body: JSON.stringify({ alert_id: alert.id })
      }));
    } catch (err) {
      setError(err.message);
    } finally {
      setInvestigating(false);
    }
  };

  return (
    <div className="risk-drawer-backdrop" role="presentation" onMouseDown={onClose}>
      <aside className="risk-drawer" role="dialog" aria-modal="true" aria-label={`Alert ${alert.alert_id}`} onMouseDown={(event) => event.stopPropagation()}>
        <header className="risk-drawer-header">
          <div>
            <div className="risk-drawer-eyebrow">{alert.alert_id}</div>
            <h2>{alert.risk_category}</h2>
            <div className="risk-drawer-meta">
              <SeverityBadge severity={alert.severity} />
              <span className="risk-score-pill">Score {alert.risk_score}</span>
              <RiskBadge status={alert.review_status} />
            </div>
          </div>
          <button type="button" className="risk-icon-btn" onClick={onClose} aria-label="Close alert detail">
            <X size={20} />
          </button>
        </header>

        <div className="risk-drawer-body">
          <div className="risk-notice">
            <AlertTriangle size={18} />
            <span>This alert is a potential anomaly, not proof of fraud. Human review is required before any financial action.</span>
          </div>

          {error && <div className="error-box">{error}</div>}

          <section className="risk-drawer-section">
            <h3>Detection summary</h3>
            <p className="risk-explanation">{alert.explanation}</p>
            <div className="risk-detail-grid">
              <div><span>Amount</span><strong>{alert.amount != null ? fmtCurrency(alert.amount, alert.currency) : '—'}</strong></div>
              <div><span>Channel</span><strong>{alert.channel || 'Unknown'}</strong></div>
              <div><span>Vendor</span><strong>{alert.vendor_name || '—'}</strong></div>
              <div><span>Transaction</span><strong>{alert.transaction_id || '—'}</strong></div>
              <div><span>Detected</span><strong>{fmtDate(alert.detected_at)}</strong></div>
              <div><span>Assigned to</span><strong>{alert.assigned_to || 'Unassigned'}</strong></div>
              <div><span>Location</span><strong>{[alert.city, alert.state, alert.country].filter(Boolean).join(', ') || 'Unavailable'}</strong></div>
              <div><span>Coordinates</span><strong>{alert.latitude != null && alert.longitude != null ? `${Number(alert.latitude).toFixed(4)}, ${Number(alert.longitude).toFixed(4)}` : 'Unavailable'}</strong></div>
            </div>
          </section>

          <section className="risk-drawer-section">
            <button type="button" className="risk-collapse" onClick={() => setShowEvidence((value) => !value)}>
              <span>Supporting evidence</span>
              {showEvidence ? <ChevronUp size={16} /> : <ChevronDown size={16} />}
            </button>
            {showEvidence && (
              <pre className="risk-evidence">{JSON.stringify(alert.evidence || {}, null, 2)}</pre>
            )}
          </section>

          <section className="risk-drawer-section">
            <h3><BrainCircuit size={18} /> AI-assisted investigation</h3>
            <p className="risk-muted">The AI explains stored evidence. Python remains the source of all calculations and totals.</p>
            <button type="button" className="primary-btn risk-inline-btn" onClick={runInvestigation} disabled={investigating}>
              {investigating ? <Loader2 size={16} className="spin" /> : <Radar size={16} />}
              {investigating ? 'Investigating…' : 'Investigate with AI'}
            </button>
            {investigation && (
              <div className="risk-investigation">
                <div className="risk-ai-answer">{investigation.ai?.answer || 'No AI response available.'}</div>
                {investigation.ai?.fallback && (
                  <div className="risk-inline-warning">AI assistance is currently unavailable. Review the deterministic facts below.</div>
                )}
                <div className="risk-investigation-grid">
                  <div>
                    <h4>Verified facts</h4>
                    <ul>{investigation.verified_facts?.map((item) => <li key={item}>{item}</li>)}</ul>
                    <h4>Hypotheses to test</h4>
                    <ul>{investigation.hypotheses?.map((item) => <li key={item}>{item}</li>)}</ul>
                  </div>
                  <div>
                    <h4>Missing evidence</h4>
                    {investigation.missing_evidence?.length
                      ? <ul>{investigation.missing_evidence.map((item) => <li key={item}>{item}</li>)}</ul>
                      : <p className="risk-muted">No missing evidence was identified in the stored records.</p>}
                    <h4>Recommended review steps</h4>
                    <ul>{investigation.recommended_review_steps?.map((item) => <li key={item}>{item}</li>)}</ul>
                  </div>
                </div>
                <p className="risk-muted">{investigation.notice}</p>
              </div>
            )}
          </section>

          <section className="risk-drawer-section">
            <h3><UserCheck size={18} /> Human review</h3>
            <div className="risk-form-grid">
              <label>
                <span>Review status</span>
                <select value={reviewStatus} onChange={(event) => setReviewStatus(event.target.value)}>
                  <option value="open">Open</option>
                  <option value="investigating">Investigating</option>
                  <option value="resolved">Resolved</option>
                  <option value="dismissed">Dismissed</option>
                </select>
              </label>
              <label>
                <span>Reviewer</span>
                <input value={reviewerName} onChange={(event) => setReviewerName(event.target.value)} placeholder="Reviewer name" />
              </label>
              <label className="risk-form-wide">
                <span>Assign to</span>
                <input value={assignedTo} onChange={(event) => setAssignedTo(event.target.value)} placeholder="Team or analyst" />
              </label>
              <label className="risk-form-wide">
                <span>Review comment</span>
                <textarea value={comment} onChange={(event) => setComment(event.target.value)} rows={3} placeholder="Document evidence reviewed and the conclusion." />
              </label>
            </div>
            <button type="button" className="primary-btn risk-inline-btn" onClick={saveReview} disabled={saving}>
              {saving ? <Loader2 size={16} className="spin" /> : <CheckCircle2 size={16} />}
              {saving ? 'Saving…' : 'Save review'}
            </button>
          </section>

          <section className="risk-drawer-section">
            <h3><MessageSquare size={18} /> Review history</h3>
            {alert.events?.length ? (
              <ol className="risk-timeline">
                {alert.events.map((event) => (
                  <li key={event.id}>
                    <span className="risk-timeline-dot" />
                    <div>
                      <strong>{event.actor}</strong> {event.action} · {fmtDate(event.created_at)}
                      <div className="risk-muted">{event.previous_status || '—'} → {event.new_status || '—'}</div>
                      {event.comment && <p>{event.comment}</p>}
                    </div>
                  </li>
                ))}
              </ol>
            ) : (
              <p className="risk-muted">No review actions recorded yet.</p>
            )}
          </section>
        </div>
      </aside>
    </div>
  );
}

export default function RiskIntelligence({ apiBase = 'http://127.0.0.1:8000' }) {
  const [overview, setOverview] = useState(null);
  const [alertsData, setAlertsData] = useState({ items: [], total: 0, categories: [] });
  const [channels, setChannels] = useState(null);
  const [trends, setTrends] = useState(null);
  const [config, setConfig] = useState(null);
  const [refreshSignal, setRefreshSignal] = useState(0);
  const [loading, setLoading] = useState(true);
  const [loadingAlerts, setLoadingAlerts] = useState(true);
  const [analyzing, setAnalyzing] = useState(false);
  const [error, setError] = useState('');
  const [selectedAlert, setSelectedAlert] = useState(null);
  const [filters, setFilters] = useState({
    q: '',
    severity: '',
    category: '',
    status: '',
    start_date: '',
    end_date: ''
  });
  const [offset, setOffset] = useState(0);
  const [granularity, setGranularity] = useState('daily');
  const limit = 25;

  const loadOverview = async () => {
    setLoading(true);
    setError('');
    try {
      const [overviewData, channelData, trendData, configData] = await Promise.all([
        riskApi(apiBase, '/api/risk/overview'),
        riskApi(apiBase, '/api/risk/channels'),
        riskApi(apiBase, `/api/risk/trends?granularity=${granularity}`),
        riskApi(apiBase, '/api/risk/config')
      ]);
      setOverview(overviewData);
      setChannels(channelData);
      setTrends(trendData);
      setConfig(configData);
    } catch (err) {
      setError(err.message);
    } finally {
      setLoading(false);
    }
  };

  const loadAlerts = async (nextOffset = offset) => {
    setLoadingAlerts(true);
    setError('');
    try {
      const params = new URLSearchParams({ limit: String(limit), offset: String(nextOffset) });
      Object.entries(filters).forEach(([key, value]) => {
        if (value) params.set(key, value);
      });
      setAlertsData(await riskApi(apiBase, `/api/risk/alerts?${params.toString()}`));
    } catch (err) {
      setError(err.message);
    } finally {
      setLoadingAlerts(false);
    }
  };

  useEffect(() => {
    loadOverview();
  }, [apiBase, granularity]);

  useEffect(() => {
    const timer = setTimeout(() => loadAlerts(offset), 250);
    return () => clearTimeout(timer);
  }, [apiBase, filters, offset]);

  const runAnalysis = async () => {
    setAnalyzing(true);
    setError('');
    try {
      await riskApi(apiBase, '/api/risk/analyze', {
        method: 'POST',
        body: JSON.stringify({ triggered_by: 'dashboard' })
      });
      setOffset(0);
      setRefreshSignal((value) => value + 1);
      await Promise.all([loadOverview(), loadAlerts(0)]);
    } catch (err) {
      setError(err.message);
    } finally {
      setAnalyzing(false);
    }
  };

  const openAlert = async (alert) => {
    try {
      setSelectedAlert(await riskApi(apiBase, `/api/risk/alerts/${alert.id}`));
    } catch (err) {
      setError(err.message);
    }
  };

  const updateFilter = (key, value) => {
    setOffset(0);
    setFilters((current) => ({ ...current, [key]: value }));
  };

  const clearFilters = () => {
    setOffset(0);
    setFilters({ q: '', severity: '', category: '', status: '', start_date: '', end_date: '' });
  };

  const categoryOptions = useMemo(() => {
    if (alertsData.categories?.length) return alertsData.categories;
    return config?.categories || [];
  }, [alertsData.categories, config]);

  const trendSeries = trends?.series || [];
  const channelRows = channels?.channels || [];
  const hasFilters = Object.values(filters).some(Boolean);
  const start = alertsData.total === 0 ? 0 : offset + 1;
  const end = Math.min(offset + limit, alertsData.total);

  const refreshDashboard = () => {
    setRefreshSignal((value) => value + 1);
    return Promise.all([loadOverview(), loadAlerts(offset)]);
  };

  return (
    <section className="risk-page">
      <div className="risk-toolbar">
        <div>
          <div className="risk-toolbar-title"><ShieldAlert size={20} /> Risk operations</div>
          <p>{overview?.last_analysis ? `Last analysis ${fmtDate(overview.last_analysis.finished_at)} · ${fmtNumber(overview.last_analysis.transaction_count)} transactions` : 'No analysis has run yet. Analyze stored records to generate deterministic alerts.'}</p>
        </div>
        <div className="risk-toolbar-actions">
          <button type="button" className="secondary-btn" onClick={refreshDashboard} disabled={loading || loadingAlerts}>
            <RefreshCw size={16} className={loading ? 'spin' : ''} /> Refresh
          </button>
          <button type="button" className="primary-btn" onClick={runAnalysis} disabled={analyzing}>
            {analyzing ? <Loader2 size={16} className="spin" /> : <Radar size={16} />}
            {analyzing ? 'Analyzing…' : 'Run risk analysis'}
          </button>
        </div>
      </div>

      {error && <div className="error-box">{error}</div>}

      <div className="risk-kpi-grid">
        <Kpi label="Total alerts" value={overview ? fmtNumber(overview.total_alerts) : '—'} hint={`${fmtNumber(overview?.unresolved_alerts)} unresolved`} icon={Siren} tone="purple" />
        <Kpi label="Critical" value={overview ? fmtNumber(overview.critical_alerts) : '—'} hint={`${fmtNumber(overview?.high_alerts)} high severity`} icon={AlertOctagon} tone="red" />
        <Kpi label="Flagged value" value={overview ? fmtCurrency(overview.total_flagged_value) : '—'} hint="Sum of stored alert amounts" icon={CircleDollarSign} tone="amber" />
        <Kpi label="Transactions analyzed" value={overview ? fmtNumber(overview.transactions_analyzed) : '—'} hint="Latest snapshot per source file" icon={FileSearch} tone="blue" />
        <Kpi label="Suspicious vendor patterns" value={overview ? fmtNumber(overview.suspicious_vendors) : '—'} hint="Derived from stored alerts" icon={UserCheck} tone="cyan" />
        <Kpi label="Average risk score" value={overview ? `${overview.average_risk_score} / 100` : '—'} hint="Deterministic score across alerts" icon={TrendingUp} tone="green" />
      </div>

      <GeographicRiskIntelligence apiBase={apiBase} refreshSignal={refreshSignal} />

      <div className="risk-grid-2">
        <section className="risk-card">
          <div className="risk-card-header">
            <div>
              <h2>Alert trend</h2>
              <p>Daily alert volume by severity from actual detection timestamps.</p>
            </div>
            <div className="risk-segmented">
              {['daily', 'weekly', 'monthly'].map((item) => (
                <button key={item} type="button" className={granularity === item ? 'active' : ''} onClick={() => setGranularity(item)}>{item}</button>
              ))}
            </div>
          </div>
          {trendSeries.length === 0 ? (
            <EmptyState icon={TrendingUp} title="No trend data" detail="Run risk analysis to populate alert trends." />
          ) : (
            <div className="risk-chart">
              <ResponsiveContainer width="100%" height={260}>
                <AreaChart data={trendSeries} margin={{ top: 10, right: 10, left: -18, bottom: 0 }}>
                  <defs>
                    <linearGradient id="riskCritical" x1="0" y1="0" x2="0" y2="1">
                      <stop offset="5%" stopColor={SEVERITY_COLORS.critical} stopOpacity={0.55} />
                      <stop offset="95%" stopColor={SEVERITY_COLORS.critical} stopOpacity={0.02} />
                    </linearGradient>
                    <linearGradient id="riskHigh" x1="0" y1="0" x2="0" y2="1">
                      <stop offset="5%" stopColor={SEVERITY_COLORS.high} stopOpacity={0.45} />
                      <stop offset="95%" stopColor={SEVERITY_COLORS.high} stopOpacity={0.02} />
                    </linearGradient>
                  </defs>
                  <CartesianGrid stroke="rgba(148,163,184,0.12)" vertical={false} />
                  <XAxis dataKey="label" tick={{ fill: '#7f8da8', fontSize: 11 }} axisLine={false} tickLine={false} minTickGap={24} />
                  <YAxis allowDecimals={false} tick={{ fill: '#7f8da8', fontSize: 11 }} axisLine={false} tickLine={false} />
                  <Tooltip contentStyle={{ background: '#111c30', border: '1px solid #2b3c61', borderRadius: 10, color: '#e8ecf8' }} />
                  <Area type="monotone" dataKey="critical" stackId="1" stroke={SEVERITY_COLORS.critical} fill="url(#riskCritical)" strokeWidth={2} />
                  <Area type="monotone" dataKey="high" stackId="1" stroke={SEVERITY_COLORS.high} fill="url(#riskHigh)" strokeWidth={2} />
                  <Area type="monotone" dataKey="medium" stackId="1" stroke={SEVERITY_COLORS.medium} fill="transparent" strokeWidth={2} />
                  <Area type="monotone" dataKey="low" stackId="1" stroke={SEVERITY_COLORS.low} fill="transparent" strokeWidth={2} />
                </AreaChart>
              </ResponsiveContainer>
            </div>
          )}
        </section>

        <section className="risk-card">
          <div className="risk-card-header">
            <div>
              <h2>Channel risk</h2>
              <p>Transactions and flagged activity by payment channel.</p>
            </div>
            <span className="risk-meta-pill">{fmtNumber(channels?.unknown_channel_count)} unknown</span>
          </div>
          {channelRows.length === 0 ? (
            <EmptyState icon={Radar} title="No channel data" detail="Upload transactions with a channel or payment mode to see channel analytics." />
          ) : (
            <>
              <div className="risk-chart">
                <ResponsiveContainer width="100%" height={220}>
                  <BarChart data={channelRows.slice(0, 8)} margin={{ top: 10, right: 10, left: -18, bottom: 0 }}>
                    <CartesianGrid stroke="rgba(148,163,184,0.12)" vertical={false} />
                    <XAxis dataKey="channel" tick={{ fill: '#7f8da8', fontSize: 10 }} axisLine={false} tickLine={false} interval={0} angle={-16} textAnchor="end" height={48} />
                    <YAxis allowDecimals={false} tick={{ fill: '#7f8da8', fontSize: 11 }} axisLine={false} tickLine={false} />
                    <Tooltip content={<ChannelTooltip />} />
                    <Bar dataKey="transaction_count" name="Transactions" fill="#3b82f6" radius={[5, 5, 0, 0]} />
                    <Bar dataKey="flagged_count" name="Flagged" fill="#8b5cf6" radius={[5, 5, 0, 0]} />
                  </BarChart>
                </ResponsiveContainer>
              </div>
              <div className="risk-channel-list">
                {channelRows.slice(0, 6).map((item) => (
                  <div key={item.channel}>
                    <span>{item.channel}</span>
                    <div className="risk-channel-bar"><span style={{ width: `${Math.min(item.risk_percentage, 100)}%` }} /></div>
                    <strong>{fmtPercent(item.risk_percentage)}</strong>
                  </div>
                ))}
              </div>
            </>
          )}
        </section>
      </div>

      <section className="risk-card risk-alerts-card">
        <div className="risk-card-header risk-alerts-header">
          <div>
            <h2>Risk alerts</h2>
            <p>Search, filter and review anomalies generated from stored records.</p>
          </div>
          <button type="button" className="risk-text-btn" onClick={clearFilters} disabled={!hasFilters}>
            <X size={14} /> Clear filters
          </button>
        </div>

        <div className="risk-filter-bar">
          <label className="risk-search">
            <Search size={16} />
            <input value={filters.q} onChange={(event) => updateFilter('q', event.target.value)} placeholder="Search alert, vendor, transaction…" />
          </label>
          <label>
            <span><Filter size={13} /> Severity</span>
            <select value={filters.severity} onChange={(event) => updateFilter('severity', event.target.value)}>
              <option value="">All severities</option>
              {SEVERITY_ORDER.map((item) => <option key={item} value={item}>{item}</option>)}
            </select>
          </label>
          <label>
            <span>Category</span>
            <select value={filters.category} onChange={(event) => updateFilter('category', event.target.value)}>
              <option value="">All categories</option>
              {categoryOptions.map((item) => <option key={item} value={item}>{item}</option>)}
            </select>
          </label>
          <label>
            <span>Status</span>
            <select value={filters.status} onChange={(event) => updateFilter('status', event.target.value)}>
              <option value="">All statuses</option>
              {(config?.review_statuses || ['open', 'investigating', 'resolved', 'dismissed']).map((item) => <option key={item} value={item}>{item.replaceAll('_', ' ')}</option>)}
            </select>
          </label>
          <label>
            <span>Detected from</span>
            <input type="date" value={filters.start_date} onChange={(event) => updateFilter('start_date', event.target.value)} />
          </label>
          <label>
            <span>Detected to</span>
            <input type="date" value={filters.end_date} onChange={(event) => updateFilter('end_date', event.target.value)} />
          </label>
        </div>

        {loadingAlerts ? (
          <div className="risk-loading"><Loader2 size={22} className="spin" /> Loading alerts…</div>
        ) : alertsData.items.length === 0 ? (
          <EmptyState
            icon={hasFilters ? Filter : ShieldAlert}
            title={hasFilters ? 'No alerts match these filters' : 'No risk alerts yet'}
            detail={hasFilters ? 'Adjust or clear the filters to see more results.' : 'Run risk analysis to evaluate stored transactions, invoices and reconciliations.'}
          />
        ) : (
          <>
            <div className="data-table-wrap">
              <table className="data-table risk-alert-table">
                <thead>
                  <tr>
                    <th>Alert</th>
                    <th>Severity</th>
                    <th>Category</th>
                    <th>Summary</th>
                    <th>Amount</th>
                    <th>Channel</th>
                    <th>Detected</th>
                    <th>Status</th>
                    <th />
                  </tr>
                </thead>
                <tbody>
                  {alertsData.items.map((alert) => (
                    <tr key={alert.id} onClick={() => openAlert(alert)} className="risk-alert-row">
                      <td><span className="risk-alert-code">{alert.alert_id}</span><span className="risk-alert-score">{alert.risk_score}</span></td>
                      <td><SeverityBadge severity={alert.severity} /></td>
                      <td>{alert.risk_category}</td>
                      <td className="risk-summary-cell">{alert.explanation}</td>
                      <td>{alert.amount != null ? fmtCurrency(alert.amount, alert.currency) : '—'}</td>
                      <td>{alert.channel || 'Unknown'}</td>
                      <td>{fmtDate(alert.detected_at)}</td>
                      <td><RiskBadge status={alert.review_status} /></td>
                      <td><button type="button" className="risk-open-btn" onClick={(event) => { event.stopPropagation(); openAlert(alert); }}>Review <ArrowRight size={14} /></button></td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
            <div className="risk-pagination">
              <span>Showing {fmtNumber(start)}–{fmtNumber(end)} of {fmtNumber(alertsData.total)}</span>
              <div>
                <button type="button" className="secondary-btn" onClick={() => setOffset((value) => Math.max(0, value - limit))} disabled={offset === 0}>
                  <ArrowLeft size={15} /> Previous
                </button>
                <button type="button" className="secondary-btn" onClick={() => setOffset((value) => value + limit)} disabled={offset + limit >= alertsData.total}>
                  Next <ArrowRight size={15} />
                </button>
              </div>
            </div>
          </>
        )}
      </section>

      <div className="risk-grid-2 geo-risk-distribution-grid">
        <section className="risk-card">
          <div className="risk-card-header">
            <div>
              <h2>Risk score distribution</h2>
              <p>Configurable severity bands applied to deterministic scores.</p>
            </div>
          </div>
          <div className="risk-bands">
            {(overview?.risk_score_distribution || []).map((band) => {
              const total = overview?.total_alerts || 0;
              const percent = total ? (band.count / total) * 100 : 0;
              const tone = band.name.toLowerCase();
              return (
                <div key={band.name} className="risk-band">
                  <div className="risk-band-label"><strong>{band.name}</strong><span>{band.min}–{band.max}</span><b>{fmtNumber(band.count)}</b></div>
                  <div className="risk-band-track"><span style={{ width: `${percent}%`, background: SEVERITY_COLORS[tone] }} /></div>
                </div>
              );
            })}
          </div>
          {overview?.category_breakdown?.length > 0 && (
            <div className="risk-category-list">
              {overview.category_breakdown.slice(0, 8).map((item) => (
                <div key={item.category}><span>{item.category}</span><strong>{fmtNumber(item.count)}</strong></div>
              ))}
            </div>
          )}
          <div className="risk-config-footer">
            <AlertTriangle size={15} />
            <span>Alert thresholds: low ≤ {config?.thresholds?.low_max ?? 29}, medium ≤ {config?.thresholds?.medium_max ?? 59}, high ≤ {config?.thresholds?.high_max ?? 79}, critical ≥ {config?.risk_bands?.critical?.[0] ?? 80}. Configurable with RISK_* environment variables.</span>
          </div>
        </section>
      </div>

      {selectedAlert && (
        <AlertDetail
          alert={selectedAlert}
          apiBase={apiBase}
          onClose={() => setSelectedAlert(null)}
          onUpdated={(updated) => setSelectedAlert(updated)}
          onReviewSaved={() => Promise.all([loadOverview(), loadAlerts(offset)])}
        />
      )}
    </section>
  );
}
