
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
  Users
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
  { id: 'monitoring', label: 'Monitoring', icon: Activity },
  { id: 'investigations', label: 'Investigations', icon: FileSearch },
  { id: 'cases', label: 'Cases', icon: Users },
  { id: 'reconciliation', label: 'Bank Reconciliation', icon: ArrowLeftRight },
  { id: 'reports', label: 'Reports', icon: BarChart3 },
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
    monitoring: { eyebrow: 'Monitoring', title: 'Transaction Monitoring', description: 'Track incoming transactions and detection signals.' },
    investigations: { eyebrow: 'Investigations', title: 'Investigations Workspace', description: 'Review audit findings and evidence.' },
    cases: { eyebrow: 'Cases', title: 'Case Management', description: 'Manage investigation cases and assignments.' },
    reconciliation: { eyebrow: 'Reconciliation', title: 'Bank Reconciliation', description: 'Match bank statements to the accounting ledger.' },
    reports: { eyebrow: 'Reports', title: 'Reports & Exports', description: 'Generate and download compliance reports.' },
    compliance: { eyebrow: 'Compliance', title: 'Compliance Overview', description: 'Track policy adherence and review checkpoints.' },
    settings: { eyebrow: 'Settings', title: 'Configuration', description: 'Application and integration settings.' }
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
          <PlaceholderPage
            title="Transaction Monitoring"
            description="Real-time monitoring views are not implemented in the current backend. Audit runs are the available entry point for review."
            icon={Activity}
          />
        )}
        {activeView === 'investigations' && (
          <PlaceholderPage
            title="Investigations Workspace"
            description="Use the overview audit findings or the existing assistant workflow to investigate exceptions. Case-level workflow is not yet implemented."
            icon={FileSearch}
          />
        )}
        {activeView === 'cases' && (
          <PlaceholderPage
            title="Case Management"
            description="Case tracking is not implemented in the current codebase."
            icon={Users}
          />
        )}
        {activeView === 'reconciliation' && (
          <ReconciliationPage
            reconciliation={reconciliation}
            setReconciliation={setReconciliation}
            history={reconciliations}
            loadHistory={loadOverview}
          />
        )}
        {activeView === 'reports' && (
          <PlaceholderPage
            title="Reports & Exports"
            description="Use Audit CSV export or Reconciliation CSV export from their respective workflows."
            icon={BarChart3}
          />
        )}
        {activeView === 'compliance' && (
          <PlaceholderPage
            title="Compliance"
            description="Compliance policies exist in the RAG layer for explanations, but a compliance dashboard is not implemented."
            icon={ShieldCheck}
          />
        )}
        {activeView === 'settings' && (
          <PlaceholderPage
            title="Settings"
            description="Configuration is handled via environment variables and existing Vite API configuration."
            icon={Settings}
          />
        )}
      </main>
    </div>
  );
}

createRoot(document.getElementById('root')).render(<App />);
