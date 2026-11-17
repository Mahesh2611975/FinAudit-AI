import React, { useCallback, useEffect, useState } from 'react';
import {
  AlertTriangle,
  Bot,
  CheckCircle2,
  FileSearch,
  FileText,
  Loader2,
  RefreshCw,
  Send,
  ShieldCheck,
  Upload
} from 'lucide-react';

const API = (import.meta.env.VITE_API_URL || 'http://127.0.0.1:8000').replace(/\/$/, '');

async function assistantRequest(path, options = {}) {
  const response = await fetch(`${API}${path}`, options);
  const data = await response.json().catch(() => ({}));
  if (!response.ok) {
    const detail = Array.isArray(data.detail) ? data.detail.join(' ') : data.detail;
    throw new Error(typeof detail === 'string' ? detail : 'Request failed.');
  }
  return data;
}

const pretty = (value) => (value === null || value === undefined || value === '' ? '—' : String(value));

export default function Assistant() {
  const [question, setQuestion] = useState('');
  const [suggestions, setSuggestions] = useState([]);
  const [answer, setAnswer] = useState(null);
  const [asking, setAsking] = useState(false);
  const [error, setError] = useState('');

  const [config, setConfig] = useState(null);
  const [documents, setDocuments] = useState([]);
  const [docFile, setDocFile] = useState(null);
  const [indexing, setIndexing] = useState(false);
  const [docMessage, setDocMessage] = useState('');

  const loadMeta = useCallback(async () => {
    try {
      const [configData, docData, suggestionData] = await Promise.all([
        assistantRequest('/api/assistant/config'),
        assistantRequest('/api/documents'),
        assistantRequest('/api/assistant/suggestions'),
      ]);
      setConfig(configData);
      setDocuments(docData.documents || []);
      setSuggestions(suggestionData.questions || []);
    } catch (err) {
      setError(err.message || 'Could not load assistant configuration.');
    }
  }, []);

  useEffect(() => { loadMeta(); }, [loadMeta]);

  const ask = async (text = question) => {
    const trimmed = String(text || '').trim();
    if (!trimmed) return;
    setQuestion(trimmed);
    setAsking(true);
    setError('');
    try {
      setAnswer(await assistantRequest('/api/assistant/query', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ question: trimmed }),
      }));
    } catch (err) {
      setError(err.message || 'Assistant request failed.');
    } finally {
      setAsking(false);
    }
  };

  const ingest = async () => {
    if (!docFile) return;
    setIndexing(true);
    setDocMessage('');
    setError('');
    try {
      const form = new FormData();
      form.append('files', docFile);
      const result = await assistantRequest('/api/documents/ingest', { method: 'POST', body: form });
      const failures = (result.results || []).filter((item) => item.status === 'error');
      setDocMessage(failures.length ? failures.map((item) => `${item.filename}: ${item.error}`).join(' ') : `${result.results?.[0]?.chunks || 0} chunk(s) indexed from ${docFile.name}.`);
      setDocFile(null);
      await loadMeta();
    } catch (err) {
      setError(err.message || 'Document ingestion failed.');
    } finally {
      setIndexing(false);
    }
  };

  const syncTransactions = async () => {
    setIndexing(true);
    setError('');
    try {
      const result = await assistantRequest('/api/documents/sync-transactions', { method: 'POST' });
      setDocMessage(`${result.chunks || 0} transaction record(s) indexed for evidence retrieval.`);
      await loadMeta();
    } catch (err) {
      setError(err.message || 'Transaction sync failed.');
    } finally {
      setIndexing(false);
    }
  };

  const openTransaction = (reference) => {
    document.dispatchEvent(new CustomEvent('finaudit:open-transaction', { detail: reference }));
  };

  return (
    <div className="ai-workspace">
      <section className="panel ai-hero">
        <div className="ai-hero-icon"><Bot size={24} /></div>
        <div>
          <h2>Ask a grounded financial question</h2>
          <p>The orchestrator selects only relevant agents. Exact values come from SQL; documents come from the local RAG index; the model only narrates supplied evidence.</p>
        </div>
        <div className="ai-config-pills">
          <span>{config?.backend || 'ollama'}</span>
          <span className={config?.available ? 'ai-online' : 'ai-offline'}>{config?.available ? 'Ollama online' : 'Ollama offline'}</span>
          <span>{config?.model || 'model unknown'}</span>
        </div>
      </section>

      <section className="panel ai-ask">
        <div className="ai-suggestions">
          {(suggestions.length ? suggestions : [
            'Explain my highest-risk transaction.',
            'Find possible duplicate payments.',
            'Explain the latest reconciliation results.',
          ]).map((item) => <button key={item} type="button" onClick={() => ask(item)}>{item}</button>)}
        </div>
        <div className="ai-input-row">
          <textarea
            value={question}
            onChange={(e) => setQuestion(e.target.value)}
            onKeyDown={(e) => { if (e.key === 'Enter' && !e.shiftKey) { e.preventDefault(); ask(); } }}
            placeholder="e.g. Why was transaction D1 flagged and what evidence supports it?"
            rows={2}
          />
          <button className="primary-btn" type="button" onClick={() => ask()} disabled={asking || !question.trim()}>
            {asking ? <Loader2 size={16} className="spin" /> : <Send size={16} />}Ask
          </button>
        </div>
        {error && <div className="ai-alert ai-alert-error"><AlertTriangle size={15} />{error}</div>}
      </section>

      {answer && (
        <div className="ai-result-grid">
          <section className="panel ai-answer-card">
            <div className="section-header">
              <div><h2>Assistant answer</h2><p>Intent: {pretty(answer.intent)} · Route: {(answer.agents_selected || []).join(' → ') || '—'}</p></div>
              <span className={`ai-sufficiency ai-sufficiency-${answer.sufficiency}`}>Evidence: {pretty(answer.sufficiency)}</span>
            </div>
            <div className="ai-answer-text">{answer.answer}</div>
            <p className="ai-route-reason">{answer.route_reason}</p>
            <div className="ai-meta-row">
              <span>LLM: {answer.llm?.fallback ? 'evidence-only fallback' : 'grounded response'}</span>
              <span>Model: {pretty(answer.llm?.model)}</span>
              <span>SQL evidence: {answer.evidence?.length || 0}</span>
              <span>Document chunks: {answer.document_evidence?.length || 0}</span>
            </div>
            <div className="ai-disclaimer"><ShieldCheck size={15} />{answer.disclaimer}</div>

            <h3>Agent timeline</h3>
            <div className="ai-timeline">
              {(answer.timeline || []).map((item, index) => (
                <div key={`${item.agent_name}-${index}`}><span className="ai-step">{index + 1}</span><div><strong>{item.agent_name}</strong><p>{item.summary}</p><small>{item.duration_ms} ms</small></div></div>
              ))}
            </div>

            <h3>Citations and evidence links</h3>
            {answer.citations?.length ? (
              <div className="ai-citations">
                {answer.citations.map((item, index) => (
                  <article className="ai-citation" key={`${item.source}-${index}`}>
                    <div className="ai-citation-head"><strong>{pretty(item.source)}</strong><span>{pretty(item.type)}</span></div>
                    <p>{pretty(item.excerpt)}</p>
                    <div className="ai-citation-foot">
                      <span>File: {pretty(item.source_file)}{item.page_number ? ` · page ${item.page_number}` : ''}</span>
                      {item.transaction_id && <button className="text-btn" type="button" onClick={() => openTransaction(item.transaction_id)}>Open transaction →</button>}
                    </div>
                  </article>
                ))}
              </div>
            ) : <p className="ai-muted">No citations were retrieved. The assistant does not answer from unsupported memory.</p>}
          </section>

          <div className="ai-side-stack">
            <section className="panel ai-panel">
              <div className="section-header"><div><h2>Exact SQL evidence</h2><p>Deterministic values used by specialist agents</p></div><FileSearch size={18} /></div>
              {(answer.evidence || []).length ? answer.evidence.map((item, index) => (
                <div className="ai-evidence" key={index}><strong>{pretty(item.source_id)}</strong><span>{pretty(item.source_file)}{item.source_row ? ` row ${item.source_row}` : ''}</span><p>{pretty(item.text)}</p>{item.rule && <small>Rule: {pretty(item.rule)}</small>}</div>
              )) : <p className="ai-muted">No SQL evidence.</p>}
            </section>
            <section className="panel ai-panel">
              <div className="section-header"><div><h2>Document context</h2><p>RAG chunks with provenance</p></div><FileText size={18} /></div>
              {(answer.document_evidence || []).length ? answer.document_evidence.map((item, index) => (
                <div className="ai-evidence" key={index}><strong>{pretty(item.source)}</strong><span>{pretty(item.source_filename)}{item.page_number ? ` page ${item.page_number}` : ''} · score {Number(item.score || 0).toFixed(3)}</span><p>{pretty(item.text)}</p></div>
              )) : <p className="ai-muted">No document chunk matched this question.</p>}
            </section>
          </div>
        </div>
      )}

      <section className="panel ai-documents">
        <div className="section-header">
          <div><h2>Knowledge sources</h2><p>Ingest CSV, PDF or text evidence into the local TF-IDF + SQLite vector store</p></div>
          <button className="secondary-btn" type="button" onClick={loadMeta}><RefreshCw size={15} />Refresh</button>
        </div>
        <div className="ai-doc-row">
          <label className="file-picker"><Upload size={15} />{docFile ? docFile.name : 'Choose CSV / PDF / TXT'}<input type="file" accept=".csv,.pdf,.txt,.md,.log,.json" onChange={(e) => setDocFile(e.target.files?.[0] || null)} /></label>
          <button className="primary-btn" type="button" onClick={ingest} disabled={!docFile || indexing}>Index document</button>
          <button className="secondary-btn" type="button" onClick={syncTransactions} disabled={indexing}>Sync transactions</button>
          {docMessage && <span className="ai-doc-message"><CheckCircle2 size={14} />{docMessage}</span>}
        </div>
        <div className="ai-doc-stats">
          <span>Documents: {config?.rag?.documents || 0}</span><span>Chunks: {config?.rag?.chunks || 0}</span><span>Backend: {config?.rag?.backend || 'local-tfidf-sqlite'}</span>
        </div>
        {documents.length ? (
          <div className="data-table-wrap"><table className="data-table"><thead><tr><th>File</th><th>Type</th><th>Chunks</th><th>Ingested</th></tr></thead><tbody>
            {documents.map((item) => <tr key={item.doc_id}><td className="table-primary">{pretty(item.source_filename)}</td><td>{pretty(item.document_type)}</td><td>{item.chunk_count}</td><td>{pretty(item.upload_timestamp)}</td></tr>)}
          </tbody></table></div>
        ) : <p className="ai-muted">No documents ingested yet.</p>}
      </section>
    </div>
  );
}
