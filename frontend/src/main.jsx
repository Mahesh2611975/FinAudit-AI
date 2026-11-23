
import React, { useEffect, useMemo, useState } from 'react';
import { createRoot } from 'react-dom/client';
import {
  Activity,
  AlertTriangle,
  ArrowLeftRight,
  BarChart3,
  Bot,
  BrainCircuit,
  Calendar,
  CheckCircle2,
  ChevronDown,
  ChevronLeft,
  ChevronRight,
  ChevronsLeft,
  ChevronsRight,
  CircleDollarSign,
  Clock3,
  Download,
  FileSearch,
  FileSpreadsheet,
  FileText,
  Filter,
  GitCompare,
  Globe2,
  History,
  LayoutDashboard,
  Loader2,
  Menu,
  PackageCheck,
  PieChart as PieChartIcon,
  Plus,
  Receipt,
  RefreshCw,
  Search,
  Settings,
  ShieldAlert,
  ShieldCheck,
  ShoppingCart,
  Sparkles,
  Store,
  Upload,
  Users,
  X
} from 'lucide-react';
import {
  Area,
  AreaChart,
  Bar,
  BarChart,
  CartesianGrid,
  Cell,
  Pie,
  PieChart,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis
} from 'recharts';
import './style.css';
import './workspace.css';
import RiskIntelligence from './RiskIntelligence';
import GeographicRiskIntelligence from './GeographicRiskIntelligence';
import Dashboard from './Dashboard';
import Transactions from './Transactions';
import Assistant from './Assistant';

const API = (
  import.meta.env.VITE_API_URL || 'http://127.0.0.1:8000'
).trim().replace(/\/+$/, '').replace(/\/api$/, '');

// Health polling: the lightweight /api/health probe is fast, but a transient
// failure (cold start, brief network hiccup) must not leave the sidebar stuck
// on "Backend offline" for a full polling interval while data loads normally.
const HEALTH_TIMEOUT_MS = 8000;
const HEALTH_POLL_MS = 30000;
const HEALTH_RETRY_MS = 5000;

const INVOICE_NAV_ITEMS = [
  { id: 'invoice-management', label: 'Invoice Management', icon: Receipt },
  { id: 'purchase-orders', label: 'Purchase Orders', icon: ShoppingCart },
  { id: 'goods-receipts', label: 'Goods Receipts', icon: PackageCheck },
  { id: 'invoice-matching', label: 'Invoice Matching', icon: GitCompare },
  { id: 'invoice-discrepancies', label: 'Discrepancies', icon: AlertTriangle },
  { id: 'vendor-intelligence', label: 'Vendor Intelligence', icon: Store },
  { id: 'invoice-agent-center', label: 'Invoice Agent Center', icon: Bot },
  { id: 'invoice-analytics', label: 'Invoice Analytics', icon: BarChart3 }
];

// Enterprise sidebar structure (active items map to existing, working pages/APIs).
// Unimplemented workflows are intentionally excluded from active navigation.
const SIDEBAR_GROUPS = [
  {
    id: 'workspace',
    label: 'Workspace',
    items: [
      { navId: 'dashboard', view: 'overview', label: 'Dashboard', icon: LayoutDashboard },
      { navId: 'transactions', view: 'transactions', label: 'Transactions', icon: FileSpreadsheet },
      { navId: 'reconciliation', view: 'reconciliation', label: 'Reconciliation', icon: ArrowLeftRight }
    ]
  },
  {
    id: 'risk-intelligence',
    label: 'Risk & Intelligence',
    items: [
      { navId: 'risk-intelligence', view: 'risk-intelligence', label: 'Risk Intelligence', icon: ShieldAlert },
      { navId: 'geographic-intelligence', view: 'geographic-intelligence', label: 'Geographic Intelligence', icon: Globe2 },
      { navId: 'vendor-intelligence', view: 'vendor-intelligence', label: 'Vendor Intelligence', icon: Store }
    ]
  },
  {
    id: 'investigation-center',
    label: 'Investigation Center',
    items: [
      { navId: 'investigation-cases', view: 'cases', label: 'Investigation Cases', icon: Users },
      { navId: 'ai-investigation-assistant', view: 'assistant', label: 'AI Investigation Assistant', icon: Bot },
      { navId: 'agent-execution-history', view: 'agents', label: 'Agent Execution History', icon: History }
    ]
  },
  {
    id: 'evidence-documents',
    label: 'Evidence & Documents',
    items: []
  },
  {
    id: 'governance',
    label: 'Governance',
    items: [
      { navId: 'reports-exports', view: 'reports', label: 'Reports & Exports', icon: FileText }
    ]
  },
  {
    id: 'administration',
    label: 'Administration',
    items: [
      { navId: 'settings', view: 'settings', label: 'Settings', icon: Settings }
    ]
  }
];

const SIDEBAR_NAV_ITEMS = SIDEBAR_GROUPS.flatMap((group) => group.items);

// Preserve all existing routable views (including secondary/invoice workspaces).
const ALL_NAV_ITEMS = [
  ...SIDEBAR_NAV_ITEMS.map((item) => ({ id: item.view })),
  ...INVOICE_NAV_ITEMS,
  { id: 'analytics' },
  { id: 'monitoring' },
  { id: 'investigations' },
  { id: 'compliance' }
];

const ROUTABLE_VIEW_IDS = new Set(ALL_NAV_ITEMS.map((item) => item.id));

const PRIMARY_SIDEBAR_VIEWS = new Set(SIDEBAR_NAV_ITEMS.map((item) => item.view));

const SECONDARY_VIEWS = new Set([
  'analytics',
  'monitoring',
  'investigations',
  'compliance',
  ...INVOICE_NAV_ITEMS.filter((item) => !PRIMARY_SIDEBAR_VIEWS.has(item.id)).map((item) => item.id)
]);

const VIEW_ROUTE_ALIASES = {
  dashboard: 'overview',
  overview: 'overview'
};

const viewToHash = (viewId) => `#/${viewId || 'overview'}`;

const viewFromHash = () => {
  if (typeof window === 'undefined') return 'overview';
  const raw = window.location.hash || '';
  const cleaned = raw.replace(/^#\/?/, '').split('?')[0].split('/')[0].trim();
  if (!cleaned) return 'overview';
  const resolved = VIEW_ROUTE_ALIASES[cleaned] || cleaned;
  return ROUTABLE_VIEW_IDS.has(resolved) ? resolved : 'overview';
};


const RECON_STEPS = [
  'Select Files',
  'Review & Match',
  'Resolve Exceptions',
  'Reconcile',
  'Complete'
];

const RISK_COLORS = {
  high: '#ef4444',
  medium: '#f59e0b',
  low: '#22c55e'
};

const fmtCurrency = (amount) =>
  new Intl.NumberFormat('en-IN', {
    style: 'currency',
    currency: 'INR',
    maximumFractionDigits: 2
  }).format(Number(amount) || 0);

const fmtNumber = (value) => new Intl.NumberFormat('en-IN').format(Number(value) || 0);

const fmtMinor = (minor, currency = 'INR') => {
  const code = String(currency || 'INR').toUpperCase();
  if (!/^[A-Z]{3}$/.test(code)) {
    return `${code} ${((Number(minor) || 0) / 100).toFixed(2)}`;
  }
  try {
    return new Intl.NumberFormat('en-IN', {
      style: 'currency',
      currency: code,
      maximumFractionDigits: 2
    }).format((Number(minor) || 0) / 100);
  } catch {
    return `${code} ${((Number(minor) || 0) / 100).toFixed(2)}`;
  }
};

async function invoiceApi(path, options = {}) {
  const response = await fetch(`${API}${path}`, options);
  const data = await response.json().catch(() => ({}));
  if (!response.ok) {
    const detail = Array.isArray(data.detail) ? data.detail.join(' ') : data.detail;
    const nested = detail && typeof detail === 'object' ? detail.errors?.join(' ') || detail.message : detail;
    throw new Error(nested || 'Request failed.');
  }
  return data;
}

function formatDateTime(value) {
  if (!value) return '—';
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return value;
  return date.toLocaleString();
}

function StatusBadge({ status }) {
  const label = String(status || '').replaceAll('_', ' ');
  if (['completed', 'matched', 'reconciled', 'paid', 'complete', 'normal', 'clear', 'no_discrepancies', 'extracted', 'resolved'].includes(status)) {
    return <span className="badge badge-matched">{label}</span>;
  }
  if (status === 'failed') {
    return <span className="badge badge-high">{label}</span>;
  }
  if (['needs_human_review', 'completed_with_review', 'partially_paid', 'partial', 'needs_review', 'review', 'needs_more_evidence', 'possible', 'open', 'confirmed'].includes(status)) {
    return <span className="badge badge-medium">{label}</span>;
  }
  if (status === 'overpaid' || status === 'mismatched' || status === 'missing' || status === 'duplicate') {
    return <span className="badge badge-high">{label}</span>;
  }
  if (status === 'unpaid') {
    return <span className="badge badge-unmatched">{label}</span>;
  }
  if (status === 'accepted_risk') {
    return <span className="badge badge-info">{label}</span>;
  }
  if (status === 'suggested') {
    return <span className="badge badge-suggested">{label}</span>;
  }
  if (status === 'unmatched' || status === 'bank_only' || status === 'ledger_only') {
    return <span className="badge badge-unmatched">{label}</span>;
  }
  if (status === 'amount_mismatch' || status === 'date_mismatch') {
    return <span className="badge badge-mismatch">{label}</span>;
  }
  if (status === 'ambiguous') {
    return <span className="badge badge-info">{label}</span>;
  }
  if (status === 'high') {
    return <span className="badge badge-high">{label}</span>;
  }
  if (status === 'medium') {
    return <span className="badge badge-medium">{label}</span>;
  }
  if (status === 'low') {
    return <span className="badge badge-low">{label}</span>;
  }
  return <span className="badge badge-neutral">{label}</span>;
}

function PlaceholderPage({ title, description, icon: Icon }) {
  return (
    <section className="panel placeholder-panel">
      <div className="empty-icon">
        <Icon size={28} />
      </div>
      <h2>{title}</h2>
      <p>{description}</p>
      <span className="kpi-subtle">
        This section is not implemented in the current codebase. UI structure is
        in place for future expansion.
      </span>
    </section>
  );
}

function OverviewPage({ audits, reconciliations, loading, onAuditCreated, onNavigateInvestigations }) {
  const latestAudit = audits?.[0];
  const latestRecon = reconciliations?.[0];
  const findings = latestAudit?.findings || [];
  const reconSummary = latestRecon?.summary || {};
  const [uploadFile, setUploadFile] = useState(null);
  const [uploading, setUploading] = useState(false);
  const [uploadError, setUploadError] = useState('');
  const [uploadSuccess, setUploadSuccess] = useState('');
  const fileInputRef = React.useRef(null);

  const totalTransactions = audits?.reduce(
    (sum, item) => sum + (Number(item.row_count) || 0),
    0
  );
  const totalFindings = audits?.reduce(
    (sum, item) => sum + (Number(item.finding_count) || 0),
    0
  );
  const unmatched = (reconSummary.bank_unmatched_total || 0) + (reconSummary.ledger_unmatched_total || 0);
  const totalRows = reconSummary.bank_total + reconSummary.ledger_total;
  const reconciledCount = reconSummary.matched_count || 0;
  const reconciliationRate =
    totalRows > 0 ? Math.round((reconciledCount / totalRows) * 100) : null;

  const riskBuckets = [
    {
      name: 'High',
      value: findings.filter((item) => item.risk_level === 'high').length,
      color: RISK_COLORS.high
    },
    {
      name: 'Medium',
      value: findings.filter((item) => item.risk_level === 'medium').length,
      color: RISK_COLORS.medium
    },
    {
      name: 'Low',
      value: findings.filter((item) => item.risk_level === 'low').length,
      color: RISK_COLORS.low
    }
  ];
  const riskData = riskBuckets.filter((item) => item.value > 0);
  if (riskData.length === 0 && findings.length === 0) {
    riskData.push({ name: 'No data', value: 1, color: '#475569' });
  }

  const trendData = (audits || [])
    .slice(0, 6)
    .reverse()
    .map((item, index) => ({
      name: `Run ${index + 1}`,
      rows: Number(item.row_count) || 0,
      findings: Number(item.finding_count) || 0
    }));

  const reconStatusData = [
    {
      name: 'Matched',
      value: reconSummary.matched_count || 0,
      color: '#22c55e'
    },
    {
      name: 'Unmatched',
      value:
        (reconSummary.bank_only_count || 0) +
        (reconSummary.ledger_only_count || 0),
      color: '#f59e0b'
    },
    {
      name: 'Mismatch',
      value:
        (reconSummary.amount_mismatch_count || 0) +
        (reconSummary.date_mismatch_count || 0),
      color: '#ef4444'
    },
    {
      name: 'Ambiguous',
      value: reconSummary.ambiguous_count || 0,
      color: '#a78bfa'
    }
  ].filter((item) => item.value > 0);
  if (reconStatusData.length === 0) {
    reconStatusData.push({ name: 'No data', value: 1, color: '#475569' });
  }

  return (
    <>
      <div className="kpi-grid">
        <article className="kpi-card">
          <div className="kpi-top">
            <div className="kpi-label">
              <span>Transactions analyzed</span>
              <strong>{totalTransactions ? fmtNumber(totalTransactions) : '—'}</strong>
            </div>
            <div className="kpi-icon blue">
              <FileSearch size={20} />
            </div>
          </div>
          <span className="kpi-subtle">
            {audits?.length ? `${audits.length} audit run(s)` : 'No audit runs yet'}
          </span>
        </article>

        <article className="kpi-card">
          <div className="kpi-top">
            <div className="kpi-label">
              <span>Flagged findings</span>
              <strong>{totalFindings ? fmtNumber(totalFindings) : '—'}</strong>
            </div>
            <div className="kpi-icon amber">
              <AlertTriangle size={20} />
            </div>
          </div>
          <span className="kpi-subtle">Rule-based exceptions requiring review</span>
        </article>

        <article className="kpi-card">
          <div className="kpi-top">
            <div className="kpi-label">
              <span>Unmatched transactions</span>
              <strong>{unmatched ? fmtCurrency(unmatched) : '—'}</strong>
            </div>
            <div className="kpi-icon cyan">
              <CircleDollarSign size={20} />
            </div>
          </div>
          <span className="kpi-subtle">
            {latestRecon ? 'From latest reconciliation run' : 'No reconciliation run yet'}
          </span>
        </article>

        <article className="kpi-card">
          <div className="kpi-top">
            <div className="kpi-label">
              <span>Reconciliation rate</span>
              <strong>{reconciliationRate == null ? '—' : `${reconciliationRate}%`}</strong>
            </div>
            <div className="kpi-icon green">
              <CheckCircle2 size={20} />
            </div>
          </div>
          <span className="kpi-subtle">
            {reconciledCount ? `${fmtNumber(reconciledCount)} matched result(s)` : 'No matches yet'}
          </span>
        </article>
      </div>

      <div className="quick-actions">
        <button
          className="action-card"
          type="button"
          onClick={() => fileInputRef.current?.click()}
          onKeyDown={(event) => {
            if (event.key === 'Enter' || event.key === ' ') {
              event.preventDefault();
              fileInputRef.current?.click();
            }
          }}
          aria-label="Upload statement CSV for audit"
        >
          <div className="icon-wrap">
            <Upload size={20} />
          </div>
          <div>
            <strong>Upload statement</strong>
            <span>Run a transaction audit from the overview workspace</span>
          </div>
        </button>
        <input
          ref={fileInputRef}
          type="file"
          accept=".csv,text/csv"
          style={{ display: 'none' }}
          onChange={(event) => {
            const selected = event.target.files?.[0] || null;
            setUploadError('');
            setUploadSuccess('');
            if (selected && !selected.name.toLowerCase().endsWith('.csv')) {
              setUploadError('Please select a .csv file.');
              setUploadFile(null);
              event.target.value = '';
              return;
            }
            setUploadFile(selected);
            event.target.value = '';
          }}
        />
        {uploadFile && (
          <div
            className="panel"
            style={{
              gridColumn: '1 / -1',
              padding: '14px 18px',
              display: 'flex',
              alignItems: 'center',
              justifyContent: 'space-between',
              gap: 12,
              flexWrap: 'wrap'
            }}
          >
            <div style={{ display: 'flex', alignItems: 'center', gap: 10 }}>
              <FileText size={18} />
              <span>{uploadFile.name}</span>
            </div>
            <div style={{ display: 'flex', alignItems: 'center', gap: 10 }}>
              <button
                className="secondary-btn"
                type="button"
                onClick={() => {
                  setUploadFile(null);
                  setUploadError('');
                  setUploadSuccess('');
                }}
                disabled={uploading}
              >
                Cancel
              </button>
              <button
                className="primary-btn"
                type="button"
                disabled={uploading}
                onClick={async () => {
                  if (!uploadFile) {
                    setUploadError('Select a .csv file to upload.');
                    return;
                  }
                  setUploading(true);
                  setUploadError('');
                  setUploadSuccess('');
                  try {
                    const formData = new FormData();
                    formData.append('files', uploadFile);
                    // Re-uploading the same statement must not create duplicate rows.
                    formData.append('skip_duplicates', 'true');
                    const response = await fetch(`${API}/api/audits`, {
                      method: 'POST',
                      body: formData
                    });
                    const data = await response.json();
                    if (!response.ok) {
                      const detail = Array.isArray(data.detail)
                        ? data.detail.join(' ')
                        : data.detail;
                      throw new Error(detail || 'Audit upload failed.');
                    }
                    setUploadSuccess(
                      `Audit #${data.audit_id || data.id} created with ${data.finding_count} finding(s).`
                    );
                    setUploadFile(null);
                    await onAuditCreated?.();
                    if (onNavigateInvestigations) {
                      onNavigateInvestigations();
                    }
                  } catch (err) {
                    setUploadError(err.message || 'Could not connect to the backend.');
                  } finally {
                    setUploading(false);
                  }
                }}
              >
                {uploading ? (
                  <>
                    <RefreshCw size={16} className="spin" />
                    Uploading...
                  </>
                ) : (
                  <>
                    <Upload size={16} />
                    Run Audit
                  </>
                )}
              </button>
            </div>
          </div>
        )}
        {uploadError && (
          <div className="error-box" style={{ gridColumn: '1 / -1' }}>
            {uploadError}
          </div>
        )}
        {uploadSuccess && (
          <div
            className="warning-box"
            style={{ gridColumn: '1 / -1', color: '#86efac', borderColor: 'rgba(34,197,94,0.4)', background: 'rgba(34,197,94,0.16)' }}
          >
            {uploadSuccess}
          </div>
        )}
        <button className="action-card" type="button" onClick={() => document.dispatchEvent(new CustomEvent('finaudit:navigate', { detail: 'reconciliation' }))}>
          <div className="icon-wrap">
            <ArrowLeftRight size={20} />
          </div>
          <div>
            <strong>Start reconciliation</strong>
            <span>Match bank and ledger CSV files</span>
          </div>
        </button>
        <button className="action-card" type="button" onClick={() => document.dispatchEvent(new CustomEvent('finaudit:navigate', { detail: 'investigations' }))}>
          <div className="icon-wrap">
            <FileSearch size={20} />
          </div>
          <div>
            <strong>Inspect findings</strong>
            <span>Review exceptions from the latest audit</span>
          </div>
        </button>
      </div>

      <div className="dashboard-grid">
        <section className="chart-card">
          <div className="section-header">
            <div>
              <h2>Transaction activity trend</h2>
              <p>Rows reviewed and findings per recent audit run</p>
            </div>
          </div>
          {trendData.length === 0 ? (
            <div className="empty-state">
              <div className="empty-icon">
                <BarChart3 size={24} />
              </div>
              <strong>No activity yet</strong>
              <span>Run an audit to see trend data.</span>
            </div>
          ) : (
            <div className="chart-wrap">
              <ResponsiveContainer width="100%" height="100%">
                <AreaChart data={trendData}>
                  <defs>
                    <linearGradient id="rows" x1="0" y1="0" x2="0" y2="1">
                      <stop offset="5%" stopColor="#3b82f6" stopOpacity={0.28} />
                      <stop offset="95%" stopColor="#3b82f6" stopOpacity={0} />
                    </linearGradient>
                    <linearGradient id="findings" x1="0" y1="0" x2="0" y2="1">
                      <stop offset="5%" stopColor="#f59e0b" stopOpacity={0.24} />
                      <stop offset="95%" stopColor="#f59e0b" stopOpacity={0} />
                    </linearGradient>
                  </defs>
                  <CartesianGrid stroke="rgba(148,163,184,0.14)" vertical={false} />
                  <XAxis dataKey="name" stroke="#64748b" fontSize={11} />
                  <YAxis stroke="#64748b" fontSize={11} />
                  <Tooltip
                    contentStyle={{
                      background: 'rgba(15,23,42,0.96)',
                      border: '1px solid rgba(148,163,184,0.28)',
                      borderRadius: 10,
                      color: '#e8ecf8'
                    }}
                  />
                  <Area type="monotone" dataKey="rows" stroke="#3b82f6" fill="url(#rows)" strokeWidth={2} />
                  <Area type="monotone" dataKey="findings" stroke="#f59e0b" fill="url(#findings)" strokeWidth={2} />
                </AreaChart>
              </ResponsiveContainer>
            </div>
          )}
        </section>

        <div style={{ display: 'grid', gap: 18 }}>
          <section className="chart-card">
            <div className="section-header">
              <div>
                <h2>Risk distribution</h2>
                <p>Findings by risk level from latest audit</p>
              </div>
            </div>
            {findings.length === 0 ? (
              <div className="empty-state">
                <div className="empty-icon">
                  <ShieldCheck size={24} />
                </div>
                <strong>No findings</strong>
                <span>Run an audit to populate risk distribution.</span>
              </div>
            ) : (
              <div style={{ display: 'grid', gridTemplateColumns: 'minmax(0,1fr) minmax(0,1fr)', gap: 16, alignItems: 'center' }}>
                <div className="chart-wrap" style={{ height: 220 }}>
                  <ResponsiveContainer width="100%" height="100%">
                    <PieChart>
                      <Pie data={riskData} dataKey="value" innerRadius={58} outerRadius={84} paddingAngle={4}>
                        {riskData.map((entry) => (
                          <Cell key={entry.name} fill={entry.color} />
                        ))}
                      </Pie>
                      <Tooltip
                        contentStyle={{
                          background: 'rgba(15,23,42,0.96)',
                          border: '1px solid rgba(148,163,184,0.28)',
                          borderRadius: 10,
                          color: '#e8ecf8'
                        }}
                      />
                    </PieChart>
                  </ResponsiveContainer>
                </div>
                <div style={{ display: 'flex', flexDirection: 'column', gap: 12 }}>
                  {riskBuckets.map((item) => (
                    <div key={item.name} style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between' }}>
                      <div style={{ display: 'flex', alignItems: 'center', gap: 10 }}>
                        <span style={{ width: 8, height: 8, borderRadius: '50%', background: item.color }} />
                        <span>{item.name} risk</span>
                      </div>
                      <span style={{ color: 'var(--dash-text-primary)' }}>{fmtNumber(item.value)}</span>
                    </div>
                  ))}
                </div>
              </div>
            )}
          </section>

          <section className="chart-card">
            <div className="section-header">
              <div>
                <h2>Reconciliation status</h2>
                <p>Breakdown from latest reconciliation run</p>
              </div>
            </div>
            {!latestRecon ? (
              <div className="empty-state">
                <div className="empty-icon">
                  <ArrowLeftRight size={24} />
                </div>
                <strong>No reconciliation data</strong>
                <span>Start a reconciliation to see status breakdown.</span>
              </div>
            ) : (
              <div className="chart-wrap" style={{ height: 220 }}>
                <ResponsiveContainer width="100%" height="100%">
                  <BarChart data={reconStatusData}>
                    <CartesianGrid stroke="rgba(148,163,184,0.14)" vertical={false} />
                    <XAxis dataKey="name" stroke="#64748b" fontSize={11} />
                    <YAxis stroke="#64748b" fontSize={11} allowDecimals={false} />
                    <Tooltip
                      contentStyle={{
                        background: 'rgba(15,23,42,0.96)',
                        border: '1px solid rgba(148,163,184,0.28)',
                        borderRadius: 10,
                        color: '#e8ecf8'
                      }}
                    />
                    <Bar dataKey="value" radius={[8, 8, 0, 0]}>
                      {reconStatusData.map((entry) => (
                        <Cell key={entry.name} fill={entry.color} />
                      ))}
                    </Bar>
                  </BarChart>
                </ResponsiveContainer>
              </div>
            )}
          </section>
        </div>
      </div>

      <div className="dashboard-grid">
        <section className="table-card">
          <div className="section-header">
            <div>
              <h2>Recent audit findings</h2>
              <p>Latest exceptions from the most recent audit run</p>
            </div>
          </div>
          {findings.length === 0 ? (
            <div className="empty-state">
              <div className="empty-icon">
                <FileSearch size={24} />
              </div>
              <strong>No findings available</strong>
              <span>Upload transaction CSV files and run an audit.</span>
            </div>
          ) : (
            <div className="data-table-wrap">
              <table className="data-table">
                <thead>
                  <tr>
                    <th>Finding</th>
                    <th>Transaction</th>
                    <th>Source file</th>
                    <th>Amount</th>
                    <th>Risk</th>
                  </tr>
                </thead>
                <tbody>
                  {findings.slice(0, 6).map((finding) => (
                    <tr key={finding.id}>
                      <td className="table-primary">{finding.summary}</td>
                      <td>{finding.transaction_id || '—'}</td>
                      <td>{finding.source_file || '—'}</td>
                      <td>{fmtCurrency(finding.amount)}</td>
                      <td>
                        <StatusBadge status={finding.risk_level} />
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}
        </section>

        <section className="table-card">
          <div className="section-header">
            <div>
              <h2>Recent reconciliation runs</h2>
              <p>Saved reconciliations from the backend</p>
            </div>
          </div>
          {(reconciliations || []).length === 0 ? (
            <div className="empty-state">
              <div className="empty-icon">
                <ArrowLeftRight size={24} />
              </div>
              <strong>No reconciliations yet</strong>
              <span>Match a bank statement and ledger to get started.</span>
            </div>
          ) : (
            <div className="data-table-wrap">
              <table className="data-table">
                <thead>
                  <tr>
                    <th>Files</th>
                    <th>Matched</th>
                    <th>Difference</th>
                    <th>Created</th>
                  </tr>
                </thead>
                <tbody>
                  {reconciliations.slice(0, 6).map((item) => (
                    <tr key={item.id}>
                      <td className="table-primary">
                        {(item.files || [item.bank_file, item.ledger_file])
                          .filter(Boolean)
                          .join(' ↔ ')}
                      </td>
                      <td>{fmtNumber(item.summary?.matched_count || 0)}</td>
                      <td>{fmtCurrency(item.summary?.difference || 0)}</td>
                      <td>{formatDateTime(item.created_at)}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}
        </section>
      </div>
    </>
  );
}

function ReconciliationPage({
  reconciliation,
  setReconciliation,
  history,
  loadHistory
}) {
  const [bankFile, setBankFile] = useState(null);
  const [ledgerFile, setLedgerFile] = useState(null);
  const [dateTolerance, setDateTolerance] = useState(3);
  const [amountTolerance, setAmountTolerance] = useState('0.005');
  const [loading, setLoading] = useState(false);
  const [previewLoading, setPreviewLoading] = useState(false);
  const [actionLoadingId, setActionLoadingId] = useState(null);
  const [error, setError] = useState('');
  const [warnings, setWarnings] = useState([]);
  const [preview, setPreview] = useState(null);
  const [filter, setFilter] = useState('all');
  const [search, setSearch] = useState('');
  const [showMapping, setShowMapping] = useState(false);
  const [reviewer, setReviewer] = useState('reviewer');
  const [activeStep, setActiveStep] = useState(0);
  const [selectedRunId, setSelectedRunId] = useState(null);
  const [historyLoadingId, setHistoryLoadingId] = useState(null);
  const [manualBankId, setManualBankId] = useState('');
  const [manualLedgerId, setManualLedgerId] = useState('');
  const [manualNote, setManualNote] = useState('');

  const MAPPING_FIELDS = ['date', 'amount', 'debit', 'credit', 'reference', 'description'];
  const emptyMapping = () => MAPPING_FIELDS.reduce((acc, key) => ({ ...acc, [key]: '' }), {});
  const [bankMapping, setBankMapping] = useState(emptyMapping());
  const [ledgerMapping, setLedgerMapping] = useState(emptyMapping());

  const mappingJson = (mapping) => {
    const cleaned = Object.fromEntries(
      Object.entries(mapping).filter(([, value]) => String(value || '').trim())
    );
    return Object.keys(cleaned).length ? JSON.stringify(cleaned) : '';
  };

  const results = reconciliation?.results || [];
  const summary = reconciliation?.summary || {};
  const reviewActions = reconciliation?.review_actions || [];
  const runId = reconciliation?.reconciliation_id || reconciliation?.run_id || reconciliation?.id || null;

  useEffect(() => {
    if (reconciliation) {
      setSelectedRunId(runId);
      setWarnings(Array.isArray(reconciliation.warnings) ? reconciliation.warnings : []);
      if (activeStep < 2) setActiveStep(2);
    }
  }, [reconciliation, runId]);

  const stepIndex = reconciliation
    ? Math.max(activeStep, 2)
    : preview
    ? 1
    : bankFile && ledgerFile
    ? 1
    : 0;

  const buildFormData = () => {
    const formData = new FormData();
    formData.append('bank_file', bankFile);
    formData.append('ledger_file', ledgerFile);
    formData.append('date_tolerance_days', String(dateTolerance));
    formData.append('amount_tolerance', String(amountTolerance || '0'));
    const bankMappingJson = mappingJson(bankMapping);
    const ledgerMappingJson = mappingJson(ledgerMapping);
    if (bankMappingJson) formData.append('bank_mapping', bankMappingJson);
    if (ledgerMappingJson) formData.append('ledger_mapping', ledgerMappingJson);
    return formData;
  };

  const validateFiles = () => {
    if (!bankFile || !ledgerFile) {
      setError('Select both a bank statement CSV and an accounting ledger CSV.');
      return false;
    }
    if (!String(bankFile.name || '').toLowerCase().endsWith('.csv') || !String(ledgerFile.name || '').toLowerCase().endsWith('.csv')) {
      setError('Both bank and ledger uploads must be .csv files.');
      return false;
    }
    if (Number(dateTolerance) < 0) {
      setError('Date tolerance must be a non-negative whole number.');
      return false;
    }
    if (Number(amountTolerance) < 0 || Number.isNaN(Number(amountTolerance))) {
      setError('Amount tolerance must be a non-negative number.');
      return false;
    }
    return true;
  };

  const chooseFile = (side, event) => {
    const selected = event.target.files?.[0] || null;
    if (selected && !selected.name.toLowerCase().endsWith('.csv')) {
      setError(`${side === 'bank' ? 'Bank statement' : 'Accounting ledger'} must be a .csv file.`);
      return;
    }
    setError('');
    setPreview(null);
    setReconciliation?.(null);
    setActiveStep(0);
    if (side === 'bank') setBankFile(selected);
    else setLedgerFile(selected);
  };

  const runPreview = async () => {
    if (!validateFiles()) return;
    setPreviewLoading(true);
    setError('');
    setWarnings([]);
    try {
      const response = await fetch(`${API}/api/reconciliations/preview`, {
        method: 'POST',
        body: buildFormData(),
      });
      const data = await response.json();
      if (!response.ok) {
        const detail = Array.isArray(data.detail) ? data.detail.join(' ') : data.detail;
        throw new Error(detail || 'Preview failed.');
      }
      setPreview(data);
      setWarnings(Array.isArray(data.warnings) ? data.warnings : []);
      setActiveStep(1);
    } catch (err) {
      setError(err.message || 'Could not preview reconciliation.');
    } finally {
      setPreviewLoading(false);
    }
  };

  const runReconciliation = async () => {
    if (!validateFiles()) return;
    setLoading(true);
    setError('');
    setWarnings([]);
    setPreview(null);
    try {
      const response = await fetch(`${API}/api/reconciliations`, {
        method: 'POST',
        body: buildFormData(),
      });
      const data = await response.json();
      if (!response.ok) {
        const detail = Array.isArray(data.detail) ? data.detail.join(' ') : data.detail;
        throw new Error(detail || 'Reconciliation failed.');
      }
      setReconciliation(data);
      setSelectedRunId(data.reconciliation_id || data.run_id || data.id);
      setWarnings(Array.isArray(data.warnings) ? data.warnings : []);
      setActiveStep(2);
      await loadHistory?.();
    } catch (err) {
      setError(err.message || 'Could not create reconciliation run.');
    } finally {
      setLoading(false);
    }
  };

  const loadRun = async (id) => {
    if (!id) return;
    setHistoryLoadingId(id);
    setError('');
    try {
      const response = await fetch(`${API}/api/reconciliations/${id}`);
      const data = await response.json();
      if (!response.ok) throw new Error(data.detail || 'Could not load reconciliation run.');
      setReconciliation(data);
      setSelectedRunId(id);
      setPreview(null);
      setActiveStep(2);
    } catch (err) {
      setError(err.message || 'Could not load reconciliation run.');
    } finally {
      setHistoryLoadingId(null);
    }
  };

  const refreshCurrentRun = async () => {
    if (!runId) return;
    await loadRun(runId);
    await loadHistory?.();
  };

  const doReviewAction = async (resultId, action, extra = {}) => {
    if (!reviewer.trim()) {
      setError('Enter a reviewer name before recording a review action.');
      return;
    }
    setActionLoadingId(resultId || 'manual');
    setError('');
    try {
      const response = await fetch(`${API}/api/reconciliations/results/${resultId}/review`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ action, reviewer: reviewer.trim(), ...extra }),
      });
      const data = await response.json();
      if (!response.ok) throw new Error(data.detail || 'Could not record review action.');
      await refreshCurrentRun();
    } catch (err) {
      setError(err.message || 'Could not record review action.');
    } finally {
      setActionLoadingId(null);
    }
  };

  const doManualLink = async () => {
    if (!runId) return;
    if (!reviewer.trim()) {
      setError('Enter a reviewer name before manually linking records.');
      return;
    }
    if (!manualBankId || !manualLedgerId) {
      setError('Select both a bank record and a ledger record to link manually.');
      return;
    }
    setActionLoadingId('manual');
    setError('');
    try {
      const response = await fetch(`${API}/api/reconciliations/${runId}/manual-link`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          reviewer: reviewer.trim(),
          bank_result_id: Number(manualBankId),
          ledger_result_id: Number(manualLedgerId),
          note: manualNote || undefined,
        }),
      });
      const data = await response.json();
      if (!response.ok) throw new Error(data.detail || 'Could not manually link records.');
      setManualBankId('');
      setManualLedgerId('');
      setManualNote('');
      await refreshCurrentRun();
    } catch (err) {
      setError(err.message || 'Could not manually link records.');
    } finally {
      setActionLoadingId(null);
    }
  };

  const statusLabel = (status) => String(status || '').replaceAll('_', ' ');

  const filtered = useMemo(() => {
    const q = search.trim().toLowerCase();
    return results.filter((item) => {
      const status = item.status || '';
      const reviewStatus = item.review_status || '';
      let statusMatch = true;
      if (filter === 'all') statusMatch = true;
      else if (filter === 'unmatched') statusMatch = ['bank_only', 'ledger_only'].includes(status);
      else if (filter === 'exceptions') statusMatch = ['amount_mismatch', 'date_mismatch', 'ambiguous', 'duplicate_candidate', 'invalid_records'].includes(status);
      else if (filter === 'pending_review') statusMatch = ['amount_mismatch', 'date_mismatch', 'ambiguous'].includes(status) && !['confirmed', 'rejected', 'explained', 'linked'].includes(reviewStatus);
      else if (filter === 'confirmed') statusMatch = reviewStatus === 'confirmed' || item.is_confirmed_match;
      else statusMatch = status === filter;

      if (!statusMatch) return false;
      if (!q) return true;
      const hay = [
        item.bank_date, item.ledger_date, item.bank_description, item.ledger_description,
        item.bank_reference, item.ledger_reference, item.bank_source_file, item.ledger_source_file,
        item.status, item.review_status, item.match_method, item.explanation, item.note,
        item.bank_amount, item.ledger_amount, item.amount_difference, item.date_difference_days,
      ].map((v) => String(v ?? '').toLowerCase()).join(' ');
      return hay.includes(q);
    });
  }, [results, filter, search]);

  const exportHref = runId ? `${API}/api/reconciliations/${runId}/export.csv` : null;

  const bankLinkCandidates = results.filter((item) => item.bank_source_file && !item.ledger_source_file && !item.is_confirmed_match && !['linked', 'confirmed', 'explained'].includes(item.review_status) && ['bank_only', 'ambiguous', 'amount_mismatch', 'date_mismatch'].includes(item.status));
  const ledgerLinkCandidates = results.filter((item) => item.ledger_source_file && !item.bank_source_file && !item.is_confirmed_match && !['linked', 'confirmed', 'explained'].includes(item.review_status) && ['ledger_only', 'ambiguous', 'amount_mismatch', 'date_mismatch'].includes(item.status));

  const summaryCards = [
    { label: 'Match rate', value: summary.match_rate == null ? '—' : `${summary.match_rate}%`, hint: 'Confirmed matched records', tone: 'matched' },
    { label: 'Bank-only', value: fmtNumber(summary.bank_only_count || 0), hint: 'No ledger match', tone: 'unmatched' },
    { label: 'Ledger-only', value: fmtNumber(summary.ledger_only_count || 0), hint: 'No bank match', tone: 'unmatched' },
    { label: 'Amount mismatch', value: fmtNumber(summary.amount_mismatch_count || 0), hint: 'Review required', tone: 'suggested' },
    { label: 'Date mismatch', value: fmtNumber(summary.date_mismatch_count || 0), hint: 'Review required', tone: 'suggested' },
    { label: 'Ambiguous', value: fmtNumber(summary.ambiguous_count || 0), hint: 'Human review only', tone: 'suggested' },
    { label: 'Unreconciled value', value: fmtCurrency(summary.unreconciled_value ?? summary.unmatched_total ?? 0), hint: 'Not reconciled', tone: 'unmatched' },
    { label: 'Total difference', value: fmtCurrency(summary.total_difference ?? summary.difference ?? 0), hint: 'Bank − ledger', tone: 'reconciled' },
  ];

  return (
    <>
      <div className="breadcrumb">
        <span>Bank Reconciliation</span>
        <ChevronRight size={14} />
        <span className="breadcrumb-current">Workspace</span>
      </div>

      <header className="page-header">
        <div>
          <h1>Bank Statement Reconciliation</h1>
          <p>Import bank and ledger CSVs independently, preview validation and proposed matching, review exceptions, and persist run history with audit actions.</p>
        </div>
        <div style={{ display: 'flex', gap: 10, flexWrap: 'wrap' }}>
          {runId && <button className="secondary-btn" type="button" onClick={refreshCurrentRun}><RefreshCw size={16} /> Refresh run</button>}
          {exportHref && <a className="secondary-btn" href={exportHref}><Download size={16} /> Export CSV</a>}
        </div>
      </header>

      <div className="stepper">
        {RECON_STEPS.map((label, index) => (
          <div key={label} className={`step-item ${index === stepIndex ? 'active' : ''} ${index < stepIndex ? 'completed' : ''}`}>
            <div className="step-circle">{index < stepIndex ? <CheckCircle2 size={16} /> : index + 1}</div>
            <span>{label}</span>
          </div>
        ))}
      </div>

      <div className="rec-grid">
        <section className="panel upload-card">
          <div className="section-header"><div><h2>1. Select Files — Bank Statement</h2><p>Independent bank CSV upload and column mapping</p></div></div>
          <label className="dropzone-light">
            <input type="file" accept=".csv,text/csv" onChange={(event) => chooseFile('bank', event)} />
            <div className="dropzone-icon"><Upload size={22} /></div>
            <strong>{bankFile ? bankFile.name : 'Drop or browse bank CSV'}</strong>
            <span className="kpi-subtle">Supports date/amount/debit/credit/reference/description columns</span>
          </label>
        </section>

        <section className="panel upload-card">
          <div className="section-header"><div><h2>1. Select Files — Accounting Ledger</h2><p>Independent ledger CSV upload and column mapping</p></div></div>
          <label className="dropzone-light">
            <input type="file" accept=".csv,text/csv" onChange={(event) => chooseFile('ledger', event)} />
            <div className="dropzone-icon"><FileText size={22} /></div>
            <strong>{ledgerFile ? ledgerFile.name : 'Drop or browse ledger CSV'}</strong>
            <span className="kpi-subtle">Original source file and row are preserved in results</span>
          </label>
        </section>
      </div>

      <section className="panel" style={{ padding: '16px 26px', marginBottom: 22 }}>
        <button className="text-btn" type="button" onClick={() => setShowMapping((value) => !value)}>
          {showMapping ? 'Hide' : 'Show'} independent column mapping (optional)
        </button>
        <p className="kpi-subtle">Map columns separately for bank and ledger files. Leave blank to auto-detect common names.</p>
        {showMapping && (
          <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit,minmax(280px,1fr))', gap: 18, marginTop: 12 }}>
            {[
              { label: 'Bank statement', value: bankMapping, set: setBankMapping },
              { label: 'Accounting ledger', value: ledgerMapping, set: setLedgerMapping },
            ].map(({ label, value, set }) => (
              <div key={label}>
                <h3 style={{ fontSize: 13, marginBottom: 8 }}>{label} columns</h3>
                <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 6 }}>
                  {MAPPING_FIELDS.map((key) => (
                    <label key={key} style={{ fontSize: 12, color: 'var(--rec-text-secondary)' }}>
                      {key}
                      <input
                        placeholder={key}
                        value={value[key] || ''}
                        onChange={(event) => set({ ...value, [key]: event.target.value })}
                        style={{ width: '100%', marginTop: 3, padding: '6px 8px', borderRadius: 6, border: '1px solid var(--rec-border-strong)', background: 'var(--rec-surface)' }}
                      />
                    </label>
                  ))}
                </div>
              </div>
            ))}
          </div>
        )}
      </section>

      <section className="panel" style={{ padding: '18px 26px', marginBottom: 22 }}>
        <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', gap: 16, flexWrap: 'wrap' }}>
          <div style={{ display: 'flex', alignItems: 'center', gap: 14, flexWrap: 'wrap' }}>
            <label style={{ display: 'flex', alignItems: 'center', gap: 10, fontSize: 13, color: 'var(--rec-text-secondary)' }}>
              Date tolerance
              <input type="number" min="0" max="365" value={dateTolerance} onChange={(event) => setDateTolerance(event.target.value)} style={{ width: 90, padding: '8px 10px', borderRadius: 8, border: '1px solid var(--rec-border-strong)', background: 'var(--rec-surface)' }} /> days
            </label>
            <label style={{ display: 'flex', alignItems: 'center', gap: 10, fontSize: 13, color: 'var(--rec-text-secondary)' }}>
              Amount tolerance
              <input type="number" min="0" step="0.001" value={amountTolerance} onChange={(event) => setAmountTolerance(event.target.value)} style={{ width: 110, padding: '8px 10px', borderRadius: 8, border: '1px solid var(--rec-border-strong)', background: 'var(--rec-surface)' }} />
            </label>
            <label style={{ display: 'flex', alignItems: 'center', gap: 10, fontSize: 13, color: 'var(--rec-text-secondary)' }}>
              Reviewer
              <input value={reviewer} onChange={(event) => setReviewer(event.target.value)} placeholder="Reviewer name" style={{ padding: '8px 10px', borderRadius: 8, border: '1px solid var(--rec-border-strong)', background: 'var(--rec-surface)' }} />
            </label>
          </div>
          <div style={{ display: 'flex', gap: 10, flexWrap: 'wrap' }}>
            <button className="secondary-btn" type="button" onClick={runPreview} disabled={!bankFile || !ledgerFile || previewLoading || loading}>
              {previewLoading ? <><RefreshCw size={16} className="spin" /> Previewing...</> : <><Search size={16} /> 2. Preview & Validate</>}
            </button>
            <button className="primary-btn" type="button" onClick={runReconciliation} disabled={!bankFile || !ledgerFile || loading || previewLoading}>
              {loading ? <><RefreshCw size={16} className="spin" /> Running reconciliation...</> : <><ArrowLeftRight size={16} /> 4. Reconcile</>}
            </button>
          </div>
        </div>
        <p className="kpi-subtle" style={{ marginTop: 10 }}>Preview validates mappings, normalization, missing values, duplicates and proposed matches before persisting a reconciliation run.</p>
        {error && <div className="error-box">{error}</div>}
        {warnings.length > 0 && <div className="warning-box">{warnings.join(' ')}</div>}
      </section>

      {preview && (
        <section className="panel table-card-light" style={{ marginBottom: 22 }}>
          <div className="table-header">
            <div>
              <h2>2. Review & Match (Preview)</h2>
              <p>Validation and proposed matching before persistence. Invalid rows are preserved and never silently discarded.</p>
            </div>
          </div>
          <div className="summary-grid" style={{ marginBottom: 12 }}>
            <article className="panel summary-card"><div className="summary-card-header"><span className="kpi-label"><span>Bank records</span></span></div><strong>{fmtNumber(preview.preview?.bank?.record_count || 0)}</strong><span className="kpi-subtle">Valid {fmtNumber(preview.preview?.bank?.valid_count || 0)} · Invalid {fmtNumber(preview.preview?.bank?.invalid_count || 0)} · Duplicates {fmtNumber(preview.preview?.bank?.duplicate_candidate_count || 0)}</span></article>
            <article className="panel summary-card"><div className="summary-card-header"><span className="kpi-label"><span>Ledger records</span></span></div><strong>{fmtNumber(preview.preview?.ledger?.record_count || 0)}</strong><span className="kpi-subtle">Valid {fmtNumber(preview.preview?.ledger?.valid_count || 0)} · Invalid {fmtNumber(preview.preview?.ledger?.invalid_count || 0)} · Duplicates {fmtNumber(preview.preview?.ledger?.duplicate_candidate_count || 0)}</span></article>
            <article className="panel summary-card matched"><div className="summary-card-header"><span className="kpi-label"><span>Proposed matched</span></span></div><strong>{fmtNumber(preview.proposed_summary?.matched_count || 0)}</strong><span className="kpi-subtle">Ambiguous {fmtNumber(preview.proposed_summary?.ambiguous_count || 0)}</span></article>
            <article className="panel summary-card unmatched"><div className="summary-card-header"><span className="kpi-label"><span>Proposed exceptions</span></span></div><strong>{fmtNumber((preview.proposed_summary?.amount_mismatch_count || 0) + (preview.proposed_summary?.date_mismatch_count || 0) + (preview.proposed_summary?.duplicate_candidate_count || 0) + (preview.proposed_summary?.invalid_record_count || preview.proposed_summary?.invalid_records_count || 0))}</strong><span className="kpi-subtle">Bank-only {fmtNumber(preview.proposed_summary?.bank_only_count || 0)} · Ledger-only {fmtNumber(preview.proposed_summary?.ledger_only_count || 0)}</span></article>
          </div>
          <p className="kpi-subtle">Files: {preview.bank_file} ↔ {preview.ledger_file} · Date tolerance {preview.date_tolerance_days} day(s) · Amount tolerance {preview.amount_tolerance}</p>
        </section>
      )}

      {reconciliation && (
        <>
          <div className="summary-grid" style={{ marginBottom: 22 }}>
            {summaryCards.map((card) => (
              <article key={card.label} className={`panel summary-card ${card.tone}`}>
                <div className="summary-card-header"><span className="kpi-label"><span>{card.label}</span></span></div>
                <strong>{card.value}</strong>
                <span className="kpi-subtle">{card.hint}</span>
              </article>
            ))}
          </div>

          <section className="panel" style={{ padding: '16px 26px', marginBottom: 22 }}>
            <div className="section-header"><div><h2>3. Resolve Exceptions</h2><p>Review suggested/ambiguous matches, confirm or reject, manually link records, mark explained, add notes, or reopen exceptions. Every action is recorded with timestamp and audit history.</p></div></div>
            <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit,minmax(220px,1fr))', gap: 10, alignItems: 'end' }}>
              <label style={{ fontSize: 12, color: 'var(--rec-text-secondary)' }}>Bank record to link
                <select value={manualBankId} onChange={(e) => setManualBankId(e.target.value)} style={{ width: '100%', marginTop: 4, padding: '8px 10px', borderRadius: 8, border: '1px solid var(--rec-border-strong)', background: 'var(--rec-surface)' }}>
                  <option value="">Select bank record</option>
                  {bankLinkCandidates.map((item) => <option key={item.id} value={item.id}>#{item.id} · row {item.bank_source_row} · {item.bank_reference || item.bank_description || '—'} · {fmtCurrency(item.bank_amount || 0)}</option>)}
                </select>
              </label>
              <label style={{ fontSize: 12, color: 'var(--rec-text-secondary)' }}>Ledger record to link
                <select value={manualLedgerId} onChange={(e) => setManualLedgerId(e.target.value)} style={{ width: '100%', marginTop: 4, padding: '8px 10px', borderRadius: 8, border: '1px solid var(--rec-border-strong)', background: 'var(--rec-surface)' }}>
                  <option value="">Select ledger record</option>
                  {ledgerLinkCandidates.map((item) => <option key={item.id} value={item.id}>#{item.id} · row {item.ledger_source_row} · {item.ledger_reference || item.ledger_description || '—'} · {fmtCurrency(item.ledger_amount || 0)}</option>)}
                </select>
              </label>
              <label style={{ fontSize: 12, color: 'var(--rec-text-secondary)' }}>Resolution note
                <input value={manualNote} onChange={(e) => setManualNote(e.target.value)} placeholder="Optional note" style={{ width: '100%', marginTop: 4, padding: '8px 10px', borderRadius: 8, border: '1px solid var(--rec-border-strong)', background: 'var(--rec-surface)' }} />
              </label>
              <button className="primary-btn" type="button" onClick={doManualLink} disabled={actionLoadingId === 'manual' || !manualBankId || !manualLedgerId}><CheckCircle2 size={16} /> {actionLoadingId === 'manual' ? 'Linking...' : 'Manually link records'}</button>
            </div>
          </section>

          <section className="panel table-card-light">
            <div className="table-header" style={{ gap: 12, flexWrap: 'wrap' }}>
              <div>
                <h2>Transactions & Exceptions</h2>
                <p>Run #{runId} · {reconciliation.bank_file} ↔ {reconciliation.ledger_file} · Date tolerance {reconciliation.date_tolerance_days} day(s) · Amount tolerance {reconciliation.amount_tolerance ?? 0.005}</p>
              </div>
              <div style={{ display: 'flex', gap: 8, flexWrap: 'wrap', alignItems: 'center' }}>
                <input value={search} onChange={(e) => setSearch(e.target.value)} placeholder="Search reference, description, file, status..." style={{ minWidth: 220, padding: '8px 10px', borderRadius: 8, border: '1px solid var(--rec-border-strong)', background: 'var(--rec-surface)' }} />
              </div>
            </div>

            <div className="filter-bar">
              {[
                ['all', `All (${results.length})`],
                ['matched', `Matched (${summary.matched_count || 0})`],
                ['bank_only', `Bank only (${summary.bank_only_count || 0})`],
                ['ledger_only', `Ledger only (${summary.ledger_only_count || 0})`],
                ['amount_mismatch', `Amount mismatch (${summary.amount_mismatch_count || 0})`],
                ['date_mismatch', `Date mismatch (${summary.date_mismatch_count || 0})`],
                ['ambiguous', `Ambiguous (${summary.ambiguous_count || 0})`],
                ['duplicate_candidate', `Duplicate (${summary.duplicate_candidate_count || 0})`],
                ['invalid_records', `Invalid (${summary.invalid_record_count || summary.invalid_records_count || 0})`],
                ['pending_review', 'Pending review'],
                ['confirmed', 'Confirmed'],
              ].map(([key, label]) => (
                <button key={key} className={`filter-chip-light ${filter === key ? 'active' : ''}`} type="button" onClick={() => setFilter(key)}>{label}</button>
              ))}
            </div>

            {filtered.length === 0 ? (
              <div className="empty-state">
                <div className="empty-icon"><Filter size={24} /></div>
                <strong>No records in this view</strong>
                <span>Adjust filters or search to inspect other reconciliation results.</span>
              </div>
            ) : (
              <div className="table-scroll">
                <table className="rec-table">
                  <thead>
                    <tr>
                      <th>Status / Review</th>
                      <th>Match method</th>
                      <th>Bank record</th>
                      <th>Ledger record</th>
                      <th>Amount diff</th>
                      <th>Date diff</th>
                      <th>Explanation</th>
                      <th>Actions</th>
                    </tr>
                  </thead>
                  <tbody>
                    {filtered.map((item) => {
                      const paired = Boolean(item.bank_source_file && item.ledger_source_file);
                      const canConfirm = paired && !item.is_confirmed_match && !['invalid_records', 'duplicate_candidate', 'bank_only', 'ledger_only'].includes(item.status) && item.review_status !== 'confirmed';
                      const canReject = ['ambiguous', 'amount_mismatch', 'date_mismatch', 'matched'].includes(item.status) && item.review_status !== 'rejected';
                      const canExplain = ['bank_only', 'ledger_only', 'amount_mismatch', 'date_mismatch', 'ambiguous', 'duplicate_candidate', 'invalid_records'].includes(item.status) && item.review_status !== 'explained';
                      const canReopen = ['confirmed', 'rejected', 'explained', 'linked'].includes(item.review_status);
                      const note = window.prompt ? undefined : undefined; // avoid prompts in render
                      return (
                        <tr key={item.id}>
                          <td>
                            <div style={{ display: 'grid', gap: 4 }}>
                              <StatusBadge status={item.status} />
                              <span className="cell-muted">Review: {statusLabel(item.review_status || 'pending')}</span>
                              {item.is_confirmed_match ? <span className="cell-muted">Confirmed match</span> : null}
                            </div>
                          </td>
                          <td className="cell-muted">{item.match_method || '—'}</td>
                          <td>
                            <div className="source-stack">
                              <span className="cell-primary">{item.bank_date || '—'} · {item.bank_amount == null ? '—' : fmtCurrency(item.bank_amount)}</span>
                              <span className="cell-muted">{item.bank_reference || item.bank_description || '—'}</span>
                              <span className="cell-muted">{item.bank_source_file || '—'} {item.bank_source_row ? `(row ${item.bank_source_row})` : ''}</span>
                            </div>
                          </td>
                          <td>
                            <div className="source-stack">
                              <span className="cell-primary">{item.ledger_date || '—'} · {item.ledger_amount == null ? '—' : fmtCurrency(item.ledger_amount)}</span>
                              <span className="cell-muted">{item.ledger_reference || item.ledger_description || '—'}</span>
                              <span className="cell-muted">{item.ledger_source_file || '—'} {item.ledger_source_row ? `(row ${item.ledger_source_row})` : ''}</span>
                            </div>
                          </td>
                          <td className="cell-muted">{item.amount_difference == null ? '—' : fmtCurrency(item.amount_difference)}</td>
                          <td className="cell-muted">{item.date_difference_days == null ? '—' : `${item.date_difference_days} day(s)`}</td>
                          <td>
                            <div style={{ maxWidth: 340 }}>
                              <div className="cell-primary" style={{ whiteSpace: 'normal' }}>{item.explanation || item.note || '—'}</div>
                              {item.resolution_note ? <div className="cell-muted" style={{ whiteSpace: 'normal', marginTop: 4 }}>Resolution: {item.resolution_note}</div> : null}
                              {item.reviewed_by ? <div className="cell-muted" style={{ marginTop: 4 }}>By {item.reviewed_by}{item.reviewed_at ? ` · ${formatDateTime(item.reviewed_at)}` : ''}</div> : null}
                            </div>
                          </td>
                          <td>
                            <div style={{ display: 'flex', gap: 6, flexWrap: 'wrap' }}>
                              {canConfirm && <button className="text-btn" type="button" disabled={actionLoadingId === item.id} onClick={() => doReviewAction(item.id, 'confirm_match')}>Confirm</button>}
                              {canReject && <button className="text-btn" type="button" disabled={actionLoadingId === item.id} onClick={() => doReviewAction(item.id, 'reject_match')}>Reject</button>}
                              {canExplain && <button className="text-btn" type="button" disabled={actionLoadingId === item.id} onClick={() => { const resolution = window.prompt('Resolution note (optional):', item.resolution_note || ''); doReviewAction(item.id, 'mark_explained', { note: resolution || undefined }); }}>Mark explained</button>}
                              {canReopen && <button className="text-btn" type="button" disabled={actionLoadingId === item.id} onClick={() => doReviewAction(item.id, 'reopen')}>Reopen</button>}
                              <button className="text-btn" type="button" disabled={actionLoadingId === item.id} onClick={() => { const resolution = window.prompt('Add resolution/review note:', item.resolution_note || item.reviewer_comment || ''); if (resolution != null) doReviewAction(item.id, 'add_note', { note: resolution }); }}>Add note</button>
                            </div>
                          </td>
                        </tr>
                      );
                    })}
                  </tbody>
                </table>
              </div>
            )}
          </section>

          <section className="panel" style={{ padding: '16px 26px', marginTop: 22 }}>
            <div className="section-header"><div><h2>5. Complete — Run Summary & Audit History</h2><p>Persisted run details, actual backend summary data, and reviewer action history.</p></div></div>
            <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit,minmax(220px,1fr))', gap: 10, fontSize: 12 }}>
              <div><strong>Run ID:</strong> {runId}</div>
              <div><strong>Status:</strong> {reconciliation.run_status || 'completed'}</div>
              <div><strong>Created:</strong> {formatDateTime(reconciliation.created_at)}</div>
              <div><strong>Bank records:</strong> {fmtNumber(summary.bank_record_count || 0)}</div>
              <div><strong>Ledger records:</strong> {fmtNumber(summary.ledger_record_count || 0)}</div>
              <div><strong>Matched count:</strong> {fmtNumber(summary.matched_count || 0)}</div>
              <div><strong>Unmatched count:</strong> {fmtNumber(summary.unmatched_count || ((summary.bank_only_count || 0) + (summary.ledger_only_count || 0)))}</div>
              <div><strong>Exception count:</strong> {fmtNumber(summary.exception_count || 0)}</div>
              <div><strong>Difference:</strong> {fmtCurrency(summary.total_difference ?? summary.difference ?? 0)}</div>
            </div>
            {reviewActions.length > 0 ? (
              <div style={{ marginTop: 12, display: 'grid', gap: 6 }}>
                <strong style={{ fontSize: 12 }}>Reviewer actions</strong>
                {reviewActions.slice(0, 20).map((action) => (
                  <div key={action.id} style={{ fontSize: 11, opacity: 0.85, borderBottom: '1px solid rgba(148,163,184,.12)', paddingBottom: 4 }}>
                    {formatDateTime(action.created_at)} · {action.actor} · {statusLabel(action.action)}{action.result_id ? ` · result #${action.result_id}` : ''}{action.comment ? ` · ${action.comment}` : ''}
                  </div>
                ))}
              </div>
            ) : <p className="kpi-subtle" style={{ marginTop: 10 }}>No reviewer actions recorded yet for this run.</p>}
          </section>
        </>
      )}

      <section className="panel table-card-light" style={{ marginTop: 22 }}>
        <div className="section-header"><div><h2>Reconciliation History</h2><p>Inspect previous runs without overwriting their results.</p></div><button className="secondary-btn" type="button" onClick={loadHistory}><RefreshCw size={16} /> Refresh history</button></div>
        {!history || history.length === 0 ? (
          <div className="empty-state"><div className="empty-icon"><ArrowLeftRight size={24} /></div><strong>No reconciliation runs yet</strong><span>Create a run to persist reconciliation history.</span></div>
        ) : (
          <div className="data-table-wrap">
            <table className="data-table">
              <thead><tr><th>Run</th><th>Files</th><th>Tolerances</th><th>Matched</th><th>Unmatched</th><th>Exceptions</th><th>Difference</th><th>Created</th><th></th></tr></thead>
              <tbody>
                {history.map((run) => {
                  const rid = run.reconciliation_id || run.run_id || run.id;
                  const s = run.summary || {};
                  return (
                    <tr key={rid}>
                      <td className="table-primary">#{rid}</td>
                      <td>{run.bank_file || '—'} ↔ {run.ledger_file || '—'}</td>
                      <td>{run.date_tolerance_days ?? 0}d / {run.amount_tolerance ?? '—'}</td>
                      <td>{fmtNumber(s.matched_count || 0)}</td>
                      <td>{fmtNumber(s.unmatched_count || ((s.bank_only_count || 0) + (s.ledger_only_count || 0)))}</td>
                      <td>{fmtNumber(s.exception_count || 0)}</td>
                      <td>{fmtCurrency(s.total_difference ?? s.difference ?? 0)}</td>
                      <td>{formatDateTime(run.created_at)}</td>
                      <td><button className="text-btn" type="button" onClick={() => loadRun(rid)} disabled={historyLoadingId === rid}>{historyLoadingId === rid ? 'Loading…' : (selectedRunId === rid ? 'Viewing' : 'Inspect')}</button></td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          </div>
        )}
      </section>
    </>
  );
}


function InvoiceEmpty({ icon: Icon = Receipt, title, detail }) {
  return (
    <div className="empty-state">
      <div className="empty-icon"><Icon size={24} /></div>
      <strong>{title}</strong>
      <span>{detail}</span>
    </div>
  );
}

function InvoiceMessage({ error, message }) {
  if (!error && !message) return null;
  return (
    <div style={{ fontSize: 12, color: error ? '#f87171' : '#34d399', marginTop: 8 }}>
      {error || message}
    </div>
  );
}

function InvoiceManagementPage() {
  const [invoices, setInvoices] = useState([]);
  const [vendors, setVendors] = useState([]);
  const [selected, setSelected] = useState(null);
  const [files, setFiles] = useState([]);
  const [vendorId, setVendorId] = useState('');
  const [loading, setLoading] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');
  const [message, setMessage] = useState('');

  const load = async () => {
    setLoading(true);
    try {
      const [invoiceData, vendorData] = await Promise.all([
        invoiceApi('/api/invoices'),
        invoiceApi('/api/vendors')
      ]);
      setInvoices(invoiceData);
      setVendors(vendorData);
    } catch (err) {
      setError(err.message);
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => { load(); }, []);

  const openInvoice = async (id) => {
    setError('');
    try {
      setSelected(await invoiceApi(`/api/invoices/${id}`));
    } catch (err) {
      setError(err.message);
    }
  };

  const upload = async () => {
    if (!files.length) {
      setError('Select at least one invoice document.');
      return;
    }
    setBusy(true);
    setError('');
    setMessage('');
    try {
      const formData = new FormData();
      files.forEach((file) => formData.append('files', file));
      if (vendorId) formData.append('vendor_id', vendorId);
      const data = await invoiceApi('/api/invoices/upload', { method: 'POST', body: formData });
      setMessage(data.message || 'Invoices uploaded.');
      if (data.errors?.length) setError(data.errors.join(' '));
      setFiles([]);
      await load();
      if (data.invoices?.[0]) await openInvoice(data.invoices[0].id);
    } catch (err) {
      setError(err.message);
    } finally {
      setBusy(false);
    }
  };

  const runAudit = async () => {
    if (!selected) return;
    setBusy(true);
    setError('');
    setMessage('');
    try {
      const result = await invoiceApi(`/api/invoices/${selected.id}/audit`, { method: 'POST' });
      setMessage(`Invoice audit ${result.status}. Review the exceptions before payment.`);
      await load();
      await openInvoice(selected.id);
    } catch (err) {
      setError(err.message);
    } finally {
      setBusy(false);
    }
  };

  return (
    <>
      <section className="panel" style={{ padding: 18, marginBottom: 18, display: 'grid', gap: 10 }}>
        <div className="section-header">
          <div><h2>Upload invoices</h2><p>CSV, Excel or PDF. Original files are hashed and retained locally for evidence.</p></div>
        </div>
        <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit,minmax(220px,1fr))', gap: 10 }}>
          <input type="file" multiple accept=".csv,.xlsx,.xls,.pdf" onChange={(e) => setFiles(Array.from(e.target.files || []))} style={{ padding: 8, borderRadius: 8 }} />
          <select value={vendorId} onChange={(e) => setVendorId(e.target.value)} style={{ padding: 8, borderRadius: 8 }}>
            <option value="">Resolve vendor from invoice</option>
            {vendors.map((vendor) => <option key={vendor.id} value={vendor.id}>{vendor.vendor_name}</option>)}
          </select>
          <button className="primary-btn" type="button" onClick={upload} disabled={busy}>
            {busy ? <Loader2 size={16} className="spin" /> : <Upload size={16} />} Upload & extract
          </button>
        </div>
        <InvoiceMessage error={error} message={message} />
      </section>

      <section className="table-card" style={{ marginBottom: 18 }}>
        <div className="section-header"><div><h2>Invoices</h2><p>{loading ? 'Loading persisted invoices…' : `${invoices.length} invoice(s) stored`}</p></div></div>
        {invoices.length === 0 ? <InvoiceEmpty title="No invoices yet" detail="Upload a CSV, Excel or PDF invoice to begin." /> : (
          <div className="data-table-wrap"><table className="data-table">
            <thead><tr><th>Invoice</th><th>Vendor</th><th>Date</th><th>Total</th><th>Outstanding</th><th>Extraction</th><th>Audit</th><th>Docs</th><th>Open exceptions</th><th /></tr></thead>
            <tbody>{invoices.map((invoice) => (
              <tr key={invoice.id}>
                <td className="table-primary">{invoice.invoice_number || `Invoice ${invoice.id}`}</td>
                <td>{invoice.vendor_name || 'Unresolved'}</td>
                <td>{invoice.invoice_date || '—'}</td>
                <td>{fmtMinor(invoice.total_minor, invoice.currency)}</td>
                <td>{fmtMinor(invoice.outstanding_minor, invoice.currency)}</td>
                <td><StatusBadge status={invoice.extraction_status} /></td>
                <td><StatusBadge status={invoice.audit_status} /></td>
                <td>{invoice.document_count}</td>
                <td>{invoice.open_discrepancy_count}</td>
                <td><button className="text-btn" type="button" onClick={() => openInvoice(invoice.id)}>View</button></td>
              </tr>
            ))}</tbody>
          </table></div>
        )}
      </section>

      {selected && (
        <section className="panel" style={{ padding: 18, display: 'grid', gap: 14 }}>
          <div className="section-header" style={{ display: 'flex', justifyContent: 'space-between', gap: 12, alignItems: 'start' }}>
            <div>
              <h2>{selected.invoice_number || `Invoice ${selected.id}`}</h2>
              <p>{selected.vendor_name || 'Unresolved vendor'} · {selected.invoice_date || 'No date'} · {selected.currency}</p>
            </div>
            <div style={{ display: 'flex', gap: 8 }}>
              <button className="primary-btn" type="button" onClick={runAudit} disabled={busy}>{busy ? <Loader2 size={16} className="spin" /> : <Bot size={16} />} Run invoice audit</button>
              <button className="secondary-btn" type="button" onClick={() => setSelected(null)}><X size={16} /> Close</button>
            </div>
          </div>
          <div className="kpi-grid">
            <article className="kpi-card"><div className="kpi-label"><span>Invoice total</span><strong>{fmtMinor(selected.total_minor, selected.currency)}</strong></div></article>
            <article className="kpi-card"><div className="kpi-label"><span>Paid</span><strong>{fmtMinor(selected.paid_minor, selected.currency)}</strong></div></article>
            <article className="kpi-card"><div className="kpi-label"><span>Outstanding</span><strong>{fmtMinor(selected.outstanding_minor, selected.currency)}</strong></div></article>
            <article className="kpi-card"><div className="kpi-label"><span>Exceptions</span><strong>{selected.discrepancies?.length || 0}</strong></div></article>
          </div>
          <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit,minmax(300px,1fr))', gap: 14 }}>
            <div className="table-card" style={{ padding: 16 }}>
              <div className="section-header"><div><h3>Line items</h3><p>Extracted values with source rows</p></div></div>
              {selected.items?.length ? <div className="data-table-wrap"><table className="data-table"><thead><tr><th>Item</th><th>Qty</th><th>Unit</th><th>Line total</th></tr></thead><tbody>{selected.items.map((item) => <tr key={item.id}><td className="table-primary">{item.item_code || item.description}</td><td>{item.quantity}</td><td>{item.unit_price ?? '—'}</td><td>{fmtMinor(item.line_total_minor, selected.currency)}</td></tr>)}</tbody></table></div> : <InvoiceEmpty title="No line items" detail="The document needs human extraction review." />}
            </div>
            <div className="table-card" style={{ padding: 16 }}>
              <div className="section-header"><div><h3>Documents</h3><p>SHA-256 document provenance</p></div></div>
              {selected.documents?.length ? <div style={{ display: 'grid', gap: 8 }}>{selected.documents.map((doc) => <div key={doc.id} style={{ borderBottom: '1px solid rgba(148,163,184,.14)', paddingBottom: 8 }}><strong>{doc.filename}</strong><div style={{ fontSize: 11, opacity: .75 }}>{doc.file_hash?.slice(0, 24)}… · {doc.extraction_status}</div></div>)}</div> : <InvoiceEmpty title="No document" detail="Attach the original invoice for evidence." />}
            </div>
          </div>
          <div className="table-card" style={{ padding: 16 }}>
            <div className="section-header"><div><h3>Discrepancies</h3><p>Human review required before payment</p></div></div>
            {selected.discrepancies?.length ? <div className="data-table-wrap"><table className="data-table"><thead><tr><th>Type</th><th>Severity</th><th>Expected</th><th>Actual</th><th>Status</th></tr></thead><tbody>{selected.discrepancies.map((item) => <tr key={item.id}><td className="table-primary">{String(item.discrepancy_type).replaceAll('_', ' ')}</td><td><StatusBadge status={item.severity} /></td><td>{item.expected_value || '—'}</td><td>{item.actual_value || '—'}</td><td><StatusBadge status={item.status} /></td></tr>)}</tbody></table></div> : <InvoiceEmpty title="No discrepancies recorded" detail="Run the invoice audit to generate exception checks." />}
          </div>
        </section>
      )}
    </>
  );
}

function PurchaseOrdersPage() {
  const [orders, setOrders] = useState([]);
  const [vendors, setVendors] = useState([]);
  const [selected, setSelected] = useState(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');
  const [message, setMessage] = useState('');
  const [form, setForm] = useState({ purchase_order_number: '', vendor_id: '', vendor_name: '', order_date: '', expected_delivery_date: '', currency: 'INR', notes: '', items: '[{"item_code":"A1","description":"Item","quantity":1,"unit_price":100,"tax_rate":18}]' });

  const load = async () => {
    try {
      const [orderData, vendorData] = await Promise.all([invoiceApi('/api/purchase-orders'), invoiceApi('/api/vendors')]);
      setOrders(orderData);
      setVendors(vendorData);
    } catch (err) { setError(err.message); }
  };
  useEffect(() => { load(); }, []);

  const create = async () => {
    setBusy(true); setError(''); setMessage('');
    try {
      let items;
      try { items = JSON.parse(form.items || '[]'); } catch { throw new Error('Items must be valid JSON.'); }
      const payload = { ...form, items };
      if (!payload.vendor_id) delete payload.vendor_id;
      if (!payload.vendor_name) delete payload.vendor_name;
      const data = await invoiceApi('/api/purchase-orders', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(payload) });
      setMessage(`Purchase order ${data.purchase_order_number} created.`);
      setForm({ ...form, purchase_order_number: '' });
      await load();
      setSelected(data);
    } catch (err) { setError(err.message); }
    finally { setBusy(false); }
  };

  const importFile = async (event) => {
    const file = event.target.files?.[0];
    if (!file) return;
    setBusy(true); setError(''); setMessage('');
    try {
      const formData = new FormData();
      formData.append('file', file);
      const data = await invoiceApi('/api/purchase-orders/import', { method: 'POST', body: formData });
      setMessage(`Imported ${data.imported} purchase order(s).${data.errors?.length ? ` ${data.errors.join(' ')}` : ''}`);
      await load();
    } catch (err) { setError(err.message); }
    finally { setBusy(false); event.target.value = ''; }
  };

  return (
    <>
      <section className="panel" style={{ padding: 18, marginBottom: 18, display: 'grid', gap: 10 }}>
        <div className="section-header"><div><h2>Create purchase order</h2><p>Line items are validated deterministically and stored in minor units.</p></div></div>
        <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit,minmax(190px,1fr))', gap: 10 }}>
          <input placeholder="PO number" value={form.purchase_order_number} onChange={(e) => setForm({ ...form, purchase_order_number: e.target.value })} style={{ padding: 8, borderRadius: 8 }} />
          <select value={form.vendor_id} onChange={(e) => setForm({ ...form, vendor_id: e.target.value })} style={{ padding: 8, borderRadius: 8 }}>
            <option value="">Select vendor</option>
            {vendors.map((vendor) => <option key={vendor.id} value={vendor.id}>{vendor.vendor_name}</option>)}
          </select>
          <input placeholder="Or vendor name" value={form.vendor_name} onChange={(e) => setForm({ ...form, vendor_name: e.target.value })} style={{ padding: 8, borderRadius: 8 }} />
          <input type="date" value={form.order_date} onChange={(e) => setForm({ ...form, order_date: e.target.value })} style={{ padding: 8, borderRadius: 8 }} />
          <input type="date" value={form.expected_delivery_date} onChange={(e) => setForm({ ...form, expected_delivery_date: e.target.value })} style={{ padding: 8, borderRadius: 8 }} />
          <input placeholder="Currency" value={form.currency} onChange={(e) => setForm({ ...form, currency: e.target.value })} style={{ padding: 8, borderRadius: 8 }} />
        </div>
        <textarea value={form.items} onChange={(e) => setForm({ ...form, items: e.target.value })} style={{ padding: 8, borderRadius: 8, minHeight: 90, fontFamily: 'monospace' }} />
        <div style={{ display: 'flex', gap: 10, alignItems: 'center', flexWrap: 'wrap' }}>
          <button className="primary-btn" type="button" onClick={create} disabled={busy}><Plus size={16} /> Create PO</button>
          <label className="secondary-btn" style={{ cursor: 'pointer' }}><FileSpreadsheet size={16} /> Import CSV/Excel<input type="file" accept=".csv,.xlsx,.xls" onChange={importFile} style={{ display: 'none' }} /></label>
          <InvoiceMessage error={error} message={message} />
        </div>
      </section>

      <section className="table-card" style={{ marginBottom: 18 }}>
        <div className="section-header"><div><h2>Purchase orders</h2><p>{orders.length} stored</p></div></div>
        {orders.length === 0 ? <InvoiceEmpty icon={ShoppingCart} title="No purchase orders" detail="Create or import a PO above." /> : (
          <div className="data-table-wrap"><table className="data-table"><thead><tr><th>PO</th><th>Vendor</th><th>Order date</th><th>Total</th><th>Items</th><th>Receipts</th><th>Status</th><th /></tr></thead><tbody>{orders.map((po) => <tr key={po.id}><td className="table-primary">{po.purchase_order_number}</td><td>{po.vendor_name}</td><td>{po.order_date || '—'}</td><td>{fmtMinor(po.total_minor, po.currency)}</td><td>{po.item_count}</td><td>{po.receipt_count}</td><td><StatusBadge status={po.status} /></td><td><button className="text-btn" type="button" onClick={() => setSelected(po)}>View</button></td></tr>)}</tbody></table></div>
        )}
      </section>

      {selected && (
        <section className="panel" style={{ padding: 18 }}>
          <div className="section-header"><div><h2>{selected.purchase_order_number}</h2><p>{selected.vendor_name} · {selected.currency}</p></div></div>
          <div className="data-table-wrap"><table className="data-table"><thead><tr><th>Item</th><th>Description</th><th>Qty</th><th>Unit price</th><th>Tax rate</th><th>Line total</th></tr></thead><tbody>{(selected.items || []).map((item) => <tr key={item.id}><td className="table-primary">{item.item_code || '—'}</td><td>{item.description}</td><td>{item.quantity}</td><td>{item.unit_price ?? '—'}</td><td>{item.tax_rate ?? '—'}</td><td>{fmtMinor(item.line_total_minor, selected.currency)}</td></tr>)}</tbody></table></div>
        </section>
      )}
    </>
  );
}

function GoodsReceiptsPage() {
  const [receipts, setReceipts] = useState([]);
  const [orders, setOrders] = useState([]);
  const [selected, setSelected] = useState(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');
  const [message, setMessage] = useState('');
  const [form, setForm] = useState({ receipt_number: '', purchase_order_id: '', receipt_date: '', received_by: '', items: '[{"item_code":"A1","quantity_received":1,"quantity_accepted":1,"quantity_rejected":0}]' });

  const load = async () => {
    try {
      const [receiptData, orderData] = await Promise.all([invoiceApi('/api/goods-receipts'), invoiceApi('/api/purchase-orders')]);
      setReceipts(receiptData); setOrders(orderData);
    } catch (err) { setError(err.message); }
  };
  useEffect(() => { load(); }, []);

  const create = async () => {
    setBusy(true); setError(''); setMessage('');
    try {
      let items;
      try { items = JSON.parse(form.items || '[]'); } catch { throw new Error('Items must be valid JSON.'); }
      const data = await invoiceApi('/api/goods-receipts', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ ...form, items }) });
      setMessage(`Goods receipt ${data.receipt_number} created.`);
      setForm({ ...form, receipt_number: '' });
      await load(); setSelected(data);
    } catch (err) { setError(err.message); }
    finally { setBusy(false); }
  };

  const importFile = async (event) => {
    const file = event.target.files?.[0];
    if (!file) return;
    setBusy(true); setError(''); setMessage('');
    try {
      const formData = new FormData(); formData.append('file', file);
      const data = await invoiceApi('/api/goods-receipts/import', { method: 'POST', body: formData });
      setMessage(`Imported ${data.imported} goods receipt(s).${data.errors?.length ? ` ${data.errors.join(' ')}` : ''}`);
      await load();
    } catch (err) { setError(err.message); }
    finally { setBusy(false); event.target.value = ''; }
  };

  return (
    <>
      <section className="panel" style={{ padding: 18, marginBottom: 18, display: 'grid', gap: 10 }}>
        <div className="section-header"><div><h2>Record goods receipt</h2><p>Accepted and rejected quantities are compared with the invoice.</p></div></div>
        <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit,minmax(190px,1fr))', gap: 10 }}>
          <input placeholder="Receipt number" value={form.receipt_number} onChange={(e) => setForm({ ...form, receipt_number: e.target.value })} style={{ padding: 8, borderRadius: 8 }} />
          <select value={form.purchase_order_id} onChange={(e) => setForm({ ...form, purchase_order_id: e.target.value })} style={{ padding: 8, borderRadius: 8 }}><option value="">Select PO</option>{orders.map((po) => <option key={po.id} value={po.id}>{po.purchase_order_number} · {po.vendor_name}</option>)}</select>
          <input type="date" value={form.receipt_date} onChange={(e) => setForm({ ...form, receipt_date: e.target.value })} style={{ padding: 8, borderRadius: 8 }} />
          <input placeholder="Received by" value={form.received_by} onChange={(e) => setForm({ ...form, received_by: e.target.value })} style={{ padding: 8, borderRadius: 8 }} />
        </div>
        <textarea value={form.items} onChange={(e) => setForm({ ...form, items: e.target.value })} style={{ padding: 8, borderRadius: 8, minHeight: 90, fontFamily: 'monospace' }} />
        <div style={{ display: 'flex', gap: 10, alignItems: 'center', flexWrap: 'wrap' }}>
          <button className="primary-btn" type="button" onClick={create} disabled={busy}><Plus size={16} /> Create receipt</button>
          <label className="secondary-btn" style={{ cursor: 'pointer' }}><FileSpreadsheet size={16} /> Import CSV/Excel<input type="file" accept=".csv,.xlsx,.xls" onChange={importFile} style={{ display: 'none' }} /></label>
          <InvoiceMessage error={error} message={message} />
        </div>
      </section>

      <section className="table-card" style={{ marginBottom: 18 }}>
        <div className="section-header"><div><h2>Goods receipts</h2><p>{receipts.length} stored</p></div></div>
        {receipts.length === 0 ? <InvoiceEmpty icon={PackageCheck} title="No goods receipts" detail="Record or import a receipt above." /> : <div className="data-table-wrap"><table className="data-table"><thead><tr><th>Receipt</th><th>PO</th><th>Vendor</th><th>Date</th><th>Items</th><th>Status</th><th /></tr></thead><tbody>{receipts.map((receipt) => <tr key={receipt.id}><td className="table-primary">{receipt.receipt_number}</td><td>{receipt.purchase_order_number}</td><td>{receipt.vendor_name}</td><td>{receipt.receipt_date || '—'}</td><td>{receipt.item_count}</td><td><StatusBadge status={receipt.status} /></td><td><button className="text-btn" type="button" onClick={() => setSelected(receipt)}>View</button></td></tr>)}</tbody></table></div>}
      </section>

      {selected && <section className="panel" style={{ padding: 18 }}><div className="section-header"><div><h2>{selected.receipt_number}</h2><p>{selected.purchase_order_number} · {selected.vendor_name}</p></div></div><div className="data-table-wrap"><table className="data-table"><thead><tr><th>Item</th><th>Received</th><th>Accepted</th><th>Rejected</th></tr></thead><tbody>{(selected.items || []).map((item) => <tr key={item.id}><td className="table-primary">{item.item_code || item.description}</td><td>{item.quantity_received}</td><td>{item.quantity_accepted}</td><td>{item.quantity_rejected}</td></tr>)}</tbody></table></div></section>}
    </>
  );
}

function InvoiceMatchingPage() {
  const [invoices, setInvoices] = useState([]);
  const [invoiceId, setInvoiceId] = useState('');
  const [detail, setDetail] = useState(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');
  const [message, setMessage] = useState('');

  useEffect(() => { invoiceApi('/api/invoices').then(setInvoices).catch((err) => setError(err.message)); }, []);
  useEffect(() => {
    if (!invoiceId) { setDetail(null); return; }
    invoiceApi(`/api/invoices/${invoiceId}`).then(setDetail).catch((err) => setError(err.message));
  }, [invoiceId]);

  const runAudit = async () => {
    if (!detail) return;
    setBusy(true); setError(''); setMessage('');
    try {
      const result = await invoiceApi(`/api/invoices/${detail.id}/audit`, { method: 'POST' });
      setMessage(`Audit ${result.status}. ${result.outputs?.financial_analysis?.persisted_discrepancies?.length || 0} exception(s) recorded.`);
      setDetail(await invoiceApi(`/api/invoices/${detail.id}`));
    } catch (err) { setError(err.message); }
    finally { setBusy(false); }
  };

  const goodsMatch = detail?.matches?.find((match) => match.match_type === 'goods_receipt');
  const paymentMatch = detail?.matches?.find((match) => match.match_type === 'payment');
  const poMatch = detail?.matches?.find((match) => match.match_type === 'purchase_order');

  return (
    <>
      <section className="panel" style={{ padding: 18, marginBottom: 18, display: 'grid', gap: 10 }}>
        <div className="section-header"><div><h2>Select invoice</h2><p>Inspect invoice, purchase order, receipt and payment evidence side by side.</p></div></div>
        <div style={{ display: 'flex', gap: 10, flexWrap: 'wrap', alignItems: 'center' }}>
          <select value={invoiceId} onChange={(e) => setInvoiceId(e.target.value)} style={{ padding: 8, borderRadius: 8, minWidth: 280 }}><option value="">Choose an invoice</option>{invoices.map((invoice) => <option key={invoice.id} value={invoice.id}>{invoice.invoice_number || `Invoice ${invoice.id}`} · {invoice.vendor_name || 'Unresolved'}</option>)}</select>
          <button className="primary-btn" type="button" onClick={runAudit} disabled={!detail || busy}>{busy ? <Loader2 size={16} className="spin" /> : <GitCompare size={16} />} Run matching audit</button>
        </div>
        <InvoiceMessage error={error} message={message} />
      </section>

      {!detail ? <section className="panel" style={{ padding: 18 }}><InvoiceEmpty icon={GitCompare} title="No invoice selected" detail="Choose an invoice to view matching evidence." /></section> : (
        <>
          <div className="kpi-grid" style={{ marginBottom: 18 }}>
            <article className="kpi-card"><div className="kpi-label"><span>PO match</span><strong><StatusBadge status={poMatch?.match_status || (detail.purchase_order_id ? 'matched' : 'unmatched')} /></strong></div><span className="kpi-subtle">{detail.purchase_order_number || 'No PO reference'}</span></article>
            <article className="kpi-card"><div className="kpi-label"><span>Goods receipt</span><strong><StatusBadge status={goodsMatch?.match_status || 'unmatched'} /></strong></div><span className="kpi-subtle">{goodsMatch?.details?.receipts?.length || 0} receipt(s)</span></article>
            <article className="kpi-card"><div className="kpi-label"><span>Payment</span><strong><StatusBadge status={paymentMatch?.match_status || 'unmatched'} /></strong></div><span className="kpi-subtle">{fmtMinor(detail.paid_minor, detail.currency)} paid</span></article>
            <article className="kpi-card"><div className="kpi-label"><span>Open exceptions</span><strong>{detail.discrepancies?.filter((item) => item.status === 'open').length || 0}</strong></div><span className="kpi-subtle">Human review required</span></article>
          </div>
          <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit,minmax(320px,1fr))', gap: 14 }}>
            <section className="table-card" style={{ padding: 16 }}><div className="section-header"><div><h3>Invoice</h3><p>{detail.invoice_number || `Invoice ${detail.id}`}</p></div></div><div style={{ display: 'grid', gap: 8, fontSize: 12 }}><div>Vendor: <strong>{detail.vendor_name || 'Unresolved'}</strong></div><div>Date: {detail.invoice_date || '—'}</div><div>Total: {fmtMinor(detail.total_minor, detail.currency)}</div><div>Outstanding: {fmtMinor(detail.outstanding_minor, detail.currency)}</div><div>Extraction: <StatusBadge status={detail.extraction_status} /></div></div></section>
            <section className="table-card" style={{ padding: 16 }}><div className="section-header"><div><h3>Purchase order</h3><p>{detail.purchase_order?.purchase_order_number || 'Not linked'}</p></div></div>{detail.purchase_order ? <div style={{ display: 'grid', gap: 8, fontSize: 12 }}><div>Vendor: <strong>{detail.purchase_order.vendor_name}</strong></div><div>Total: {fmtMinor(detail.purchase_order.total_minor, detail.purchase_order.currency)}</div><div>Status: <StatusBadge status={detail.purchase_order.status} /></div><div>Items: {detail.purchase_order.items?.length || 0}</div></div> : <InvoiceEmpty icon={ShoppingCart} title="No PO linked" detail="A missing PO is recorded as an exception." />}</section>
            <section className="table-card" style={{ padding: 16 }}><div className="section-header"><div><h3>Payments</h3><p>{detail.payments?.length || 0} recorded</p></div></div>{detail.payments?.length ? <div style={{ display: 'grid', gap: 8 }}>{detail.payments.map((payment) => <div key={payment.id} style={{ borderBottom: '1px solid rgba(148,163,184,.14)', paddingBottom: 8 }}><strong>{fmtMinor(payment.amount_minor, payment.currency)}</strong><div style={{ fontSize: 11, opacity: .75 }}>{payment.payment_date || 'No date'} · {payment.payment_reference || 'No reference'} · {payment.status}</div></div>)}</div> : <InvoiceEmpty title="No payments" detail="No verified payment is recorded." />}</section>
          </div>
          <section className="table-card" style={{ padding: 16, marginTop: 14 }}><div className="section-header"><div><h3>Exception detail</h3><p>Deterministic match results and explanations</p></div></div>{detail.discrepancies?.length ? <div className="data-table-wrap"><table className="data-table"><thead><tr><th>Type</th><th>Severity</th><th>Expected</th><th>Actual</th><th>Status</th><th>Explanation</th></tr></thead><tbody>{detail.discrepancies.map((item) => <tr key={item.id}><td className="table-primary">{String(item.discrepancy_type).replaceAll('_', ' ')}</td><td><StatusBadge status={item.severity} /></td><td>{item.expected_value || '—'}</td><td>{item.actual_value || '—'}</td><td><StatusBadge status={item.status} /></td><td>{item.explanation}</td></tr>)}</tbody></table></div> : <InvoiceEmpty title="No exceptions" detail="Run the matching audit to compare all sources." />}</section>
        </>
      )}
    </>
  );
}

function InvoiceDiscrepancyPage() {
  const [items, setItems] = useState([]);
  const [selected, setSelected] = useState(null);
  const [filters, setFilters] = useState({ status: 'open', severity: '', discrepancy_type: '' });
  const [review, setReview] = useState({ reviewer_name: '', decision: 'confirmed', comment: '' });
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');
  const [message, setMessage] = useState('');

  const load = async () => {
    setBusy(true); setError('');
    try {
      const params = new URLSearchParams();
      Object.entries(filters).forEach(([key, value]) => { if (value) params.set(key, value); });
      setItems(await invoiceApi(`/api/discrepancies?${params.toString()}`));
    } catch (err) { setError(err.message); }
    finally { setBusy(false); }
  };
  useEffect(() => { load(); }, []);

  const submitReview = async () => {
    if (!selected) return;
    setError(''); setMessage('');
    try {
      const updated = await invoiceApi(`/api/discrepancies/${selected.id}/review`, { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(review) });
      setSelected(updated);
      setMessage(`Review recorded: ${updated.status}.`);
      setReview({ ...review, comment: '' });
      await load();
    } catch (err) { setError(err.message); }
  };

  return (
    <>
      <section className="panel" style={{ padding: 18, marginBottom: 18, display: 'grid', gap: 10 }}>
        <div className="section-header"><div><h2>Exception queue</h2><p>Review decisions are persisted with reviewer, timestamp and comment.</p></div></div>
        <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit,minmax(180px,1fr))', gap: 10 }}>
          <select value={filters.status} onChange={(e) => setFilters({ ...filters, status: e.target.value })} style={{ padding: 8, borderRadius: 8 }}><option value="">All statuses</option><option value="open">Open</option><option value="confirmed">Confirmed</option><option value="dismissed">Dismissed</option><option value="resolved">Resolved</option><option value="needs_more_evidence">Needs more evidence</option><option value="superseded">Superseded</option></select>
          <select value={filters.severity} onChange={(e) => setFilters({ ...filters, severity: e.target.value })} style={{ padding: 8, borderRadius: 8 }}><option value="">All severities</option><option value="high">High</option><option value="medium">Medium</option><option value="low">Low</option></select>
          <input placeholder="Discrepancy type" value={filters.discrepancy_type} onChange={(e) => setFilters({ ...filters, discrepancy_type: e.target.value })} style={{ padding: 8, borderRadius: 8 }} />
          <button className="secondary-btn" type="button" onClick={load} disabled={busy}>{busy ? <Loader2 size={16} className="spin" /> : <Filter size={16} />} Apply filters</button>
        </div>
        <InvoiceMessage error={error} message={message} />
      </section>

      <section className="table-card" style={{ marginBottom: 18 }}>
        <div className="section-header"><div><h2>Discrepancies</h2><p>{items.length} matching exception(s)</p></div></div>
        {items.length === 0 ? <InvoiceEmpty icon={AlertTriangle} title="No exceptions" detail="Run an invoice audit or adjust the filters." /> : <div className="data-table-wrap"><table className="data-table"><thead><tr><th>Invoice</th><th>Vendor</th><th>Type</th><th>Severity</th><th>Difference</th><th>Status</th><th /></tr></thead><tbody>{items.map((item) => <tr key={item.id}><td className="table-primary">{item.invoice_number || item.invoice_id}</td><td>{item.vendor_name || 'Unresolved'}</td><td>{String(item.discrepancy_type).replaceAll('_', ' ')}</td><td><StatusBadge status={item.severity} /></td><td>{fmtMinor(item.difference_minor, item.currency)}</td><td><StatusBadge status={item.status} /></td><td><button className="text-btn" type="button" onClick={() => setSelected(item)}>Review</button></td></tr>)}</tbody></table></div>}
      </section>

      {selected && (
        <section className="panel" style={{ padding: 18, display: 'grid', gap: 12 }}>
          <div className="section-header"><div><h2>{String(selected.discrepancy_type).replaceAll('_', ' ')}</h2><p>{selected.invoice_number} · {selected.vendor_name}</p></div></div>
          <p style={{ fontSize: 13, color: 'var(--dash-text-secondary)' }}>{selected.explanation}</p>
          <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit,minmax(220px,1fr))', gap: 10, fontSize: 12 }}>
            <div>Expected: <strong>{selected.expected_value || '—'}</strong></div>
            <div>Actual: <strong>{selected.actual_value || '—'}</strong></div>
            <div>Difference: <strong>{fmtMinor(selected.difference_minor, selected.currency)}</strong></div>
            <div>Status: <StatusBadge status={selected.status} /></div>
          </div>
          <pre style={{ maxHeight: 180, overflow: 'auto', padding: 10, borderRadius: 8, background: 'rgba(2,6,23,.35)', fontSize: 11 }}>{JSON.stringify(selected.evidence, null, 2)}</pre>
          <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit,minmax(180px,1fr))', gap: 10 }}>
            <input placeholder="Reviewer name" value={review.reviewer_name} onChange={(e) => setReview({ ...review, reviewer_name: e.target.value })} style={{ padding: 8, borderRadius: 8 }} />
            <select value={review.decision} onChange={(e) => setReview({ ...review, decision: e.target.value })} style={{ padding: 8, borderRadius: 8 }}><option value="confirmed">Confirm exception</option><option value="dismissed">Dismiss</option><option value="resolved">Resolved</option><option value="needs_more_evidence">Needs more evidence</option><option value="accepted_risk">Accepted risk</option></select>
            <input placeholder="Comment" value={review.comment} onChange={(e) => setReview({ ...review, comment: e.target.value })} style={{ padding: 8, borderRadius: 8 }} />
            <button className="primary-btn" type="button" onClick={submitReview}><CheckCircle2 size={16} /> Record decision</button>
          </div>
          {selected.review_actions?.length ? <div><strong style={{ fontSize: 12 }}>Review history</strong>{selected.review_actions.map((action) => <div key={action.id} style={{ fontSize: 11, opacity: .8, marginTop: 4 }}>{formatDateTime(action.created_at)} · {action.reviewer_name} · {action.decision} · {action.comment || 'No comment'}</div>)}</div> : null}
        </section>
      )}
    </>
  );
}

function VendorIntelligencePage() {
  const [analytics, setAnalytics] = useState([]);
  const [form, setForm] = useState({ vendor_name: '', vendor_code: '', currency: 'INR', tax_registration_number: '' });
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');
  const [message, setMessage] = useState('');

  const load = async () => {
    try { setAnalytics(await invoiceApi('/api/vendors/analytics')); } catch (err) { setError(err.message); }
  };
  useEffect(() => { load(); }, []);

  const createVendor = async () => {
    setBusy(true); setError(''); setMessage('');
    try {
      const data = await invoiceApi('/api/vendors', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(form) });
      setMessage(`Vendor ${data.vendor_name} created.`);
      setForm({ vendor_name: '', vendor_code: '', currency: 'INR', tax_registration_number: '' });
      await load();
    } catch (err) { setError(err.message); }
    finally { setBusy(false); }
  };

  return (
    <>
      <section className="panel" style={{ padding: 18, marginBottom: 18, display: 'grid', gap: 10 }}>
        <div className="section-header"><div><h2>Add vendor</h2><p>Vendor master data used by PO, receipt and invoice matching.</p></div></div>
        <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit,minmax(190px,1fr))', gap: 10 }}>
          <input placeholder="Vendor name" value={form.vendor_name} onChange={(e) => setForm({ ...form, vendor_name: e.target.value })} style={{ padding: 8, borderRadius: 8 }} />
          <input placeholder="Vendor code" value={form.vendor_code} onChange={(e) => setForm({ ...form, vendor_code: e.target.value })} style={{ padding: 8, borderRadius: 8 }} />
          <input placeholder="Currency" value={form.currency} onChange={(e) => setForm({ ...form, currency: e.target.value })} style={{ padding: 8, borderRadius: 8 }} />
          <input placeholder="Tax registration" value={form.tax_registration_number} onChange={(e) => setForm({ ...form, tax_registration_number: e.target.value })} style={{ padding: 8, borderRadius: 8 }} />
          <button className="primary-btn" type="button" onClick={createVendor} disabled={busy}>{busy ? <Loader2 size={16} className="spin" /> : <Plus size={16} />} Create vendor</button>
        </div>
        <InvoiceMessage error={error} message={message} />
      </section>

      <section className="table-card">
        <div className="section-header"><div><h2>Vendor intelligence</h2><p>Invoice volume, payment position and exception counts</p></div></div>
        {analytics.length === 0 ? <InvoiceEmpty icon={Store} title="No vendor history" detail="Add a vendor and upload invoices." /> : <div className="data-table-wrap"><table className="data-table"><thead><tr><th>Vendor</th><th>Code</th><th>Invoices</th><th>Total invoiced</th><th>Paid</th><th>Outstanding</th><th>Average invoice</th><th>Open exceptions</th><th>Duplicates</th></tr></thead><tbody>{analytics.map((vendor) => <tr key={vendor.vendor_id}><td className="table-primary">{vendor.vendor_name}</td><td>{vendor.vendor_code || '—'}</td><td>{vendor.invoice_count}</td><td>{fmtMinor(vendor.total_invoiced_minor, vendor.currency)}</td><td>{fmtMinor(vendor.paid_minor, vendor.currency)}</td><td>{fmtMinor(vendor.outstanding_minor, vendor.currency)}</td><td>{fmtMinor(vendor.average_invoice_minor, vendor.currency)}</td><td>{vendor.open_discrepancy_count}</td><td>{vendor.duplicate_count}</td></tr>)}</tbody></table></div>}
      </section>
    </>
  );
}

function InvoiceAgentCenterPage() {
  const [invoices, setInvoices] = useState([]);
  const [runs, setRuns] = useState([]);
  const [invoiceId, setInvoiceId] = useState('');
  const [result, setResult] = useState(null);
  const [selectedRun, setSelectedRun] = useState(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');
  const [message, setMessage] = useState('');

  const load = async () => {
    try {
      const [invoiceData, runData] = await Promise.all([invoiceApi('/api/invoices'), invoiceApi('/api/invoice-audits/runs')]);
      setInvoices(invoiceData); setRuns(runData);
    } catch (err) { setError(err.message); }
  };
  useEffect(() => { load(); }, []);

  const runAudit = async () => {
    if (!invoiceId) { setError('Select an invoice first.'); return; }
    setBusy(true); setError(''); setMessage('');
    try {
      const data = await invoiceApi(`/api/invoices/${invoiceId}/audit`, { method: 'POST' });
      setResult(data);
      setSelectedRun({ id: data.invoice_audit_run_id, status: data.status, workflow_run_id: data.run_id });
      setMessage(`Workflow ${data.status}. ${data.duration_ms} ms.`);
      await load();
    } catch (err) { setError(err.message); }
    finally { setBusy(false); }
  };

  const openRun = async (run) => {
    setError('');
    try {
      const data = await invoiceApi(`/api/invoice-audits/runs/${run.id}`);
      setSelectedRun(data);
      setResult(data.structured_result);
    } catch (err) { setError(err.message); }
  };

  const report = result?.outputs?.report;
  const financial = result?.outputs?.financial_analysis;
  const timeline = result?.timeline || [];
  const evidence = result?.evidence || [];

  return (
    <>
      <section className="panel" style={{ padding: 18, marginBottom: 18, display: 'grid', gap: 10 }}>
        <div className="section-header"><div><h2>Run invoice audit workflow</h2><p>Ten LangGraph nodes with deterministic matching, evidence and a human-review checkpoint.</p></div></div>
        <div style={{ display: 'flex', gap: 10, flexWrap: 'wrap', alignItems: 'center' }}>
          <select value={invoiceId} onChange={(e) => setInvoiceId(e.target.value)} style={{ padding: 8, borderRadius: 8, minWidth: 300 }}><option value="">Choose an invoice</option>{invoices.map((invoice) => <option key={invoice.id} value={invoice.id}>{invoice.invoice_number || `Invoice ${invoice.id}`} · {invoice.vendor_name || 'Unresolved'}</option>)}</select>
          <button className="primary-btn" type="button" onClick={runAudit} disabled={busy}>{busy ? <Loader2 size={16} className="spin" /> : <Bot size={16} />} Run workflow</button>
          {selectedRun?.id && <a className="secondary-btn" href={`${API}/api/invoice-audits/runs/${selectedRun.id}/export.csv`}><Download size={16} /> CSV</a>}
          {selectedRun?.id && <a className="secondary-btn" href={`${API}/api/invoice-audits/runs/${selectedRun.id}/export.pdf`}><FileText size={16} /> PDF</a>}
        </div>
        <InvoiceMessage error={error} message={message} />
      </section>

      {result && (
        <>
          <div className="kpi-grid" style={{ marginBottom: 18 }}>
            <article className="kpi-card"><div className="kpi-label"><span>Workflow status</span><strong><StatusBadge status={result.status} /></strong></div><span className="kpi-subtle">Run {selectedRun?.id || result.invoice_audit_run_id}</span></article>
            <article className="kpi-card"><div className="kpi-label"><span>Outstanding</span><strong>{fmtMinor(financial?.outstanding_minor, financial?.currency)}</strong></div><span className="kpi-subtle">Invoice total {fmtMinor(financial?.invoice_total_minor, financial?.currency)}</span></article>
            <article className="kpi-card"><div className="kpi-label"><span>Discrepancy exposure</span><strong>{fmtMinor(financial?.discrepancy_exposure_minor, financial?.currency)}</strong></div><span className="kpi-subtle">{financial?.high_discrepancy_count || 0} high severity</span></article>
            <article className="kpi-card"><div className="kpi-label"><span>Evidence items</span><strong>{evidence.length}</strong></div><span className="kpi-subtle">Documents, policies and exceptions</span></article>
          </div>

          <section className="table-card" style={{ marginBottom: 18 }}>
            <div className="section-header"><div><h2>Agent timeline</h2><p>Measured execution per node</p></div></div>
            {timeline.length ? <div className="data-table-wrap"><table className="data-table"><thead><tr><th>Agent</th><th>Status</th><th>Duration</th><th>Summary</th></tr></thead><tbody>{timeline.map((item) => <tr key={item.agent_name}><td className="table-primary">{item.agent_name}</td><td><StatusBadge status={item.status} /></td><td>{item.duration_ms} ms</td><td>{item.summary || item.error || '—'}</td></tr>)}</tbody></table></div> : <InvoiceEmpty icon={Bot} title="No timeline" detail="Run the invoice workflow to populate it." />}
          </section>

          {report && <section className="panel" style={{ padding: 18, marginBottom: 18, display: 'grid', gap: 10 }}><div className="section-header"><div><h2>Audit report</h2><p>{report.executive_summary}</p></div></div><div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit,minmax(280px,1fr))', gap: 14 }}><div><strong style={{ fontSize: 12 }}>Facts</strong>{(report.facts || []).map((fact, index) => <div key={index} style={{ fontSize: 12, marginTop: 6 }}>• {fact.type}: {fact.explanation}</div>)}</div><div><strong style={{ fontSize: 12 }}>Hypotheses</strong>{(report.hypotheses || []).map((item, index) => <div key={index} style={{ fontSize: 12, marginTop: 6 }}>• {item}</div>)}</div><div><strong style={{ fontSize: 12 }}>Limitations</strong>{(report.limitations || []).map((item, index) => <div key={index} style={{ fontSize: 12, marginTop: 6 }}>• {item}</div>)}</div></div><p style={{ fontSize: 11, opacity: .75 }}>{report.disclaimer}</p></section>}

          <section className="table-card" style={{ marginBottom: 18 }}><div className="section-header"><div><h2>Persisted discrepancies</h2><p>Review decisions are recorded separately from automated findings.</p></div></div>{financial?.persisted_discrepancies?.length ? <div className="data-table-wrap"><table className="data-table"><thead><tr><th>Type</th><th>Severity</th><th>Expected</th><th>Actual</th><th>Status</th></tr></thead><tbody>{financial.persisted_discrepancies.map((item) => <tr key={item.id}><td className="table-primary">{String(item.discrepancy_type).replaceAll('_', ' ')}</td><td><StatusBadge status={item.severity} /></td><td>{item.expected_value || '—'}</td><td>{item.actual_value || '—'}</td><td><StatusBadge status={item.status} /></td></tr>)}</tbody></table></div> : <InvoiceEmpty title="No discrepancies" detail="No exception was recorded by the deterministic checks." />}</section>

          <section className="table-card"><div className="section-header"><div><h2>Evidence</h2><p>Source documents, policies and exception records</p></div></div>{evidence.length ? <div style={{ display: 'grid', gap: 8 }}>{evidence.map((item, index) => <div key={`${item.source_id}-${index}`} style={{ borderBottom: '1px solid rgba(148,163,184,.14)', paddingBottom: 8 }}><strong style={{ fontSize: 12 }}>{item.source_type} · {item.source_reference || item.source_id}</strong><div style={{ fontSize: 11, opacity: .75 }}>{item.excerpt}</div></div>)}</div> : <InvoiceEmpty title="No evidence" detail="Evidence is collected during the workflow." />}</section>
        </>
      )}

      <section className="table-card" style={{ marginTop: 18 }}>
        <div className="section-header"><div><h2>Run history</h2><p>Persisted invoice audit workflow runs</p></div></div>
        {runs.length === 0 ? <InvoiceEmpty icon={Clock3} title="No runs" detail="Run an invoice audit to create history." /> : <div className="data-table-wrap"><table className="data-table"><thead><tr><th>Run</th><th>Invoice</th><th>Vendor</th><th>Status</th><th>Duration</th><th>Evidence</th><th>Created</th><th /></tr></thead><tbody>{runs.map((run) => <tr key={run.id}><td className="table-primary">{run.id}</td><td>{run.invoice_number || run.invoice_id}</td><td>{run.vendor_name || 'Unresolved'}</td><td><StatusBadge status={run.status} /></td><td>{run.duration_ms ? `${run.duration_ms} ms` : '—'}</td><td>{run.evidence_count}</td><td>{formatDateTime(run.created_at)}</td><td><button className="text-btn" type="button" onClick={() => openRun(run)}>Open</button></td></tr>)}</tbody></table></div>}
      </section>
    </>
  );
}

function InvoiceAnalyticsPage() {
  const now = new Date();
  const [year, setYear] = useState(now.getFullYear());
  const [month, setMonth] = useState(now.getMonth() + 1);
  const [summary, setSummary] = useState(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState('');

  const load = async () => {
    setLoading(true); setError('');
    try { setSummary(await invoiceApi(`/api/invoice-analytics/monthly?year=${year}&month=${month}`)); }
    catch (err) { setError(err.message); }
    finally { setLoading(false); }
  };
  useEffect(() => { load(); }, [year, month]);

  return (
    <>
      <section className="panel" style={{ padding: 18, marginBottom: 18, display: 'flex', gap: 10, alignItems: 'center', flexWrap: 'wrap' }}>
        <select value={year} onChange={(e) => setYear(Number(e.target.value))} style={{ padding: '8px 10px', borderRadius: 8 }}>{[2025, 2026, 2027].map((value) => <option key={value} value={value}>{value}</option>)}</select>
        <select value={month} onChange={(e) => setMonth(Number(e.target.value))} style={{ padding: '8px 10px', borderRadius: 8 }}>{Array.from({ length: 12 }, (_, index) => index + 1).map((value) => <option key={value} value={value}>{value}</option>)}</select>
        <button className="secondary-btn" type="button" onClick={load} disabled={loading}>{loading ? <Loader2 size={16} className="spin" /> : <RefreshCw size={16} />} Refresh</button>
        {error && <span style={{ color: '#f87171', fontSize: 12 }}>{error}</span>}
      </section>

      {summary && (
        <>
          <div className="kpi-grid" style={{ marginBottom: 18 }}>
            <article className="kpi-card"><div className="kpi-label"><span>Invoices</span><strong>{summary.invoice_count}</strong></div><span className="kpi-subtle">{summary.month}</span></article>
            <article className="kpi-card"><div className="kpi-label"><span>Total invoiced</span><strong>{fmtMinor(summary.total_invoiced_minor, summary.currency)}</strong></div><span className="kpi-subtle">Average {fmtMinor(summary.average_invoice_minor, summary.currency)}</span></article>
            <article className="kpi-card"><div className="kpi-label"><span>Outstanding</span><strong>{fmtMinor(summary.outstanding_minor, summary.currency)}</strong></div><span className="kpi-subtle">Paid {fmtMinor(summary.paid_minor, summary.currency)}</span></article>
            <article className="kpi-card"><div className="kpi-label"><span>Exception exposure</span><strong>{fmtMinor(summary.discrepancy_exposure_minor, summary.currency)}</strong></div><span className="kpi-subtle">{summary.open_discrepancy_count} open · {summary.high_severity_count} high</span></article>
          </div>
          <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit,minmax(320px,1fr))', gap: 14 }}>
            <section className="table-card"><div className="section-header"><div><h2>Vendor totals</h2><p>Invoiced amount by vendor</p></div></div>{summary.vendor_totals?.length ? <div className="data-table-wrap"><table className="data-table"><thead><tr><th>Vendor</th><th>Total</th></tr></thead><tbody>{summary.vendor_totals.map((vendor) => <tr key={vendor.vendor_name}><td className="table-primary">{vendor.vendor_name}</td><td>{fmtMinor(vendor.total_minor, summary.currency)}</td></tr>)}</tbody></table></div> : <InvoiceEmpty icon={Store} title="No vendor data" detail="No invoices in this month." />}</section>
            <section className="table-card"><div className="section-header"><div><h2>Discrepancy mix</h2><p>Exception types in this month</p></div></div>{summary.discrepancy_types?.length ? <div className="data-table-wrap"><table className="data-table"><thead><tr><th>Type</th><th>Count</th></tr></thead><tbody>{summary.discrepancy_types.map((item) => <tr key={item.type}><td className="table-primary">{String(item.type).replaceAll('_', ' ')}</td><td>{item.count}</td></tr>)}</tbody></table></div> : <InvoiceEmpty icon={AlertTriangle} title="No exceptions" detail="No discrepancies recorded in this month." />}</section>
          </div>
        </>
      )}
    </>
  );
}

function App() {
  const initialView = viewFromHash();
  const initialNav = SIDEBAR_NAV_ITEMS.find((item) => item.view === initialView)?.navId || (initialView === 'overview' ? 'dashboard' : 'dashboard');
  const [activeView, setActiveViewState] = useState(initialView);
  const [activeNavId, setActiveNavId] = useState(initialNav);
  const [mobileMenuOpen, setMobileMenuOpen] = useState(false);
  const [sidebarCollapsed, setSidebarCollapsed] = useState(false);
  const [expandedGroups, setExpandedGroups] = useState(() =>
    Object.fromEntries(SIDEBAR_GROUPS.map((group) => [group.id, true]))
  );
  const [audits, setAudits] = useState([]);
  const [reconciliations, setReconciliations] = useState([]);
  const [reconciliation, setReconciliation] = useState(null);
  const [loadingOverview, setLoadingOverview] = useState(false);
  const [transactionFocus, setTransactionFocus] = useState('');
  const [backendHealth, setBackendHealth] = useState({
    state: 'checking',
    label: 'Checking backend',
    detail: 'Contacting API…'
  });



  const syncHashForView = (viewId, replace = false) => {
    if (typeof window === 'undefined') return;
    const nextHash = viewToHash(viewId);
    if (window.location.hash === nextHash) return;
    if (replace) {
      window.history.replaceState(null, '', nextHash);
    } else {
      window.history.pushState(null, '', nextHash);
    }
  };

  const setActiveView = (viewId, options = {}) => {
    if (!ROUTABLE_VIEW_IDS.has(viewId)) return;
    const { navId = null, syncHash = true, replaceHash = false } = options;
    setActiveViewState(viewId);
    const resolvedNav = navId || SIDEBAR_NAV_ITEMS.find((item) => item.view === viewId)?.navId || null;
    if (resolvedNav) setActiveNavId(resolvedNav);
    if (syncHash) syncHashForView(viewId, replaceHash);
  };

  useEffect(() => {
    // Ensure URL reflects the active route on first load and supports refresh/direct links.
    syncHashForView(activeView, true);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  useEffect(() => {
    const syncFromUrl = () => {
      const nextView = viewFromHash();
      const nextNav = SIDEBAR_NAV_ITEMS.find((item) => item.view === nextView)?.navId || (nextView === 'overview' ? 'dashboard' : activeNavId);
      setActiveViewState(nextView);
      if (nextNav) setActiveNavId(nextNav);
      setMobileMenuOpen(false);
    };
    // pushState/replaceState do not emit hashchange. Listen to both so browser
    // back/forward also restores the correct independent page.
    window.addEventListener('hashchange', syncFromUrl);
    window.addEventListener('popstate', syncFromUrl);
    return () => {
      window.removeEventListener('hashchange', syncFromUrl);
      window.removeEventListener('popstate', syncFromUrl);
    };
  }, [activeNavId]);

  const themeClass = activeView === 'reconciliation' ? 'theme-reconciliation' : 'theme-dashboard';

  const loadOverview = async () => {
    setLoadingOverview(true);
    try {
      const [auditRes, reconRes] = await Promise.all([
        fetch(`${API}/api/audits`),
        fetch(`${API}/api/reconciliations`)
      ]);
      if (auditRes.ok) {
        const data = await auditRes.json();
        setAudits(Array.isArray(data) ? data : []);
      }
      if (reconRes.ok) {
        const data = await reconRes.json();
        setReconciliations(Array.isArray(data) ? data : []);
      }
    } catch (err) {
      console.error('Overview load error:', err);
    } finally {
      setLoadingOverview(false);
    }
  };

  useEffect(() => {
    loadOverview();
  }, []);

  useEffect(() => {
    let cancelled = false;
    let retryTimer = null;

    const loadHealth = async () => {
      const controller = new AbortController();
      const timeout = window.setTimeout(() => controller.abort(), HEALTH_TIMEOUT_MS);
      let next = {
        state: 'offline',
        label: 'Backend offline',
        detail: 'Unable to reach API'
      };
      try {
        const response = await fetch(`${API}/api/health`, {
          signal: controller.signal,
          cache: 'no-store'
        });
        const data = await response.json().catch(() => ({}));
        if (cancelled) return;
        if (!response.ok) {
          next = {
            state: 'degraded',
            label: 'Backend degraded',
            detail: `Health check returned HTTP ${response.status}`
          };
        } else {
          const status = String(data.status || '').toLowerCase();
          const dbStatus = String(data.database?.status || '').toLowerCase();
          const dbConnected = dbStatus === 'connected';
          const healthy = dbConnected && ['healthy', 'operational'].includes(status);
          next = {
            state: healthy ? 'healthy' : dbConnected ? 'degraded' : 'offline',
            label: healthy ? 'Backend healthy' : dbConnected ? 'Backend degraded' : 'Database unreachable',
            detail: dbConnected
              ? 'API and database reachable'
              : `Database ${data.database?.status || 'unavailable'}`
          };
        }
      } catch (err) {
        if (cancelled) return;
        const timedOut = err?.name === 'AbortError';
        next = {
          state: 'offline',
          label: timedOut ? 'Backend slow/unreachable' : 'Backend offline',
          detail: timedOut ? 'Health check timed out' : 'Unable to reach API'
        };
      } finally {
        window.clearTimeout(timeout);
      }
      if (cancelled) return;
      setBackendHealth(next);
      // Retry quickly after a failure so a transient miss cannot leave a stale
      // offline label while the rest of the dashboard is clearly working.
      retryTimer = window.setTimeout(
        loadHealth,
        next.state === 'healthy' ? HEALTH_POLL_MS : HEALTH_RETRY_MS
      );
    };

    loadHealth();
    return () => {
      cancelled = true;
      if (retryTimer) window.clearTimeout(retryTimer);
    };
  }, []);

  useEffect(() => {
    const match = SIDEBAR_NAV_ITEMS.find((item) => item.view === activeView);
    if (match) setActiveNavId(match.navId);
  }, [activeView]);

  useEffect(() => {
    const handler = (event) => {
      if (typeof event.detail !== 'string') return;
      const target = event.detail;
      if (ROUTABLE_VIEW_IDS.has(target)) {
        setActiveView(target);
        setMobileMenuOpen(false);
        return;
      }
      const navItem = SIDEBAR_NAV_ITEMS.find((item) => item.navId === target);
      if (navItem) {
        setActiveView(navItem.view, { navId: navItem.navId });
        setMobileMenuOpen(false);
      }
    };
    const transactionHandler = (event) => {
      if (typeof event.detail === 'string') {
        setTransactionFocus(`${Date.now()}:${event.detail}`);
        setActiveView('transactions', { navId: 'transactions' });
        setMobileMenuOpen(false);
      }
    };
    document.addEventListener('finaudit:navigate', handler);
    document.addEventListener('finaudit:open-transaction', transactionHandler);
    return () => {
      document.removeEventListener('finaudit:navigate', handler);
      document.removeEventListener('finaudit:open-transaction', transactionHandler);
    };
  }, []);

  const pageTitle = {
    overview: { eyebrow: 'Dashboard', title: 'Financial Intelligence Dashboard', description: 'Database-backed KPIs, five charts and recent evidence from actual records.' },
    transactions: { eyebrow: 'Transactions', title: 'Transaction Management', description: 'Import, validate, search and inspect transactions with full source provenance.' },
    assistant: { eyebrow: 'AI Assistant', title: 'AI Investigation Assistant', description: 'Selective multi-agent investigation with SQL evidence, RAG citations and human review.' },
    analytics: { eyebrow: 'Analytics', title: 'Financial Analytics', description: 'Monthly spending, categories, trends and transaction details from actual data.' },
    'risk-intelligence': { eyebrow: 'Risk Intelligence', title: 'Financial Risk & Fraud Intelligence', description: 'Deterministic risk scoring, anomaly alerts, channel analytics, trends and alert review from stored records.' },
    'geographic-intelligence': { eyebrow: 'Geographic Intelligence', title: 'Geographic Risk Intelligence', description: 'Map, country/city analytics and geographic risk patterns from stored transaction and alert records.' },
    monitoring: { eyebrow: 'Monitoring', title: 'Audit Activity Monitoring', description: 'Historical audit runs, findings by risk/type, and activity.' },
    investigations: { eyebrow: 'Investigations', title: 'Investigations', description: 'Findings with source evidence and provenance.' },
    cases: { eyebrow: 'Cases', title: 'Investigation Cases', description: 'Create and manage cases linked to findings with status/notes.' },
    reconciliation: { eyebrow: 'Reconciliation', title: 'Bank Reconciliation', description: 'Match bank statements to the accounting ledger.' },
    agents: { eyebrow: 'AI', title: 'Agent Execution History', description: 'Persisted LangGraph investigation runs with evidence, status, and human review context.' },
    reports: { eyebrow: 'Reports', title: 'Reports & Exports', description: 'Generate and download reports and exports from actual data.' },
    compliance: { eyebrow: 'Compliance', title: 'Compliance', description: 'Actual available audit and evidence information.' },
    settings: { eyebrow: 'Settings', title: 'Settings', description: 'Real configuration status.' },
    'invoice-management': { eyebrow: 'Invoice Audit', title: 'Invoice Management', description: 'Upload, extract, inspect and audit vendor invoices.' },
    'purchase-orders': { eyebrow: 'Invoice Audit', title: 'Purchase Orders', description: 'Create and import approved purchase orders and line items.' },
    'goods-receipts': { eyebrow: 'Invoice Audit', title: 'Goods Receipts', description: 'Record received, accepted and rejected quantities.' },
    'invoice-matching': { eyebrow: 'Invoice Audit', title: 'Invoice Matching', description: 'Compare invoice, purchase order, receipt and payment evidence.' },
    'invoice-discrepancies': { eyebrow: 'Invoice Audit', title: 'Invoice Discrepancies', description: 'Review exceptions and record human decisions.' },
    'vendor-intelligence': { eyebrow: 'Invoice Audit', title: 'Vendor Intelligence', description: 'Vendor-level invoice, payment and exception patterns.' },
    'invoice-agent-center': { eyebrow: 'Invoice Audit', title: 'Invoice Agent Center', description: 'LangGraph invoice audit workflow with evidence and human review.' },
    'invoice-analytics': { eyebrow: 'Invoice Audit', title: 'Invoice Analytics', description: 'Monthly invoice, payment and discrepancy totals from actual data.' }
  }[activeView];

  const handleNavSelect = (item) => {
    setActiveView(item.view, { navId: item.navId });
    setMobileMenuOpen(false);
  };

  const compactSidebar = sidebarCollapsed && !mobileMenuOpen;

  return (
    <div className={`app-shell ${themeClass}`}>
      <aside
        className={`sidebar ${mobileMenuOpen ? 'sidebar-open' : ''} ${compactSidebar ? 'sidebar-collapsed' : ''}`}
      >
        <div className="sidebar-header-row">
          <div className="brand">
            <div className="brand-mark">
              <ShieldCheck size={24} />
            </div>
            {!compactSidebar && (
              <div className="brand-text">
                <b>
                  FinAudit<span> AI</span>
                </b>
                <small>Enterprise Risk Platform</small>
              </div>
            )}
          </div>
          <button
            type="button"
            className="sidebar-collapse-btn"
            onClick={() => setSidebarCollapsed((value) => !value)}
            title={sidebarCollapsed ? 'Expand sidebar' : 'Collapse sidebar'}
            aria-label={sidebarCollapsed ? 'Expand sidebar' : 'Collapse sidebar'}
          >
            {sidebarCollapsed ? <ChevronsRight size={16} /> : <ChevronsLeft size={16} />}
          </button>
        </div>

        <nav className="sidebar-groups" aria-label="Primary navigation">
          {SIDEBAR_GROUPS.map((group) => {
            const hasItems = group.items.length > 0;
            const expanded = expandedGroups[group.id] ?? true;
            return (
              <div key={group.id} className="sidebar-group">
                <button
                  type="button"
                  className={`sidebar-group-toggle ${!hasItems ? 'disabled' : ''}`}
                  onClick={() => {
                    if (!compactSidebar && hasItems) {
                      setExpandedGroups((current) => ({ ...current, [group.id]: !expanded }));
                    }
                  }}
                  disabled={!hasItems || compactSidebar}
                  title={group.label}
                >
                  {!compactSidebar && <span className="sidebar-group-label">{group.label}</span>}
                  {!compactSidebar && hasItems && (
                    <ChevronDown size={14} className={`sidebar-group-chevron ${expanded ? 'open' : ''}`} />
                  )}
                  {compactSidebar && <span className="sidebar-group-dot" aria-hidden="true" />}
                </button>
                {(compactSidebar || expanded) && hasItems && (
                  <div className="sidebar-group-items">
                    {group.items.map((item) => {
                      const Icon = item.icon;
                      const active = activeNavId === item.navId || (!SIDEBAR_NAV_ITEMS.some((nav) => nav.navId === activeNavId) && activeView === item.view);
                      return (
                        <button
                          key={item.navId}
                          type="button"
                          className={`nav ${active ? 'active' : ''} ${compactSidebar ? 'nav-collapsed' : ''}`}
                          onClick={() => handleNavSelect(item)}
                          title={item.label}
                          aria-label={item.label}
                        >
                          <Icon size={18} />
                          {!compactSidebar && <span>{item.label}</span>}
                        </button>
                      );
                    })}
                  </div>
                )}
              </div>
            );
          })}
        </nav>

        <div className="sidebar-bottom">
          <div className={`health-indicator health-${backendHealth.state}`} title={backendHealth.detail}>
            <span className="health-dot" />
            {!compactSidebar ? (
              <div className="health-copy">
                <strong>{backendHealth.label}</strong>
                <small>{backendHealth.detail}</small>
              </div>
            ) : (
              <span className="sr-only">{backendHealth.label}</span>
            )}
          </div>
          {!compactSidebar && (
            <small>Local-first · Human review required · Findings are investigation leads</small>
          )}
        </div>
      </aside>

      <main className="main">
        <header className={`topbar ${activeView === 'geographic-intelligence' ? 'topbar-geo' : ''}`}>
          <div style={{ display: 'flex', alignItems: 'flex-start', gap: 16 }}>
            <button
              className="mobile-menu-btn"
              type="button"
              onClick={() => setMobileMenuOpen((open) => !open)}
              aria-label="Toggle navigation"
            >
              <Menu size={20} />
            </button>
            {activeView !== 'geographic-intelligence' && (
              <div className="topbar-heading">
                <div className="eyebrow">{pageTitle.eyebrow}</div>
                <h1>{pageTitle.title}</h1>
                <p>{pageTitle.description}</p>
              </div>
            )}
          </div>
          {activeView !== 'geographic-intelligence' && (
            <div className="topbar-actions">
              <button className="secondary-btn" type="button" onClick={loadOverview} disabled={loadingOverview}>
                <RefreshCw size={16} className={loadingOverview ? 'spin' : ''} />
                Refresh
              </button>
              <div className="status-pill">
                <span />
                {API.replace('http://', '').replace('https://', '')}
              </div>
            </div>
          )}
        </header>

        {SECONDARY_VIEWS.has(activeView) && (
          <div className="workspace-backbar">
            <button className="secondary-btn" type="button" onClick={() => setActiveView('reports', { navId: 'reports-exports' })}>← Reports workspace</button>
            <span>Additional workspace · reachable from Reports</span>
          </div>
        )}

        {activeView === 'overview' && (
          <Dashboard onNavigate={(view) => setActiveView(view)} />
        )}
        {activeView === 'transactions' && (
          <Transactions focusRef={transactionFocus} />
        )}
        {activeView === 'monitoring' && (
          <MonitoringPage audits={audits} loadOverview={loadOverview} />
        )}
        {activeView === 'investigations' && (
          <InvestigationsPage audits={audits} onNavigate={(view) => setActiveView(view)} />
        )}
        {activeView === 'cases' && (
          <CasesPage audits={audits} />
        )}
        {activeView === 'reconciliation' && (
          <ReconciliationPage
            reconciliation={reconciliation}
            setReconciliation={setReconciliation}
            history={reconciliations}
            loadHistory={loadOverview}
          />
        )}
        {activeView === 'analytics' && (
          <AnalyticsPage />
        )}
        {activeView === 'risk-intelligence' && (
          <RiskIntelligence apiBase={API} />
        )}
        {activeView === 'geographic-intelligence' && (
          <GeographicRiskIntelligence apiBase={API} backendHealth={backendHealth} />
        )}
        {activeView === 'assistant' && (
          <Assistant />
        )}
        {activeView === 'agents' && (
          <AgentsPage />
        )}
        {activeView === 'reports' && (
          <ReportsPage audits={audits} reconciliations={reconciliations} onNavigate={(view) => setActiveView(view)} />
        )}
        {activeView === 'compliance' && (
          <CompliancePage audits={audits} reconciliations={reconciliations} />
        )}
        {activeView === 'settings' && (
          <SettingsPage />
        )}
        {activeView === 'invoice-management' && <InvoiceManagementPage />}
        {activeView === 'purchase-orders' && <PurchaseOrdersPage />}
        {activeView === 'goods-receipts' && <GoodsReceiptsPage />}
        {activeView === 'invoice-matching' && <InvoiceMatchingPage />}
        {activeView === 'invoice-discrepancies' && <InvoiceDiscrepancyPage />}
        {activeView === 'vendor-intelligence' && <VendorIntelligencePage />}
        {activeView === 'invoice-agent-center' && <InvoiceAgentCenterPage />}
        {activeView === 'invoice-analytics' && <InvoiceAnalyticsPage />}
      </main>
    </div>
  );
}

createRoot(document.getElementById('root')).render(<App />);


function AnalyticsPage() {
  const now = new Date();
  const [year, setYear] = useState(now.getFullYear());
  const [month, setMonth] = useState(now.getMonth() + 1);
  const [summary, setSummary] = useState(null);
  const [categories, setCategories] = useState([]);
  const [comparison, setComparison] = useState(null);
  const [transactions, setTransactions] = useState([]);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState('');

  const load = async () => {
    setLoading(true);
    setError('');
    try {
      const [sRes, cRes, compRes, tRes] = await Promise.all([
        fetch(`${API}/api/analytics/monthly/summary?year=${year}&month=${month}`),
        fetch(`${API}/api/analytics/monthly/categories?year=${year}&month=${month}`),
        fetch(`${API}/api/analytics/monthly/comparison?year=${year}&month=${month}`),
        fetch(`${API}/api/analytics/transactions?year=${year}&month=${month}&limit=100`)
      ]);
      if (sRes.ok) setSummary(await sRes.json());
      if (cRes.ok) setCategories(await cRes.json());
      if (compRes.ok) setComparison(await compRes.json());
      if (tRes.ok) setTransactions(await tRes.json());
    } catch (e) {
      setError(e.message || 'Failed to load analytics');
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    load();
  }, [year, month]);

  return (
    <>
      <header className="page-header">
        <div>
          <h1>Financial Analytics</h1>
          <p>Monthly analytics computed from actual stored transactions. Deterministic calculations with provenance.</p>
        </div>
        <div style={{ display: 'flex', gap: 10 }}>
          <select value={year} onChange={(e) => setYear(Number(e.target.value))} style={{ padding: '8px 10px', borderRadius: 8 }}>
            {[now.getFullYear()-1, now.getFullYear(), now.getFullYear()+1].map(y => <option key={y} value={y}>{y}</option>)}
          </select>
          <select value={month} onChange={(e) => setMonth(Number(e.target.value))} style={{ padding: '8px 10px', borderRadius: 8 }}>
            {Array.from({length:12}, (_,i)=>i+1).map(m => <option key={m} value={m}>{m}</option>)}
          </select>
          <a className="secondary-btn" href={`${API}/api/analytics/export.csv?year=${year}&month=${month}`}><Download size={16}/>Export CSV</a>
        </div>
      </header>
      {error && <div className="error-box">{error}</div>}
      <div className="kpi-grid">
        <article className="kpi-card"><div className="kpi-top"><div className="kpi-label"><span>Total spending</span><strong>{summary ? fmtCurrency(summary.total_spend) : '—'}</strong></div><div className="kpi-icon amber"><CircleDollarSign size={20}/></div></div><span className="kpi-subtle">Debits/outflows from saved transactions</span></article>
        <article className="kpi-card"><div className="kpi-top"><div className="kpi-label"><span>Total received</span><strong>{summary ? fmtCurrency(summary.total_received) : '—'}</strong></div><div className="kpi-icon green"><CheckCircle2 size={20}/></div></div><span className="kpi-subtle">Credits/inflows</span></article>
        <article className="kpi-card"><div className="kpi-top"><div className="kpi-label"><span>Net cash flow</span><strong>{summary ? fmtCurrency(summary.net_cash_flow) : '—'}</strong></div><div className="kpi-icon blue"><ArrowLeftRight size={20}/></div></div><span className="kpi-subtle">Received - spending</span></article>
        <article className="kpi-card"><div className="kpi-top"><div className="kpi-label"><span>Transactions</span><strong>{summary ? fmtNumber(summary.transaction_count) : '—'}</strong></div><div className="kpi-icon cyan"><FileText size={20}/></div></div><span className="kpi-subtle">Avg out {summary ? fmtCurrency(summary.average_outgoing) : '—'} | Largest {summary ? fmtCurrency(summary.largest_expense) : '—'}</span></article>
      </div>
      <div className="dashboard-grid">
        <section className="chart-card">
          <div className="section-header"><div><h2>Spending by category</h2><p>Rule-based categorization (uncertain marked Uncategorized)</p></div></div>
          {categories.length === 0 ? <div className="empty-state"><div className="empty-icon"><PieChartIcon size={24}/></div><strong>No spending data</strong><span>Upload transactions to see category breakdown.</span></div> : (
            <div style={{ display: 'grid', gap: 10 }}>
              {categories.map(c => (
                <div key={c.category} style={{ display: 'flex', justifyContent: 'space-between', padding: '8px 0', borderBottom: '1px solid rgba(148,163,184,0.12)' }}>
                  <span>{c.category}</span>
                  <span style={{ color: 'var(--dash-text-primary)' }}>{fmtCurrency(c.amount)}</span>
                </div>
              ))}
            </div>
          )}
        </section>
        <section className="chart-card">
          <div className="section-header"><div><h2>Month over month</h2><p>Comparison with previous month (deterministic)</p></div></div>
          {comparison ? (
            <div style={{ display: 'grid', gap: 10 }}>
              <div style={{ display: 'flex', justifyContent: 'space-between' }}><span>Current spend</span><span>{fmtCurrency(comparison.current.spend)}</span></div>
              <div style={{ display: 'flex', justifyContent: 'space-between' }}><span>Previous spend</span><span>{fmtCurrency(comparison.previous.spend)}</span></div>
              <div style={{ display: 'flex', justifyContent: 'space-between' }}><span>Current net</span><span>{fmtCurrency(comparison.current.net)}</span></div>
              <div style={{ display: 'flex', justifyContent: 'space-between' }}><span>Previous net</span><span>{fmtCurrency(comparison.previous.net)}</span></div>
            </div>
          ) : <div className="empty-state"><span>No comparison data</span></div>}
        </section>
      </div>
      <section className="table-card">
        <div className="section-header"><div><h2>Transactions ({year}-{String(month).padStart(2,'0')})</h2><p>Source filename/row preserved for provenance</p></div></div>
        {transactions.length === 0 ? <div className="empty-state"><div className="empty-icon"><FileSearch size={24}/></div><strong>No transactions</strong><span>Upload CSV files and run audits to populate analytics.</span></div> : (
          <div className="data-table-wrap">
            <table className="data-table">
              <thead><tr><th>Date</th><th>Description/Vendor</th><th>Category</th><th>Direction</th><th>Amount</th><th>Source</th><th>Row</th></tr></thead>
              <tbody>
                {transactions.map(t => (
                  <tr key={t.id}>
                    <td>{t.date || '—'}</td>
                    <td className="table-primary">{t.vendor || t.description || '—'}</td>
                    <td>{t.category || 'Uncategorized'}</td>
                    <td>{t.direction || '—'}</td>
                    <td>{fmtCurrency(t.amount)}</td>
                    <td>{t.source_file || '—'}</td>
                    <td>{t.source_row || '—'}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </section>
    </>
  );
}


function AgentsPage() {
  const [taskType, setTaskType] = useState('monthly_analysis');
  const [year, setYear] = useState(new Date().getFullYear());
  const [month, setMonth] = useState(new Date().getMonth()+1);
  const [auditRunId, setAuditRunId] = useState('');
  const [reconciliationId, setReconciliationId] = useState('');
  const [running, setRunning] = useState(false);
  const [result, setResult] = useState(null);
  const [error, setError] = useState('');
  const [runs, setRuns] = useState([]);

  const loadRuns = async () => {
    try {
      const res = await fetch(`${API}/api/agents/runs`);
      if (res.ok) setRuns(await res.json());
    } catch {}
  };
  useEffect(() => { loadRuns(); }, []);

  const loadRun = async (id) => {
    setError('');
    try {
      const res = await fetch(`${API}/api/agents/runs/${id}`);
      const data = await res.json();
      if (!res.ok) throw new Error(data.detail || 'Could not load run');
      const stored = data.output_state || {};
      setResult({
        ...stored,
        run_id: data.id,
        status: data.status,
        timeline: data.timeline || stored.timeline || [],
        events: data.events || stored.events || [],
        evidence: data.evidence || stored.evidence || [],
        errors: data.errors || stored.errors || [],
        started_at: data.started_at || stored.started_at,
        finished_at: data.finished_at || stored.finished_at,
        duration_ms: data.duration_ms ?? stored.duration_ms
      });
    } catch (e) {
      setError(e.message);
    }
  };

  const run = async () => {
    setRunning(true); setError(''); setResult(null);
    try {
      const payload = { task_type: taskType, year, month };
      if (auditRunId) payload.audit_run_id = Number(auditRunId);
      if (reconciliationId) payload.reconciliation_id = Number(reconciliationId);
      const res = await fetch(`${API}/api/agents/run`, { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(payload) });
      const data = await res.json();
      if (!res.ok) throw new Error(data.detail || 'Agent run failed');
      setResult(data);
      loadRuns();
    } catch (e) {
      setError(e.message);
    } finally {
      setRunning(false);
    }
  };

  const analysis = result?.outputs?.analysis || {};
  const summary = analysis.summary || {};
  const anomaly = result?.outputs?.anomaly || {};
  const synthesis = result?.outputs?.synthesis || {};
  const report = result?.outputs?.report || {};
  const evidence = result?.evidence || [];
  const missingEvidence = result?.missing_evidence || [];
  const listBlock = (title, items) => (
    <div style={{ marginTop: 10 }}>
      <strong style={{ fontSize: 13 }}>{title}</strong>
      {(items || []).length === 0
        ? <div style={{ color: 'var(--dash-text-secondary)', fontSize: 12 }}>None recorded.</div>
        : <ul style={{ margin: '6px 0 0 18px', padding: 0, color: 'var(--dash-text-secondary)', fontSize: 12 }}>{(items || []).map((item, i) => <li key={i}>{typeof item === 'string' ? item : JSON.stringify(item)}</li>)}</ul>}
    </div>
  );

  return (
    <>
      <header className="page-header">
        <div>
          <h1>AI Agent Center</h1>
          <p>LangGraph multi-agent workflow with deterministic tools, evidence links, and human-review checkpoints.</p>
        </div>
      </header>
      <section className="panel" style={{ padding: 18, marginBottom: 18 }}>
        <div style={{ display: 'flex', gap: 12, flexWrap: 'wrap', alignItems: 'center' }}>
          <select value={taskType} onChange={(e)=>setTaskType(e.target.value)} style={{ padding: '8px 10px', borderRadius: 8 }}>
            <option value="monthly_analysis">Monthly Analysis</option>
            <option value="investigation">Investigation</option>
            <option value="reconciliation_review">Reconciliation Review</option>
          </select>
          <select value={year} onChange={(e)=>setYear(Number(e.target.value))} style={{ padding: '8px 10px', borderRadius: 8 }}>{[2025,2026,2027].map(y=><option key={y} value={y}>{y}</option>)}</select>
          <select value={month} onChange={(e)=>setMonth(Number(e.target.value))} style={{ padding: '8px 10px', borderRadius: 8 }}>{Array.from({length:12},(_,i)=>i+1).map(m=><option key={m} value={m}>{m}</option>)}</select>
          {taskType === 'investigation' && (
            <input value={auditRunId} onChange={(e)=>setAuditRunId(e.target.value)} placeholder="Audit run ID (optional)" type="number" style={{ padding: '8px 10px', borderRadius: 8, width: 180 }} />
          )}
          {taskType === 'reconciliation_review' && (
            <input value={reconciliationId} onChange={(e)=>setReconciliationId(e.target.value)} placeholder="Reconciliation ID" type="number" style={{ padding: '8px 10px', borderRadius: 8, width: 180 }} />
          )}
          <button className="primary-btn" onClick={run} disabled={running}><Sparkles size={16}/>{running ? 'Running...' : 'Start Analysis'}</button>
        </div>
        <div style={{ marginTop: 10, color: 'var(--dash-text-secondary)', fontSize: 13 }}>Agents: Intake → Reconciliation → Anomaly Investigation → Financial Analysis → Evidence Retrieval/RAG → Synthesis → Reporting</div>
      </section>
      {error && <div className="error-box">{error}</div>}
      {result && (
        <>
          <div className="dashboard-grid">
            <section className="chart-card">
              <div className="section-header">
                <div>
                  <h2>Workflow Timeline</h2>
                  <p>Run #{result.run_id || '—'} | <StatusBadge status={result.status}/> | {fmtNumber(result.duration_ms)} ms | {formatDateTime(result.started_at)} → {formatDateTime(result.finished_at)}</p>
                </div>
              </div>
              <div style={{ display: 'grid', gap: 8 }}>
                {(result.timeline||[]).map((t,i)=>(
                  <div key={i} style={{ display: 'grid', gridTemplateColumns: '1.5fr auto auto', gap: 10, alignItems: 'center', padding: '7px 0', borderBottom: '1px solid rgba(148,163,184,0.12)' }}>
                    <div>
                      <div>{t.agent_name}</div>
                      {t.summary && <div style={{ color: 'var(--dash-text-secondary)', fontSize: 12 }}>{t.summary}</div>}
                      {t.error && <div style={{ color: 'var(--danger, #ef4444)', fontSize: 12 }}>{t.error}</div>}
                    </div>
                    <StatusBadge status={t.status}/>
                    <span style={{ color: 'var(--dash-text-secondary)', fontSize: 12 }}>{fmtNumber(t.duration_ms)} ms</span>
                  </div>
                ))}
              </div>
              {(result.events||[]).length > 0 && (
                <details style={{ marginTop: 12 }}>
                  <summary style={{ cursor: 'pointer', color: 'var(--dash-text-secondary)', fontSize: 12 }}>Chronological event history ({result.events.length})</summary>
                  <div style={{ marginTop: 8, display: 'grid', gap: 4 }}>
                    {result.events.map((event, i) => (
                      <div key={i} style={{ display: 'flex', justifyContent: 'space-between', fontSize: 12, color: 'var(--dash-text-secondary)' }}>
                        <span>{event.agent_name} · {event.event}</span>
                        <span>{formatDateTime(event.timestamp)}{event.duration_ms != null ? ` · ${fmtNumber(event.duration_ms)} ms` : ''}</span>
                      </div>
                    ))}
                  </div>
                </details>
              )}
              {result.require_human_review && <div className="warning-box" style={{ marginTop: 10 }}>Human review checkpoint required. This run does not resolve a case or approve any financial action.</div>}
              {report.conclusion_status && <div style={{ marginTop: 8, fontSize: 12, color: 'var(--dash-text-secondary)' }}>Conclusion status: <strong>{report.conclusion_status.replaceAll('_', ' ')}</strong></div>}
            </section>
            <section className="chart-card">
              <div className="section-header"><div><h2>Verified Financial Results</h2><p>Deterministic service output, never generated by the LLM</p></div></div>
              {analysis.available === false ? (
                <div className="empty-state"><div className="empty-icon"><FileSearch size={24}/></div><strong>No verified financial result</strong><span>{analysis.reason || 'No dataset was available for this task.'}</span></div>
              ) : (
                <div style={{ display: 'grid', gap: 8 }}>
                  <div style={{ display: 'flex', justifyContent: 'space-between' }}><span>Total spending</span><strong>{fmtCurrency(summary.total_spending)}</strong></div>
                  <div style={{ display: 'flex', justifyContent: 'space-between' }}><span>Total credits</span><strong>{fmtCurrency(summary.total_credits)}</strong></div>
                  <div style={{ display: 'flex', justifyContent: 'space-between' }}><span>Net cash flow</span><strong>{fmtCurrency(summary.net_cash_flow)}</strong></div>
                  <div style={{ display: 'flex', justifyContent: 'space-between' }}><span>Transactions</span><strong>{fmtNumber(summary.transaction_count)}</strong></div>
                  <div style={{ display: 'flex', justifyContent: 'space-between' }}><span>Saved findings linked</span><strong>{fmtNumber(anomaly.summary?.finding_count)}</strong></div>
                  <div style={{ display: 'flex', justifyContent: 'space-between' }}><span>Evidence records</span><strong>{fmtNumber(evidence.length)}</strong></div>
                  {analysis.has_transactions === false && <div className="warning-box" style={{ marginTop: 6 }}>No transactions exist for this period. The zero totals are an empty state, not a verified zero-spend month.</div>}
                  {(analysis.data_quality?.notes || []).map((note, i) => (
                    <div key={i} style={{ color: 'var(--dash-text-secondary)', fontSize: 12 }}>{note}</div>
                  ))}
                </div>
              )}
            </section>
          </div>
          <div className="dashboard-grid">
            <section className="chart-card">
              <div className="section-header"><div><h2>Synthesis</h2><p>Facts, hypotheses, limitations and review actions are kept separate</p></div></div>
              {listBlock('Verified facts', synthesis.facts)}
              {listBlock('Hypotheses (not established)', synthesis.hypotheses)}
              {listBlock('Limitations / missing evidence', synthesis.limitations)}
              {listBlock('Recommended human review actions', synthesis.recommendations)}
              {synthesis.narrative && (
                <div style={{ marginTop: 12 }}>
                  <strong style={{ fontSize: 13 }}>Optional LLM narrative</strong>
                  <p style={{ whiteSpace: 'pre-wrap', color: 'var(--dash-text-secondary)', fontSize: 12 }}>{synthesis.narrative}</p>
                  <div style={{ fontSize: 11, color: 'var(--dash-text-secondary)' }}>Model: {synthesis.llm?.model || '—'} | fallback: {String(synthesis.llm?.fallback)}</div>
                </div>
              )}
            </section>
            <section className="chart-card">
              <div className="section-header"><div><h2>Evidence & Data Quality</h2><p>Source records with provenance and explicit gaps</p></div></div>
              {evidence.length === 0 ? <div className="empty-state"><div className="empty-icon"><FileSearch size={24}/></div><strong>No evidence records</strong><span>See missing evidence below.</span></div> : (
                <div style={{ display: 'grid', gap: 6, maxHeight: 260, overflow: 'auto' }}>
                  {evidence.slice(0, 30).map((item, i) => (
                    <div key={i} style={{ fontSize: 12, borderBottom: '1px solid rgba(148,163,184,0.12)', paddingBottom: 4 }}>
                      <div><strong>{item.source_id || item.source_type}</strong> {item.source_file ? `· ${item.source_file}${item.source_row ? ` row ${item.source_row}` : ''}` : ''}</div>
                      <div style={{ color: 'var(--dash-text-secondary)' }}>{item.excerpt}</div>
                    </div>
                  ))}
                </div>
              )}
              {listBlock('Missing evidence', missingEvidence)}
              {listBlock('Errors', result.errors)}
            </section>
          </div>
          <section className="chart-card" style={{ marginTop: 18 }}>
            <details>
              <summary style={{ cursor: 'pointer' }}><strong>Structured outputs (JSON)</strong></summary>
              <pre style={{ whiteSpace: 'pre-wrap', fontSize: 12, color: 'var(--dash-text-secondary)', maxHeight: 420, overflow: 'auto' }}>{JSON.stringify(result.outputs, null, 2)}</pre>
            </details>
          </section>
        </>
      )}
      <section className="table-card" style={{ marginTop: 18 }}>
        <div className="section-header"><div><h2>Investigation Run History</h2><p>Persisted runs from orchestration; click a row to load its saved result</p></div></div>
        {runs.length === 0 ? <div className="empty-state"><div className="empty-icon"><Sparkles size={24}/></div><strong>No runs yet</strong><span>Start an analysis to create a run.</span></div> : (
          <div className="data-table-wrap">
            <table className="data-table">
              <thead><tr><th>ID</th><th>Task</th><th>Period / target</th><th>Status</th><th>Duration</th><th>Evidence</th><th>Created</th></tr></thead>
              <tbody>
                {runs.map(r => (
                  <tr key={r.id} onClick={() => loadRun(r.id)} style={{ cursor: 'pointer' }}>
                    <td>{r.id}</td>
                    <td>{r.task_type}</td>
                    <td>{r.selected_year && r.selected_month ? `${r.selected_year}-${String(r.selected_month).padStart(2, '0')}` : (r.reconciliation_id ? `Recon #${r.reconciliation_id}` : r.audit_run_id ? `Audit #${r.audit_run_id}` : '—')}</td>
                    <td><StatusBadge status={r.status}/></td>
                    <td>{r.duration_ms != null ? `${fmtNumber(r.duration_ms)} ms` : '—'}</td>
                    <td>{fmtNumber(r.evidence_count)}</td>
                    <td>{formatDateTime(r.created_at)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </section>
    </>
  );
}


function ReportsPage({ audits = [], reconciliations = [], onNavigate }) {
  const [summary, setSummary] = useState(null);
  const [error, setError] = useState('');
  const navigate = (view) => onNavigate && onNavigate(view);

  useEffect(() => {
    let active = true;
    fetch(`${API}/api/reports/summary`)
      .then(async (response) => {
        if (!response.ok) throw new Error(`Report summary failed (${response.status})`);
        return response.json();
      })
      .then((data) => { if (active) setSummary(data); })
      .catch((err) => { if (active) setError(err.message); });
    return () => { active = false; };
  }, []);

  const financial = summary?.financial_summary || {};
  const recon = summary?.reconciliation;
  const latestAuditId = audits[0]?.id;
  const latestReconId = reconciliations[0]?.id || reconciliations[0]?.reconciliation_id;

  return (
    <div className="reports-workspace">
      <div className="report-toolbar">
        <a className="primary-btn" href={`${API}/api/reports/full.pdf`}><Download size={16} />Full report (PDF)</a>
        <a className="secondary-btn" href={`${API}/api/reports/full.csv`}><Download size={16} />Full report (CSV)</a>
        <a className="secondary-btn" href={`${API}/api/transactions/export.csv`}><Download size={16} />Transactions (filtered CSV)</a>
        <a className="secondary-btn" href={`${API}/api/risk/alerts/export.csv?severity=high,critical`}><Download size={16} />Risk alerts (CSV)</a>
        {latestAuditId && <a className="secondary-btn" href={`${API}/api/audits/${latestAuditId}/export.csv`}><Download size={16} />Audit #{latestAuditId}</a>}
        {latestReconId && <a className="secondary-btn" href={`${API}/api/reconciliations/${latestReconId}/export.csv`}><Download size={16} />Reconciliation #{latestReconId}</a>}
      </div>
      {error && <div className="ai-alert ai-alert-error"><AlertTriangle size={15} />{error}</div>}
      {summary?.generated_at && (
        <div className="panel" style={{ padding: '10px 16px', marginBottom: 14 }}>
          <span className="kpi-subtle">Generated (UTC): {formatDateTime(summary.generated_at)} · Filters: {JSON.stringify(summary.filters || {})} · {summary.provenance}</span>
        </div>
      )}

      <div className="kpi-grid">
        <article className="kpi-card"><div className="kpi-top"><div className="kpi-label"><span>Transactions</span><strong>{fmtNumber(financial.total_transactions)}</strong></div><div className="kpi-icon blue"><FileText size={19}/></div></div><span className="kpi-subtle">Stored records in scope</span></article>
        <article className="kpi-card"><div className="kpi-top"><div className="kpi-label"><span>Spending</span><strong>{fmtCurrency(financial.total_spending)}</strong></div><div className="kpi-icon amber"><CircleDollarSign size={19}/></div></div><span className="kpi-subtle">Debit-like absolute amounts</span></article>
        <article className="kpi-card"><div className="kpi-top"><div className="kpi-label"><span>Net cash flow</span><strong>{fmtCurrency(financial.net_cash_flow)}</strong></div><div className="kpi-icon cyan"><ArrowLeftRight size={19}/></div></div><span className="kpi-subtle">Received less spending</span></article>
        <article className="kpi-card"><div className="kpi-top"><div className="kpi-label"><span>Risk alerts</span><strong>{fmtNumber(financial.total_risk_alerts)}</strong></div><div className="kpi-icon red"><ShieldAlert size={19}/></div></div><span className="kpi-subtle">Deterministic findings</span></article>
      </div>

      <div className="report-grid">
        <section className="table-card report-panel">
          <div className="section-header"><div><h2>Financial summary</h2><p>{summary?.scope?.spending || 'Scope is documented by the backend.'}</p></div></div>
          <div className="report-definitions">
            {Object.entries(summary?.scope || {}).map(([key, value]) => (
              <div key={key}><strong>{key.replaceAll('_', ' ')}</strong><span>{Array.isArray(value) ? value.join(', ') : String(value)}</span></div>
            ))}
          </div>
        </section>
        <section className="table-card report-panel">
          <div className="section-header"><div><h2>Reconciliation</h2><p>{recon ? `${recon.bank_file} ↔ ${recon.ledger_file}` : 'No reconciliation run stored'}</p></div></div>
          {recon?.summary ? <div className="report-definitions">{Object.entries(recon.summary).map(([key, value]) => <div key={key}><strong>{key.replaceAll('_', ' ')}</strong><span>{String(value)}</span></div>)}</div> : <div className="empty-state"><ArrowLeftRight size={22}/><strong>No reconciliation data</strong><span>Run a reconciliation to include it in reports.</span></div>}
        </section>
        <section className="table-card report-panel">
          <div className="section-header"><div><h2>Risk alerts</h2><p>Top deterministic alerts with evidence references</p></div></div>
          {(summary?.risk_alerts || []).length ? <div className="data-table-wrap"><table className="data-table"><thead><tr><th>Alert</th><th>Category</th><th>Score</th><th>Transaction</th><th>Status</th></tr></thead><tbody>
            {summary.risk_alerts.slice(0, 12).map((alert) => <tr key={alert.alert_code}><td className="table-primary">{alert.alert_code}</td><td>{alert.risk_category}</td><td>{alert.risk_score} · {alert.severity}</td><td>{alert.transaction_id || '—'}</td><td>{alert.review_status}</td></tr>)}
          </tbody></table></div> : <div className="empty-state"><ShieldAlert size={22}/><strong>No alerts</strong><span>Run risk analysis first.</span></div>}
        </section>
        <section className="table-card report-panel">
          <div className="section-header"><div><h2>Investigations & AI explanations</h2><p>Persisted runs; AI never changes deterministic results</p></div></div>
          {(summary?.investigations || []).length ? <div className="data-table-wrap"><table className="data-table"><thead><tr><th>Run</th><th>Name</th><th>Type</th><th>Status</th></tr></thead><tbody>
            {summary.investigations.map((run) => <tr key={run.id}><td className="table-primary">#{run.id}</td><td>{run.name || '—'}</td><td>{run.task_type}</td><td>{run.status}</td></tr>)}
          </tbody></table></div> : <div className="empty-state"><FileSearch size={22}/><strong>No investigations</strong><span>Use the AI Assistant to run an investigation.</span></div>}
          {(summary?.ai_explanations || []).slice(0, 3).map((item) => (
            <div className="ai-explanation-summary" key={item.investigation_id}>
              <strong>Investigation #{item.investigation_id} · {item.conclusion_status || 'review required'}</strong>
              <p>{item.ai_narrative || item.verified_facts.join(' | ')}</p>
              {item.evidence_references?.length ? <span>Evidence: {item.evidence_references.map((ref) => ref.source_file || ref.source_id).filter(Boolean).join(', ')}</span> : null}
            </div>
          ))}
          <button className="text-btn" type="button" onClick={() => navigate('assistant')}>Open AI Assistant →</button>
        </section>
      </div>

      <section className="panel report-hub">
        <div className="section-header"><div><h2>Additional workspaces</h2><p>Existing tools remain available through internal navigation</p></div></div>
        <div className="report-shortcuts">
          <button type="button" onClick={() => navigate('analytics')}><BarChart3 size={17}/>Financial analytics</button>
          <button type="button" onClick={() => navigate('monitoring')}><Activity size={17}/>Monitoring</button>
          <button type="button" onClick={() => navigate('investigations')}><FileSearch size={17}/>Investigations</button>
          <button type="button" onClick={() => navigate('cases')}><Users size={17}/>Review cases</button>
          <button type="button" onClick={() => navigate('agents')}><Sparkles size={17}/>Legacy agent center</button>
          <button type="button" onClick={() => navigate('compliance')}><ShieldCheck size={17}/>Compliance</button>
        </div>
        <h3>Invoice audit workspace</h3>
        <div className="report-shortcuts">
          {INVOICE_NAV_ITEMS.map((item) => {
            const Icon = item.icon;
            return <button key={item.id} type="button" onClick={() => navigate(item.id)}><Icon size={17}/>{item.label}</button>;
          })}
        </div>
      </section>
      <section className="panel report-disclaimer"><AlertTriangle size={16}/><span>{summary?.disclaimer || 'Automated investigation leads for human review. Not proof of fraud.'}</span></section>
    </div>
  );
}


function MonitoringPage({ audits = [], loadOverview }) {
  const totalFindings = audits.reduce((s,a)=>s + (Number(a.finding_count)||0), 0);
  const totalRows = audits.reduce((s,a)=>s + (Number(a.row_count)||0), 0);
  const findingsByRisk = { high: 0, medium: 0, low: 0 };
  (audits[0]?.findings||[]).forEach(f => { if(findingsByRisk[f.risk_level] !== undefined) findingsByRisk[f.risk_level]++; });
  return (
    <>
      <header className="page-header"><div><h1>Audit Activity Monitoring</h1><p>Historical monitoring based on persisted audit runs (not live streaming)</p></div><div className="topbar-actions"><button className="secondary-btn" onClick={loadOverview}><RefreshCw size={16}/>Refresh</button></div></header>
      <div className="kpi-grid">
        <article className="kpi-card"><div className="kpi-top"><div className="kpi-label"><span>Audit runs</span><strong>{audits.length}</strong></div><div className="kpi-icon blue"><Activity size={20}/></div></div><span className="kpi-subtle">Persisted in database</span></article>
        <article className="kpi-card"><div className="kpi-top"><div className="kpi-label"><span>Transactions analyzed</span><strong>{fmtNumber(totalRows)}</strong></div><div className="kpi-icon cyan"><FileSearch size={20}/></div></div><span className="kpi-subtle">Across all runs</span></article>
        <article className="kpi-card"><div className="kpi-top"><div className="kpi-label"><span>Total findings</span><strong>{fmtNumber(totalFindings)}</strong></div><div className="kpi-icon amber"><AlertTriangle size={20}/></div></div><span className="kpi-subtle">Unresolved by default (review required)</span></article>
        <article className="kpi-card"><div className="kpi-top"><div className="kpi-label"><span>Latest risk mix</span><strong>H:{findingsByRisk.high} M:{findingsByRisk.medium} L:{findingsByRisk.low}</strong></div><div className="kpi-icon red"><ShieldCheck size={20}/></div></div><span className="kpi-subtle">From most recent audit</span></article>
      </div>
      <section className="table-card">
        <div className="section-header"><div><h2>Recent audit runs</h2><p>Persisted results</p></div></div>
        {audits.length === 0 ? <div className="empty-state"><div className="empty-icon"><Activity size={24}/></div><strong>No audit runs</strong><span>Upload and run an audit to see activity.</span></div> : (
          <div className="data-table-wrap"><table className="data-table"><thead><tr><th>ID</th><th>Files</th><th>Rows</th><th>Findings</th><th>Created</th></tr></thead><tbody>
            {audits.map(a=>(<tr key={a.id}><td>{a.id}</td><td>{(a.files||[]).join(', ')||a.filename||'—'}</td><td>{a.row_count}</td><td>{a.finding_count}</td><td>{formatDateTime(a.created_at)}</td></tr>))}
          </tbody></table></div>
        )}
      </section>
    </>
  );
}


function InvestigationsPage({ audits = [], onNavigate }) {
  const [cases, setCases] = useState([]);
  const [total, setTotal] = useState(0);
  const [loading, setLoading] = useState(true);
  const [err, setErr] = useState('');
  const findings = (audits[0]?.findings) || [];

  useEffect(() => {
    let mounted = true;
    (async () => {
      setLoading(true);
      setErr('');
      try {
        const res = await fetch(`${API}/api/investigations?limit=50`);
        const data = await res.json();
        if (!res.ok) throw new Error(data.detail || 'Failed to load investigations');
        if (mounted) {
          setCases(data.items || []);
          setTotal(data.total || 0);
        }
      } catch (e) {
        if (mounted) setErr(e.message);
      } finally {
        if (mounted) setLoading(false);
      }
    })();
    return () => { mounted = false; };
  }, []);

  return (
    <>
      <header className="page-header"><div><h1>Investigations</h1><p>Persisted cases with source evidence and provenance</p></div>
        <button className="primary-btn" type="button" onClick={() => onNavigate && onNavigate('cases')}><Plus size={16}/>Open case workspace</button>
      </header>
      {err && <div className="warning-box" style={{ marginBottom: 12 }}>{err}</div>}
      <section className="table-card">
        <div className="section-header"><div><h2>Investigation cases</h2><p>{total} case(s) stored in the database{loading ? ' · loading…' : ''}</p></div></div>
        {cases.length === 0 ? (
          <div className="empty-state"><div className="empty-icon"><Users size={24}/></div><strong>No investigation cases</strong><span>Create one in the case workspace, or start one from a risk alert.</span></div>
        ) : (
          <div className="data-table-wrap"><table className="data-table"><thead><tr><th>Case</th><th>Severity</th><th>Status</th><th>Transaction</th><th>Vendor</th><th>Reviewer</th><th>Created</th></tr></thead><tbody>
            {cases.map(c => (
              <tr key={c.id}>
                <td className="table-primary">{c.case_number}<div className="cell-muted">{c.title}</div></td>
                <td><StatusBadge status={c.severity}/></td>
                <td><StatusBadge status={c.status}/></td>
                <td>{c.transaction_id || '—'}</td>
                <td>{c.vendor_name || '—'}</td>
                <td>{c.assigned_to || 'unassigned'}</td>
                <td>{formatDateTime(c.created_at)}</td>
              </tr>
            ))}
          </tbody></table></div>
        )}
      </section>
      <section className="table-card" style={{ marginTop: 18 }}>
        <div className="section-header"><div><h2>Latest findings</h2><p>From the most recent audit run · source file and row included</p></div></div>
        {findings.length === 0 ? <div className="empty-state"><div className="empty-icon"><FileSearch size={24}/></div><strong>No findings</strong><span>Run an audit to generate findings.</span></div> : (
          <div className="data-table-wrap"><table className="data-table"><thead><tr><th>Finding</th><th>Txn ID</th><th>Source file</th><th>Amount</th><th>Risk</th><th>Type</th></tr></thead><tbody>
            {findings.map(f=>(<tr key={f.id}><td className="table-primary">{f.summary}</td><td>{f.transaction_id||'—'}</td><td>{f.source_file||'—'}</td><td>{fmtCurrency(f.amount)}</td><td><StatusBadge status={f.risk_level}/></td><td>{f.finding_type}</td></tr>))}
          </tbody></table></div>
        )}
      </section>
    </>
  );
}


function CasesPage({ audits = [] }) {
  const [cases, setCases] = useState([]);
  const [total, setTotal] = useState(0);
  const [loading, setLoading] = useState(false);
  const [detail, setDetail] = useState(null);
  const [selected, setSelected] = useState(null);
  const [msg, setMsg] = useState('');
  const [err, setErr] = useState('');
  const [form, setForm] = useState({ title: '', description: '', severity: 'medium', transaction_id: '', vendor_name: '', assigned_to: '' });
  const [statusForm, setStatusForm] = useState({ status: 'open', resolution: '', reviewer_notes: '', actor: 'reviewer' });
  const [note, setNote] = useState({ author: 'reviewer', note: '' });
  const [evidenceForm, setEvidenceForm] = useState({ source_type: 'document', source_reference: '', excerpt: '' });

  const load = async () => {
    setLoading(true);
    setErr('');
    try {
      const res = await fetch(`${API}/api/investigations?limit=200`);
      const data = await res.json();
      if (!res.ok) throw new Error(data.detail || 'Failed to load investigations');
      setCases(data.items || []);
      setTotal(data.total || 0);
    } catch (e) {
      setErr(e.message);
    } finally {
      setLoading(false);
    }
  };
  useEffect(() => { load(); }, []);

  const openCase = async (caseRef) => {
    setSelected(caseRef);
    setDetail(null);
    setErr('');
    try {
      const res = await fetch(`${API}/api/investigations/${encodeURIComponent(caseRef)}`);
      const data = await res.json();
      if (!res.ok) throw new Error(data.detail || 'Failed to load case');
      setDetail(data);
      setStatusForm({ status: data.status || 'open', resolution: data.resolution || '', reviewer_notes: '', actor: 'reviewer' });
    } catch (e) {
      setErr(e.message);
    }
  };

  const create = async () => {
    setMsg('');
    setErr('');
    try {
      const payload = { ...form, audit_run_id: audits[0]?.id, created_by: 'reviewer' };
      Object.keys(payload).forEach(k => { if (payload[k] === '') delete payload[k]; });
      const res = await fetch(`${API}/api/investigations`, {
        method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(payload),
      });
      const data = await res.json();
      if (!res.ok) throw new Error(data.detail || 'Failed');
      setMsg(`Created ${data.case_number}`);
      setForm({ title: '', description: '', severity: 'medium', transaction_id: '', vendor_name: '', assigned_to: '' });
      await load();
      await openCase(data.case_number);
    } catch (e) {
      setErr(e.message);
    }
  };

  const updateStatus = async () => {
    if (!selected) return;
    setErr('');
    try {
      const res = await fetch(`${API}/api/investigations/${encodeURIComponent(selected)}`, {
        method: 'PATCH', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(statusForm),
      });
      const data = await res.json();
      if (!res.ok) throw new Error(data.detail || 'Failed');
      setDetail(data);
      setMsg(`Status updated to ${data.status}`);
      await load();
    } catch (e) {
      setErr(e.message);
    }
  };

  const addNote = async () => {
    if (!selected || !note.note.trim()) return;
    setErr('');
    try {
      const res = await fetch(`${API}/api/investigations/${encodeURIComponent(selected)}/notes`, {
        method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(note),
      });
      const data = await res.json();
      if (!res.ok) throw new Error(data.detail || 'Failed');
      setNote({ ...note, note: '' });
      await openCase(selected);
      setMsg('Note added');
    } catch (e) {
      setErr(e.message);
    }
  };

  const addEvidence = async () => {
    if (!selected || !evidenceForm.source_reference.trim()) return;
    setErr('');
    try {
      const res = await fetch(`${API}/api/investigations/${encodeURIComponent(selected)}/evidence`, {
        method: 'POST', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ ...evidenceForm, added_by: 'reviewer', metadata: { added_from: 'investigation workspace' } }),
      });
      const data = await res.json();
      if (!res.ok) throw new Error(data.detail || 'Failed');
      setEvidenceForm({ source_type: 'document', source_reference: '', excerpt: '' });
      await openCase(selected);
      setMsg('Evidence attached');
    } catch (e) {
      setErr(e.message);
    }
  };

  const explain = async () => {
    if (!selected) return;
    setErr('');
    setMsg('Generating grounded explanation…');
    try {
      const res = await fetch(`${API}/api/investigations/${encodeURIComponent(selected)}/explain`, { method: 'POST' });
      const data = await res.json();
      if (!res.ok) throw new Error(data.detail || 'Failed');
      setDetail((prev) => ({ ...prev, ai_explanation: data.explanation }));
      setMsg(data.fallback ? 'Explanation generated from evidence (Ollama offline)' : 'Explanation generated');
    } catch (e) {
      setErr(e.message);
    }
  };

  return (
    <>
      <header className="page-header"><div><h1>Investigation Cases</h1><p>Alerts to cases with status, reviewer, evidence, notes and history</p></div></header>
      {err && <div className="warning-box" style={{ marginBottom: 12 }}>{err}</div>}
      {msg && <div className="success-box" style={{ marginBottom: 12 }}>{msg}</div>}
      <section className="panel" style={{ padding: 18, marginBottom: 18, display: 'grid', gap: 10 }}>
        <div className="section-header"><div><h2>Open a new investigation</h2><p>Link to a transaction or vendor when known</p></div></div>
        <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit,minmax(180px,1fr))', gap: 10 }}>
          <input placeholder="Title" value={form.title} onChange={e=>setForm({...form,title:e.target.value})} style={{ padding: 8, borderRadius: 8 }}/>
          <select value={form.severity} onChange={e=>setForm({...form,severity:e.target.value})} style={{ padding: 8, borderRadius: 8 }}><option>low</option><option>medium</option><option>high</option><option>critical</option></select>
          <input placeholder="Transaction ID" value={form.transaction_id} onChange={e=>setForm({...form,transaction_id:e.target.value})} style={{ padding: 8, borderRadius: 8 }}/>
          <input placeholder="Vendor" value={form.vendor_name} onChange={e=>setForm({...form,vendor_name:e.target.value})} style={{ padding: 8, borderRadius: 8 }}/>
          <input placeholder="Assigned reviewer" value={form.assigned_to} onChange={e=>setForm({...form,assigned_to:e.target.value})} style={{ padding: 8, borderRadius: 8 }}/>
        </div>
        <textarea placeholder="Description" value={form.description} onChange={e=>setForm({...form,description:e.target.value})} style={{ padding: 8, borderRadius: 8, minHeight: 56 }}/>
        <div><button className="primary-btn" onClick={create}><Plus size={16}/>Create Investigation</button></div>
      </section>

      <div style={{ display: 'grid', gridTemplateColumns: 'minmax(280px,0.9fr) minmax(360px,1.4fr)', gap: 18 }}>
        <section className="table-card">
          <div className="section-header"><div><h2>Cases</h2><p>{total} persisted investigation(s)</p></div>{loading && <Loader2 size={16} className="spin"/>}</div>
          {cases.length === 0 ? <div className="empty-state"><div className="empty-icon"><Users size={24}/></div><strong>No cases</strong><span>Create one above or start from a risk alert.</span></div> : (
            <div className="data-table-wrap"><table className="data-table"><thead><tr><th>Case</th><th>Severity</th><th>Status</th><th>Created</th></tr></thead><tbody>
              {cases.map(c=>(<tr key={c.id} onClick={()=>openCase(c.case_number)} style={{ cursor: 'pointer', background: selected===c.case_number ? 'var(--rec-surface-alt)' : undefined }}>
                <td className="table-primary">{c.case_number}<div className="cell-muted">{c.title}</div></td>
                <td>{c.severity}</td><td><StatusBadge status={c.status}/></td><td>{formatDateTime(c.created_at)}</td>
              </tr>))}
            </tbody></table></div>
          )}
        </section>

        <section className="panel" style={{ padding: 18 }}>
          {!detail ? (
            <div className="empty-state"><div className="empty-icon"><FileSearch size={24}/></div><strong>Select a case</strong><span>Choose a case to review evidence, notes and history.</span></div>
          ) : (
            <>
              <div className="section-header"><div><h2>{detail.case_number} · {detail.title}</h2><p>Severity {detail.severity} · Assigned to {detail.assigned_to || 'unassigned'}</p></div><a className="secondary-btn" href={`${API}/api/investigations/${encodeURIComponent(detail.case_number)}/export.csv`}><Download size={16}/>Export</a></div>
              <div style={{ display: 'grid', gap: 14, marginTop: 12 }}>
                <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit,minmax(140px,1fr))', gap: 8 }}>
                  <select value={statusForm.status} onChange={e=>setStatusForm({...statusForm,status:e.target.value})} style={{ padding: 8, borderRadius: 8 }}><option>open</option><option>under_review</option><option>resolved</option><option>dismissed</option></select>
                  <input placeholder="Actor" value={statusForm.actor} onChange={e=>setStatusForm({...statusForm,actor:e.target.value})} style={{ padding: 8, borderRadius: 8 }}/>
                  <button className="primary-btn" onClick={updateStatus}>Update status</button>
                </div>
                <input placeholder="Resolution (optional)" value={statusForm.resolution} onChange={e=>setStatusForm({...statusForm,resolution:e.target.value})} style={{ padding: 8, borderRadius: 8 }}/>
                <textarea placeholder="Reviewer note (also logged on update)" value={statusForm.reviewer_notes} onChange={e=>setStatusForm({...statusForm,reviewer_notes:e.target.value})} style={{ padding: 8, borderRadius: 8, minHeight: 48 }}/>
                <div style={{ display: 'flex', gap: 8 }}><input placeholder="Note author" value={note.author} onChange={e=>setNote({...note,author:e.target.value})} style={{ padding: 8, borderRadius: 8, width: 140 }}/><input placeholder="Add a note" value={note.note} onChange={e=>setNote({...note,note:e.target.value})} style={{ padding: 8, borderRadius: 8, flex: 1 }}/><button className="secondary-btn" onClick={addNote}>Add note</button></div>
                <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit,minmax(140px,1fr))', gap: 8 }}>
                  <select value={evidenceForm.source_type} onChange={e=>setEvidenceForm({...evidenceForm,source_type:e.target.value})} style={{ padding: 8, borderRadius: 8 }}><option>document</option><option>transaction_record</option><option>rag_chunk</option><option>invoice</option></select>
                  <input placeholder="Source reference" value={evidenceForm.source_reference} onChange={e=>setEvidenceForm({...evidenceForm,source_reference:e.target.value})} style={{ padding: 8, borderRadius: 8 }}/>
                  <input placeholder="Excerpt" value={evidenceForm.excerpt} onChange={e=>setEvidenceForm({...evidenceForm,excerpt:e.target.value})} style={{ padding: 8, borderRadius: 8 }}/>
                  <button className="secondary-btn" onClick={addEvidence}>Attach evidence</button>
                </div>
                <div><button className="secondary-btn" onClick={explain}><Sparkles size={16}/>Generate AI explanation</button></div>
                {detail.ai_explanation && <div className="warning-box"><strong>AI explanation (human review required)</strong><p>{detail.ai_explanation}</p></div>}
              </div>
              <div style={{ marginTop: 16 }}>
                <h3>Related transactions</h3>
                {detail.transactions?.length ? <div className="data-table-wrap"><table className="data-table"><thead><tr><th>Txn</th><th>Date</th><th>Amount</th><th>Vendor</th></tr></thead><tbody>{detail.transactions.map(t=><tr key={t.id}><td className="table-primary">{t.transaction_id}</td><td>{t.date}</td><td>{fmtCurrency(t.amount)}</td><td>{t.vendor||t.description||'—'}</td></tr>)}</tbody></table></div> : <p className="kpi-subtle">No linked transaction.</p>}
              </div>
              <div style={{ marginTop: 16 }}>
                <h3>Evidence ({detail.evidence?.length || 0})</h3>
                {detail.evidence?.length ? <ul>{detail.evidence.map(e=><li key={e.id}><strong>{e.source_type}</strong> {e.source_reference||'—'} — {e.excerpt||''}</li>)}</ul> : <p className="kpi-subtle">No evidence attached.</p>}
              </div>
              <div style={{ marginTop: 16 }}>
                <h3>Notes ({detail.notes?.length || 0})</h3>
                {detail.notes?.length ? <ul>{detail.notes.map(n=><li key={n.id}><strong>{n.author}</strong> ({formatDateTime(n.created_at)}): {n.note}</li>)}</ul> : <p className="kpi-subtle">No notes yet.</p>}
              </div>
              <div style={{ marginTop: 16 }}>
                <h3>History</h3>
                {detail.history?.length ? <ul>{detail.history.map(e=><li key={e.id}>{formatDateTime(e.created_at)} · {e.action} · {e.actor||'system'} {e.previous_status?`(${e.previous_status} → ${e.new_status})`:''}</li>)}</ul> : <p className="kpi-subtle">No events.</p>}
              </div>
            </>
          )}
        </section>
      </div>
    </>
  );
}


function CompliancePage({ audits = [], reconciliations = [] }) {
  return (
    <>
      <header className="page-header"><div><h1>Compliance</h1><p>Actual available audit, findings and reconciliation data (no certification claimed)</p></div></header>
      <div className="kpi-grid">
        <article className="kpi-card"><div className="kpi-top"><div className="kpi-label"><span>Audit runs with evidence</span><strong>{audits.length}</strong></div><div className="kpi-icon blue"><ShieldCheck size={20}/></div></div><span className="kpi-subtle">Provenance stored</span></article>
        <article className="kpi-card"><div className="kpi-top"><div className="kpi-label"><span>Reconciliations</span><strong>{reconciliations.length}</strong></div><div className="kpi-icon green"><ArrowLeftRight size={20}/></div></div><span className="kpi-subtle">Persisted results</span></article>
        <article className="kpi-card"><div className="kpi-top"><div className="kpi-label"><span>Total findings</span><strong>{audits.reduce((s,a)=>s+(a.finding_count||0),0)}</strong></div><div className="kpi-icon amber"><AlertTriangle size={20}/></div></div><span className="kpi-subtle">Investigation leads</span></article>
      </div>
      <section className="panel" style={{ padding: 18 }}><p style={{ color: 'var(--dash-text-secondary)' }}>This page shows available compliance-related artifacts from persisted data. No regulatory certification is claimed.</p></section>
    </>
  );
}


function SettingsPage() {
  const [health, setHealth] = useState(null);
  const [error, setError] = useState('');
  const [loading, setLoading] = useState(false);
  const load = async () => {
    setLoading(true);
    setError('');
    try {
      const res = await fetch(`${API}/api/system/health`);
      const data = await res.json();
      if (!res.ok) throw new Error(data.detail || 'Failed to load system health');
      setHealth(data);
    } catch (e) {
      setError(e.message);
    } finally {
      setLoading(false);
    }
  };
  useEffect(() => { load(); }, []);
  const rows = [
    ['API base URL', API],
    ['System status', health?.status || 'unknown'],
    ['Backend API', health?.backend?.api || 'unknown'],
    ['Checked at (UTC)', health?.checked_at || '—'],
    ['Database backend', health?.database?.backend || '—'],
    ['Database connection', health?.database?.status || 'unknown'],
    ['Tables', health?.database?.tables ?? '—'],
    ['Transaction records', health?.database?.transaction_records ?? '—'],
    ['Risk alerts', health?.database?.risk_alerts ?? '—'],
    ['Reconciliation runs', health?.database?.reconciliation_runs ?? '—'],
    ['Investigation cases', health?.database?.investigation_cases ?? '—'],
    ['LLM provider', health?.llm?.provider || 'ollama'],
    ['Local model', health?.llm?.model || 'not configured'],
    ['LLM status', health?.llm?.status || 'unknown'],
    ['RAG backend', health?.rag?.backend || 'local-tfidf-sqlite'],
    ['RAG status', health?.rag?.status || 'empty'],
    ['Indexed documents', health?.rag?.documents ?? 0],
    ['Indexed chunks', health?.rag?.chunks ?? 0],
    ['Deterministic engine', health?.deterministic_engine || 'unknown'],
    ['Authentication enabled', health?.configuration?.authentication_enabled ? 'yes' : 'no'],
  ];
  return (
    <div className="settings-workspace">
      <section className="panel settings-panel">
        <div className="section-header"><div><h2>System health</h2><p>Every value comes from an actual runtime probe; no secrets are displayed</p></div>
          <button className="secondary-btn" type="button" onClick={load} disabled={loading}><RefreshCw size={16} className={loading ? 'spin' : ''}/>Refresh</button>
        </div>
        {error && <div className="error-box">{error}</div>}
        {health?.llm?.note && <div className="warning-box">{health.llm.note}</div>}
        <div className="settings-rows">
          {rows.map(([label, value]) => <div key={label}><span>{label}</span><strong>{String(value)}</strong></div>)}
        </div>
      </section>
      <section className="panel settings-panel">
        <div className="section-header"><div><h2>Operating principles</h2><p>How this deployment handles finance data and AI</p></div></div>
        <ul className="settings-notes">
          <li>No paid LLM API is used. Ollama runs locally; when offline, answers degrade to evidence-only summaries.</li>
          <li>Financial values are computed by deterministic SQL and Python rules; the model never modifies calculations.</li>
          <li>Reconciliation suggestions and risk alerts require human confirmation before any financial action.</li>
          <li>Configuration is read from environment variables; secrets are never shown in the UI.</li>
        </ul>
      </section>
    </div>
  );
}
