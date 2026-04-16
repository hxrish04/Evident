window.__EVIDENT_REDESIGN__ = true;

(() => {
  const shell = document.querySelector('.shell');
  if (!shell) return;

  shell.innerHTML = `
    <header class="topbar">
      <div class="brand-row">
        <img class="brand-lockup" src="/assets/logo-full.svg" alt="Evident" />
        <div class="view-switch" aria-label="View switch">
          <button id="workspaceBtn" class="secondary active" type="button">Workspace</button>
          <button id="insightsBtn" class="secondary" type="button">Insights</button>
        </div>
      </div>
      <div class="hero-layout">
        <div class="hero-main">
          <h1>Find who&rsquo;s worth contacting</h1>
        </div>
        <div class="hero-side">
          <p class="hero-copy">Ranked with evidence, confidence, and clear reasoning</p>
          <p class="supporting-copy">Decision first. Proof on demand.</p>
        </div>
      </div>
    </header>

    <details id="launchPanel" class="launch-panel">
      <summary>
        <div>
          <span class="launch-title-text">Start a run</span>
          <span class="launch-summary">Target URL, interest area, and draft count stay visible. Everything else stays tucked away until needed.</span>
        </div>
      </summary>
      <div class="launch-body">
        <div class="field-row">
          <div class="field">
            <label for="url">Target URL</label>
            <input id="url" type="url" value="https://www.uab.edu/medicine/neurobiology/faculty" />
          </div>
          <div class="field">
            <label for="interest">Interest Area</label>
            <input id="interest" type="text" value="neuroscience research" />
          </div>
          <div class="field">
            <label for="topN">Draft Count</label>
            <input id="topN" type="number" min="1" max="5" value="5" />
          </div>
        </div>

        <details class="advanced-panel">
          <summary>
            Advanced profile and sender details
            <span class="advanced-summary-copy">Use this only when you want deeper personalization in the final drafts.</span>
          </summary>
          <div class="advanced-body">
            <div class="field">
              <label for="goal">Goal</label>
              <textarea id="goal">Undergraduate looking for research opportunities at UAB</textarea>
            </div>
            <div class="field">
              <label for="profile">Student Profile</label>
              <textarea id="profile" class="profile-box">Alex Carter is an undergraduate student interested in neuroscience research and looking for hands-on lab experience. Background includes coursework in biology and statistics plus lightweight data cleaning in Python/Excel. The goal is to join a lab where undergraduates can contribute and learn solid research habits.</textarea>
            </div>
            <div class="field-row" style="grid-template-columns: repeat(3, minmax(0, 1fr));">
              <div class="field">
                <label for="senderName">Sender Name</label>
                <input id="senderName" type="text" value="Alex Carter" />
              </div>
              <div class="field">
                <label for="senderEmail">Sender Email</label>
                <input id="senderEmail" type="email" value="alex.carter@example.edu" />
              </div>
              <div class="field">
                <label for="senderPhone">Sender Phone</label>
                <input id="senderPhone" type="text" value="000-000-0000" />
              </div>
            </div>
          </div>
        </details>

        <div class="launch-actions-compact">
          <button id="runBtn" class="primary" type="button">Start Run</button>
          <button id="checkSiteBtn" class="secondary" type="button">Check Site Compatibility</button>
          <button id="demoRunBtn" class="secondary" type="button">Load Example Run</button>
        </div>

        <div id="status" class="status"></div>
        <div id="progressBoard" class="progress-board"></div>
        <div id="siteCheckPanel" class="site-check-card" style="display:none;"></div>
      </div>
    </details>

    <div id="runPreview" class="run-preview"></div>
    <div id="toast" class="toast" aria-live="polite" aria-atomic="true"></div>

    <section id="workspaceView" class="workspace-view active">
      <div class="workspace-main">
        <div class="list-pane">
          <div class="pane-header">
            <div>
              <strong>Ranked Candidates</strong>
              <div id="candidateListCopy" class="pane-copy">Choose a candidate</div>
            </div>
          </div>
          <div class="pane-body">
            <div id="candidateList" class="candidate-list"></div>
          </div>
        </div>

        <div class="detail-pane">
          <div class="pane-header">
            <div>
              <strong>Active Case File</strong>
              <div class="pane-copy">Decision &rarr; Why &rarr; Proof &rarr; Action</div>
            </div>
            <div id="runLabel" class="muted"></div>
          </div>
          <div id="candidateDetail" class="pane-body"></div>
        </div>
      </div>
    </section>

    <section id="insightsView" class="insights-view">
      <div class="insights-shell">
        <div class="insights-header">
          <div class="eyebrow">Run Insights</div>
          <h2 style="margin: 6px 0 0;">Impact notes</h2>
          <p class="supporting-copy" style="margin-top: 10px;">Metrics stay here so the workspace stays focused on decisions.</p>
        </div>
        <div id="insightsBody" class="insights-body"></div>
      </div>
    </section>
  `;

  shell.style.display = '';

  const API = window.location.origin;
  const LAST_RUN_STORAGE_KEY = 'evident:lastRunId';
  const LAST_RUN_SNAPSHOT_STORAGE_KEY = 'evident:lastRunSnapshot:v1';
  const LAST_SELECTED_CONTACT_STORAGE_KEY = 'evident:lastSelectedContact:v1';
  const JUNK_DISPLAY_VALUES = new Set(['dr.', 'dr', 'prof.', 'prof', 'mr.', 'mrs.', 'ms.', 'phd', 'md', '']);
  const MIN_DISPLAY_CHARS = 15;
  const MIN_CARD_SUMMARY_CHARS = 20;
  const TRAILING_SUMMARY_WORDS = new Set(['and', 'or', 'with', 'for', 'to', 'the', 'a', 'an', 'of', 'from']);
  const state = {
    runId: null,
    run: null,
    contacts: [],
    drafts: [],
    history: [],
    metrics: {},
    overallMetrics: {},
    selectedIndex: 0,
    siteCheck: null,
    progressEvents: [],
    comparisonSentence: '',
    openDraftContactId: null,
  };

  const el = {
    workspaceBtn: document.getElementById('workspaceBtn'),
    insightsBtn: document.getElementById('insightsBtn'),
    launchPanel: document.getElementById('launchPanel'),
    url: document.getElementById('url'),
    interest: document.getElementById('interest'),
    topN: document.getElementById('topN'),
    goal: document.getElementById('goal'),
    profile: document.getElementById('profile'),
    senderName: document.getElementById('senderName'),
    senderEmail: document.getElementById('senderEmail'),
    senderPhone: document.getElementById('senderPhone'),
    runBtn: document.getElementById('runBtn'),
    checkSiteBtn: document.getElementById('checkSiteBtn'),
    demoRunBtn: document.getElementById('demoRunBtn'),
    status: document.getElementById('status'),
    progressBoard: document.getElementById('progressBoard'),
    siteCheckPanel: document.getElementById('siteCheckPanel'),
    runPreview: document.getElementById('runPreview'),
    toast: document.getElementById('toast'),
    workspaceView: document.getElementById('workspaceView'),
    insightsView: document.getElementById('insightsView'),
    runLabel: document.getElementById('runLabel'),
    candidateListCopy: document.getElementById('candidateListCopy'),
    candidateList: document.getElementById('candidateList'),
    candidateDetail: document.getElementById('candidateDetail'),
    insightsBody: document.getElementById('insightsBody'),
  };

  let runStream = null;
  let toastTimer = null;

  el.workspaceBtn.addEventListener('click', () => setActiveView('workspace'));
  el.insightsBtn.addEventListener('click', () => setActiveView('insights'));
  el.runBtn.addEventListener('click', () => runAgent(false));
  el.checkSiteBtn.addEventListener('click', checkSiteCompatibility);
  el.demoRunBtn.addEventListener('click', loadDemoRun);

  function escapeHtml(value) {
    return String(value ?? '')
      .replaceAll('&', '&amp;')
      .replaceAll('<', '&lt;')
      .replaceAll('>', '&gt;')
      .replaceAll('"', '&quot;')
      .replaceAll("'", '&#39;');
  }

  function normalizeKey(value) {
    return String(value || '').trim().toLowerCase();
  }

  function truncateText(text, maxLength = 120) {
    const value = String(text || '').trim();
    if (value.length <= maxLength) return value;
    return `${value.slice(0, maxLength - 1).trim()}...`;
  }

  function firstSentence(text) {
    const value = String(text || '').replace(/\s+/g, ' ').trim();
    if (!value) return '';
    const match = value.match(/.+?[.!?](?=\s|$)/);
    return (match ? match[0] : value).trim();
  }

  function formatNumeric(value, digits = 1) {
    const number = Number(value);
    if (!Number.isFinite(number)) return '0';
    return number.toFixed(digits).replace(/\.0$/, '');
  }

  function titleCaseStatus(status) {
    const raw = String(status || '').replaceAll('_', ' ').trim();
    return raw ? raw.replace(/\b\w/g, (char) => char.toUpperCase()) : 'Unknown';
  }

  function setActiveView(view) {
    el.workspaceBtn.classList.toggle('active', view === 'workspace');
    el.insightsBtn.classList.toggle('active', view === 'insights');
    el.workspaceView.classList.toggle('active', view === 'workspace');
    el.insightsView.classList.toggle('active', view === 'insights');
  }

  function getInterestFocus() {
    const runInterest = String(state.run?.interest_area || '').split('\n')[0].trim();
    return runInterest || el.interest.value.trim() || 'the stated goal';
  }

  // Keep client-side persistence wrapped so privacy mode or quota errors never break the UI.
  function readStorage(key) {
    try {
      return window.localStorage.getItem(key);
    } catch {
      return null;
    }
  }

  function writeStorage(key, value) {
    try {
      window.localStorage.setItem(key, value);
    } catch {
      // Ignore quota and privacy mode storage failures. The app still works without client caching.
    }
  }

  function removeStorage(key) {
    try {
      window.localStorage.removeItem(key);
    } catch {
      // Ignore storage cleanup failures.
    }
  }

  function getStoredRunId() {
    const value = Number(readStorage(LAST_RUN_STORAGE_KEY) || 0);
    return Number.isFinite(value) && value > 0 ? value : null;
  }

  function storeRunId(runId) {
    if (!Number.isFinite(Number(runId)) || Number(runId) <= 0) return;
    writeStorage(LAST_RUN_STORAGE_KEY, String(runId));
  }

  function clearStoredRunId() {
    removeStorage(LAST_RUN_STORAGE_KEY);
  }

  function getContactStorageKey(contact) {
    if (!contact) return '';
    if (contact.id != null && String(contact.id).trim()) return `id:${String(contact.id).trim()}`;
    if (contact.email) return `email:${normalizeKey(contact.email)}`;
    if (contact.url) return `url:${normalizeKey(contact.url)}`;
    if (contact.name) return `name:${normalizeKey(contact.name)}`;
    return '';
  }

  function getStoredSelectedContactKey() {
    return String(readStorage(LAST_SELECTED_CONTACT_STORAGE_KEY) || '').trim();
  }

  function storeSelectedContactKey(contact) {
    const key = getContactStorageKey(contact);
    if (!key) return;
    writeStorage(LAST_SELECTED_CONTACT_STORAGE_KEY, key);
  }

  function clearStoredSelectedContactKey() {
    removeStorage(LAST_SELECTED_CONTACT_STORAGE_KEY);
  }

  function restoreSelectedIndex(preferredKey = getStoredSelectedContactKey()) {
    if (!state.contacts.length) {
      state.selectedIndex = 0;
      clearStoredSelectedContactKey();
      return;
    }
    const key = String(preferredKey || '').trim();
    if (!key) {
      state.selectedIndex = Math.min(state.selectedIndex, Math.max(state.contacts.length - 1, 0));
      storeSelectedContactKey(state.contacts[state.selectedIndex]);
      return;
    }
    const restoredIndex = state.contacts.findIndex((contact) => getContactStorageKey(contact) === key);
    state.selectedIndex = restoredIndex >= 0 ? restoredIndex : 0;
    storeSelectedContactKey(state.contacts[state.selectedIndex]);
  }

  function getStoredRunSnapshot() {
    const raw = readStorage(LAST_RUN_SNAPSHOT_STORAGE_KEY);
    if (!raw) return null;
    try {
      const parsed = JSON.parse(raw);
      if (!parsed || !Number.isFinite(Number(parsed.runId)) || !Array.isArray(parsed.contacts)) return null;
      return parsed;
    } catch {
      return null;
    }
  }

  function clearStoredRunSnapshot() {
    removeStorage(LAST_RUN_SNAPSHOT_STORAGE_KEY);
  }

  // Persist the last fully rendered workspace so reloads can paint immediately before the network catches up.
  function storeRunSnapshot() {
    if (!state.runId || !state.contacts.length) return;
    const run = state.run ? { ...state.run } : null;
    if (run) {
      delete run.contacts;
      delete run.drafts;
    }
    const snapshot = {
      runId: state.runId,
      run,
      contacts: state.contacts,
      drafts: state.drafts,
      metrics: state.metrics,
      selectedContactKey: getStoredSelectedContactKey(),
      savedAt: new Date().toISOString(),
    };
    writeStorage(LAST_RUN_SNAPSHOT_STORAGE_KEY, JSON.stringify(snapshot));
  }

  function hydrateFromStoredSnapshot() {
    const snapshot = getStoredRunSnapshot();
    if (!snapshot) return false;
    state.runId = Number(snapshot.runId);
    state.run = snapshot.run || null;
    state.contacts = Array.isArray(snapshot.contacts) ? snapshot.contacts : [];
    state.drafts = Array.isArray(snapshot.drafts) ? snapshot.drafts : [];
    state.metrics = snapshot.metrics || {};
    state.history = [];
    state.overallMetrics = {};
    state.comparisonSentence = localComparisonSentence();
    state.openDraftContactId = null;
    restoreSelectedIndex(snapshot.selectedContactKey || getStoredSelectedContactKey());
    renderAll();
    return Boolean(state.contacts.length);
  }

  function getDisplayStatus(contact) {
    return contact.final_status || contact.evaluation_status || (contact.recommended ? 'recommended' : 'not_recommended');
  }

  function getVisibleConfidenceLabel(contact) {
    const raw = String(contact.confidence_label || 'Moderate Confidence');
    const evidenceStrength = Number(contact.evidence_strength_score || 0);
    if (raw === 'High Confidence' && evidenceStrength < 5.5) return 'Moderate Confidence';
    if (raw === 'Moderate Confidence' && evidenceStrength === 0 && !contact.email && !contact.identity_verified) return 'Low Confidence';
    if (raw === 'Moderate Confidence' && evidenceStrength < 3) return 'Low Confidence';
    return raw;
  }

  function getDisplayScores(contact) {
    const fitScore = Number(contact.final_score ?? contact.relevance_score ?? 0);
    const supportScore = Number(contact.evidence_strength_score || 0);
    const rankScore = Number(contact.ranking_score ?? contact.score_breakdown?.final_score ?? fitScore);
    return {
      fit: Number.isFinite(fitScore) ? fitScore : 0,
      support: Number.isFinite(supportScore) ? supportScore : 0,
      rank: Number.isFinite(rankScore) ? rankScore : 0,
    };
  }

  function getTrustGuardNote(contact) {
    const status = getDisplayStatus(contact);
    const confidence = getVisibleConfidenceLabel(contact);
    const scores = getDisplayScores(contact);
    if (confidence === 'High Confidence' && scores.support < 5.5) {
      return 'Confidence was capped because support depth is not strong enough for a high-confidence claim.';
    }
    if (status === 'recommended' && scores.support < 3.5) {
      return 'Recommendation is being held back because public support is below the minimum trust floor.';
    }
    if (scores.rank >= scores.fit + 3 && scores.support < 3.5) {
      return 'Rank ordering is influenced by outreach readiness signals; fit and support remain the primary trust signals.';
    }
    return '';
  }

  function getDraftForContact(contact) {
    return state.drafts.find((draft) => {
      if (draft.contact_id && contact.id) return Number(draft.contact_id) === Number(contact.id);
      return normalizeKey(draft.contact_name) === normalizeKey(contact.name);
    }) || null;
  }

  function getDraftRecipient(draft, contact) {
    return String(draft?.contact_email || contact?.email || '').trim();
  }

  function normalizeDraftBodyText(body) {
    let normalized = String(body || '').replace(/\r\n/g, '\n').replace(/\r/g, '\n').trim();
    if (!normalized) return 'No draft body stored.';

    normalized = normalized.replace(
      /^((?:Hi|Hello|Dear)\b[^\n,]*,)\s+/i,
      '$1\n\n'
    );
    normalized = normalized.replace(
      /([.!?])\s+((?:Best regards|Regards|Thanks|Thank you|Sincerely|Best),)/gi,
      '$1\n\n$2'
    );
    normalized = normalized.replace(
      /((?:Best regards|Regards|Thanks|Thank you|Sincerely|Best),)\s+([^\n])/gi,
      '$1\n$2'
    );

    const senderName = String(el.senderName?.value || '').trim();
    const senderEmail = String(el.senderEmail?.value || '').trim();
    const senderPhone = String(el.senderPhone?.value || '').trim();

    if (senderName && senderEmail) {
      const escapedName = senderName.replace(/[.*+?^${}()|[\]\\]/g, '\\$&');
      const escapedEmail = senderEmail.replace(/[.*+?^${}()|[\]\\]/g, '\\$&');
      normalized = normalized.replace(new RegExp(`\\s+(${escapedName})(?=\\s+${escapedEmail})`), '\n\n$1');
    } else if (senderName && senderPhone) {
      const escapedName = senderName.replace(/[.*+?^${}()|[\]\\]/g, '\\$&');
      const escapedPhone = senderPhone.replace(/[.*+?^${}()|[\]\\]/g, '\\$&');
      normalized = normalized.replace(new RegExp(`\\s+(${escapedName})(?=\\s+${escapedPhone})`), '\n\n$1');
    }
    if (senderEmail) {
      const escapedEmail = senderEmail.replace(/[.*+?^${}()|[\]\\]/g, '\\$&');
      normalized = normalized.replace(new RegExp(`\\s+(${escapedEmail})`), '\n$1');
    }
    if (senderPhone) {
      const escapedPhone = senderPhone.replace(/[.*+?^${}()|[\]\\]/g, '\\$&');
      normalized = normalized.replace(new RegExp(`\\s+(${escapedPhone})`), '\n$1');
    }

    normalized = normalized
      .split('\n')
      .map((line) => line.replace(/[ \t]{2,}/g, ' ').trimEnd())
      .join('\n')
      .replace(/\n{3,}/g, '\n\n')
      .trim();

    return normalized;
  }

  function formatDraftPlainText(draft, contact, options = {}) {
    const includeMeta = options.includeMeta !== false;
    const recipient = getDraftRecipient(draft, contact);
    const body = normalizeDraftBodyText(draft?.body || '');
    if (!includeMeta) return body;
    const lines = [];
    if (recipient) lines.push(`To: ${recipient}`);
    if (draft?.subject) lines.push(`Subject: ${draft.subject}`);
    if (lines.length) lines.push('');
    lines.push(body);
    return lines.join('\n').trim();
  }

  async function copyTextToClipboard(text) {
    const value = String(text || '');
    if (navigator.clipboard?.writeText) {
      try {
        await navigator.clipboard.writeText(value);
        return;
      } catch {
        // Fall through to the textarea copy path when clipboard permissions are blocked.
      }
    }
    const helper = document.createElement('textarea');
    helper.value = value;
    helper.setAttribute('readonly', 'true');
    helper.style.position = 'fixed';
    helper.style.opacity = '0';
    document.body.appendChild(helper);
    helper.select();
    document.execCommand('copy');
    document.body.removeChild(helper);
  }

  function downloadTextFile(filename, text) {
    const blob = new Blob([String(text || '')], { type: 'text/plain;charset=utf-8' });
    const objectUrl = URL.createObjectURL(blob);
    const link = document.createElement('a');
    link.href = objectUrl;
    link.download = filename;
    document.body.appendChild(link);
    link.click();
    document.body.removeChild(link);
    URL.revokeObjectURL(objectUrl);
  }

  function safeDraftFilename(contact) {
    const base = String(contact?.name || 'evident-draft')
      .toLowerCase()
      .replace(/[^a-z0-9]+/g, '-')
      .replace(/^-+|-+$/g, '');
    return `${base || 'evident-draft'}.txt`;
  }

  function normalizeDisplayText(text) {
    return String(text || '').replace(/\s+/g, ' ').trim().replace(/^[-,:;\s]+|[-,:;\s]+$/g, '');
  }

  function isMeaningfulDisplayText(text) {
    const normalized = normalizeDisplayText(text);
    if (!normalized) return false;
    if (JUNK_DISPLAY_VALUES.has(normalized.toLowerCase())) return false;
    return normalized.length >= MIN_DISPLAY_CHARS;
  }

  function pickDisplayText(...values) {
    for (const value of values) {
      const normalized = normalizeDisplayText(value);
      if (isMeaningfulDisplayText(normalized)) return normalized;
    }
    return '';
  }

  function sanitizeCandidateSummary(text) {
    // Candidate cards need to fail closed. A blank summary is better than a clipped
    // fragment like "and Dr." that makes the shortlist look broken.
    let value = normalizeDisplayText(text).replace(/[,:;\-]+$/g, '').trim();
    if (!value) return '';

    for (let i = 0; i < 4; i += 1) {
      const nextValue = value
        .replace(/(?:,?\s*(?:and|or)\s+)?(?:dr|dr\.|prof|prof\.)(?:\.{0,3})$/i, '')
        .replace(/(?:,?\s*(?:dr|dr\.|prof|prof\.))(?:\.{0,3})$/i, '')
        .replace(/[,:;\-]+$/g, '')
        .trim();

      const lastWord = nextValue.split(/\s+/).filter(Boolean).pop()?.toLowerCase().replace(/[.]+$/g, '') || '';
      value = TRAILING_SUMMARY_WORDS.has(lastWord)
        ? nextValue.replace(new RegExp(`\\b${lastWord}[.]*$`, 'i'), '').replace(/[,:;\-]+$/g, '').trim()
        : nextValue;
    }

    return value.length >= MIN_CARD_SUMMARY_CHARS ? value : '';
  }

  function isIdentityOnlySnippet(text, contact) {
    const normalized = normalizeDisplayText(text).toLowerCase();
    const name = normalizeDisplayText(contact?.name || '').toLowerCase();
    if (!normalized || !name) return false;
    const hasCredential = /\b(ph\.?d\.?|m\.?d\.?|dr\.?|prof\.?)\b/i.test(normalized);
    const researchSignal = /\b(research|study|studies|focus|interest|lab|program|neuro|gene|brain|memory|cell|molecular|disease|model)\b/i.test(normalized);
    const nameTokens = name.split(/\s+/).filter((token) => token.length >= 3);
    const hasNameSignal = nameTokens.some((token) => normalized.includes(token));
    return hasCredential && hasNameSignal && !researchSignal && normalized.length <= 48;
  }

  function getContactSummary(contact) {
    const summary = pickDisplayText(
      firstSentence(contact.research_summary),
      firstSentence(contact.reason_trace?.match),
      firstSentence(contact.reason_evidence)
    );
    if (!summary) return '';
    const cleanedSummary = sanitizeCandidateSummary(summary);
    if (!cleanedSummary) return '';
    return sanitizeCandidateSummary(truncateText(cleanedSummary, 108));
  }

  function getVerdictLine(contact) {
    const status = getDisplayStatus(contact);
    const focus = getInterestFocus();
    const supportScore = Number(contact.evidence_strength_score || 0);
    if (status === 'recommended') {
      if (supportScore < 3.5) return `Fit looked strong for ${focus}, but support stayed thin, so this was downgraded for trust.`;
      if (contact.email && contact.identity_verified) return `Strong match for ${focus} with verified identity and a direct outreach path.`;
      return `Strong match for ${focus} with enough public evidence to justify outreach.`;
    }
    if (status === 'insufficient_evidence') return 'Hold for now because the public record is too thin to support outreach confidently.';
    return `Lower-priority fit for ${focus} because the public evidence points to a weaker match.`;
  }

  function uniqueNonEmpty(items) {
    return [...new Set(items.map((item) => String(item || '').trim()).filter(Boolean))];
  }

  function getReasonBullets(contact, recipientEmail = '') {
    const reasonTrace = contact.reason_trace || {};
    const reasons = [];
    if (reasonTrace.match) reasons.push(firstSentence(reasonTrace.match));
    if (contact.research_summary) reasons.push(firstSentence(contact.research_summary));
    if (recipientEmail) reasons.push(`Direct email found: ${recipientEmail}.`);
    else if (contact.email) reasons.push(`Direct email found: ${contact.email}.`);
    else if (reasonTrace.evidence) reasons.push(firstSentence(reasonTrace.evidence));
    if (contact.second_pass_triggered && contact.revision_reason) reasons.push(firstSentence(contact.revision_reason));
    if (getDisplayStatus(contact) !== 'recommended') reasons.push(firstSentence(contact.not_recommended_reason || contact.insufficient_reason || reasonTrace.gap));
    return uniqueNonEmpty(reasons)
      .map((reason) => pickDisplayText(reason))
      .filter((reason) => reason && !isIdentityOnlySnippet(reason, contact))
      .slice(0, 4);
  }

  function getIndependentSourceCount(contact) {
    const urls = new Set();
    (Array.isArray(contact.cited_evidence) ? contact.cited_evidence : []).forEach((item) => item.source_url && urls.add(item.source_url));
    (Array.isArray(contact.evidence) ? contact.evidence : []).forEach((item) => item.source_url && urls.add(item.source_url));
    return urls.size;
  }

  function getEvaluationModeLabel() {
    const mode = String(state.run?.evaluation_mode || '').trim();
    if (mode === 'demo-seeded') return 'Curated example run';
    if (!mode || mode === 'heuristic-fallback') return 'Heuristic fallback';
    if (mode === 'cache-reuse') return 'Cached evaluation reuse';
    if (mode.includes('claude')) return 'Live Claude evaluation';
    return titleCaseStatus(mode);
  }

  function runUsedLiveModelCalls() {
    return Number(state.metrics.api_calls_made || 0) > 0;
  }

  function getRunModeNotice() {
    if (String(state.run?.evaluation_mode || '').trim() === 'demo-seeded') {
      return 'Curated run: shows recommended, not recommended, insufficient evidence, and revision states.';
    }
    if (runUsedLiveModelCalls()) return '';
    return 'No new live model calls. Shortlist uses cached or heuristic evaluation.';
  }

  function localComparisonSentence() {
    if (state.contacts.length < 2) return '';
    const [a, b] = state.contacts;
    const scoreA = getDisplayScores(a);
    const scoreB = getDisplayScores(b);
    if (a.email && !b.email) return 'Cleaner outreach path than #2.';
    if (getVisibleConfidenceLabel(a) !== getVisibleConfidenceLabel(b)) return 'Stronger confidence signal than #2.';
    if (scoreA.fit > scoreB.fit) return 'Stronger overall fit than #2.';
    if ((a.identity_verified ? 1 : 0) > (b.identity_verified ? 1 : 0)) return 'Cleaner identity verification than #2.';
    return 'Stronger fit and proof than #2.';
  }

  function getWhyRankedSentence(contact, index) {
    if (!state.contacts.length) return '';
    if (index === 0) {
      const topSummary = `${contact.research_summary || ''} ${contact.reason_trace?.match || ''}`.toLowerCase();
      if (topSummary.includes('cure') || topSummary.includes('undergraduate') || topSummary.includes('mentor')) {
        return 'Stronger undergraduate mentorship signal with equal research alignment.';
      }
      return state.comparisonSentence || localComparisonSentence();
    }
    return `Less direct outreach readiness than ${state.contacts[0].name} with a slightly weaker overall fit.`;
  }

  function getProofItems(contact) {
    const cited = Array.isArray(contact.cited_evidence) ? contact.cited_evidence : [];
    if (cited.length) {
      return cited.slice(0, 3).map((item) => ({
        label: item.source_type || 'Source',
        url: item.source_url || '',
        quote: item.quote || '',
        why: item.why_relevant || '',
      }));
    }
    return (Array.isArray(contact.evidence) ? contact.evidence : []).slice(0, 3).map((item) => ({
      label: item.title || item.source_type || 'Source',
      url: item.source_url || '',
      quote: truncateText(item.snippet || '', 120),
      why: firstSentence(contact.reason_trace?.evidence || contact.reason_evidence || 'Stored public source used to support the decision.'),
    }));
  }

  function formatMetricHours(minutes) {
    const totalMinutes = Number(minutes || 0);
    if (!Number.isFinite(totalMinutes) || totalMinutes <= 0) return '~0h';
    return `~${Math.max(1, Math.round(totalMinutes / 60))}h`;
  }

  function buildDerivedMetrics() {
    const discovered = Number(state.metrics.contacts_discovered || state.contacts.length || 0);
    const cleaned = Number(state.metrics.contacts_after_clean || discovered || state.contacts.length || 0);
    const preFiltered = Number(state.metrics.contacts_pre_filtered || state.contacts.length || 0);
    return {
      discovered,
      cleaned,
      evaluated: state.contacts.length || Number(state.metrics.contacts_evaluated || 0),
      recommended: state.contacts.filter((contact) => getDisplayStatus(contact) === 'recommended').length,
      insufficient: state.contacts.filter((contact) => getDisplayStatus(contact) === 'insufficient_evidence').length,
      highConfidence: state.contacts.filter((contact) => getVisibleConfidenceLabel(contact) === 'High Confidence').length,
      emailsFound: state.contacts.filter((contact) => contact.email).length,
      modelCallsSaved: Number(state.metrics.model_calls_saved || 0) || Math.max(0, discovered - preFiltered) + Number(state.metrics.contacts_excluded_outreach || state.metrics.contacts_excluded_sent || 0),
      avgEvidenceStrength: state.contacts.length
        ? state.contacts.reduce((sum, contact) => sum + Number(contact.evidence_strength_score || 0), 0) / state.contacts.length
        : Number(state.metrics.avg_evidence_strength || 0),
      estimatedMinutes: Number(state.metrics.estimated_minutes_saved || discovered * 6 || 0),
      requestsAttempted: Number(state.metrics.requests_attempted || 0),
      blockedResponses: Number(state.metrics.blocked_responses_count || 0),
    };
  }

  function formatEvaluationSummary() {
    const derived = buildDerivedMetrics();
    const cleaned = Math.max(derived.cleaned || 0, derived.evaluated || 0);
    if (!derived.evaluated) {
      return {
        preview: '',
        listCopy: 'Choose a candidate',
      };
    }

    const evaluatedText = cleaned > 0
      ? `${derived.evaluated} evaluated from ${cleaned} cleaned`
      : `${derived.evaluated} evaluated`;

    return {
      preview: `${evaluatedText}, ${derived.recommended} recommended`,
      listCopy: `${evaluatedText} contacts`,
    };
  }

  function setStatus(message, isError = false, keepProgress = false) {
    el.status.textContent = message || '';
    el.status.className = `status${isError ? ' error' : message ? ' success' : ''}`;
    if (!keepProgress && !message) el.progressBoard.innerHTML = '';
  }

  function showToast(message, isError = false) {
    if (!el.toast) return;
    el.toast.textContent = String(message || '').trim();
    el.toast.className = `toast visible${isError ? ' error' : ''}`;
    if (toastTimer) window.clearTimeout(toastTimer);
    toastTimer = window.setTimeout(() => {
      el.toast.className = 'toast';
      el.toast.textContent = '';
      toastTimer = null;
    }, 1800);
  }

  function toggleBusy(isBusy) {
    el.runBtn.disabled = isBusy;
    el.checkSiteBtn.disabled = isBusy;
    el.demoRunBtn.disabled = isBusy;
  }

  async function fetchJson(url, options = {}) {
    const response = await fetch(url, options);
    const data = await response.json().catch(() => ({}));
    if (!response.ok) throw new Error(data.detail || 'Request failed.');
    return data;
  }

  function getPayload() {
    return {
      target_url: el.url.value.trim(),
      interest_area: el.interest.value.trim(),
      goal_description: el.goal.value.trim(),
      student_profile: el.profile.value.trim(),
      sender_name: el.senderName.value.trim(),
      sender_email: el.senderEmail.value.trim(),
      sender_phone: el.senderPhone.value.trim(),
      top_n: Number(el.topN.value || 5),
    };
  }

  async function runAgent(nextBatch = false) {
    const payload = getPayload();
    if (!payload.target_url || !payload.interest_area) {
      setStatus('Add a target URL and interest area before running.', true);
      return;
    }
    toggleBusy(true);
    state.progressEvents = [];
    renderProgressBoard();
    setStatus(nextBatch ? 'Starting the next pass from the unsent pool...' : 'Starting the research pass...');
    try {
      const data = await fetchJson(nextBatch ? `${API}/run-next/start` : `${API}/run-agent/start`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(payload),
      });
      state.runId = data.run_id;
      state.openDraftContactId = null;
      el.launchPanel.open = false;
      startRunStream(data.run_id);
    } catch (error) {
      toggleBusy(false);
      setStatus(error.message || 'Could not start the run.', true);
    }
  }

  async function checkSiteCompatibility() {
    if (!el.url.value.trim()) {
      setStatus('Add a target URL first.', true);
      return;
    }
    setStatus('Checking site compatibility...');
    el.siteCheckPanel.style.display = 'none';
    try {
      state.siteCheck = await fetchJson(`${API}/check-site`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ target_url: el.url.value.trim() }),
      });
      renderSiteCheck();
      setStatus(`Compatibility looks ${String(state.siteCheck.compatibility_status || 'unknown').replaceAll('_', ' ')}.`, false, true);
    } catch (error) {
      setStatus(error.message || 'Could not check site compatibility.', true);
    }
  }

  async function loadDemoRun() {
    toggleBusy(true);
    setStatus('Loading the curated example run...');
    state.progressEvents = [];
    renderProgressBoard();
    try {
      const data = await fetchJson(`${API}/demo-run`, { method: 'POST' });
      state.runId = data.run_id;
      state.openDraftContactId = null;
      el.launchPanel.open = false;
      await loadRunResults(data.run_id);
      setStatus('Loaded the curated example run.', false, true);
    } catch (error) {
      setStatus(error.message || 'Could not load the example run.', true);
    } finally {
      toggleBusy(false);
    }
  }

  function startRunStream(runId) {
    if (runStream) runStream.close();
    runStream = new EventSource(`${API}/run-stream/${runId}`);
    runStream.onmessage = async (event) => {
      const data = JSON.parse(event.data);
      state.progressEvents.push(data);
      renderProgressBoard();
      if (data.stage === 'complete') {
        runStream.close();
        runStream = null;
        toggleBusy(false);
        setStatus(data.detail || 'Run complete.', false, true);
        try {
          await loadRunResults(runId);
        } catch (error) {
          setStatus(error.message || 'Run finished, but the results could not be loaded.', true);
        }
      }
      if (data.stage === 'failed') {
        runStream.close();
        runStream = null;
        toggleBusy(false);
        setStatus(data.detail || 'Run failed.', true);
      }
    };
    runStream.onerror = () => {
      if (runStream) runStream.close();
      runStream = null;
      toggleBusy(false);
    };
  }

  function renderProgressBoard() {
    const latest = state.progressEvents[state.progressEvents.length - 1];
    if (!latest) {
      el.progressBoard.innerHTML = '';
      return;
    }
    const steps = ['loading_page', 'extracting_contacts', 'cleaning_contacts', 'pre_filtering', 'researching', 'evaluating', 'ranking', 'drafting', 'complete'];
    const currentIndex = steps.indexOf(latest.stage);
    el.progressBoard.innerHTML = steps.map((step, index) => `
      <div class="progress-card ${index < currentIndex ? 'done' : index === currentIndex ? 'active' : ''}">
        <span>${escapeHtml(step.replaceAll('_', ' '))}</span>
        <strong>${index === currentIndex ? escapeHtml(latest.detail || 'Working...') : index < currentIndex ? 'Done' : 'Waiting'}</strong>
      </div>
    `).join('');
  }

  // Restore the last run immediately, then refresh it in the background so the shortlist feels instant on reload.
  async function loadLatestResults(options = {}) {
    const background = options.background === true;
    const hydratedFromCache = hydrateFromStoredSnapshot();
    const shouldShowLoadingState = !background || !hydratedFromCache;
    let waitingForStream = false;
    let health = null;
    let lastError = null;

    if (shouldShowLoadingState) {
      toggleBusy(true);
      setStatus(hydratedFromCache ? `Refreshing run #${state.runId}...` : 'Loading the latest saved run...');
    }

    try {
      try {
        health = await fetchJson(`${API}/health`);
      } catch (error) {
        lastError = error;
      }

      const candidateRunIds = [];
      const storedRunId = getStoredRunId();
      if (storedRunId) candidateRunIds.push(storedRunId);
      const healthRunIds = [health?.latest_completed_run_id, health?.latest_run_id].filter(Boolean);
      healthRunIds.forEach((runId) => {
        if (!candidateRunIds.includes(runId)) candidateRunIds.push(runId);
      });

      for (const runId of candidateRunIds) {
        try {
          await loadRunResults(runId);
          if (health?.latest_run_status === 'running' && Number(health?.latest_run_id) === Number(runId)) {
            waitingForStream = true;
            toggleBusy(true);
            setStatus(`Restored run #${runId}. Waiting for completion...`, false, true);
            startRunStream(runId);
          } else if (shouldShowLoadingState) {
            setStatus(`Restored run #${runId}.`, false, true);
          }
          return;
        } catch (error) {
          lastError = error;
          if (storedRunId && Number(runId) === Number(storedRunId)) {
            clearStoredRunId();
            clearStoredRunSnapshot();
          }
        }
      }

      renderAll();
      if (lastError && hydratedFromCache) {
        setStatus('Could not refresh the latest run. Showing the last saved workspace instead.', true, true);
      } else if (lastError && candidateRunIds.length) {
        setStatus(lastError.message || 'Could not restore the last saved run.', true);
      } else if (lastError && !candidateRunIds.length) {
        setStatus(lastError.message || 'Could not load saved runs.', true);
      }
    } finally {
      if (shouldShowLoadingState && !waitingForStream) {
        toggleBusy(false);
      }
    }
  }

  // Render the primary workspace as soon as the run payload arrives; non-blocking panels fill in afterward.
  async function loadRunResults(runId) {
    const preservedSelectionKey = getStoredSelectedContactKey();
    const historyPromise = fetchJson(`${API}/history?limit=12`);
    const overallMetricsPromise = fetchJson(`${API}/metrics`);
    const run = await fetchJson(`${API}/runs/${runId}`);

    state.runId = runId;
    state.run = run;
    state.contacts = Array.isArray(run.contacts) ? run.contacts : [];
    state.drafts = Array.isArray(run.drafts) ? run.drafts : [];
    state.history = [];
    state.metrics = run.metrics || {};
    state.overallMetrics = {};
    state.comparisonSentence = localComparisonSentence();
    state.openDraftContactId = null;
    storeRunId(runId);
    restoreSelectedIndex(preservedSelectionKey);
    storeRunSnapshot();
    renderAll();

    void Promise.allSettled([historyPromise, overallMetricsPromise]).then((results) => {
      if (Number(state.runId) !== Number(runId)) return;
      const [historyResult, overallMetricsResult] = results;
      if (historyResult.status === 'fulfilled') {
        state.history = Array.isArray(historyResult.value.history) ? historyResult.value.history : [];
      }
      if (overallMetricsResult.status === 'fulfilled') {
        state.overallMetrics = overallMetricsResult.value || {};
      }
      renderInsights();
    });

    if (state.contacts.length >= 2) {
      void fetchJson(`${API}/compare-top?run_id=${runId}`)
        .then((comparison) => {
          if (Number(state.runId) !== Number(runId)) return;
          state.comparisonSentence = firstSentence(comparison.comparison_explanation || '') || localComparisonSentence();
          renderCandidateDetail();
        })
        .catch(() => {
          if (Number(state.runId) !== Number(runId)) return;
          state.comparisonSentence = localComparisonSentence();
        });
    }
  }

  async function refreshSupplementalState() {
    const [history, overallMetrics] = await Promise.all([
      fetchJson(`${API}/history?limit=12`),
      fetchJson(`${API}/metrics`),
    ]);
    state.history = Array.isArray(history.history) ? history.history : [];
    state.overallMetrics = overallMetrics || {};
  }

  async function applyDraftAction(draftId, action) {
    const endpoint = action === 'sent'
      ? `${API}/drafts/${draftId}/mark-sent`
      : action === 'skipped'
        ? `${API}/drafts/${draftId}/mark-skipped`
        : `${API}/drafts/${draftId}/restore`;
    const updated = await fetchJson(endpoint, { method: 'POST' });
    state.drafts = state.drafts.map((draft) => (draft.id === updated.id ? { ...draft, ...updated } : draft));
    await refreshSupplementalState();
    storeRunSnapshot();
    renderAll();
    return updated;
  }

  function renderAll() {
    renderRunPreview();
    renderCandidateList();
    renderCandidateDetail();
    renderInsights();
  }

  function renderRunPreview() {
    if (!state.runId || !state.contacts.length) {
      el.runPreview.classList.remove('visible');
      el.runPreview.innerHTML = '';
      el.runLabel.textContent = '';
      if (el.candidateListCopy) el.candidateListCopy.textContent = 'Choose a candidate';
      return;
    }
    const derived = buildDerivedMetrics();
    const evaluationSummary = formatEvaluationSummary();
    el.runLabel.textContent = `Run #${state.runId}`;
    if (el.candidateListCopy) el.candidateListCopy.textContent = evaluationSummary.listCopy;
    el.runPreview.classList.add('visible');
    el.runPreview.innerHTML = `
      <strong>Run #${escapeHtml(String(state.runId))}</strong>
      <span>${escapeHtml(evaluationSummary.preview)}</span>
      <span>${escapeHtml(formatMetricHours(derived.estimatedMinutes))} saved</span>
    `;
  }

  function renderCandidateList() {
    if (!state.contacts.length) {
      el.candidateList.innerHTML = '<div class="empty">Start a run to build a ranked shortlist.</div>';
      return;
    }

    el.candidateList.innerHTML = state.contacts.map((contact, index) => {
      const status = getDisplayStatus(contact);
      const scores = getDisplayScores(contact);
      const primaryBadge = status === 'recommended' ? 'Recommended' : status === 'insufficient_evidence' ? 'Insufficient' : 'Lower priority';
      const badges = [];
      const summary = getContactSummary(contact);
      if (contact.decision_revision?.revised) {
        badges.push('Revised');
      }
      return `
        <button class="candidate-item ${index === state.selectedIndex ? 'active' : ''}" type="button" data-index="${index}">
          <div class="candidate-top">
            <div>
              <h3 class="candidate-name">${escapeHtml(contact.name || 'Unknown')}</h3>
              <div class="candidate-role">${escapeHtml(contact.title || 'Unknown role')}</div>
            </div>
            <div class="candidate-score">${escapeHtml(formatNumeric(scores.fit))}</div>
          </div>
          <div class="candidate-badges">
            <span class="signal-pill signal-pill--primary">${escapeHtml(primaryBadge)} &middot; ${escapeHtml(getVisibleConfidenceLabel(contact))}</span>
            ${badges.slice(0, 1).map((badge) => `<span class="signal-pill">${escapeHtml(badge)}</span>`).join('')}
          </div>
          ${summary ? `<p class="candidate-summary">${escapeHtml(summary)}</p>` : ''}
        </button>
      `;
    }).join('');

    el.candidateList.querySelectorAll('[data-index]').forEach((button) => {
      button.addEventListener('click', () => {
        state.selectedIndex = Number(button.dataset.index || 0);
        state.openDraftContactId = null;
        storeSelectedContactKey(state.contacts[state.selectedIndex]);
        renderCandidateList();
        renderCandidateDetail();
      });
    });
  }

  function renderCandidateDetail() {
    const contact = state.contacts[state.selectedIndex];
    if (!contact) {
      el.candidateDetail.innerHTML = '<div class="empty">Select a ranked candidate to open the case file.</div>';
      return;
    }
    storeSelectedContactKey(contact);

    const draft = getDraftForContact(contact);
    const reasonTrace = contact.reason_trace || {};
    const decisionRevision = contact.decision_revision || { revised: false };
    const proofItems = getProofItems(contact);
    const shouldShowDraft = state.openDraftContactId === contact.id;
    const draftState = draft?.status || '';
    const draftRecipient = getDraftRecipient(draft, contact);
    const reasonBullets = getReasonBullets(contact, draftRecipient);
    const scores = getDisplayScores(contact);
    const modeNotice = getRunModeNotice();
    const trustGuardNote = getTrustGuardNote(contact);
    const matchText = pickDisplayText(
      firstSentence(reasonTrace.match || ''),
      firstSentence(contact.research_summary || ''),
      firstSentence(reasonTrace.evidence || '')
    ) || 'Research detail is limited. Decision uses the strongest available public summary.';
    const gapText = pickDisplayText(
      firstSentence(contact.not_recommended_reason || contact.insufficient_reason || reasonTrace.gap || '')
    ) || 'No major gap was stored.';

    el.candidateDetail.innerHTML = `
      <div class="decision-sheet">
        <section class="decision-card">
          <div class="decision-head">
            <div class="decision-title-block">
              <h3 class="person">${escapeHtml(contact.name || 'Unknown')}</h3>
              <div class="role">${escapeHtml(contact.title || 'Unknown role')}</div>
            </div>
            <div class="decision-score">${escapeHtml(formatNumeric(scores.fit))}</div>
          </div>
          <div class="decision-badges">
            <span class="signal-pill signal-pill--primary">${escapeHtml(titleCaseStatus(getDisplayStatus(contact)))} &middot; ${escapeHtml(getVisibleConfidenceLabel(contact))}</span>
            ${decisionRevision.revised ? '<span class="signal-pill">Decision revised</span>' : ''}
          </div>
          <p class="verdict-line">${escapeHtml(getVerdictLine(contact))}</p>
          <div class="decision-contact-line">
            <span class="draft-contact-label">Direct email</span>
            ${draftRecipient
              ? `<a class="profile-link" href="mailto:${escapeHtml(draftRecipient)}">${escapeHtml(draftRecipient)}</a>`
              : '<span class="pane-copy">No direct email stored for this contact.</span>'}
            ${contact.url ? `<a class="draft-contact-link" href="${escapeHtml(contact.url)}" target="_blank" rel="noreferrer">Open profile</a>` : ''}
          </div>
          <div class="decision-actions-row">
            <button class="secondary" type="button" id="quickToggleDraftBtn" ${draft ? '' : 'disabled'}>${shouldShowDraft ? 'Hide Draft' : 'View Draft'}</button>
            <button class="secondary" type="button" id="quickCopyEmailBtn" ${draftRecipient ? '' : 'disabled'}>Copy Email</button>
            <button class="secondary" type="button" id="quickCopyDraftBtn" ${draft ? '' : 'disabled'}>Copy Draft</button>
            <button class="secondary" type="button" id="quickDownloadDraftBtn" ${draft ? '' : 'disabled'}>Export TXT</button>
          </div>
          ${modeNotice ? `<p class="compact-copy">${escapeHtml(modeNotice)}</p>` : ''}
        </section>

        <section class="case-section">
          <span class="section-kicker">Key reasons</span>
          <ul class="reason-list">
            ${reasonBullets.length ? reasonBullets.map((reason) => `<li>${escapeHtml(reason)}</li>`).join('') : '<li>No key reasons were stored for this contact.</li>'}
          </ul>
        </section>

        ${getWhyRankedSentence(contact, state.selectedIndex) ? `
          <section class="case-section">
            <span class="section-kicker">Why #1</span>
            <p class="compact-copy">${escapeHtml(firstSentence(getWhyRankedSentence(contact, state.selectedIndex)))}</p>
          </section>
        ` : ''}

        <section class="case-section">
          <div class="match-gap-grid">
            <div>
              <span class="section-kicker">Match</span>
              <p class="compact-copy">${escapeHtml(matchText)}</p>
            </div>
            <div>
              <span class="section-kicker">Gap</span>
              <p class="compact-copy">${escapeHtml(gapText)}</p>
            </div>
          </div>
        </section>

        <section class="case-section">
          <span class="section-kicker">Confidence</span>
          <div class="confidence-block">
            <strong>${escapeHtml(getVisibleConfidenceLabel(contact))}</strong>
            <div class="pane-copy">Fit score ${escapeHtml(formatNumeric(scores.fit))}/10 &middot; Support level ${escapeHtml(formatNumeric(scores.support))}/10 &middot; Rank score ${escapeHtml(formatNumeric(scores.rank))}</div>
            <div class="pane-copy">Fit = match quality &middot; Support = proof depth &middot; Confidence = certainty from fit + support + agreement.</div>
            <div class="confidence-line">${escapeHtml(contact.confidence_justification || 'Confidence was derived from public evidence and identity checks.')}</div>
            ${trustGuardNote ? `<div class="confidence-line">${escapeHtml(trustGuardNote)}</div>` : ''}
          </div>
        </section>

        <section class="case-section">
          <details class="proof-panel">
            <summary>Show evidence</summary>
            <div class="proof-body">
              <ul class="proof-list">
                ${proofItems.length ? proofItems.map((item) => `
                  <li>
                    <strong>${item.url ? `<a class="profile-link" href="${escapeHtml(item.url)}" target="_blank" rel="noreferrer">${escapeHtml(item.label)}</a>` : escapeHtml(item.label)}</strong>
                    ${item.quote ? ` - "${escapeHtml(item.quote)}"` : ''}
                    ${item.why ? ` - ${escapeHtml(item.why)}` : ''}
                  </li>
                `).join('') : '<li>No stored proof was available for this contact.</li>'}
              </ul>
            </div>
          </details>
        </section>

        ${decisionRevision.revised ? `
          <section class="case-section">
            <span class="section-kicker">Decision revision</span>
            <p class="compact-copy">Original ${escapeHtml(formatNumeric(decisionRevision.original_score))} ${escapeHtml(titleCaseStatus(decisionRevision.original_status))} -> Revised ${escapeHtml(formatNumeric(decisionRevision.final_score))} ${escapeHtml(titleCaseStatus(decisionRevision.final_status))}.</p>
            <p class="compact-copy">${escapeHtml(firstSentence(decisionRevision.reason || 'The second pass updated the final decision.'))}</p>
          </section>
        ` : ''}

        <section class="case-section">
          <span class="section-kicker">Action</span>
          <div class="draft-contact-line">
            <span class="draft-contact-label">Recipient</span>
            ${draftRecipient
              ? `<a class="profile-link" href="mailto:${escapeHtml(draftRecipient)}">${escapeHtml(draftRecipient)}</a>`
              : '<span class="pane-copy">No direct email was stored for this contact.</span>'}
            ${contact.url ? `<a class="draft-contact-link" href="${escapeHtml(contact.url)}" target="_blank" rel="noreferrer">Open profile</a>` : ''}
          </div>
          <div class="action-row">
            <button class="secondary" type="button" id="markContactedBtn" ${draft && draftState === 'draft' ? '' : 'disabled'}>${draftState === 'sent' ? 'Contacted' : 'Mark Contacted'}</button>
            <button class="secondary" type="button" id="skipDraftBtn" ${draft && draftState === 'draft' ? '' : 'disabled'}>${draftState === 'skipped' ? 'Skipped' : 'Skip'}</button>
          </div>
          ${shouldShowDraft ? renderInlineDraft(draft, contact) : ''}
        </section>
      </div>
    `;

    const quickToggleDraftBtn = document.getElementById('quickToggleDraftBtn');
    const quickCopyEmailBtn = document.getElementById('quickCopyEmailBtn');
    const quickCopyDraftBtn = document.getElementById('quickCopyDraftBtn');
    const quickDownloadDraftBtn = document.getElementById('quickDownloadDraftBtn');
    const copyDraftInlineBtn = document.getElementById('copyDraftInlineBtn');
    const downloadDraftInlineBtn = document.getElementById('downloadDraftInlineBtn');
    const copyEmailBtn = document.getElementById('copyEmailBtn');
    const markContactedBtn = document.getElementById('markContactedBtn');
    const skipDraftBtn = document.getElementById('skipDraftBtn');

    if (quickToggleDraftBtn && draft) {
      quickToggleDraftBtn.addEventListener('click', () => {
        state.openDraftContactId = state.openDraftContactId === contact.id ? null : contact.id;
        renderCandidateDetail();
      });
    }
    if (quickCopyDraftBtn && draft) {
      quickCopyDraftBtn.addEventListener('click', async () => {
        try {
          await copyTextToClipboard(formatDraftPlainText(draft, contact, { includeMeta: false }));
          setStatus(`Copied ${contact.name}'s draft body.`, false, true);
          showToast('Draft copied');
        } catch (error) {
          setStatus(error.message || 'Could not copy the draft.', true);
          showToast('Could not copy draft', true);
        }
      });
    }
    if (quickDownloadDraftBtn && draft) {
      quickDownloadDraftBtn.addEventListener('click', () => {
        try {
          downloadTextFile(safeDraftFilename(contact), formatDraftPlainText(draft, contact, { includeMeta: true }));
          setStatus(`Downloaded ${contact.name}'s draft as a text file.`, false, true);
          showToast('Draft exported');
        } catch (error) {
          setStatus(error.message || 'Could not download the draft.', true);
          showToast('Could not export draft', true);
        }
      });
    }
    if (quickCopyEmailBtn && draftRecipient) {
      quickCopyEmailBtn.addEventListener('click', async () => {
        try {
          await copyTextToClipboard(draftRecipient);
          setStatus(`Copied ${contact.name}'s email address.`, false, true);
          showToast('Email copied');
        } catch (error) {
          setStatus(error.message || 'Could not copy the email address.', true);
          showToast('Could not copy email', true);
        }
      });
    }
    if (copyDraftInlineBtn && draft) {
      copyDraftInlineBtn.addEventListener('click', async () => {
        try {
          await copyTextToClipboard(formatDraftPlainText(draft, contact, { includeMeta: false }));
          setStatus(`Copied ${contact.name}'s draft body.`, false, true);
          showToast('Draft copied');
        } catch (error) {
          setStatus(error.message || 'Could not copy the draft.', true);
          showToast('Could not copy draft', true);
        }
      });
    }
    if (downloadDraftInlineBtn && draft) {
      downloadDraftInlineBtn.addEventListener('click', () => {
        try {
          downloadTextFile(safeDraftFilename(contact), formatDraftPlainText(draft, contact, { includeMeta: true }));
          setStatus(`Exported ${contact.name}'s draft as a text file.`, false, true);
          showToast('Draft exported');
        } catch (error) {
          setStatus(error.message || 'Could not export the draft.', true);
          showToast('Could not export draft', true);
        }
      });
    }
    if (copyEmailBtn && draftRecipient) {
      copyEmailBtn.addEventListener('click', async () => {
        try {
          await copyTextToClipboard(draftRecipient);
          setStatus(`Copied ${contact.name}'s email address.`, false, true);
          showToast('Email copied');
        } catch (error) {
          setStatus(error.message || 'Could not copy the email address.', true);
          showToast('Could not copy email', true);
        }
      });
    }
    if (markContactedBtn && draft && draftState === 'draft') {
      markContactedBtn.addEventListener('click', async () => {
        try {
          const updated = await applyDraftAction(draft.id, 'sent');
          setStatus(`Marked ${updated.contact_name} as contacted. Future next-batch runs will skip them.`, false, true);
        } catch (error) {
          setStatus(error.message || 'Could not mark the draft as contacted.', true);
        }
      });
    }
    if (skipDraftBtn && draft && draftState === 'draft') {
      skipDraftBtn.addEventListener('click', async () => {
        try {
          const updated = await applyDraftAction(draft.id, 'skipped');
          setStatus(`Skipped ${updated.contact_name}.`, false, true);
        } catch (error) {
          setStatus(error.message || 'Could not skip the draft.', true);
        }
      });
    }
  }

  function renderInlineDraft(draft, contact) {
    if (!draft) return '<div class="draft-inline">No draft was generated for this contact in the current run.</div>';
    const recipient = getDraftRecipient(draft, contact);
    const profileUrl = String(contact?.url || '').trim();
    return `
      <div class="draft-inline">
        <div class="draft-meta">
          <div class="draft-meta-row">
            <span class="draft-meta-label">To</span>
            ${recipient
              ? `<a class="profile-link" href="mailto:${escapeHtml(recipient)}">${escapeHtml(recipient)}</a>`
              : '<span class="pane-copy">No direct email stored</span>'}
            ${recipient ? '<button class="secondary draft-tool-btn" type="button" id="copyEmailBtn">Copy Email</button>' : ''}
          </div>
          <div class="draft-meta-row">
            <span class="draft-meta-label">Subject</span>
            <span>${escapeHtml(draft.subject || 'No subject stored')}</span>
          </div>
          ${profileUrl
            ? `<div class="draft-meta-row"><span class="draft-meta-label">Profile</span><a class="profile-link" href="${escapeHtml(profileUrl)}" target="_blank" rel="noreferrer">Open profile</a></div>`
            : ''}
        </div>
        <div class="draft-tools-row">
          <button class="secondary draft-tool-btn" type="button" id="copyDraftInlineBtn">Copy Draft</button>
          <button class="secondary draft-tool-btn" type="button" id="downloadDraftInlineBtn">Export TXT</button>
        </div>
        <strong>${escapeHtml(draft.subject || 'No subject stored')}</strong>
        <div class="draft-body">${escapeHtml(normalizeDraftBodyText(draft.body || ''))}</div>
      </div>
    `;
  }

  function renderInsights() {
    if (!state.runId) {
      el.insightsBody.innerHTML = '<div class="empty">Start a run to populate impact notes, compatibility context, and outreach history.</div>';
      return;
    }

    const derived = buildDerivedMetrics();
    const confidenceDistribution = state.metrics.confidence_distribution || {};
    const compatibilityStatus = state.siteCheck?.compatibility_status || state.metrics.compatibility_status || 'unknown';
    const robotsNote = state.siteCheck?.robots_policy?.notes || state.metrics.robots_policy?.notes || 'No robots note was stored for this run.';
    const historyItems = state.history.slice(0, 8);
    const impactNotes = Array.isArray(state.overallMetrics.recent_impact_notes) ? state.overallMetrics.recent_impact_notes.slice(0, 6) : [];
    const modelCallsMade = Number(state.metrics.api_calls_made || 0);
    const avgTokensText = modelCallsMade > 0
      ? formatNumeric(state.metrics.avg_tokens_per_evaluation || 0)
      : 'n/a in cached or heuristic runs';

    el.insightsBody.innerHTML = `
      <section class="insight-block">
        <span class="section-kicker">System assessment</span>
        <div class="copy">${escapeHtml(state.run?.run_insight || 'No run assessment stored yet.')}</div>
        <div class="pane-copy" style="margin-top:8px;">Mode: ${escapeHtml(getEvaluationModeLabel())}${Number(state.metrics.api_calls_made || 0) === 0 ? ' - no live model calls in this run.' : '.'}</div>
      </section>

      <section class="insight-block">
        <span class="section-kicker">Impact summary</span>
        <ul class="insight-list">
          <li>Time saved: ${escapeHtml(formatMetricHours(derived.estimatedMinutes))} from automated first-pass review across ${escapeHtml(String(derived.discovered))} discovered contacts.</li>
          <li>Recommended: ${escapeHtml(String(derived.recommended))} of ${escapeHtml(String(derived.evaluated))} evaluated profiles.</li>
          <li>High-confidence picks: ${escapeHtml(String(derived.highConfidence))} candidates.</li>
          <li>Total runs: ${escapeHtml(String(state.overallMetrics.total_runs || 0))} (repeatable usage signal).</li>
        </ul>
      </section>

      <section class="insight-block">
        <span class="section-kicker">Evidence quality</span>
        <ul class="insight-list">
          <li>Avg evidence strength: ${escapeHtml(formatNumeric(derived.avgEvidenceStrength))}.</li>
          <li>Confidence mix: ${escapeHtml(String(confidenceDistribution.high || 0))} high, ${escapeHtml(String(confidenceDistribution.moderate || 0))} moderate, ${escapeHtml(String(confidenceDistribution.low || 0))} low, ${escapeHtml(String(confidenceDistribution.insufficient || 0))} insufficient.</li>
          <li>Conflicts detected: ${escapeHtml(String(state.metrics.conflicts_detected_count || 0))}.</li>
          <li>Second-pass reviews: ${escapeHtml(String(state.metrics.second_pass_count || 0))}.</li>
          <li>Adaptive retrieval triggers: ${escapeHtml(String(state.metrics.deep_retrieval_triggered_count || 0))}; chunks added: ${escapeHtml(String(state.metrics.deep_retrieval_chunks_added || 0))}.</li>
        </ul>
      </section>

      <section class="insight-block">
        <details class="disclosure">
          <summary>Run details</summary>
          <div style="padding: 0 16px 16px;">
            <div style="display:grid; gap:14px;">
              <div>
                <span class="section-kicker">Efficiency and cost</span>
                <ul class="insight-list">
                  <li>Model calls avoided: ${escapeHtml(String(derived.modelCallsSaved))} via pre-filtering and sent-contact exclusions.</li>
                  <li>Model calls made: ${escapeHtml(String(modelCallsMade))}${modelCallsMade === 0 ? ' (cached / heuristic run).' : '.'}</li>
                  <li>Avg tokens per evaluation: ${escapeHtml(avgTokensText)}.</li>
                  <li>Requests processed: ${escapeHtml(String(derived.requestsAttempted))}, with ${escapeHtml(String(derived.blockedResponses))} blocked or rate-limited.</li>
                </ul>
              </div>
              <div>
                <span class="section-kicker">Compatibility and boundaries</span>
                <ul class="insight-list">
                  <li>Compatibility: ${escapeHtml(String(compatibilityStatus).replaceAll('_', ' '))}.</li>
                  <li>Robots note: ${escapeHtml(robotsNote)}</li>
                  <li>Adapter: ${escapeHtml(String(state.metrics.adapter_selected || state.siteCheck?.adapter_selected || 'unknown').replaceAll('_', ' '))}.</li>
                </ul>
              </div>
            </div>
          </div>
        </details>
      </section>

      <section class="insight-block">
        <div style="display:flex; justify-content:space-between; gap:12px; align-items:flex-start; flex-wrap:wrap;">
          <div>
            <span class="section-kicker">Outreach history</span>
            <div class="copy" style="margin-top:6px;">Sent and skipped drafts live here so the main workspace stays focused on the current decision.</div>
          </div>
          <button id="runNextBtn" class="secondary" type="button">Run Next Batch</button>
        </div>
        <div class="history-mini-list" style="margin-top:14px;">
          ${historyItems.length ? historyItems.map((item) => `
            <div class="history-mini-item">
              <div class="history-mini-top">
                <div>
                  <h3 class="history-mini-title">${escapeHtml(item.contact_name || 'Unknown contact')}</h3>
                  <div class="pane-copy">${escapeHtml(item.contact_title || 'Unknown role')} - ${escapeHtml(item.contact_email || 'No direct email stored')}</div>
                </div>
                <span class="signal-pill">${escapeHtml(titleCaseStatus(item.status || 'draft'))}</span>
              </div>
              <div class="copy" style="margin:0 0 8px;">${escapeHtml(item.subject || 'No subject stored')}</div>
              <div class="pane-copy">Run #${escapeHtml(String(item.run_id || ''))} - ${escapeHtml(item.sent_at || item.created_at || '')}</div>
              ${item.status === 'sent' || item.status === 'skipped' ? `<div class="action-row" style="margin-top:10px;"><button class="secondary" type="button" data-restore-draft="${item.id}">Restore</button></div>` : ''}
            </div>
          `).join('') : '<div class="empty">No outreach actions have been logged yet.</div>'}
        </div>
      </section>

      <section class="insight-block">
        <span class="section-kicker">Impact notes archive</span>
        <ul class="insight-list">
          ${impactNotes.length ? impactNotes.map((item) => `
            <li>Run #${escapeHtml(String(item.run_id))}: ${escapeHtml(truncateText(firstSentence(item.run_insight || 'No impact note stored yet.'), 120) || 'No impact note stored yet.')}</li>
          `).join('') : '<li>No prior impact notes are stored yet.</li>'}
        </ul>
      </section>
    `;

    const runNextBtn = document.getElementById('runNextBtn');
    if (runNextBtn) runNextBtn.addEventListener('click', () => runAgent(true));

    el.insightsBody.querySelectorAll('[data-restore-draft]').forEach((button) => {
      button.addEventListener('click', async () => {
        try {
          const updated = await applyDraftAction(button.dataset.restoreDraft, 'restore');
          setStatus(`Restored ${updated.contact_name} to draft.`, false, true);
        } catch (error) {
          setStatus(error.message || 'Could not restore the draft.', true);
        }
      });
    });
  }

  function renderSiteCheck() {
    const data = state.siteCheck;
    if (!data) {
      el.siteCheckPanel.style.display = 'none';
      return;
    }
    const notes = Array.isArray(data.notes) ? data.notes : [];
    el.siteCheckPanel.style.display = '';
    el.siteCheckPanel.innerHTML = `
      <div class="detail-head" style="margin-bottom:0;">
        <div>
          <strong>${escapeHtml(titleCaseStatus(data.compatibility_status || 'unknown'))}</strong>
          <div class="pane-copy">${escapeHtml(data.target_url || '')}</div>
        </div>
      </div>
      <ul>
        <li>${escapeHtml(`${data.cleaned_contacts_found || 0} cleaned contacts were detected on the page.`)}</li>
        <li>${escapeHtml(`${data.contacts_with_profile_urls || 0} profiles had clear URLs and ${data.contacts_with_direct_emails || 0} had direct emails.`)}</li>
        <li>${escapeHtml(data.robots_policy?.notes || 'No robots note was stored.')}</li>
        ${notes.map((note) => `<li>${escapeHtml(note)}</li>`).join('')}
      </ul>
    `;
  }

  loadLatestResults({ background: true });
})();
