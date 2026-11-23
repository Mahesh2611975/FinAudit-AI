import React, { useState } from 'react';
import {
  AlertTriangle,
  ArrowDown,
  ArrowRight,
  ArrowUpRight,
  BarChart3,
  Bot,
  CheckCircle2,
  ChevronRight,
  CircleDollarSign,
  FileSearch,
  FileSpreadsheet,
  GitCompare,
  LockKeyhole,
  Menu,
  Receipt,
  Search,
  ShieldCheck,
  Sparkles,
  Store,
  Users,
  Workflow,
  X
} from 'lucide-react';

const CAPABILITIES = [
  {
    icon: GitCompare,
    title: 'Financial reconciliation',
    text: 'Match bank statements to ledger records, inspect exceptions and keep source provenance attached to each result.',
    tone: 'orange'
  },
  {
    icon: FileSpreadsheet,
    title: 'Transaction intelligence',
    text: 'Import, validate, search and inspect transactions with the original filename and row preserved for review.',
    tone: 'blue'
  },
  {
    icon: ShieldCheck,
    title: 'Financial risk intelligence',
    text: 'Deterministic risk scoring, anomaly alerts, channel analytics and a review queue for stored alert records.',
    tone: 'purple'
  },
  {
    icon: Store,
    title: 'Vendor intelligence',
    text: 'Review vendor-level invoice, payment and exception patterns across linked invoice-audit evidence.',
    tone: 'green'
  },
  {
    icon: Receipt,
    title: 'Invoice auditing',
    text: 'Work with invoices, purchase orders, goods receipts, payments and matching results in one audit trail.',
    tone: 'orange'
  },
  {
    icon: Users,
    title: 'Investigation case management',
    text: 'Create and manage cases linked to findings, with status, notes and human decisions retained for review.',
    tone: 'blue'
  },
  {
    icon: Bot,
    title: 'AI investigation assistant',
    text: 'Run selective specialist agents that read SQL evidence and RAG citations before returning a grounded answer.',
    tone: 'purple'
  },
  {
    icon: BarChart3,
    title: 'Reports and analytics',
    text: 'Generate reports and exports from actual stored records, including monthly spend, categories and discrepancies.',
    tone: 'green'
  }
];

const WORKFLOW_STEPS = [
  {
    number: '01',
    title: 'Bring the records in',
    text: 'Upload transaction files and invoice evidence. Imported rows remain linked to their source for traceability.'
  },
  {
    number: '02',
    title: 'Match and score',
    text: 'Run reconciliation and deterministic risk rules, then review matches, alerts and exceptions side by side.'
  },
  {
    number: '03',
    title: 'Investigate with evidence',
    text: 'Open a case, ask the AI assistant or inspect linked records. Specialist agents surface supporting evidence.'
  },
  {
    number: '04',
    title: 'Decide and export',
    text: 'Record human review decisions and generate reports from the same records that produced the findings.'
  }
];

const AGENTS = [
  {
    icon: FileSpreadsheet,
    name: 'Financial Analyst Agent',
    text: 'Inspects transaction records and activity.'
  },
  {
    icon: GitCompare,
    name: 'Reconciliation Agent',
    text: 'Explains matched and unmatched records.'
  },
  {
    icon: AlertTriangle,
    name: 'Risk Investigation Agent',
    text: 'Explains rules, scores and severity.'
  },
  {
    icon: FileSearch,
    name: 'Evidence Retrieval Agent',
    text: 'Retrieves grounded document evidence.'
  },
  {
    icon: Sparkles,
    name: 'Investigation Summary Agent',
    text: 'Combines evidence and citations into an answer.'
  }
];

const REVIEW_POINTS = [
  {
    icon: FileSearch,
    title: 'Source-level provenance',
    text: 'Imported rows keep their source filename and row number, so a finding can be traced back to the record.'
  },
  {
    icon: Search,
    title: 'Evidence you can open',
    text: 'Assistant answers carry SQL evidence and document citations instead of unsupported claims.'
  },
  {
    icon: CheckCircle2,
    title: 'Human decisions stay in control',
    text: 'Alerts, discrepancies and cases retain review status and notes. Findings are investigation leads, not automatic actions.'
  },
  {
    icon: LockKeyhole,
    title: 'Access is verified by the backend',
    text: 'JWT sessions, approval checks, role permissions and account isolation are enforced by the API.'
  }
];

function PreviewDashboard() {
  return (
    <div className="lp-preview" aria-label="Illustrative preview of the FinAudit AI workspace">
      <div className="lp-preview-bar">
        <div className="lp-preview-brand">
          <span className="lp-preview-logo"><ShieldCheck size={16} /></span>
          <span>FinAudit AI</span>
        </div>
        <span className="lp-preview-badge">Illustrative preview</span>
      </div>

      <div className="lp-preview-body">
        <div className="lp-preview-rail" aria-hidden="true">
          <span className="lp-rail-item lp-rail-active"><BarChart3 size={15} /></span>
          <span className="lp-rail-item"><FileSpreadsheet size={15} /></span>
          <span className="lp-rail-item"><GitCompare size={15} /></span>
          <span className="lp-rail-item"><ShieldCheck size={15} /></span>
          <span className="lp-rail-item"><Bot size={15} /></span>
        </div>

        <div className="lp-preview-main">
          <div className="lp-preview-heading">
            <div>
              <small>Financial reconciliation overview</small>
              <strong>Control center</strong>
            </div>
            <span className="lp-preview-chip">Sample data</span>
          </div>

          <div className="lp-preview-kpis">
            <article className="lp-preview-kpi">
              <span className="lp-preview-kpi-label"><CircleDollarSign size={13} /> Total transactions</span>
              <strong>12,480</strong>
              <small>Across linked sources</small>
            </article>
            <article className="lp-preview-kpi lp-preview-kpi-risk">
              <span className="lp-preview-kpi-label"><AlertTriangle size={13} /> Risk alerts</span>
              <strong>37</strong>
              <small>Severity-ranked</small>
            </article>
            <article className="lp-preview-kpi lp-preview-kpi-warning">
              <span className="lp-preview-kpi-label"><FileSearch size={13} /> Unresolved discrepancies</span>
              <strong>08</strong>
              <small>Awaiting human review</small>
            </article>
          </div>

          <div className="lp-preview-panels">
            <article className="lp-preview-panel">
              <div className="lp-preview-panel-head">
                <strong>Reconciliation overview</strong>
                <span>96.4% matched</span>
              </div>
              <div className="lp-mini-chart" aria-hidden="true">
                {[48, 64, 57, 78, 69, 88, 76].map((height, index) => (
                  <span key={index} style={{ height: `${height}%` }} />
                ))}
              </div>
              <div className="lp-mini-chart-labels" aria-hidden="true">
                {['M', 'T', 'W', 'T', 'F', 'S', 'S'].map((day, index) => <span key={index}>{day}</span>)}
              </div>
            </article>

            <article className="lp-preview-panel">
              <div className="lp-preview-panel-head">
                <strong>Investigation status</strong>
                <span>4 items</span>
              </div>
              <ul className="lp-preview-list">
                <li><span>In review</span><em className="lp-status lp-status-orange">Active</em></li>
                <li><span>Evidence attached</span><em className="lp-status lp-status-blue">Ready</em></li>
                <li><span>Awaiting approval</span><em className="lp-status lp-status-purple">Queued</em></li>
                <li><span>Discrepancy closed</span><em className="lp-status lp-status-green">Resolved</em></li>
              </ul>
            </article>
          </div>

          <div className="lp-preview-evidence">
            <span className="lp-preview-evidence-icon"><FileSearch size={17} /></span>
            <div>
              <strong>Evidence-backed review</strong>
              <span>Source rows and linked artifacts stay attached to every finding.</span>
            </div>
            <ArrowUpRight size={16} aria-hidden="true" />
          </div>
        </div>
      </div>
    </div>
  );
}

export default function LandingPage({
  currentUser,
  demoMode = false,
  demoLoading = false,
  demoError = '',
  onOpenDashboard,
  onSignIn,
  onRegister
}) {
  const [menuOpen, setMenuOpen] = useState(false);

  const scrollToSection = (event, id) => {
    if (event) event.preventDefault();
    setMenuOpen(false);
    if (typeof document === 'undefined') return;
    const target = document.getElementById(id);
    if (target) target.scrollIntoView({ behavior: 'smooth', block: 'start' });
  };

  const primaryAction = () => {
    setMenuOpen(false);
    if (typeof onOpenDashboard === 'function') onOpenDashboard();
  };

  return (
    <div className="lp-root" id="lp-top">
      <a className="lp-skip" href="#lp-main" onClick={(event) => scrollToSection(event, 'lp-main')}>
        Skip to main content
      </a>

      <header className="lp-header">
        <nav className="lp-nav" aria-label="Public navigation">
          <a className="lp-brand" href="#lp-top" onClick={(event) => scrollToSection(event, 'lp-top')}>
            <span className="lp-brand-mark"><ShieldCheck size={20} /></span>
            <span className="lp-brand-copy">
              <strong>FinAudit AI</strong>
              <small>FINANCIAL INTELLIGENCE</small>
            </span>
          </a>

          <div className={`lp-nav-links ${menuOpen ? 'lp-nav-links-open' : ''}`}>
            <a href="#lp-platform" onClick={(event) => scrollToSection(event, 'lp-platform')}>Platform</a>
            <a href="#lp-capabilities" onClick={(event) => scrollToSection(event, 'lp-capabilities')}>Capabilities</a>
            <a href="#lp-workflow" onClick={(event) => scrollToSection(event, 'lp-workflow')}>How it works</a>
            <a href="#lp-agents" onClick={(event) => scrollToSection(event, 'lp-agents')}>AI agents</a>
            <a href="#lp-evidence" onClick={(event) => scrollToSection(event, 'lp-evidence')}>Evidence</a>
            {!demoMode && (
              <button className="lp-mobile-auth" type="button" onClick={onSignIn}>Sign in</button>
            )}
          </div>

          <div className="lp-nav-actions">
            {demoMode && <span className="lp-demo-pill">DEMO MODE</span>}
            {!demoMode && (
              <button className="lp-signin" type="button" onClick={onSignIn}>Sign in</button>
            )}
            <button className="lp-nav-cta" type="button" onClick={primaryAction} disabled={demoLoading} aria-busy={demoLoading}>
              {demoLoading ? 'Opening…' : currentUser ? 'My Dashboard' : 'Open Dashboard'}
              {!demoLoading && <ArrowRight size={15} aria-hidden="true" />}
            </button>
            <button
              className="lp-menu-toggle"
              type="button"
              onClick={() => setMenuOpen((open) => !open)}
              aria-expanded={menuOpen}
              aria-label={menuOpen ? 'Close navigation menu' : 'Open navigation menu'}
            >
              {menuOpen ? <X size={20} /> : <Menu size={20} />}
            </button>
          </div>
        </nav>
      </header>

      <main id="lp-main">
        <section className="lp-hero" aria-labelledby="lp-hero-title">
          <div className="lp-hero-copy">
            <p className="lp-eyebrow"><span className="lp-eyebrow-dot" /> FINANCIAL INTELLIGENCE</p>
            <h1 id="lp-hero-title">
              Every transaction.<br />
              Every discrepancy.<br />
              <span>Accounted for.</span>
            </h1>
            <p className="lp-hero-text">
              FinAudit AI brings financial reconciliation, risk intelligence, invoice auditing and
              AI-powered investigations into one evidence-driven workspace.
            </p>
            <div className="lp-hero-actions">
              <button
                className="lp-btn lp-btn-primary"
                type="button"
                onClick={primaryAction}
                disabled={demoLoading}
                aria-busy={demoLoading}
              >
                {demoLoading ? 'Opening demo…' : 'Open Dashboard'}
                {!demoLoading && <ArrowRight size={17} aria-hidden="true" />}
              </button>
              <button
                className="lp-btn lp-btn-outline"
                type="button"
                onClick={(event) => scrollToSection(event, 'lp-capabilities')}
              >
                Discover capabilities <ArrowDown size={16} aria-hidden="true" />
              </button>
            </div>
            <div className="lp-hero-trust" aria-label="Platform principles">
              <span><CheckCircle2 size={15} /> Evidence-linked findings</span>
              <span><CheckCircle2 size={15} /> Human review required</span>
              <span><CheckCircle2 size={15} /> Role-based access</span>
            </div>
            {demoMode && (
              <div className="lp-demo-note" role="status">
                <strong>DEMO MODE</strong>
                <span>Local demo is active. Open Dashboard enters the workspace without a sign-in screen.</span>
              </div>
            )}
            {demoError && <div className="lp-demo-error" role="alert">{demoError}</div>}
          </div>

          <div className="lp-hero-visual">
            <span className="lp-shape lp-shape-orange" aria-hidden="true" />
            <span className="lp-shape lp-shape-purple" aria-hidden="true" />
            <span className="lp-shape lp-shape-green" aria-hidden="true" />
            <span className="lp-shape lp-shape-blue" aria-hidden="true" />
            <PreviewDashboard />
          </div>
        </section>

        <section className="lp-section lp-platform" id="lp-platform" aria-labelledby="lp-platform-title">
          <div className="lp-section-head">
            <p className="lp-kicker">THE PLATFORM</p>
            <h2 id="lp-platform-title">One workspace for the records behind every decision.</h2>
            <p>
              FinAudit AI connects reconciliation, transaction analysis, risk signals, invoice evidence and
              investigation workflows. Every result stays attached to the records that produced it, so reviewers
              can move from a question to a source row without losing context.
            </p>
          </div>
          <div className="lp-platform-grid">
            <article className="lp-platform-card lp-platform-card-main">
              <span className="lp-platform-icon"><GitCompare size={22} /></span>
              <h3>Reconcile with context</h3>
              <p>Compare statements and ledger records, then work through mismatches with provenance intact.</p>
              <span className="lp-card-arrow"><ChevronRight size={16} /></span>
            </article>
            <article className="lp-platform-card">
              <span className="lp-platform-icon lp-icon-blue"><FileSpreadsheet size={21} /></span>
              <h3>Trace every row</h3>
              <p>Search saved transactions and see the source file and row behind each record.</p>
            </article>
            <article className="lp-platform-card">
              <span className="lp-platform-icon lp-icon-purple"><ShieldCheck size={21} /></span>
              <h3>Prioritize real risk</h3>
              <p>Review deterministic alerts and geographic patterns ranked from stored records.</p>
            </article>
            <article className="lp-platform-card lp-platform-card-accent">
              <span className="lp-platform-icon lp-icon-green"><Bot size={21} /></span>
              <h3>Investigate faster</h3>
              <p>Select relevant specialist agents and inspect their evidence before reaching a conclusion.</p>
            </article>
          </div>
        </section>

        <section className="lp-section lp-capabilities" id="lp-capabilities" aria-labelledby="lp-capabilities-title">
          <div className="lp-section-head lp-section-head-split">
            <div>
              <p className="lp-kicker">CORE CAPABILITIES</p>
              <h2 id="lp-capabilities-title">Built for the full evidence trail.</h2>
            </div>
            <p>From the first imported file to the final human decision, each capability works on the same secured record set.</p>
          </div>
          <div className="lp-capability-grid">
            {CAPABILITIES.map(({ icon: Icon, title, text, tone }) => (
              <article className={`lp-capability lp-tone-${tone}`} key={title}>
                <span className="lp-capability-icon"><Icon size={20} /></span>
                <h3>{title}</h3>
                <p>{text}</p>
              </article>
            ))}
          </div>
          <p className="lp-capability-note">
            <span className="lp-upcoming">Upcoming</span>
            Autonomous remediation and always-on control monitoring are not part of the current release.
          </p>
        </section>

        <section className="lp-section lp-workflow" id="lp-workflow" aria-labelledby="lp-workflow-title">
          <div className="lp-section-head">
            <p className="lp-kicker">HOW IT WORKS</p>
            <h2 id="lp-workflow-title">A clear path from records to resolution.</h2>
            <p>Four connected steps keep financial review repeatable, inspectable and ready for human sign-off.</p>
          </div>
          <div className="lp-workflow-track">
            {WORKFLOW_STEPS.map((step, index) => (
              <React.Fragment key={step.number}>
                <article className="lp-workflow-step">
                  <span className="lp-workflow-number">{step.number}</span>
                  <div className="lp-workflow-icon"><Workflow size={20} /></div>
                  <h3>{step.title}</h3>
                  <p>{step.text}</p>
                </article>
                {index < WORKFLOW_STEPS.length - 1 && <span className="lp-workflow-line" aria-hidden="true" />}
              </React.Fragment>
            ))}
          </div>
        </section>

        <section className="lp-section lp-agents" id="lp-agents" aria-labelledby="lp-agents-title">
          <div className="lp-agents-panel">
            <div className="lp-agents-copy">
              <p className="lp-kicker">AI SPECIALIST AGENTS</p>
              <h2 id="lp-agents-title">Specialists on call. Evidence in hand.</h2>
              <p>
                The orchestrator selects only the agents needed for a question. Each specialist reads exact values
                from SQL records or retrieved documents, and the summary agent combines the findings with citations.
              </p>
              <button className="lp-text-link" type="button" onClick={primaryAction}>
                Explore the workspace <ArrowUpRight size={16} aria-hidden="true" />
              </button>
            </div>
            <div className="lp-agent-list">
              {AGENTS.map(({ icon: Icon, name, text }) => (
                <article className="lp-agent" key={name}>
                  <span className="lp-agent-icon"><Icon size={18} /></span>
                  <div>
                    <strong>{name}</strong>
                    <p>{text}</p>
                  </div>
                </article>
              ))}
            </div>
          </div>
        </section>

        <section className="lp-section lp-evidence" id="lp-evidence" aria-labelledby="lp-evidence-title">
          <div className="lp-section-head lp-section-head-split">
            <div>
              <p className="lp-kicker">EVIDENCE & HUMAN REVIEW</p>
              <h2 id="lp-evidence-title">Answers with a trail, not a leap of faith.</h2>
            </div>
            <p>
              AI helps assemble the context. The final decision stays with an authorized reviewer who can inspect
              the underlying records and record the outcome.
            </p>
          </div>
          <div className="lp-review-grid">
            {REVIEW_POINTS.map(({ icon: Icon, title, text }) => (
              <article className="lp-review" key={title}>
                <span className="lp-review-icon"><Icon size={19} /></span>
                <div>
                  <h3>{title}</h3>
                  <p>{text}</p>
                </div>
              </article>
            ))}
          </div>
        </section>

        <section className="lp-final-cta" aria-labelledby="lp-final-title">
          <span className="lp-shape lp-shape-cta-one" aria-hidden="true" />
          <span className="lp-shape lp-shape-cta-two" aria-hidden="true" />
          <div className="lp-final-cta-inner">
            <p className="lp-kicker">GET STARTED</p>
            <h2 id="lp-final-title">Bring every exception into focus.</h2>
            <p>Open your secure workspace, or sign in to continue an investigation already in progress.</p>
            <div className="lp-hero-actions">
              <button
                className="lp-btn lp-btn-light"
                type="button"
                onClick={primaryAction}
                disabled={demoLoading}
                aria-busy={demoLoading}
              >
                Open Dashboard {!demoLoading && <ArrowRight size={17} aria-hidden="true" />}
              </button>
              {!demoMode && (
                <button className="lp-btn lp-btn-ghost-light" type="button" onClick={onSignIn}>Sign in</button>
              )}
            </div>
          </div>
        </section>
      </main>

      <footer className="lp-footer">
        <div className="lp-footer-grid">
          <div className="lp-footer-brand">
            <div className="lp-brand">
              <span className="lp-brand-mark"><ShieldCheck size={20} /></span>
              <span className="lp-brand-copy">
                <strong>FinAudit AI</strong>
                <small>FINANCIAL INTELLIGENCE</small>
              </span>
            </div>
            <p>
              An evidence-driven workspace for reconciliation, risk intelligence, invoice auditing and
              AI-assisted investigation.
            </p>
          </div>
          <div className="lp-footer-col">
            <h3>Platform</h3>
            <a href="#lp-platform" onClick={(event) => scrollToSection(event, 'lp-platform')}>Overview</a>
            <a href="#lp-capabilities" onClick={(event) => scrollToSection(event, 'lp-capabilities')}>Capabilities</a>
            <a href="#lp-workflow" onClick={(event) => scrollToSection(event, 'lp-workflow')}>How it works</a>
          </div>
          <div className="lp-footer-col">
            <h3>Intelligence</h3>
            <a href="#lp-agents" onClick={(event) => scrollToSection(event, 'lp-agents')}>AI specialist agents</a>
            <a href="#lp-evidence" onClick={(event) => scrollToSection(event, 'lp-evidence')}>Evidence & review</a>
          </div>
          <div className="lp-footer-col">
            <h3>Account</h3>
            <button type="button" onClick={primaryAction}>Open Dashboard</button>
            {demoMode ? (
              <span className="lp-footer-demo">Local demo · sign-in screens disabled</span>
            ) : (
              <>
                <button type="button" onClick={onSignIn}>Sign in</button>
                <button type="button" onClick={onRegister}>Create account</button>
              </>
            )}
          </div>
        </div>
        <div className="lp-footer-bottom">
          <span>© {new Date().getFullYear()} FinAudit AI. All rights reserved.</span>
          <span>Public preview only · No protected financial data is shown · Sample figures are illustrative</span>
        </div>
      </footer>
    </div>
  );
}
