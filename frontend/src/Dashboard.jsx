import React, { useCallback, useEffect, useState } from 'react';
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
import { AlertTriangle, ArrowLeftRight, CircleDollarSign, RefreshCw, ShieldAlert, Wallet } from 'lucide-react';

const API = (import.meta.env.VITE_API_URL || 'http://127.0.0.1:8000').replace(/\/$/, '');

const currency = (value) =>
  new Intl.NumberFormat('en-IN', { style: 'currency', currency: 'INR', maximumFractionDigits: 0 }).format(Number(value) || 0);

const number = (value) => new Intl.NumberFormat('en-IN').format(Number(value) || 0);

const COLORS = ['#3b82f6', '#22d3ee', '#8b5cf6', '#f59e0b', '#22c55e', '#ef4444', '#ec4899', '#14b8a6', '#f97316', '#6366f1'];

function ChartFrame({ title, subtitle, children }) {
  return (
    <section className="chart-card dash-chart">
      <div className="section-header">
        <div>
          <h2>{title}</h2>
          <p>{subtitle}</p>
        </div>
      </div>
      <div className="dash-chart-body">{children}</div>
    </section>
  );
}

export default function Dashboard({ onNavigate }) {
  const [data, setData] = useState(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState('');

  const load = useCallback(async () => {
    setLoading(true);
    setError('');
    try {
      const response = await fetch(`${API}/api/dashboard/overview`);
      if (!response.ok) throw new Error(`Dashboard request failed (${response.status})`);
      setData(await response.json());
    } catch (err) {
      setError(err.message || 'Could not load dashboard data.');
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => { load(); }, [load]);

  if (loading) return <div className="panel dash-message">Loading database-backed dashboard…</div>;
  if (error) return <div className="panel dash-message dash-error">{error}</div>;
  if (!data) return null;

  const metrics = data.metrics || {};
  const charts = data.charts || {};
  const recent = data.recent_activity || [];
  const cards = [
    { label: 'Transactions', value: number(metrics.total_transactions), detail: 'All stored imports', icon: Wallet },
    { label: 'Total spending', value: currency(metrics.total_spending), detail: 'Debit-like records', icon: CircleDollarSign },
    { label: 'Net cash flow', value: currency(metrics.net_cash_flow), detail: 'Received less spending', icon: ArrowLeftRight },
    { label: 'Risk alerts', value: number(metrics.total_risk_alerts), detail: `${number(metrics.high_risk_alerts)} high · ${number(metrics.critical_alerts)} critical`, icon: ShieldAlert },
  ];

  return (
    <div className="dash-workspace">
      <div className="dash-toolbar">
        <span className="dash-source-note">All figures are computed from persisted SQLite records.</span>
        <button className="secondary-btn" type="button" onClick={load}><RefreshCw size={15} />Reload</button>
      </div>

      <div className="kpi-grid dash-kpis">
        {cards.map((card) => {
          const Icon = card.icon;
          return (
            <article className="kpi-card" key={card.label}>
              <div className="kpi-top">
                <div className="kpi-label"><span>{card.label}</span><strong>{card.value}</strong></div>
                <div className="kpi-icon blue"><Icon size={19} /></div>
              </div>
              <span className="kpi-subtle">{card.detail}</span>
            </article>
          );
        })}
      </div>

      <div className="dash-chart-grid">
        <ChartFrame title="Transaction activity" subtitle="Monthly count, spending and receipts">
          <ResponsiveContainer width="100%" height="100%">
            <AreaChart data={charts.transaction_activity || []} margin={{ top: 8, right: 16, left: 0, bottom: 0 }}>
              <defs>
                <linearGradient id="spendFill" x1="0" y1="0" x2="0" y2="1">
                  <stop offset="5%" stopColor="#3b82f6" stopOpacity={0.45} />
                  <stop offset="95%" stopColor="#3b82f6" stopOpacity={0} />
                </linearGradient>
              </defs>
              <CartesianGrid strokeDasharray="3 3" stroke="rgba(148,163,184,.16)" />
              <XAxis dataKey="month" tick={{ fill: '#9aa9c9', fontSize: 11 }} />
              <YAxis tick={{ fill: '#9aa9c9', fontSize: 11 }} />
              <Tooltip contentStyle={{ background: '#111c30', border: '1px solid #2b3c61', borderRadius: 10 }} />
              <Area type="monotone" dataKey="spending" stroke="#3b82f6" fill="url(#spendFill)" name="Spending" />
              <Area type="monotone" dataKey="received" stroke="#22c55e" fill="transparent" name="Received" />
            </AreaChart>
          </ResponsiveContainer>
        </ChartFrame>

        <ChartFrame title="Spending trend" subtitle="Outgoing value by transaction month">
          <ResponsiveContainer width="100%" height="100%">
            <BarChart data={charts.spending_trends || []} margin={{ top: 8, right: 16, left: 0, bottom: 0 }}>
              <CartesianGrid strokeDasharray="3 3" stroke="rgba(148,163,184,.16)" />
              <XAxis dataKey="month" tick={{ fill: '#9aa9c9', fontSize: 11 }} />
              <YAxis tick={{ fill: '#9aa9c9', fontSize: 11 }} />
              <Tooltip contentStyle={{ background: '#111c30', border: '1px solid #2b3c61', borderRadius: 10 }} />
              <Bar dataKey="spending" fill="#22d3ee" radius={[5, 5, 0, 0]} name="Spending" />
            </BarChart>
          </ResponsiveContainer>
        </ChartFrame>

        <ChartFrame title="Risk distribution" subtitle="Persisted alerts by severity">
          <ResponsiveContainer width="100%" height="100%">
            <PieChart>
              <Pie data={charts.risk_distribution || []} dataKey="count" nameKey="severity" innerRadius={48} outerRadius={78} paddingAngle={3}>
                {(charts.risk_distribution || []).map((item, index) => (
                  <Cell key={item.severity} fill={{ critical: '#ef4444', high: '#f97316', medium: '#f59e0b', low: '#22c55e' }[item.severity] || COLORS[index]} />
                ))}
              </Pie>
              <Tooltip contentStyle={{ background: '#111c30', border: '1px solid #2b3c61', borderRadius: 10 }} />
            </PieChart>
          </ResponsiveContainer>
        </ChartFrame>

        <ChartFrame title="Reconciliation status" subtitle="Stored match results, including possible matches">
          <ResponsiveContainer width="100%" height="100%">
            <BarChart data={charts.reconciliation_status || []} margin={{ top: 8, right: 16, left: 0, bottom: 0 }}>
              <CartesianGrid strokeDasharray="3 3" stroke="rgba(148,163,184,.16)" />
              <XAxis dataKey="status" tick={{ fill: '#9aa9c9', fontSize: 10 }} />
              <YAxis tick={{ fill: '#9aa9c9', fontSize: 11 }} />
              <Tooltip contentStyle={{ background: '#111c30', border: '1px solid #2b3c61', borderRadius: 10 }} />
              <Bar dataKey="count" fill="#8b5cf6" radius={[5, 5, 0, 0]} name="Results" />
            </BarChart>
          </ResponsiveContainer>
        </ChartFrame>

        <ChartFrame title="Category mix" subtitle="Top ten stored transaction categories">
          <ResponsiveContainer width="100%" height="100%">
            <BarChart data={charts.category_distribution || []} layout="vertical" margin={{ top: 8, right: 24, left: 24, bottom: 0 }}>
              <CartesianGrid strokeDasharray="3 3" stroke="rgba(148,163,184,.16)" />
              <XAxis type="number" tick={{ fill: '#9aa9c9', fontSize: 11 }} />
              <YAxis type="category" dataKey="category" width={110} tick={{ fill: '#9aa9c9', fontSize: 10 }} />
              <Tooltip contentStyle={{ background: '#111c30', border: '1px solid #2b3c61', borderRadius: 10 }} />
              <Bar dataKey="count" radius={[0, 5, 5, 0]} name="Transactions">
                {(charts.category_distribution || []).map((item, index) => <Cell key={item.category} fill={COLORS[index % COLORS.length]} />)}
              </Bar>
            </BarChart>
          </ResponsiveContainer>
        </ChartFrame>
      </div>

      <div className="dash-bottom-grid">
        <section className="table-card dash-panel">
          <div className="section-header"><div><h2>Recent database activity</h2><p>Newest transactions and alerts with provenance</p></div></div>
          {recent.length === 0 ? <div className="empty-state"><AlertTriangle size={22} /><strong>No records yet</strong><span>Upload a CSV to populate the dashboard.</span></div> : (
            <div className="data-table-wrap"><table className="data-table"><thead><tr><th>Reference</th><th>Date</th><th>Description</th><th>Amount</th><th>Source</th></tr></thead><tbody>
              {recent.map((item) => (
                <tr key={`${item.kind}-${item.id}`}>
                  <td className="table-primary">{item.reference || '—'}</td>
                  <td>{item.date || '—'}</td>
                  <td>{item.description || '—'}</td>
                  <td>{currency(item.amount)}</td>
                  <td>{item.source_file ? `${item.source_file}${item.source_row ? ` #${item.source_row}` : ''}` : '—'}</td>
                </tr>
              ))}
            </tbody></table></div>
          )}
        </section>
        <section className="panel dash-panel dash-scope">
          <div className="section-header"><div><h2>Metric definitions</h2><p>Exact scope used for every KPI</p></div></div>
          <dl>
            {Object.entries(data.scope || {}).map(([key, value]) => (
              <div key={key}><dt>{key.replaceAll('_', ' ')}</dt><dd>{Array.isArray(value) ? value.join(', ') : String(value)}</dd></div>
            ))}
          </dl>
          <button className="text-btn" type="button" onClick={() => onNavigate && onNavigate('transactions')}>Open transaction workspace →</button>
        </section>
      </div>
    </div>
  );
}
