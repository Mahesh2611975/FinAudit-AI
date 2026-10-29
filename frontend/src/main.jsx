
import React, { useEffect, useState } from 'react';
import { createRoot } from 'react-dom/client';
import {
  ShieldCheck,
  Upload,
  FileSearch,
  Activity,
  AlertTriangle,
  CircleDollarSign,
  Download,
  BrainCircuit,
  RefreshCw,
  ArrowLeft,
  FileText,
  Search,
  Sparkles,
  Clock3,
  Menu,
  ArrowLeftRight
} from 'lucide-react';
import './style.css';

const API = (
  import.meta.env.VITE_API_URL || 'http://127.0.0.1:8000'
).replace(/\/$/, '');

function App() {
  const [activeView, setActiveView] = useState('overview');
  const [files, setFiles] = useState([]);
  const [audit, setAudit] = useState(null);
  const [audits, setAudits] = useState([]);
  const [loading, setLoading] = useState(false);
  const [historyLoading, setHistoryLoading] = useState(false);
  const [error, setError] = useState('');
  const [warnings, setWarnings] = useState([]);
  const [explanations, setExplanations] = useState({});
  const [selectedFindingId, setSelectedFindingId] = useState('');
  const [assistantQuestion, setAssistantQuestion] = useState('');
  const [mobileMenuOpen, setMobileMenuOpen] = useState(false);

  const findings = audit?.findings || [];
  const selectedFinding = findings.find(
    (finding) => String(finding.id) === String(selectedFindingId)
  );

  const highRisk = findings.filter(
    (finding) => finding.risk_level === 'high'
  ).length;

  const flaggedAmount = findings.reduce(
    (total, finding) => total + (Number(finding.amount) || 0),
    0
  );

  const fmt = (amount) =>
    new Intl.NumberFormat('en-IN', {
      style: 'currency',
      currency: 'INR',
      maximumFractionDigits: 2
    }).format(Number(amount) || 0);

  async function loadAudits() {
    setHistoryLoading(true);

    try {
      const response = await fetch(`${API}/api/audits`);

      if (!response.ok) {
        throw new Error('Could not load audit history.');
      }

      const data = await response.json();
      setAudits(Array.isArray(data) ? data : []);
    } catch (err) {
      console.error('Audit history error:', err);
    } finally {
      setHistoryLoading(false);
    }
  }

  useEffect(() => {
    loadAudits();
  }, []);

  async function runAudit() {
    if (files.length === 0) {
      setError('Please select at least one CSV file first.');
      return;
    }

    setLoading(true);
    setError('');
    setWarnings([]);
    setAudit(null);
    setSelectedFindingId('');
    setExplanations({});

    try {
      const formData = new FormData();
      files.forEach((selectedFile) => {
        formData.append('files', selectedFile);
      });

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

      setAudit(data);
      setWarnings(Array.isArray(data.warnings) ? data.warnings : []);

      if (data.findings?.length) {
        setSelectedFindingId(String(data.findings[0].id));
      }

      await loadAudits();
    } catch (err) {
      setError(
        err.message ||
          'Could not connect to the backend. Make sure FastAPI is running.'
      );
    } finally {
      setLoading(false);
    }
  }

  async function explainFinding(id, question = '') {
    if (!id) {
      setError('Select a finding to investigate.');
      return;
    }

    setExplanations((previous) => ({
      ...previous,
      [id]: {
        ...previous[id],
        loading: true,
        error: ''
      }
    }));

    try {
      // The existing backend endpoint explains a stored finding.
      // No unsupported general chat endpoint is assumed here.
      const response = await fetch(
        `${API}/api/findings/${id}/explain`,
        { method: 'POST' }
      );

      const data = await response.json();

      if (!response.ok) {
        throw new Error(
          data.detail || 'Could not explain this finding.'
        );
      }

      setExplanations((previous) => ({
        ...previous,
        [id]: {
          loading: false,
          data,
          error: ''
        }
      }));

      if (question) {
        setAssistantQuestion(question);
      }
    } catch (err) {
      setExplanations((previous) => ({
        ...previous,
        [id]: {
          ...previous[id],
          loading: false,
          error: err.message || 'AI explanation failed.'
        }
      }));
    }
  }

  function navigate(view) {
    setActiveView(view);
    setMobileMenuOpen(false);
    setError('');
  }

  function NavItem({ view, icon: Icon, children }) {
    return (
      <button
        type="button"
        className={`nav ${activeView === view ? 'active' : ''}`}
        onClick={() => navigate(view)}
        aria-current={activeView === view ? 'page' : undefined}
      >
        <Icon size={17} />
        <span>{children}</span>
      </button>
    );
  }

  function FindingCard({ finding }) {
    const explanation = explanations[finding.id];
    const isLoading = Boolean(explanation?.loading);

    return (
      <article className="finding" key={finding.id}>
        <div className="finding-icon">
          <AlertTriangle size={18} />
        </div>

        <div className="finding-body">
          <div className="finding-title-row">
            <b>{finding.summary}</b>
            <span className={`risk ${finding.risk_level}`}>
              {finding.risk_level} risk
            </span>
          </div>

          <p>{finding.evidence}</p>

          <div className="finding-meta">
            <span>Ref: {finding.transaction_id || 'N/A'}</span>
            {finding.source_file && (
              <span>File: {finding.source_file}</span>
            )}
            <span>
              {String(finding.finding_type || '').replaceAll('_', ' ')}
            </span>
            <span>{fmt(finding.amount)}</span>
          </div>

          {explanation?.data && (
            <div className="ai-answer">
              <div className="ai-answer-title">
                <BrainCircuit size={16} />
                AI investigation notes
                {explanation.data.fallback ? (
                  <span className="muted">
                    (Fallback explanation)
                  </span>
                ) : (
                  <span className="muted">
                    · {explanation.data.model || 'AI model'}
                  </span>
                )}
              </div>

              <p>{explanation.data.answer}</p>

              <div className="sources">
                Retrieved sources:{' '}
                {(explanation.data.retrieved_sources || [])
                  .map((source) => source.source)
                  .join(', ') || 'No sources returned'}
              </div>

              {explanation.data.fallback && (
                <p className="error-text">
                  The response used the backend fallback. Verify
                  the policy and evidence before making decisions.
                </p>
              )}
            </div>
          )}

          {explanation?.error && (
            <p className="error-text">{explanation.error}</p>
          )}

          <button
            type="button"
            className="text-btn"
            onClick={() => explainFinding(finding.id)}
            disabled={isLoading}
          >
            {isLoading ? (
              <>
                <RefreshCw size={14} className="spin" />
                Investigating...
              </>
            ) : (
              <>
                <BrainCircuit size={14} />
                Explain finding
              </>
            )}
          </button>
        </div>
      </article>
    );
  }

  function FindingsList({ title = 'Audit findings' }) {
    return (
      <section className="findings-panel">
        <div className="section-heading">
          <div>
            <h3>{title}</h3>
            <p>
              {audit
                ? `Audit #${audit.audit_id} · ${
                    audit.files?.length
                      ? audit.files.join(', ')
                      : audit.filename
                  }`
                : 'Upload one or more CSVs and run an audit to see findings.'}
            </p>
          </div>

          {audit && (
            <a
              className="secondary-btn"
              href={`${API}/api/audits/${audit.audit_id}/export.csv`}
            >
              <Download size={15} />
              Export CSV
            </a>
          )}
        </div>

        {!audit && (
          <div className="empty-state">
            <FileSearch size={28} />
            <b>No active investigation</b>
            <span>
              Run an audit from Audit overview to investigate
              flagged transactions.
            </span>
            <button
              type="button"
              className="secondary-btn"
              onClick={() => navigate('overview')}
            >
              <ArrowLeft size={15} />
              Go to overview
            </button>
          </div>
        )}

        {audit && findings.length === 0 && (
          <div className="empty-state">
            <ShieldCheck size={28} />
            <b>No exceptions detected</b>
            <span>
              The configured rules did not flag any rows.
              This does not guarantee that all transactions are correct.
            </span>
          </div>
        )}

        {findings.map((finding) => (
          <FindingCard key={finding.id} finding={finding} />
        ))}
      </section>
    );
  }

  function Overview() {
    return (
      <>
        <section className="welcome">
          <div>
            <div className="welcome-kicker">
              FINANCIAL CONTROL CENTER
            </div>
            <h2>See the signals behind your numbers.</h2>
            <p>
              Upload transaction data to run explainable audit
              checks and investigate exceptions with AI.
            </p>
          </div>

          <div className="welcome-art">
            <div className="orbit orbit-one" />
            <div className="orbit orbit-two" />
            <ShieldCheck size={72} />
          </div>
        </section>

        <section className="upload-panel">
          <div className="section-heading">
            <div>
              <h3>Run a new audit</h3>
              <p>CSV format · Up to 5 MB per file · Multiple files supported</p>
            </div>
            <span className="step-tag">01 / INGEST</span>
          </div>

          <label className="dropzone">
            <input
              type="file"
              accept=".csv,text/csv"
              multiple
              onChange={(event) => {
                const selected = Array.from(
                  event.target.files || []
                ).filter((selectedFile) =>
                  selectedFile.name.toLowerCase().endsWith('.csv')
                );
                setFiles(selected);
                setError(
                  event.target.files?.length && selected.length === 0
                    ? 'Please choose one or more .csv files.'
                    : ''
                );
              }}
            />

            <div className="upload-icon">
              <Upload size={22} />
            </div>

            <b>
              {files.length
                ? `${files.length} CSV file${
                    files.length === 1 ? '' : 's'
                  } selected`
                : 'Choose transaction CSV files'}
            </b>

            <span>
              {files.length
                ? `${(
                    files.reduce(
                      (total, selectedFile) =>
                        total + selectedFile.size,
                      0
                    ) / 1024
                  ).toFixed(1)} KB total`
                : 'Select one or more CSV files from your computer'}
            </span>

            {files.length > 0 && (
              <div className="selected-files">
                {files.map((selectedFile) => (
                  <span
                    className="selected-file"
                    key={`${selectedFile.name}-${selectedFile.size}-${selectedFile.lastModified}`}
                  >
                    <FileText size={13} />
                    {selectedFile.name}
                  </span>
                ))}
              </div>
            )}

            <small>
              Supports bank CSVs with date, type, amount, account,
              recipient, upi_ref, balance_after, source_sms — or
              the original transaction_id, date, vendor, amount,
              description format.
            </small>
          </label>

          <div className="upload-actions">
            <span className="privacy-note">
              <ShieldCheck size={15} />
              Processed by your configured backend. Bank data is not
              sent to an external LLM during upload.
            </span>

            <button
              type="button"
              className="primary-btn"
              onClick={runAudit}
              disabled={files.length === 0 || loading}
            >
              {loading ? (
                <>
                  <RefreshCw size={16} className="spin" />
                  Analyzing...
                </>
              ) : (
                <>
                  <Activity size={16} />
                  Run audit
                </>
              )}
            </button>
          </div>

          {error && <div className="error-box">{error}</div>}
          {warnings.length > 0 && (
            <div className="warning-box">{warnings.join(' ')}</div>
          )}
        </section>

        <section className="kpi-grid">
          <div className="kpi">
            <div className="kpi-top">
              <span>Transactions reviewed</span>
              <div className="kpi-icon blue">
                <FileSearch size={18} />
              </div>
            </div>
            <strong>{audit ? audit.row_count : '—'}</strong>
            <small>Rows in latest audit</small>
          </div>

          <div className="kpi">
            <div className="kpi-top">
              <span>Exceptions detected</span>
              <div className="kpi-icon amber">
                <AlertTriangle size={18} />
              </div>
            </div>
            <strong>{audit ? findings.length : '—'}</strong>
            <small>Rule-based flags for review</small>
          </div>

          <div className="kpi">
            <div className="kpi-top">
              <span>High-risk flags</span>
              <div className="kpi-icon red">
                <ShieldCheck size={18} />
              </div>
            </div>
            <strong>{audit ? highRisk : '—'}</strong>
            <small>Not a fraud determination</small>
          </div>

          <div className="kpi">
            <div className="kpi-top">
              <span>Flagged amount total</span>
              <div className="kpi-icon green">
                <CircleDollarSign size={18} />
              </div>
            </div>
            <strong className="amount">
              {audit ? fmt(flaggedAmount) : '—'}
            </strong>
            <small>May include overlapping findings</small>
          </div>
        </section>

        <FindingsList />

        <section className="history-panel">
          <div className="section-heading">
            <div>
              <h3>Recent audit runs</h3>
              <p>Saved audit history from your backend.</p>
            </div>
            <button
              type="button"
              className="secondary-btn"
              onClick={loadAudits}
              disabled={historyLoading}
            >
              <RefreshCw
                size={14}
                className={historyLoading ? 'spin' : ''}
              />
              Refresh
            </button>
          </div>

          {historyLoading && <p>Loading audit history...</p>}

          {!historyLoading && audits.length === 0 && (
            <p className="history-empty">
              No saved runs found, or the API is unavailable.
            </p>
          )}

          {audits.slice(0, 5).map((item) => (
            <div className="history-row" key={item.id ?? item.audit_id}>
              <div className="history-file">
                <div className="file-icon">CSV</div>
                <div>
                  <b>{item.filename}</b>
                  <small>
                    Audit #{item.id ?? item.audit_id}
                    {item.created_at
                      ? ` · ${new Date(item.created_at).toLocaleString()}`
                      : ''}
                  </small>
                </div>
              </div>

              <div className="history-count">
                {item.row_count} rows · {item.finding_count} findings
              </div>
            </div>
          ))}
        </section>
      </>
    );
  }

  function Investigations() {
    return (
      <>
        <section className="page-intro">
          <div className="eyebrow">WORKSPACE / INVESTIGATIONS</div>
          <h2>Investigate audit exceptions</h2>
          <p>
            Review transaction evidence, assess risk, and request
            AI-assisted explanations.
          </p>
        </section>
        <FindingsList title="Investigation findings" />
      </>
    );
  }

  function Assistant() {
    const explanation = selectedFindingId
      ? explanations[selectedFindingId]
      : null;

    function askAboutFinding(event) {
      event.preventDefault();

      if (!selectedFinding) {
        setError('Select a finding before requesting an explanation.');
        return;
      }

      if (!assistantQuestion.trim()) {
        explainFinding(selectedFinding.id);
        return;
      }

      // The current API accepts no question body.
      // Keep the question visible, but use the supported finding
      // explanation endpoint instead of inventing a chat API.
      explainFinding(selectedFinding.id, assistantQuestion.trim());
    }

    return (
      <>
        <section className="page-intro">
          <div className="eyebrow">WORKSPACE / AI ASSISTANT</div>
          <h2>AI audit assistant</h2>
          <p>
            Get an evidence-based explanation of a finding using
            the configured AI and retrieved policy sources.
          </p>
        </section>

        <section className="upload-panel assistant-panel">
          <div className="section-heading">
            <div>
              <h3>
                <Sparkles size={18} /> Investigate a finding
              </h3>
              <p>Select a finding from your most recent audit.</p>
            </div>
          </div>

          {!audit && (
            <div className="empty-state">
              <BrainCircuit size={30} />
              <b>Run an audit first</b>
              <span>
                The assistant needs a finding from an audit to
                generate an explanation.
              </span>
              <button
                type="button"
                className="primary-btn"
                onClick={() => navigate('overview')}
              >
                Start an audit
              </button>
            </div>
          )}

          {audit && findings.length === 0 && (
            <div className="empty-state">
              No findings are available to explain.
            </div>
          )}

          {audit && findings.length > 0 && (
            <form onSubmit={askAboutFinding}>
              <label className="field-label" htmlFor="finding-select">
                Select finding
              </label>

              <select
                id="finding-select"
                className="assistant-select"
                value={selectedFindingId}
                onChange={(event) =>
                  setSelectedFindingId(event.target.value)
                }
              >
                {findings.map((finding) => (
                  <option
                    key={finding.id}
                    value={String(finding.id)}
                  >
                    {finding.transaction_id} — {finding.summary}
                  </option>
                ))}
              </select>

              {selectedFinding && (
                <div className="assistant-evidence">
                  <b>{selectedFinding.summary}</b>
                  <p>{selectedFinding.evidence}</p>
                  <span className={`risk ${selectedFinding.risk_level}`}>
                    {selectedFinding.risk_level} risk
                  </span>
                </div>
              )}

              <label className="field-label" htmlFor="assistant-question">
                What would you like to investigate?
              </label>

              <textarea
                id="assistant-question"
                className="assistant-textarea"
                rows={3}
                value={assistantQuestion}
                onChange={(event) =>
                  setAssistantQuestion(event.target.value)
                }
                placeholder="Example: What evidence should an auditor verify?"
              />

              <p className="muted">
                The current backend generates an explanation for the
                selected finding. The question above is not sent to
                the model because the existing endpoint does not
                accept a question body.
              </p>

              {error && <div className="error-box">{error}</div>}

              <button
                type="submit"
                className="primary-btn"
                disabled={Boolean(explanation?.loading)}
              >
                {explanation?.loading ? (
                  <>
                    <RefreshCw size={16} className="spin" />
                    Investigating...
                  </>
                ) : (
                  <>
                    <BrainCircuit size={16} />
                    Explain selected finding
                  </>
                )}
              </button>
            </form>
          )}

          {explanation?.error && (
            <div className="error-box">{explanation.error}</div>
          )}

          {explanation?.data && (
            <div className="ai-answer assistant-result">
              <div className="ai-answer-title">
                <BrainCircuit size={18} />
                Investigation explanation
                <span className="muted">
                  {explanation.data.model || ''}
                </span>
              </div>

              <p>{explanation.data.answer}</p>

              <h4>Retrieved sources</h4>

              {(explanation.data.retrieved_sources || []).length > 0 ? (
                <ul>
                  {explanation.data.retrieved_sources.map(
                    (source, index) => (
                      <li key={`${source.source}-${index}`}>
                        <b>{source.source}</b>
                        {source.score != null &&
                          ` · relevance ${Number(source.score).toFixed(3)}`}
                      </li>
                    )
                  )}
                </ul>
              ) : (
                <p>No retrieval sources were returned.</p>
              )}

              {explanation.data.fallback && (
                <p className="error-text">
                  A fallback response was returned. Check your Groq
                  configuration and backend logs if you expected a
                  generated AI answer.
                </p>
              )}
            </div>
          )}
        </section>
      </>
    );
  }

  function Reconciliation() {
    const [bankFile, setBankFile] = useState(null);
    const [ledgerFile, setLedgerFile] = useState(null);
    const [tolerance, setTolerance] = useState(3);
    const [reconciliation, setReconciliation] = useState(null);
    const [reconLoading, setReconLoading] = useState(false);
    const [reconError, setReconError] = useState('');
    const [reconWarnings, setReconWarnings] = useState([]);
    const [filter, setFilter] = useState('all');
    const [reconHistory, setReconHistory] = useState([]);
    const [reconHistoryLoading, setReconHistoryLoading] = useState(false);

    const statusOptions = [
      { key: 'matched', label: 'Matched' },
      { key: 'bank_only', label: 'Bank only' },
      { key: 'ledger_only', label: 'Ledger only' },
      { key: 'amount_mismatch', label: 'Amount mismatch' },
      { key: 'date_mismatch', label: 'Date mismatch' },
      { key: 'ambiguous', label: 'Ambiguous' }
    ];

    const results = reconciliation?.results || [];
    const summary = reconciliation?.summary || {};
    const visibleResults =
      filter === 'all'
        ? results
        : results.filter((item) => item.status === filter);

    async function loadReconciliationHistory() {
      setReconHistoryLoading(true);
      try {
        const response = await fetch(`${API}/api/reconciliations`);
        if (!response.ok) {
          throw new Error('Could not load reconciliation history.');
        }
        const data = await response.json();
        setReconHistory(Array.isArray(data) ? data : []);
      } catch (err) {
        console.error('Reconciliation history error:', err);
      } finally {
        setReconHistoryLoading(false);
      }
    }

    useEffect(() => {
      loadReconciliationHistory();
    }, []);

    function chooseFile(side, event) {
      const selected = event.target.files?.[0] || null;
      if (selected && !selected.name.toLowerCase().endsWith('.csv')) {
        setReconError(`${side === 'bank' ? 'Bank statement' : 'Ledger'} must be a .csv file.`);
        return;
      }
      setReconError('');
      if (side === 'bank') {
        setBankFile(selected);
      } else {
        setLedgerFile(selected);
      }
    }

    async function runReconciliation() {
      if (!bankFile || !ledgerFile) {
        setReconError('Select both a bank statement CSV and an accounting ledger CSV.');
        return;
      }

      setReconLoading(true);
      setReconError('');
      setReconWarnings([]);
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
        setReconWarnings(Array.isArray(data.warnings) ? data.warnings : []);
        await loadReconciliationHistory();
      } catch (err) {
        setReconError(
          err.message ||
            'Could not connect to the backend. Make sure FastAPI is running.'
        );
      } finally {
        setReconLoading(false);
      }
    }

    async function openReconciliation(id) {
      setReconLoading(true);
      setReconError('');
      try {
        const response = await fetch(`${API}/api/reconciliations/${id}`);
        const data = await response.json();
        if (!response.ok) {
          throw new Error(data.detail || 'Could not load this reconciliation.');
        }
        setReconciliation(data);
        setReconWarnings([]);
        setFilter('all');
      } catch (err) {
        setReconError(err.message || 'Could not load this reconciliation.');
      } finally {
        setReconLoading(false);
      }
    }

    const money = (value) => (value == null ? '—' : fmt(value));
    const text = (value) => value || '—';

    return (
      <>
        <section className="page-intro">
          <div className="eyebrow">WORKSPACE / BANK RECONCILIATION</div>
          <h2>Match the bank statement against your ledger</h2>
          <p>
            Upload two CSV files. Reference matches are preferred; remaining
            rows are matched by amount, direction, and a configurable date
            tolerance. Uncertain matches are reported, never forced.
          </p>
        </section>

        <section className="upload-panel">
          <div className="section-heading">
            <div>
              <h3>New reconciliation</h3>
              <p>One bank statement and one accounting ledger, up to 5 MB each.</p>
            </div>
            <span className="step-tag">02 / RECONCILE</span>
          </div>

          <div className="recon-upload-grid">
            <label className="dropzone recon-dropzone">
              <input
                type="file"
                accept=".csv,text/csv"
                onChange={(event) => chooseFile('bank', event)}
              />
              <div className="upload-icon">
                <Upload size={20} />
              </div>
              <b>Bank statement</b>
              <span>
                {bankFile ? bankFile.name : 'Choose the bank CSV file'}
              </span>
              <small>
                Supports date, amount, debit, credit, withdrawal, deposit,
                reference, narration, and related aliases.
              </small>
            </label>

            <label className="dropzone recon-dropzone">
              <input
                type="file"
                accept=".csv,text/csv"
                onChange={(event) => chooseFile('ledger', event)}
              />
              <div className="upload-icon">
                <FileText size={20} />
              </div>
              <b>Accounting ledger</b>
              <span>
                {ledgerFile ? ledgerFile.name : 'Choose the ledger CSV file'}
              </span>
              <small>
                Column mapping is handled independently for each file, so the
                two files do not need identical headers.
              </small>
            </label>
          </div>

          <div className="recon-controls">
            <label className="recon-tolerance">
              <span>Date tolerance (days)</span>
              <input
                type="number"
                min="0"
                max="365"
                value={tolerance}
                onChange={(event) => setTolerance(event.target.value)}
              />
            </label>

            <button
              type="button"
              className="primary-btn"
              onClick={runReconciliation}
              disabled={!bankFile || !ledgerFile || reconLoading}
            >
              {reconLoading ? (
                <>
                  <RefreshCw size={16} className="spin" />
                  Reconciling...
                </>
              ) : (
                <>
                  <ArrowLeftRight size={16} />
                  Run reconciliation
                </>
              )}
            </button>
          </div>

          {reconError && <div className="error-box">{reconError}</div>}
          {reconWarnings.length > 0 && (
            <div className="warning-box">{reconWarnings.join(' ')}</div>
          )}
        </section>

        {reconciliation && (
          <>
            <section className="kpi-grid recon-summary-grid">
              <div className="kpi">
                <div className="kpi-top">
                  <span>Bank statement total</span>
                  <div className="kpi-icon blue">
                    <FileSearch size={18} />
                  </div>
                </div>
                <strong className="amount">{money(summary.bank_total)}</strong>
                <small>All bank rows in this run</small>
              </div>

              <div className="kpi">
                <div className="kpi-top">
                  <span>Ledger total</span>
                  <div className="kpi-icon green">
                    <CircleDollarSign size={18} />
                  </div>
                </div>
                <strong className="amount">{money(summary.ledger_total)}</strong>
                <small>All ledger rows in this run</small>
              </div>

              <div className="kpi">
                <div className="kpi-top">
                  <span>Matched total</span>
                  <div className="kpi-icon amber">
                    <ShieldCheck size={18} />
                  </div>
                </div>
                <strong className="amount">{money(summary.matched_total)}</strong>
                <small>{summary.matched_count || 0} matched result(s)</small>
              </div>

              <div className="kpi">
                <div className="kpi-top">
                  <span>Reconciliation difference</span>
                  <div className="kpi-icon red">
                    <AlertTriangle size={18} />
                  </div>
                </div>
                <strong className="amount">{money(summary.difference)}</strong>
                <small>Bank total minus ledger total</small>
              </div>
            </section>

            <section className="recon-subtotals">
              <span>Bank unmatched: <b>{money(summary.bank_unmatched_total)}</b></span>
              <span>Ledger unmatched: <b>{money(summary.ledger_unmatched_total)}</b></span>
              <span>Ambiguous items: <b>{summary.ambiguous_count || 0}</b></span>
              <span>Run #{reconciliation.reconciliation_id}</span>
            </section>

            <section className="findings-panel recon-results-panel">
              <div className="section-heading">
                <div>
                  <h3>Reconciliation results</h3>
                  <p>
                    {reconciliation.bank_file} ↔ {reconciliation.ledger_file} ·{' '}
                    {reconciliation.date_tolerance_days} day tolerance
                  </p>
                </div>

                <a
                  className="secondary-btn"
                  href={`${API}/api/reconciliations/${reconciliation.reconciliation_id}/export.csv`}
                >
                  <Download size={15} />
                  Export CSV
                </a>
              </div>

              <div className="filter-row">
                <button
                  type="button"
                  className={`filter-chip ${filter === 'all' ? 'active' : ''}`}
                  onClick={() => setFilter('all')}
                >
                  All ({results.length})
                </button>
                {statusOptions.map((option) => (
                  <button
                    key={option.key}
                    type="button"
                    className={`filter-chip ${filter === option.key ? 'active' : ''}`}
                    onClick={() => setFilter(option.key)}
                  >
                    {option.label} ({summary[`${option.key}_count`] || 0})
                  </button>
                ))}
              </div>

              {visibleResults.length === 0 ? (
                <div className="empty-state">
                  <ShieldCheck size={28} />
                  <b>No results in this filter</b>
                  <span>Choose another status filter to inspect the run.</span>
                </div>
              ) : (
                <div className="recon-table-wrap">
                  <table className="recon-table">
                    <thead>
                      <tr>
                        <th>Status</th>
                        <th>Date (B / L)</th>
                        <th>Amount (B / L)</th>
                        <th>Reference (B / L)</th>
                        <th>Description (B / L)</th>
                        <th>Source row</th>
                        <th>Note</th>
                      </tr>
                    </thead>
                    <tbody>
                      {visibleResults.map((item, index) => (
                        <tr
                          key={
                            item.id ||
                            `${item.bank_source_file}-${item.bank_source_row}-${item.ledger_source_file}-${item.ledger_source_row}-${index}`
                          }
                        >
                          <td>
                            <span className={`status-badge status-${item.status}`}>
                              {String(item.status || '').replaceAll('_', ' ')}
                            </span>
                          </td>
                          <td>
                            <div className="pair-cell">
                              <span>B: {text(item.bank_date)}</span>
                              <span>L: {text(item.ledger_date)}</span>
                            </div>
                          </td>
                          <td>
                            <div className="pair-cell">
                              <span>B: {money(item.bank_amount)}</span>
                              <span>L: {money(item.ledger_amount)}</span>
                            </div>
                          </td>
                          <td>
                            <div className="pair-cell">
                              <span>B: {text(item.bank_reference)}</span>
                              <span>L: {text(item.ledger_reference)}</span>
                            </div>
                          </td>
                          <td>
                            <div className="pair-cell">
                              <span>B: {text(item.bank_description)}</span>
                              <span>L: {text(item.ledger_description)}</span>
                            </div>
                          </td>
                          <td>
                            <div className="source-cell">
                              {item.bank_source_file && (
                                <span>
                                  B: {item.bank_source_file} · row {item.bank_source_row}
                                </span>
                              )}
                              {item.ledger_source_file && (
                                <span>
                                  L: {item.ledger_source_file} · row {item.ledger_source_row}
                                </span>
                              )}
                            </div>
                          </td>
                          <td className="note-cell">{item.note || '—'}</td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
              )}
            </section>
          </>
        )}

        <section className="history-panel">
          <div className="section-heading">
            <div>
              <h3>Recent reconciliations</h3>
              <p>Saved runs from your backend.</p>
            </div>
            <button
              type="button"
              className="secondary-btn"
              onClick={loadReconciliationHistory}
              disabled={reconHistoryLoading}
            >
              <RefreshCw
                size={14}
                className={reconHistoryLoading ? 'spin' : ''}
              />
              Refresh
            </button>
          </div>

          {reconHistoryLoading && <p>Loading reconciliation history...</p>}

          {!reconHistoryLoading && reconHistory.length === 0 && (
            <p className="history-empty">No saved reconciliations found.</p>
          )}

          {reconHistory.slice(0, 5).map((item) => (
            <div className="history-row" key={item.id}>
              <div className="history-file">
                <div className="file-icon">REC</div>
                <div>
                  <b>
                    {item.bank_file} ↔ {item.ledger_file}
                  </b>
                  <small>
                    Run #{item.id}
                    {item.created_at
                      ? ` · ${new Date(item.created_at).toLocaleString()}`
                      : ''}
                  </small>
                </div>
              </div>

              <div className="recon-history-actions">
                <span className="history-count">
                  {item.summary?.matched_count || 0} matched ·{' '}
                  {item.summary?.difference != null
                    ? fmt(item.summary.difference)
                    : '—'}{' '}
                  difference
                </span>
                <button
                  type="button"
                  className="text-btn"
                  onClick={() => openReconciliation(item.id)}
                  disabled={reconLoading}
                >
                  Open
                </button>
              </div>
            </div>
          ))}
        </section>
      </>
    );
  }

  const viewTitles = {
    overview: {
      eyebrow: 'AUDIT WORKSPACE / OVERVIEW',
      title: 'Financial audit intelligence',
      description: 'Detect exceptions. Investigate evidence. Make better decisions.'
    },
    investigations: {
      eyebrow: 'AUDIT WORKSPACE / INVESTIGATIONS',
      title: 'Investigations',
      description: 'Review flagged transactions and supporting evidence.'
    },
    reconciliation: {
      eyebrow: 'AUDIT WORKSPACE / BANK RECONCILIATION',
      title: 'Bank reconciliation',
      description: 'Match bank statement transactions to the accounting ledger.'
    },
    assistant: {
      eyebrow: 'AUDIT WORKSPACE / AI ASSISTANT',
      title: 'AI audit assistant',
      description: 'Explore explanations and retrieved policy sources.'
    }
  };

  const currentTitle = viewTitles[activeView];

  return (
    <div className="app-shell">
      <aside className={`sidebar ${mobileMenuOpen ? 'sidebar-open' : ''}`}>
        <div className="brand">
          <div className="brand-mark">
            <ShieldCheck size={23} />
          </div>
          <div>
            <b>FinAudit<span> AI</span></b>
            <small>FINANCIAL INTELLIGENCE</small>
          </div>
        </div>

        <div className="side-label">WORKSPACE</div>

        <NavItem view="overview" icon={Activity}>
          Audit overview
        </NavItem>
        <NavItem view="investigations" icon={FileSearch}>
          Investigations
        </NavItem>
        <NavItem view="reconciliation" icon={ArrowLeftRight}>
          Bank reconciliation
        </NavItem>
        <NavItem view="assistant" icon={BrainCircuit}>
          AI assistant
        </NavItem>

        <div className="sidebar-bottom">
          <div className="local-badge">
            <span /> API-connected mode
          </div>
          <small>
            Prototype · Review findings before taking action.
          </small>
        </div>
      </aside>

      <main className="main">
        <header className="topbar">
          <button
            type="button"
            className="mobile-menu-btn"
            onClick={() => setMobileMenuOpen((open) => !open)}
            aria-label="Toggle navigation"
          >
            <Menu size={21} />
          </button>

          <div>
            <div className="eyebrow">{currentTitle.eyebrow}</div>
            <h1>{currentTitle.title}</h1>
            <p>{currentTitle.description}</p>
          </div>

          <div className="status-pill">
            <span /> API workspace
          </div>
        </header>

        {activeView === 'overview' && <Overview />}
        {activeView === 'investigations' && <Investigations />}
        {activeView === 'reconciliation' && <Reconciliation />}
        {activeView === 'assistant' && <Assistant />}

        <footer>
          FinAudit AI <span>•</span> Audit prototype
          <span>•</span> Human review required
        </footer>
      </main>
    </div>
  );
}

createRoot(document.getElementById('root')).render(<App />);
