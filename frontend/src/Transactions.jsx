import React, { useCallback, useEffect, useMemo, useState } from 'react';
import {
  AlertTriangle,
  CheckCircle2,
  FileSearch,
  FileSpreadsheet,
  Loader2,
  RefreshCw,
  Upload,
  X
} from 'lucide-react';

const API = (import.meta.env.VITE_API_URL || 'http://127.0.0.1:8000').replace(/\/$/, '');

const money = (value) =>
  new Intl.NumberFormat('en-IN', { style: 'currency', currency: 'INR', maximumFractionDigits: 2 }).format(Number(value) || 0);

const text = (value) => (value === null || value === undefined || value === '' ? '—' : String(value));

async function jsonRequest(path, options = {}) {
  const response = await fetch(`${API}${path}`, options);
  const data = await response.json().catch(() => ({}));
  if (!response.ok) {
    const detail = Array.isArray(data.detail) ? data.detail.join(' ') : data.detail;
    throw new Error(typeof detail === 'string' ? detail : 'Request failed.');
  }
  return data;
}

export default function Transactions({ focusRef }) {
  const [file, setFile] = useState(null);
  const [preview, setPreview] = useState(null);
  const [importing, setImporting] = useState(false);
  const [previewing, setPreviewing] = useState(false);
  const [indexDocument, setIndexDocument] = useState(true);
  const [message, setMessage] = useState('');
  const [error, setError] = useState('');

  const [filters, setFilters] = useState({ q: '', date_from: '', date_to: '', min_amount: '', max_amount: '', direction: '', source_file: '' });
  const [result, setResult] = useState({ total: 0, items: [] });
  const [loading, setLoading] = useState(false);
  const [offset, setOffset] = useState(0);
  const [detail, setDetail] = useState(null);
  const [detailLoading, setDetailLoading] = useState(false);

  const filterKey = JSON.stringify(filters);
  const load = useCallback(async (nextOffset = offset) => {
    setLoading(true);
    setError('');
    try {
      const params = new URLSearchParams();
      Object.entries(filters).forEach(([key, value]) => { if (value !== '') params.set(key, value); });
      params.set('limit', '30');
      params.set('offset', String(nextOffset));
      setResult(await jsonRequest(`/api/transactions?${params.toString()}`));
    } catch (err) {
      setError(err.message || 'Could not load transactions.');
    } finally {
      setLoading(false);
    }
  }, [filterKey, offset]);

  useEffect(() => { load(0); setOffset(0); }, [filterKey]);

  const validatePreview = async () => {
    if (!file) { setError('Choose a CSV file first.'); return; }
    setPreviewing(true);
    setError('');
    setMessage('');
    try {
      const form = new FormData();
      form.append('file', file);
      setPreview(await jsonRequest('/api/transactions/preview', { method: 'POST', body: form }));
    } catch (err) {
      setError(err.message || 'Preview failed.');
    } finally {
      setPreviewing(false);
    }
  };

  const importFile = async () => {
    if (!file) { setError('Choose a CSV file first.'); return; }
    setImporting(true);
    setError('');
    setMessage('');
    try {
      const form = new FormData();
      form.append('file', file);
      form.append('skip_duplicates', 'true');
      const imported = await jsonRequest('/api/audits', { method: 'POST', body: form });
      let indexNote = '';
      if (indexDocument) {
        try {
          const indexForm = new FormData();
          indexForm.append('files', file);
          await jsonRequest('/api/documents/ingest', { method: 'POST', body: indexForm });
          indexNote = ' Document indexed for AI evidence.';
        } catch (err) {
          indexNote = ` AI indexing was skipped: ${err.message}`;
        }
      }
      setMessage(
        `Imported ${imported.row_count} rows from ${imported.files?.join(', ') || file.name}. ` +
        `${imported.skipped_duplicates || 0} duplicate row(s) skipped. ${imported.finding_count} deterministic finding(s).${indexNote}`
      );
      setPreview(null);
      setFile(null);
      await load(0);
      setOffset(0);
    } catch (err) {
      setError(err.message || 'Import failed.');
    } finally {
      setImporting(false);
    }
  };

  const openDetail = async (reference) => {
    setDetailLoading(true);
    setError('');
    try {
      setDetail(await jsonRequest(`/api/transactions/${encodeURIComponent(reference)}`));
    } catch (err) {
      setError(err.message || 'Could not open transaction.');
    } finally {
      setDetailLoading(false);
    }
  };

  useEffect(() => {
    if (focusRef) {
      const raw = String(focusRef);
      const reference = raw.includes(':') ? raw.slice(raw.indexOf(':') + 1) : raw;
      openDetail(reference);
    }
  }, [focusRef]);

  const pages = Math.max(1, Math.ceil((result.total || 0) / 30));
  const filterFields = useMemo(() => [
    ['q', 'Search vendor, ID, description', 'text'],
    ['date_from', 'Date from (YYYY-MM-DD)', 'date'],
    ['date_to', 'Date to (YYYY-MM-DD)', 'date'],
    ['min_amount', 'Min amount', 'number'],
    ['max_amount', 'Max amount', 'number'],
    ['source_file', 'Source file', 'text'],
  ], []);

  return (
    <div className="txn-workspace">
      <section className="panel txn-upload">
        <div className="section-header">
          <div><h2>Import bank or ledger CSV</h2><p>Automatic header mapping · validation preview · duplicate prevention</p></div>
          <FileSpreadsheet size={22} />
        </div>
        <div className="txn-upload-row">
          <label className="file-picker">
            <Upload size={16} />
            {file ? file.name : 'Choose CSV file'}
            <input type="file" accept=".csv,text/csv" onChange={(e) => { setFile(e.target.files?.[0] || null); setPreview(null); }} />
          </label>
          <button className="secondary-btn" type="button" onClick={validatePreview} disabled={!file || previewing}>
            {previewing ? <Loader2 size={15} className="spin" /> : <FileSearch size={15} />}Validate & preview
          </button>
          <button className="primary-btn" type="button" onClick={importFile} disabled={!file || importing}>
            {importing ? <Loader2 size={15} className="spin" /> : <Upload size={15} />}Import transactions
          </button>
          <label className="txn-check"><input type="checkbox" checked={indexDocument} onChange={(e) => setIndexDocument(e.target.checked)} />Index file for AI evidence</label>
        </div>
        {error && <div className="txn-alert txn-alert-error"><AlertTriangle size={15} />{error}</div>}
        {message && <div className="txn-alert txn-alert-success"><CheckCircle2 size={15} />{message}</div>}

        {preview && (
          <div className="txn-preview">
            <div className="txn-preview-head">
              <div><strong>{preview.filename}</strong><span>{preview.row_count} row(s) · schema: {preview.schema} · {preview.duplicate_count} already imported</span></div>
              <button className="text-btn" type="button" onClick={() => setPreview(null)}><X size={14} /></button>
            </div>
            {preview.errors?.length > 0 && <div className="txn-alert txn-alert-error">{preview.errors.join(' ')}</div>}
            {preview.warnings?.length > 0 && <div className="txn-alert txn-alert-warning"><AlertTriangle size={15} />{preview.warnings.slice(0, 5).join(' ')}</div>}
            <div className="txn-preview-grid">
              <div>
                <h3>Detected column mapping</h3>
                <div className="data-table-wrap"><table className="data-table"><thead><tr><th>CSV header</th><th>Canonical field</th></tr></thead><tbody>
                  {preview.mapping?.map((item, index) => <tr key={`${item.source}-${index}`}><td className="table-primary">{text(item.source)}</td><td>{text(item.canonical)}</td></tr>)}
                </tbody></table></div>
              </div>
              <div>
                <h3>Normalized row preview</h3>
                <div className="data-table-wrap"><table className="data-table"><thead><tr><th>ID</th><th>Date</th><th>Vendor</th><th>Amount</th><th>Duplicate</th></tr></thead><tbody>
                  {preview.preview?.map((item, index) => <tr key={index} className={item.duplicate ? 'txn-duplicate-row' : ''}><td>{text(item.transaction_id)}</td><td>{text(item.date)}</td><td>{text(item.vendor)}</td><td>{money(item.amount)}</td><td>{item.duplicate ? 'Yes' : 'No'}</td></tr>)}
                </tbody></table></div>
              </div>
            </div>
          </div>
        )}
      </section>

      <section className="table-card txn-list">
        <div className="section-header">
          <div><h2>Transaction register</h2><p>{result.total || 0} stored record(s) · source file and row preserved</p></div>
          <button className="secondary-btn" type="button" onClick={() => load(offset)}><RefreshCw size={15} />Refresh</button>
        </div>
        <div className="txn-filters">
          {filterFields.map(([name, placeholder, type]) => (
            <input key={name} type={type} placeholder={placeholder} value={filters[name]} onChange={(e) => setFilters({ ...filters, [name]: e.target.value })} />
          ))}
          <select value={filters.direction} onChange={(e) => setFilters({ ...filters, direction: e.target.value })}>
            <option value="">All directions</option><option value="debit">Debit</option><option value="credit">Credit</option>
          </select>
        </div>
        {loading ? <div className="dash-message">Loading transactions…</div> : result.items?.length ? (
          <div className="data-table-wrap"><table className="data-table"><thead><tr><th>Transaction</th><th>Date</th><th>Vendor / description</th><th>Category</th><th>Amount</th><th>Source</th><th /></tr></thead><tbody>
            {result.items.map((item) => (
              <tr key={item.id}>
                <td className="table-primary">{text(item.transaction_id)}</td>
                <td>{text(item.date)}</td>
                <td><strong>{text(item.vendor)}</strong><div className="txn-subtext">{text(item.description)}</div></td>
                <td>{text(item.category)}</td>
                <td>{money(item.amount)} <span className="txn-direction">{item.direction}</span></td>
                <td>{text(item.source_file)}{item.source_row ? ` #${item.source_row}` : ''}</td>
                <td><button className="text-btn" type="button" onClick={() => openDetail(item.transaction_id || String(item.id))}>Details</button></td>
              </tr>
            ))}
          </tbody></table></div>
        ) : <div className="empty-state"><FileSearch size={24} /><strong>No transactions found</strong><span>Import a CSV or change the filters.</span></div>}
        <div className="txn-pagination">
          <button className="secondary-btn" type="button" disabled={offset === 0} onClick={() => { const next = Math.max(0, offset - 30); setOffset(next); load(next); }}>Previous</button>
          <span>Page {Math.floor(offset / 30) + 1} of {pages}</span>
          <button className="secondary-btn" type="button" disabled={offset + 30 >= result.total} onClick={() => { const next = offset + 30; setOffset(next); load(next); }}>Next</button>
        </div>
      </section>

      {(detail || detailLoading) && (
        <div className="txn-drawer-backdrop" onClick={() => setDetail(null)}>
          <aside className="txn-drawer" onClick={(e) => e.stopPropagation()}>
            <div className="txn-drawer-head"><div><h2>Transaction evidence</h2><p>{detail?.transaction?.transaction_id || 'Loading…'}</p></div><button className="text-btn" type="button" onClick={() => setDetail(null)}><X size={18} /></button></div>
            {detailLoading ? <div className="dash-message">Loading evidence…</div> : detail && (
              <>
                <div className="txn-detail-grid">
                  {[
                    ['Date', detail.transaction.date], ['Amount', money(detail.transaction.amount)],
                    ['Direction', detail.transaction.direction], ['Vendor', detail.transaction.vendor],
                    ['Category', detail.transaction.category], ['Account', detail.transaction.account],
                    ['UPI reference', detail.transaction.upi_ref], ['Source file', detail.transaction.source_file],
                    ['Source row', detail.transaction.source_row], ['Duplicate key', detail.transaction.duplicate_key],
                  ].map(([label, value]) => <div key={label}><span>{label}</span><strong>{text(value)}</strong></div>)}
                </div>
                <h3>Risk alerts</h3>
                {detail.risk_alerts?.length ? detail.risk_alerts.map((alert) => (
                  <div className="txn-evidence-card" key={alert.id}>
                    <strong>{alert.alert_code} · {alert.risk_category}</strong>
                    <span>Score {alert.risk_score} · {alert.severity} · {alert.review_status}</span>
                    <p>{alert.explanation}</p>
                  </div>
                )) : <p className="txn-muted">No deterministic alert linked to this transaction.</p>}
                <h3>Reconciliation evidence</h3>
                {detail.reconciliation?.length ? detail.reconciliation.map((item) => (
                  <div className="txn-evidence-card" key={item.id}><strong>{item.status}</strong><span>Amount difference: {text(item.amount_difference)} · Date difference: {text(item.date_difference_days)} day(s)</span><p>{text(item.note)}</p></div>
                )) : <p className="txn-muted">No reconciliation row linked to this transaction.</p>}
              </>
            )}
          </aside>
        </div>
      )}
    </div>
  );
}
