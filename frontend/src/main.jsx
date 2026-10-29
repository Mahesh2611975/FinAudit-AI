
import React, { useEffect, useMemo, useState } from 'react';
import { createRoot } from 'react-dom/client';
import {
  Activity,
  AlertTriangle,
  ArrowLeftRight,
  BarChart3,
  BrainCircuit,
  CheckCircle2,
  ChevronRight,
  CircleDollarSign,
  Clock3,
  Download,
  FileSearch,
  FileText,
  Filter,
  LayoutDashboard,
  Menu,
  RefreshCw,
  Search,
  Settings,
  ShieldCheck,
  Upload,
  Users,
  Sparkles,
  Calendar,
  PieChart as PieChartIcon
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

const API = (
  import.meta.env.VITE_API_URL || 'http://127.0.0.1:8000'
).replace(/\/$/, '');

const NAV_ITEMS = [
  { id: 'overview', label: 'Overview', icon: LayoutDashboard },
  { id: 'analytics', label: 'Financial Analytics', icon: BarChart3 },
  { id: 'monitoring', label: 'Monitoring', icon: Activity },
  { id: 'investigations', label: 'Investigations', icon: FileSearch },
  { id: 'cases', label: 'Cases', icon: Users },
  { id: 'reconciliation', label: 'Bank Reconciliation', icon: ArrowLeftRight },
  { id: 'agents', label: 'AI Agent Center', icon: Sparkles },
  { id: 'reports', label: 'Reports', icon: FileText },
  { id: 'compliance', label: 'Compliance', icon: ShieldCheck },
  { id: 'settings', label: 'Settings', icon: Settings }
];

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

function formatDateTime(value) {
  if (!value) return '—';
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return value;
  return date.toLocaleString();
}

function StatusBadge({ status }) {
  const label = String(status || '').replaceAll('_', ' ');
  if (status === 'matched' || status === 'reconciled') {
    return <span className="badge badge-matched">{label}</span>;
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
  const [tolerance, setTolerance] = useState(3);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState('');
  const [warnings, setWarnings] = useState([]);
  const [filter, setFilter] = useState('all');

  const results = reconciliation?.results || [];
  const summary = reconciliation?.summary || {};
  const step = reconciliation ? 3 : bankFile && ledgerFile ? 1 : 0;

  const filtered = useMemo(() => {
    if (filter === 'all') return results;
    if (filter === 'unmatched') {
      return results.filter((item) =>
        ['bank_only', 'ledger_only'].includes(item.status)
      );
    }
    return results.filter((item) => item.status === filter);
  }, [results, filter]);

  const chooseFile = (side, event) => {
    const selected = event.target.files?.[0] || null;
    if (selected && !selected.name.toLowerCase().endsWith('.csv')) {
      setError(
        `${side === 'bank' ? 'Bank statement' : 'Accounting ledger'} must be a .csv file.`
      );
      return;
    }
    setError('');
    if (side === 'bank') {
      setBankFile(selected);
    } else {
      setLedgerFile(selected);
    }
  };

  const runReconciliation = async () => {
    if (!bankFile || !ledgerFile) {
      setError('Select both a bank statement CSV and an accounting ledger CSV.');
      return;
    }

    setLoading(true);
    setError('');
    setWarnings([]);
    setReconciliation(null);
    setFilter('all');

    try {
      const formData = new FormData();
      formData.append('bank_file', bankFile);
      formData.append('ledger_file', ledgerFile);
      formData.append('date_tolerance_days', String(tolerance));

      const response = await fetch(`${API}/api/reconciliations`, {
        method: 'POST',
        body: formData
      });
      const data = await response.json();

      if (!response.ok) {
        const detail = Array.isArray(data.detail)
          ? data.detail.join(' ')
          : data.detail;
        throw new Error(detail || 'Reconciliation failed.');
      }

      setReconciliation(data);
      setWarnings(Array.isArray(data.warnings) ? data.warnings : []);
      await loadHistory?.();
    } catch (err) {
      setError(err.message || 'Could not connect to the backend.');
    } finally {
      setLoading(false);
    }
  };

  const exportHref = reconciliation
    ? `${API}/api/reconciliations/${reconciliation.reconciliation_id}/export.csv`
    : null;

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
          <p>
            Match transactions between your bank statement and accounting ledger.
            Reference matches are preferred; ambiguous or uncertain matches are
            surfaced for review.
          </p>
        </div>
        {exportHref && (
          <a className="secondary-btn" href={exportHref}>
            <Download size={16} />
            Export CSV
          </a>
        )}
      </header>

      <div className="stepper">
        {RECON_STEPS.map((label, index) => (
          <div
            key={label}
            className={`step-item ${index === step ? 'active' : ''} ${
              index < step ? 'completed' : ''
            }`}
          >
            <div className="step-circle">
              {index < step ? <CheckCircle2 size={16} /> : index + 1}
            </div>
            <span>{label}</span>
          </div>
        ))}
      </div>

      <div className="rec-grid">
        <section className="panel upload-card">
          <div className="section-header">
            <div>
              <h2>Bank Statement</h2>
              <p>Upload the CSV extract from your bank</p>
            </div>
          </div>
          <label className="dropzone-light">
            <input
              type="file"
              accept=".csv,text/csv"
              onChange={(event) => chooseFile('bank', event)}
            />
            <div className="dropzone-icon">
              <Upload size={22} />
            </div>
            <strong>{bankFile ? bankFile.name : 'Drop or browse bank CSV'}</strong>
            <span className="kpi-subtle">
              Accepts date, amount, debit, credit, reference, narration
            </span>
          </label>
        </section>

        <section className="panel upload-card">
          <div className="section-header">
            <div>
              <h2>Accounting Ledger</h2>
              <p>Upload the CSV extract from your ledger</p>
            </div>
          </div>
          <label className="dropzone-light">
            <input
              type="file"
              accept=".csv,text/csv"
              onChange={(event) => chooseFile('ledger', event)}
            />
            <div className="dropzone-icon">
              <FileText size={22} />
            </div>
            <strong>{ledgerFile ? ledgerFile.name : 'Drop or browse ledger CSV'}</strong>
            <span className="kpi-subtle">
              Column mapping is applied independently per file
            </span>
          </label>
        </section>
      </div>

      <section className="panel" style={{ padding: '18px 26px', marginBottom: 22 }}>
        <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', gap: 16, flexWrap: 'wrap' }}>
          <div style={{ display: 'flex', alignItems: 'center', gap: 14 }}>
            <label style={{ display: 'flex', alignItems: 'center', gap: 10, fontSize: 13, color: 'var(--rec-text-secondary)' }}>
              Date tolerance
              <input
                type="number"
                min="0"
                max="365"
                value={tolerance}
                onChange={(event) => setTolerance(event.target.value)}
                style={{
                  width: 90,
                  padding: '8px 10px',
                  borderRadius: 8,
                  border: '1px solid var(--rec-border-strong)',
                  background: 'var(--rec-surface)'
                }}
              />
              days
            </label>
            <span className="kpi-subtle">Used only when no safe reference match exists</span>
          </div>
          <button
            className="primary-btn"
            type="button"
            onClick={runReconciliation}
            disabled={!bankFile || !ledgerFile || loading}
          >
            {loading ? (
              <>
                <RefreshCw size={16} className="spin" />
                Matching transactions...
              </>
            ) : (
              <>
                <ArrowLeftRight size={16} />
                Review & Match
              </>
            )}
          </button>
        </div>
        {error && <div className="error-box">{error}</div>}
        {warnings.length > 0 && <div className="warning-box">{warnings.join(' ')}</div>}
      </section>

      {reconciliation && (
        <>
          <div className="summary-grid">
            <article className="panel summary-card matched">
              <div className="summary-card-header">
                <span className="kpi-label">
                  <span>Matched</span>
                </span>
                <CheckCircle2 size={18} />
              </div>
              <strong>{fmtNumber(summary.matched_count || 0)}</strong>
              <span className="kpi-subtle">{fmtCurrency(summary.matched_total || 0)}</span>
            </article>
            <article className="panel summary-card suggested">
              <div className="summary-card-header">
                <span className="kpi-label">
                  <span>Suggested</span>
                </span>
                <Search size={18} />
              </div>
              <strong>{fmtNumber((summary.amount_mismatch_count || 0) + (summary.date_mismatch_count || 0))}</strong>
              <span className="kpi-subtle">Needs review (mismatch)</span>
            </article>
            <article className="panel summary-card unmatched">
              <div className="summary-card-header">
                <span className="kpi-label">
                  <span>Unmatched</span>
                </span>
                <AlertTriangle size={18} />
              </div>
              <strong>{fmtNumber((summary.bank_only_count || 0) + (summary.ledger_only_count || 0))}</strong>
              <span className="kpi-subtle">{fmtCurrency((summary.bank_unmatched_total || 0) + (summary.ledger_unmatched_total || 0))}</span>
            </article>
            <article className="panel summary-card reconciled">
              <div className="summary-card-header">
                <span className="kpi-label">
                  <span>Already reconciled</span>
                </span>
                <ShieldCheck size={18} />
              </div>
              <strong>{fmtNumber(summary.matched_count || 0)}</strong>
              <span className="kpi-subtle">Difference {fmtCurrency(summary.difference || 0)}</span>
            </article>
          </div>

          <section className="panel table-card-light">
            <div className="table-header">
              <div>
                <h2>Transactions</h2>
                <p>
                  {reconciliation.bank_file} ↔ {reconciliation.ledger_file} ·{' '}
                  {reconciliation.date_tolerance_days} day tolerance
                </p>
              </div>
              <div className="filter-bar" style={{ marginBottom: 0 }}>
                <button
                  className={`filter-chip-light ${filter === 'all' ? 'active' : ''}`}
                  type="button"
                  onClick={() => setFilter('all')}
                >
                  All ({results.length})
                </button>
                <button
                  className={`filter-chip-light ${filter === 'matched' ? 'active' : ''}`}
                  type="button"
                  onClick={() => setFilter('matched')}
                >
                  Matched ({summary.matched_count || 0})
                </button>
                <button
                  className={`filter-chip-light ${filter === 'unmatched' ? 'active' : ''}`}
                  type="button"
                  onClick={() => setFilter('unmatched')}
                >
                  Unmatched ({(summary.bank_only_count || 0) + (summary.ledger_only_count || 0)})
                </button>
                <button
                  className={`filter-chip-light ${filter === 'amount_mismatch' ? 'active' : ''}`}
                  type="button"
                  onClick={() => setFilter('amount_mismatch')}
                >
                  Mismatch ({(summary.amount_mismatch_count || 0) + (summary.date_mismatch_count || 0)})
                </button>
                <button
                  className={`filter-chip-light ${filter === 'ambiguous' ? 'active' : ''}`}
                  type="button"
                  onClick={() => setFilter('ambiguous')}
                >
                  Ambiguous ({summary.ambiguous_count || 0})
                </button>
              </div>
            </div>

            {filtered.length === 0 ? (
              <div className="empty-state">
                <div className="empty-icon">
                  <Filter size={24} />
                </div>
                <strong>No transactions in this view</strong>
                <span>Adjust the filter to review other statuses.</span>
              </div>
            ) : (
              <div className="table-scroll">
                <table className="rec-table">
                  <thead>
                    <tr>
                      <th>Select</th>
                      <th>Date</th>
                      <th>Description</th>
                      <th>Amount</th>
                      <th>Reference</th>
                      <th>Match status</th>
                      <th>Source</th>
                      <th>Confidence</th>
                      <th>Note</th>
                    </tr>
                  </thead>
                  <tbody>
                    {filtered.map((item, index) => {
                      const bankSide = item.bank_date || item.bank_amount != null;
                      const ledgerSide = item.ledger_date || item.ledger_amount != null;
                      const dateValue = item.bank_date || item.ledger_date;
                      const descriptionValue = item.bank_description || item.ledger_description;
                      const amountValue = bankSide ? item.bank_amount : item.ledger_amount;
                      const referenceValue = item.bank_reference || item.ledger_reference;
                      const sourceFile = item.bank_source_file || item.ledger_source_file;
                      const sourceRow = item.bank_source_row || item.ledger_source_row;
                      const confidence =
                        item.status === 'matched'
                          ? 'High'
                          : item.status === 'ambiguous'
                          ? 'Unavailable'
                          : ['amount_mismatch', 'date_mismatch'].includes(item.status)
                          ? 'Low'
                          : 'N/A';

                      return (
                        <tr key={item.id || index}>
                          <td>
                            <input type="checkbox" disabled title="No backend action to confirm matches in this view" />
                          </td>
                          <td className="cell-primary">{dateValue || '—'}</td>
                          <td>
                            <div style={{ maxWidth: 260 }}>
                              <div className="cell-primary">{descriptionValue || '—'}</div>
                            </div>
                          </td>
                          <td className="cell-primary">{amountValue == null ? '—' : fmtCurrency(amountValue)}</td>
                          <td className="cell-muted">{referenceValue || '—'}</td>
                          <td>
                            <StatusBadge status={item.status} />
                          </td>
                          <td>
                            <div className="source-stack">
                              <span className="cell-primary">{sourceFile || '—'}</span>
                              <span className="cell-muted">Row {sourceRow || '—'}</span>
                            </div>
                          </td>
                          <td className="cell-muted">{confidence}</td>
                          <td>
                            <span className="cell-muted">{item.note || '—'}</span>
                          </td>
                        </tr>
                      );
                    })}
                  </tbody>
                </table>
              </div>
            )}
          </section>
        </>
      )}
    </>
  );
}

function App() {
  const [activeView, setActiveView] = useState('overview');
  const [mobileMenuOpen, setMobileMenuOpen] = useState(false);
  const [audits, setAudits] = useState([]);
  const [reconciliations, setReconciliations] = useState([]);
  const [reconciliation, setReconciliation] = useState(null);
  const [loadingOverview, setLoadingOverview] = useState(false);

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
    const handler = (event) => {
      if (typeof event.detail === 'string' && NAV_ITEMS.some((item) => item.id === event.detail)) {
        setActiveView(event.detail);
        setMobileMenuOpen(false);
      }
    };
    document.addEventListener('finaudit:navigate', handler);
    return () => document.removeEventListener('finaudit:navigate', handler);
  }, []);

  const pageTitle = {
    overview: { eyebrow: 'Dashboard', title: 'Financial Risk Overview', description: 'Monitor audits, findings, and reconciliation activity at a glance.' },
    analytics: { eyebrow: 'Analytics', title: 'Financial Analytics', description: 'Monthly spending, categories, trends and transaction details from actual data.' },
    monitoring: { eyebrow: 'Monitoring', title: 'Audit Activity Monitoring', description: 'Historical audit runs, findings by risk/type, and activity.' },
    investigations: { eyebrow: 'Investigations', title: 'Investigations', description: 'Findings with source evidence and provenance.' },
    cases: { eyebrow: 'Cases', title: 'Investigation Cases', description: 'Create and manage cases linked to findings with status/notes.' },
    reconciliation: { eyebrow: 'Reconciliation', title: 'Bank Reconciliation', description: 'Match bank statements to the accounting ledger.' },
    agents: { eyebrow: 'AI', title: 'AI Agent Center', description: 'LangGraph multi-agent investigation workflow with evidence and human review.' },
    reports: { eyebrow: 'Reports', title: 'Reports & Exports', description: 'Generate and download reports and exports from actual data.' },
    compliance: { eyebrow: 'Compliance', title: 'Compliance', description: 'Actual available audit and evidence information.' },
    settings: { eyebrow: 'Settings', title: 'Settings', description: 'Real configuration status.' }
  }[activeView];

  return (
    <div className={`app-shell ${themeClass}`}>
      <aside className={`sidebar ${mobileMenuOpen ? 'sidebar-open' : ''}`}>
        <div className="brand">
          <div className="brand-mark">
            <ShieldCheck size={24} />
          </div>
          <div className="brand-text">
            <b>
              FinAudit<span> AI</span>
            </b>
            <small>Enterprise Risk Platform</small>
          </div>
        </div>

        <div className="side-label">Navigation</div>
        {NAV_ITEMS.map((item) => {
          const Icon = item.icon;
          return (
            <button
              key={item.id}
              type="button"
              className={`nav ${activeView === item.id ? 'active' : ''}`}
              onClick={() => {
                setActiveView(item.id);
                setMobileMenuOpen(false);
              }}
            >
              <Icon size={18} />
              <span>{item.label}</span>
            </button>
          );
        })}

        <div className="sidebar-bottom">
          <div className="local-badge">
            <span />
            API-connected mode
          </div>
          <small>Local-first · Human review required · Findings are investigation leads</small>
        </div>
      </aside>

      <main className="main">
        <header className="topbar">
          <div style={{ display: 'flex', alignItems: 'flex-start', gap: 16 }}>
            <button
              className="mobile-menu-btn"
              type="button"
              onClick={() => setMobileMenuOpen((open) => !open)}
              aria-label="Toggle navigation"
            >
              <Menu size={20} />
            </button>
            <div className="topbar-heading">
              <div className="eyebrow">{pageTitle.eyebrow}</div>
              <h1>{pageTitle.title}</h1>
              <p>{pageTitle.description}</p>
            </div>
          </div>
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
        </header>

        {activeView === 'overview' && (
          <OverviewPage
            audits={audits}
            reconciliations={reconciliations}
            loading={loadingOverview}
            onAuditCreated={loadOverview}
            onNavigateInvestigations={() => {
              setActiveView('investigations');
              setMobileMenuOpen(false);
            }}
          />
        )}
        {activeView === 'monitoring' && (
          <MonitoringPage audits={audits} loadOverview={loadOverview} />
        )}
        {activeView === 'investigations' && (
          <InvestigationsPage audits={audits} onNavigate={setActiveView} />
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
        {activeView === 'agents' && (
          <AgentsPage />
        )}
        {activeView === 'reports' && (
          <ReportsPage audits={audits} reconciliations={reconciliations} />
        )}
        {activeView === 'compliance' && (
          <CompliancePage audits={audits} reconciliations={reconciliations} />
        )}
        {activeView === 'settings' && (
          <SettingsPage />
        )}
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
        <article className="kpi-card"><div className="kpi-top"><div className="kpi-label"><span>Total spending</span><strong>{summary ? fmtCurrency(summary.total_spend) : '—'}</strong></div><div className="kpi-icon amber"><CircleDollarSign size={20}/></div></div><span className="kpi-subtle">Debits/outflows (transfers excluded from consumption)</span></article>
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

  const run = async () => {
    setRunning(true); setError(''); setResult(null);
    try {
      const res = await fetch(`${API}/api/agents/run`, { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ task_type: taskType, year, month }) });
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
          <button className="primary-btn" onClick={run} disabled={running}><Sparkles size={16}/>{running ? 'Running...' : 'Start Analysis'}</button>
        </div>
        <div style={{ marginTop: 10, color: 'var(--dash-text-secondary)', fontSize: 13 }}>Agents: Intake → Reconciliation → Anomaly Investigation → Financial Analysis → Evidence Retrieval/RAG → Synthesis → Reporting</div>
      </section>
      {error && <div className="error-box">{error}</div>}
      {result && (
        <div className="dashboard-grid">
          <section className="chart-card">
            <div className="section-header"><div><h2>Workflow Timeline</h2><p>Status: {result.status} {result.duration_ms ? `(${result.duration_ms}ms)` : ''}</p></div></div>
            <div style={{ display: 'grid', gap: 8 }}>
              {(result.timeline||[]).map((t,i)=>(
                <div key={i} style={{ display: 'flex', justifyContent: 'space-between', padding: '6px 0', borderBottom: '1px solid rgba(148,163,184,0.12)' }}>
                  <span>{t.agent_name}</span>
                  <span>{t.status}</span>
                </div>
              ))}
            </div>
            {result.require_human_review && <div className="warning-box" style={{ marginTop: 10 }}>Human review checkpoint triggered (insufficient evidence)</div>}
          </section>
          <section className="chart-card">
            <div className="section-header"><div><h2>Outputs</h2><p>Structured results with evidence/uncertainty</p></div></div>
            <pre style={{ whiteSpace: 'pre-wrap', fontSize: 12, color: 'var(--dash-text-secondary)' }}>{JSON.stringify(result.outputs, null, 2)}</pre>
          </section>
        </div>
      )}
      <section className="table-card">
        <div className="section-header"><div><h2>Investigation Run History</h2><p>Persisted runs from orchestration</p></div></div>
        {runs.length === 0 ? <div className="empty-state"><div className="empty-icon"><Sparkles size={24}/></div><strong>No runs yet</strong><span>Start an analysis to create a run.</span></div> : (
          <div className="data-table-wrap">
            <table className="data-table">
              <thead><tr><th>ID</th><th>Task</th><th>Status</th><th>Created</th></tr></thead>
              <tbody>
                {runs.map(r => (<tr key={r.id}><td>{r.id}</td><td>{r.task_type}</td><td>{r.status}</td><td>{r.created_at || '—'}</td></tr>))}
              </tbody>
            </table>
          </div>
        )}
      </section>
    </>
  );
}


function ReportsPage({ audits = [], reconciliations = [] }) {
  return (
    <>
      <header className="page-header">
        <div>
          <h1>Reports & Exports</h1>
          <p>Export audit findings, reconciliation results, and analytics.</p>
        </div>
      </header>
      <div className="quick-actions">
        <a className="action-card" href={`${API}/api/analytics/export.csv`}><div className="icon-wrap"><Download size={20}/></div><div><strong>Export all analytics (CSV)</strong><span>Transactions with provenance</span></div></a>
        {(audits[0]?.id) && <a className="action-card" href={`${API}/api/audits/${audits[0].id}/export.csv`}><div className="icon-wrap"><Download size={20}/></div><div><strong>Latest audit findings (CSV)</strong><span>Audit #{audits[0].id}</span></div></a>}
        {(reconciliations[0]?.id || reconciliations[0]?.reconciliation_id) && <a className="action-card" href={`${API}/api/reconciliations/${reconciliations[0].id || reconciliations[0].reconciliation_id}/export.csv`}><div className="icon-wrap"><Download size={20}/></div><div><strong>Latest reconciliation (CSV)</strong><span>Run export</span></div></a>}
      </div>
      <section className="panel" style={{ padding: 18 }}>
        <p style={{ color: 'var(--dash-text-secondary)' }}>All exports link to actual backend data. Deterministic results distinguished from AI interpretations in Agent Center.</p>
      </section>
    </>
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
  const findings = (audits[0]?.findings)||[];
  return (
    <>
      <header className="page-header"><div><h1>Investigations</h1><p>Findings with source evidence and provenance</p></div></header>
      <section className="table-card">
        <div className="section-header"><div><h2>Latest findings</h2><p>From most recent audit run</p></div></div>
        {findings.length === 0 ? <div className="empty-state"><div className="empty-icon"><FileSearch size={24}/></div><strong>No findings</strong><span>Run an audit to generate findings.</span></div> : (
          <div className="data-table-wrap"><table className="data-table"><thead><tr><th>Finding</th><th>Txn ID</th><th>Source file</th><th>Amount</th><th>Risk</th><th>Type</th></tr></thead><tbody>
            {findings.map(f=>(<tr key={f.id}><td className="table-primary">{f.summary}</td><td>{f.transaction_id||'—'}</td><td>{f.source_file||'—'}</td><td>{fmtCurrency(f.amount)}</td><td><StatusBadge status={f.risk_level}/></td><td>{f.finding_type}</td></tr>))}
          </tbody></table></div>
        )}
      </section>
      <section className="panel" style={{ padding: 18 }}><p style={{ color: 'var(--dash-text-secondary)' }}>Use Cases page to create/update investigation cases linked to findings. Evidence includes source filename/row from normalized transactions.</p></section>
    </>
  );
}


function CasesPage({ audits = [] }) {
  const [cases, setCases] = useState([]);
  const [loading, setLoading] = useState(false);
  const [form, setForm] = useState({ title: '', description: '', severity: 'medium', status: 'open' });
  const [msg, setMsg] = useState('');

  const load = async () => {
    setLoading(true);
    try {
      const res = await fetch(`${API}/api/cases`);
      if (res.ok) setCases(await res.json());
    } catch {}
    finally { setLoading(false); }
  };
  useEffect(()=>{ load(); }, []);

  const create = async () => {
    setMsg('');
    try {
      const res = await fetch(`${API}/api/cases`, { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ ...form, audit_run_id: audits[0]?.id }) });
      const data = await res.json();
      if (!res.ok) throw new Error(data.detail || 'Failed');
      setMsg('Case created');
      setForm({ title: '', description: '', severity: 'medium', status: 'open' });
      load();
    } catch(e) { setMsg(e.message); }
  };

  return (
    <>
      <header className="page-header"><div><h1>Cases</h1><p>Investigation cases with status, priority, notes and timestamps</p></div></header>
      <section className="panel" style={{ padding: 18, marginBottom: 18, display: 'grid', gap: 10 }}>
        <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit,minmax(200px,1fr))', gap: 10 }}>
          <input placeholder="Title" value={form.title} onChange={e=>setForm({...form,title:e.target.value})} style={{ padding: 8, borderRadius: 8 }}/>
          <select value={form.severity} onChange={e=>setForm({...form,severity:e.target.value})} style={{ padding: 8, borderRadius: 8 }}><option>low</option><option>medium</option><option>high</option><option>critical</option></select>
          <select value={form.status} onChange={e=>setForm({...form,status:e.target.value})} style={{ padding: 8, borderRadius: 8 }}><option>open</option><option>in_review</option><option>resolved</option><option>closed</option></select>
        </div>
        <textarea placeholder="Description" value={form.description} onChange={e=>setForm({...form,description:e.target.value})} style={{ padding: 8, borderRadius: 8, minHeight: 60 }}/>
        <div><button className="primary-btn" onClick={create}><Users size={16}/>Create Case</button> {msg && <span style={{ marginLeft: 10 }}>{msg}</span>}</div>
      </section>
      <section className="table-card">
        <div className="section-header"><div><h2>Cases</h2><p>Persisted in database</p></div></div>
        {cases.length === 0 ? <div className="empty-state"><div className="empty-icon"><Users size={24}/></div><strong>No cases</strong><span>Create a case above.</span></div> : (
          <div className="data-table-wrap"><table className="data-table"><thead><tr><th>ID</th><th>Title</th><th>Severity</th><th>Status</th><th>Audit run</th><th>Created</th></tr></thead><tbody>
            {cases.map(c=>(<tr key={c.id}><td>{c.id}</td><td className="table-primary">{c.title}</td><td>{c.severity}</td><td>{c.status}</td><td>{c.audit_run_id||'—'}</td><td>{formatDateTime(c.created_at)}</td></tr>))}
          </tbody></table></div>
        )}
      </section>
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
  const load = async () => {
    try {
      const res = await fetch(`${API}/api/health`);
      if (res.ok) setHealth(await res.json());
    } catch {}
  };
  useEffect(()=>{ load(); }, []);
  return (
    <>
      <header className="page-header"><div><h1>Settings</h1><p>Real configuration status (secrets not displayed)</p></div></header>
      <section className="panel" style={{ padding: 18, marginBottom: 18 }}>
        <div style={{ display: 'grid', gap: 8 }}>
          <div style={{ display: 'flex', justifyContent: 'space-between' }}><span>API Base URL</span><span>{API}</span></div>
          <div style={{ display: 'flex', justifyContent: 'space-between' }}><span>Backend health</span><span>{health ? health.status : 'unknown'}</span></div>
          <div style={{ display: 'flex', justifyContent: 'space-between' }}><span>Database</span><span>SQLite (local)</span></div>
          <div style={{ display: 'flex', justifyContent: 'space-between' }}><span>LLM</span><span>Groq optional, Ollama optional (env-based)</span></div>
        </div>
      </section>
      <section className="panel" style={{ padding: 18 }}><p style={{ color: 'var(--dash-text-secondary)' }}>No API keys or secrets are displayed. Configure via environment variables (.env not committed).</p></section>
    </>
  );
}
