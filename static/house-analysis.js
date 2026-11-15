/**
 * Pre-failure interior review UI.
 *
 *   <field-note-leads viewer="building-interior-viewer">  buildings flagged by scan technicians
 *   <house-analysis viewer="building-interior-viewer">    the agent review and its 3D scenario
 *
 * Both follow the building selected in the tour viewer and stay independent of it.
 */
const categories = { moisture: 'Moisture', structure: 'Structure', fire_electrical: 'Fire & electrical', fall_safety: 'Fall safety' };
const agents = { lead: 'Lead', surveyor: 'Surveyor', skeptic: 'Skeptic', analyst: 'Analyst', system: 'System' };
const regions = { houston: 'Houston', los_angeles: 'Los Angeles' };
const create = (tag, className, text) => {
  const node = document.createElement(tag);
  if (className) node.className = className;
  if (text !== undefined && text !== null) node.textContent = String(text);
  return node;
};
const strings = (value) => Array.isArray(value) ? value.filter((item) => typeof item === 'string') : [];
const number = (value, digits = 1) => Number(value).toLocaleString(undefined, { maximumFractionDigits: digits });
const reducedMotion = () => matchMedia('(prefers-reduced-motion: reduce)').matches;

class FieldNoteLeads extends HTMLElement {
  connectedCallback() {
    if (this.initialized) return;
    this.initialized = true;
    this.attachShadow({ mode: 'open' });
    this.shadowRoot.innerHTML = `
      <link rel="stylesheet" href="/static/house-analysis.css">
      <section class="leads" aria-labelledby="leads-title">
        <div class="leads-head">
          <div><p class="eyebrow">A SIGNAL HIDING IN THE SCAN DATA</p><h2 id="leads-title">Flagged by the people who scanned them</h2></div>
          <p class="leads-intro">Scan technicians leave notes for the listing team. Searched across the whole collection, those notes point at interiors worth a closer look.</p>
        </div>
        <div class="lead-list" role="list"><p class="leads-status" role="status">Reading field notes…</p></div>
      </section>`;
    this.load();
  }

  get api() { return this.getAttribute('api-base') || '/api/interiors'; }

  async load() {
    const list = this.shadowRoot.querySelector('.lead-list');
    try {
      const response = await fetch(`${this.api}/leads`);
      const data = await response.json();
      if (!response.ok) throw new Error(data.error || 'The field notes could not be searched.');
      list.replaceChildren();
      if (!data.leads.length) {
        list.append(create('p', 'leads-status', 'No field notes in this collection mention an interior problem.'));
        return;
      }
      for (const lead of data.leads.slice(0, 6)) list.append(this.card(lead));
    } catch (error) {
      list.replaceChildren(create('p', 'leads-status', error.message));
    }
  }

  card(lead) {
    const button = create('button', 'lead');
    button.type = 'button';
    button.setAttribute('role', 'listitem');
    const top = create('span', 'lead-top');
    top.append(create('span', 'lead-region', regions[lead.region] || ''));
    if (lead.has_saved_review) top.append(create('span', 'lead-saved', 'Saved'));
    const note = create('span', 'lead-note', `“${lead.note}”`);
    const chips = create('span', 'chips');
    for (const signal of lead.signals.slice(0, 4)) chips.append(create('span', 'chip', signal.label));
    button.append(top, create('span', 'lead-address', lead.address || lead.name), note, chips, create('span', 'lead-action', 'Open this building →'));
    button.addEventListener('click', () => {
      const viewer = document.querySelector(this.getAttribute('viewer') || 'building-interior-viewer');
      viewer?.selectModel({ id: lead.id, name: lead.name, address: lead.address, thumbnail: lead.thumbnail, floors: lead.floors });
      viewer?.scrollIntoView({ behavior: reducedMotion() ? 'instant' : 'smooth', block: 'start' });
    });
    return button;
  }
}

class HouseAnalysis extends HTMLElement {
  constructor() {
    super();
    this.attachShadow({ mode: 'open' });
    this.version = 0;
    this.house = null;
    this.configured = null;
    this.busy = false;
    this.viewer3d = null;
    this.activityEvents = [];
    this.pendingOutcome = null;
    this.activityTimer = null;
    this.terminalActivityDelay = null;
  }

  connectedCallback() {
    if (this.initialized) return;
    this.initialized = true;
    this.shadowRoot.innerHTML = `
      <link rel="stylesheet" href="/static/house-analysis.css">
      <section class="assessment" aria-labelledby="assessment-title">
        <header class="assessment-header">
          <div><p class="eyebrow">PRE-FAILURE REVIEW</p><h2 id="assessment-title">Find the weakness before it fails.</h2><p class="intro">A team of agents walks the scan room by room, looks up at every ceiling, and argues over what it sees. What survives is placed on the building’s real 3D model, with a picture of what could happen next.</p></div>
          <span class="screening-label">VISUAL SCREENING</span>
        </header>
        <div class="assessment-body">
          <div class="assessment-form">
            <div class="house-caption"><span class="field-label">SELECTED BUILDING</span><h3 class="selected-house">Choose a building above</h3><p class="selected-address">The review follows your selected tour.</p></div>
            <p class="saved-note" hidden></p>
            <button class="analyze primary" type="button" disabled>Run live review <span aria-hidden="true">↗</span></button>
            <div class="configuration" role="status"><span class="config-message">Checking the review team…</span><button type="button" class="check-connection" hidden>Check connection</button></div>
            <div class="method-note"><span class="field-label">THE REVIEW TEAM</span><ol>
              <li><b>Lead</b> plans the review and sends a surveyor into every room.</li>
              <li><b>Room surveyors</b> open each scan position, zoom in, and flag early signs of weakness.</li>
              <li><b>Skeptic</b> re-opens every flagged view, blind to the surveyor’s reasoning, and confirms or rejects it.</li>
              <li><b>Failure analyst</b> ties the most serious ceiling finding to a modelled failure.</li>
            </ol></div>
            <p class="boundary-note">A scan shows visible conditions on the day it was captured. This review cannot see inside a ceiling or wall, certify safety, or predict when anything will fail.</p>
          </div>
          <div class="assessment-output">
            <div class="initial-state"><span class="outline-house" aria-hidden="true">⌂</span><p class="eyebrow">EVIDENCE BEFORE CONCLUSIONS</p><h3>Small signs. Earlier attention.</h3><p>Pick a flagged building, or any building in the collection, then run the review to see what the agents find and what it could lead to.</p></div>
            <div class="progress" hidden role="status" aria-live="polite"><span class="spinner" aria-hidden="true"></span><div><p class="progress-stage">Preparing review</p><p class="progress-message">Loading the scan.</p><span class="elapsed"></span></div></div>
            <ol class="activity live-activity" hidden aria-label="Agent activity"></ol>
            <div class="assessment-error" hidden role="alert"><strong>Review paused.</strong><p class="error-message"></p><p>The recorded activity is kept above. Run a fresh review to try again.</p></div>
            <div class="report" hidden></div>
          </div>
        </div>
      </section>
      <dialog class="evidence-dialog"><div class="dialog-header"><span class="dialog-label"></span><button type="button" class="close-dialog" aria-label="Close image">×</button></div><div class="dialog-frame"></div><div class="dialog-body"><h3 class="dialog-title"></h3><p class="dialog-location"></p><button type="button" class="show-location secondary" hidden>Show this location in the tour ↑</button><p class="navigation-note" role="status"></p></div></dialog>`;
    this.viewer = document.querySelector(this.getAttribute('viewer') || 'building-interior-viewer');
    this.onSelection = (event) => this.selectHouse(event.detail);
    this.viewer?.addEventListener('building-selected', this.onSelection);
    if (this.viewer?.selected) this.selectHouse(this.viewer.selected);
    this.$('.analyze').addEventListener('click', () => this.analyze());
    this.$('.check-connection').addEventListener('click', () => this.loadConfiguration());
    this.$('.close-dialog').addEventListener('click', () => this.$('dialog').close());
    this.$('dialog').addEventListener('click', (event) => {
      if (event.target === this.$('dialog')) this.$('dialog').close();
    });
    this.loadConfiguration();
  }

  disconnectedCallback() {
    this.stopPolling();
    this.disposeViewer();
    this.viewer?.removeEventListener('building-selected', this.onSelection);
    this.$('dialog')?.close();
    this.initialized = false;
  }

  $(selector) { return this.shadowRoot.querySelector(selector); }
  get api() { return this.getAttribute('api-base') || '/api/interiors'; }

  stopPolling() {
    this.version++;
    this.request?.abort();
    clearTimeout(this.pollTimer);
    clearInterval(this.elapsedTimer);
    clearTimeout(this.activityTimer);
    this.activityTimer = null;
    this.activityEvents = [];
    this.pendingOutcome = null;
    this.terminalActivityDelay = null;
  }

  disposeViewer() {
    this.viewerToken = (this.viewerToken || 0) + 1; // a viewer still loading is discarded when it arrives
    this.viewer3d?.dispose();
    this.viewer3d = null;
  }

  updateButton() {
    const button = this.$('.analyze');
    button.disabled = !this.house || this.busy || this.configured !== true;
    button.textContent = this.busy ? 'Review in progress…' : this.hasReport ? 'Run a fresh live review ↗' : 'Run live review ↗';
  }

  async loadConfiguration() {
    this.$('.config-message').textContent = 'Checking the review team…';
    this.$('.check-connection').hidden = true;
    try {
      const response = await fetch(`${this.api}/analysis-config`);
      if (!response.ok) throw new Error();
      const config = await response.json();
      if (!this.isConnected) return;
      this.configured = config.configured === true;
      this.$('.config-message').textContent = this.configured
        ? `Ready · vision model ${strings(config.models).join(', ') || config.model}`
        : config.message || 'Connect a vision model on the server to run a live review.';
    } catch {
      this.configured = false;
      this.$('.config-message').textContent = 'The review service is unavailable. Check the connection and try again.';
    }
    this.$('.configuration').dataset.ready = String(this.configured);
    this.$('.check-connection').hidden = this.configured;
    this.updateButton();
  }

  reset() {
    this.disposeViewer();
    this.hasReport = false;
    this.$('.report').replaceChildren();
    this.$('.report').hidden = true;
    this.$('.assessment-error').hidden = true;
    this.$('.progress').hidden = true;
    this.$('.live-activity').hidden = true;
    this.$('.live-activity').replaceChildren();
    this.$('.saved-note').hidden = true;
    this.$('.initial-state').hidden = false;
    this.$('dialog').close();
  }

  selectHouse(house) {
    if (house?.id && this.house?.id === house.id) {
      this.house = house;
      return;
    }
    this.stopPolling();
    this.house = house?.id ? house : null;
    this.busy = false;
    this.$('.selected-house').textContent = this.house?.name || 'Choose a building above';
    this.$('.selected-address').textContent = this.house?.address || 'The review follows your selected tour.';
    this.reset();
    this.updateButton();
    if (this.house) this.loadSaved(this.house.id, this.version);
  }

  /** A review saved on this server replays instantly, with no model call. */
  async loadSaved(modelId, version) {
    try {
      const response = await fetch(`${this.api}/reviews/${encodeURIComponent(modelId)}`);
      if (!response.ok || version !== this.version) return;
      const saved = await response.json();
      if (version !== this.version || saved.report?.model_id !== modelId) return;
      const date = new Date(saved.saved_at);
      this.$('.saved-note').textContent = saved.report.provenance === 'preview' ? 'Showing a hand-assembled preview. Run a live review to replace it with the agents’ own.' : `Showing the review saved ${Number.isNaN(date.valueOf()) ? 'earlier' : date.toLocaleString(undefined, { dateStyle: 'medium', timeStyle: 'short' })}. It replays without a model call.`;
      this.$('.saved-note').hidden = false;
      this.$('.initial-state').hidden = true;
      this.renderReport(saved.report, saved.activity, saved.evidence_base);
    } catch { /* No saved review: the initial state stays. */ }
  }

  async analyze() {
    if (!this.house || this.busy || this.configured !== true) return;
    this.stopPolling();
    const version = this.version;
    this.busy = true;
    this.reset();
    this.$('.initial-state').hidden = true;
    this.$('.progress').hidden = false;
    this.$('.progress-stage').textContent = 'Preparing review';
    this.$('.progress-message').textContent = 'Loading the scan.';
    this.$('.live-activity').hidden = false;
    this.started = Date.now();
    this.$('.elapsed').textContent = '0:00 elapsed';
    this.elapsedTimer = setInterval(() => {
      const seconds = Math.floor((Date.now() - this.started) / 1000);
      this.$('.elapsed').textContent = `${Math.floor(seconds / 60)}:${String(seconds % 60).padStart(2, '0')} elapsed`;
    }, 1000);
    this.updateButton();
    this.request = new AbortController();
    try {
      const response = await fetch(`${this.api}/assessments`, {
        method: 'POST', headers: { 'Content-Type': 'application/json' }, signal: this.request.signal,
        body: JSON.stringify({ model_id: this.house.id }),
      });
      const job = await response.json();
      if (version !== this.version) return;
      if (!response.ok) throw new Error(job.error || 'The review could not start. Please try again.');
      if (!job.id) throw new Error('The review service returned an incomplete response.');
      this.pollJob(job.id, version);
    } catch (error) {
      if (version === this.version && error.name !== 'AbortError') this.fail(error.message);
    }
  }

  async pollJob(jobId, version) {
    if (version !== this.version || !this.isConnected) return;
    this.request = new AbortController();
    try {
      const response = await fetch(`${this.api}/assessments/${encodeURIComponent(jobId)}`, { signal: this.request.signal });
      const job = await response.json();
      if (version !== this.version || !this.isConnected) return;
      if (!response.ok) throw new Error(job.error || 'The review service lost this review. Please try again.');
      this.activityEvents = Array.isArray(job.activity) ? job.activity : [];
      if (job.status === 'completed') {
        if (!job.report || job.report.model_id !== this.house.id) throw new Error('The report does not match the selected building. Please start a new review.');
        clearInterval(this.elapsedTimer);
        this.pendingOutcome = job;
      } else if (job.status === 'failed') {
        clearInterval(this.elapsedTimer);
        this.pendingOutcome = job;
      } else if (job.status === 'queued' || job.status === 'running') {
        this.$('.progress-stage').textContent = typeof job.stage === 'string' ? job.stage.replaceAll('_', ' ') : 'Reviewing';
        this.$('.progress-message').textContent = job.message || 'The review is running.';
        this.pollTimer = setTimeout(() => this.pollJob(jobId, version), 1500);
      } else throw new Error('The review service returned an unknown status.');
      if (this.pendingOutcome) {
        this.$('.progress-stage').textContent = 'Review activity';
        this.$('.progress-message').textContent = 'Showing the final recorded steps.';
      }
      this.playActivity(version);
    } catch (error) {
      if (version === this.version && error.name !== 'AbortError') {
        this.pendingOutcome = { status: 'failed', error: error.message };
        clearInterval(this.elapsedTimer);
        this.$('.progress-stage').textContent = 'Review activity';
        this.$('.progress-message').textContent = 'Showing the final recorded steps.';
        this.playActivity(version);
      }
    }
  }

  // Play only recorded tool activity. Never invent work when a provider stops.
  playActivity(version) {
    if (version !== this.version || !this.isConnected || this.activityTimer !== null) return;
    const list = this.$('.live-activity');
    const shown = list.childElementCount;
    if (shown < this.activityEvents.length) {
      this.renderActivity(list, this.activityEvents.slice(0, shown + 1), true);
      // Keep batched responses readable, without adding minutes to a long run.
      if (this.pendingOutcome && this.terminalActivityDelay === null) {
        this.terminalActivityDelay = Math.min(700, 20000 / (this.activityEvents.length - shown));
      }
      const delay = this.pendingOutcome ? this.terminalActivityDelay : 700;
      this.activityTimer = setTimeout(() => {
        this.activityTimer = null;
        this.playActivity(version);
      }, delay);
      return;
    }
    const outcome = this.pendingOutcome;
    if (!outcome) return;
    this.pendingOutcome = null;
    if (outcome.status === 'failed') {
      this.fail(outcome.error || 'The review paused before all tasks were complete. Please try again.');
      return;
    }
    this.busy = false;
    this.$('.progress').hidden = true;
    this.$('.live-activity').hidden = true;
    this.renderReport(outcome.report, outcome.activity, outcome.evidence_base);
  }

  fail(message) {
    this.busy = false;
    clearInterval(this.elapsedTimer);
    clearTimeout(this.pollTimer);
    clearTimeout(this.activityTimer);
    this.activityTimer = null;
    this.pendingOutcome = null;
    this.$('.progress').hidden = true;
    this.$('.error-message').textContent = message;
    this.$('.assessment-error').hidden = false;
    this.updateButton();
  }

  renderActivity(list, events, follow = false) {
    const items = Array.isArray(events) ? events : [];
    for (const event of items.slice(list.childElementCount)) {
      const row = create('li', 'event');
      row.dataset.agent = agents[event.agent] ? event.agent : 'system';
      const seconds = Math.max(Number(event.t) || 0, 0);
      row.append(
        create('span', 'event-time', `${Math.floor(seconds / 60)}:${String(Math.floor(seconds % 60)).padStart(2, '0')}`),
        create('span', 'event-agent', agents[event.agent] || 'System'),
        create('span', 'event-text', event.text),
      );
      list.append(row);
    }
    if (follow) list.scrollTop = list.scrollHeight;
  }

  // --- Evidence images -----------------------------------------------------------

  imageUrl(viewId) {
    return /^R\d{1,2}-[A-Z]-(ceiling|wall-[1-4]|floor)$/.test(viewId) && this.evidenceBase ? `${this.evidenceBase}/${viewId}` : null;
  }

  /** A scan view with the flagged region outlined on it. */
  boxedImage(viewId, box, alt) {
    const frame = create('div', 'boxed');
    const url = this.imageUrl(viewId);
    if (url) {
      const img = create('img');
      img.src = url;
      img.alt = alt;
      img.loading = 'lazy';
      frame.append(img);
    }
    if (box) {
      const outline = create('span', 'box-outline');
      outline.style.left = `${box.left * 100}%`;
      outline.style.top = `${box.top * 100}%`;
      outline.style.width = `${(box.right - box.left) * 100}%`;
      outline.style.height = `${(box.bottom - box.top) * 100}%`;
      frame.append(outline);
    }
    return frame;
  }

  openEvidence(finding, viewId = finding.view_id) {
    const second = viewId !== finding.view_id;
    this.$('.dialog-label').textContent = `${finding.id} · ${viewId}`;
    this.$('.dialog-title').textContent = second ? 'Second angle used by the skeptic' : finding.title;
    this.$('.dialog-location').textContent = second ? `${finding.room_name}, the same surface from another scan position.` : finding.view_label;
    this.$('.dialog-frame').replaceChildren(this.boxedImage(viewId, second ? null : finding.box, finding.title));
    this.$('.navigation-note').textContent = '';
    const navigate = this.$('.show-location');
    navigate.hidden = second || !finding.sweep_id || typeof this.viewer?.focusEvidence !== 'function';
    navigate.onclick = async () => {
      navigate.disabled = true;
      try {
        if (await this.viewer.focusEvidence({ sweep_id: finding.sweep_id })) {
          this.$('dialog').close();
          this.viewer.scrollIntoView({ behavior: reducedMotion() ? 'instant' : 'smooth', block: 'center' });
        } else this.$('.navigation-note').textContent = 'The tour controls are still connecting. Use the tour’s own navigation to reach this room.';
      } catch { this.$('.navigation-note').textContent = 'This scan position could not be opened in the tour.'; }
      finally { navigate.disabled = false; }
    };
    if (!this.$('dialog').open) this.$('dialog').showModal();
  }

  // --- Report ----------------------------------------------------------------------

  renderReport(report, activity, evidenceBase) {
    this.disposeViewer();
    this.showScenario = null;
    this.evidenceBase = typeof evidenceBase === 'string' && evidenceBase.startsWith(`${this.api}/reviews/`) ? evidenceBase : null;
    this.hasReport = true;
    this.updateButton();
    const root = this.$('.report');
    root.replaceChildren();
    const findings = Array.isArray(report.findings) ? report.findings : [];
    const sources = new Map((Array.isArray(report.sources) ? report.sources : []).map((item) => [item.id, item]));

    const heading = create('div', 'report-heading');
    heading.append(create('p', 'eyebrow', 'THE REVIEW'), create('h3', '', report.headline), create('p', 'report-summary', report.summary));
    if (report.provenance === 'preview') {
      const notice = create('p', 'preview-notice');
      notice.append(create('strong', '', 'Preview, not a recorded agent run. '), report.provenance_note || '');
      heading.append(notice);
    }
    root.append(heading);

    // Reviews saved before every finding had a scenario carry only the lead one.
    const scenarios = (Array.isArray(report.scenarios) && report.scenarios.length ? report.scenarios : [report.scenario])
      .filter((item) => item && findings.some((finding) => finding.id === item.finding_id));
    if (scenarios.length) root.append(this.scenarioDeck(report, scenarios, findings, sources));
    const covered = new Set(scenarios.map((item) => item.finding_id));
    const others = findings.filter((finding) => !covered.has(finding.id));
    if (others.length) {
      const section = create('section', 'block');
      section.append(create('p', 'eyebrow', scenarios.length ? 'ALSO CONFIRMED' : 'CONFIRMED FINDINGS'), create('h3', '', 'Early signs the skeptic could not knock down'));
      const cards = create('div', 'findings');
      for (const finding of others) cards.append(this.findingCard(finding));
      section.append(cards);
      root.append(section);
    }
    if (Array.isArray(report.rejected) && report.rejected.length) {
      const details = create('details', 'block rejected');
      details.append(create('summary', '', `${report.rejected.length} flagged ${report.rejected.length === 1 ? 'candidate' : 'candidates'} rejected by the skeptic`));
      for (const item of report.rejected) {
        const row = create('div', 'rejected-row');
        row.append(create('p', 'rejected-title', `${item.room_name}: ${item.title}`), create('p', 'rejected-reason', item.reason));
        details.append(row);
      }
      root.append(details);
    }
    if (Array.isArray(activity) && activity.length) {
      const details = create('details', 'block');
      details.append(create('summary', '', `How the agents got here · ${activity.length} steps`));
      const list = create('ol', 'activity');
      this.renderActivity(list, activity);
      details.append(list);
      root.append(details);
    }
    root.append(this.coverageSection(report, sources));
    root.hidden = false;
    this.showScenario?.(0);
  }

  /** One confirmed finding at a time: its evidence, what could happen, and how to fix it. */
  scenarioDeck(report, scenarios, findings, sources) {
    const deck = create('div', 'scenario-deck');
    const tabs = create('div', 'scenario-tabs');
    tabs.setAttribute('role', 'tablist');
    tabs.setAttribute('aria-label', 'Confirmed findings');
    const body = create('div', 'scenario-body');
    const findingOf = (scenario) => findings.find((finding) => finding.id === scenario.finding_id);
    this.showScenario = (index) => {
      const scenario = scenarios[index];
      this.disposeViewer(); // also discards a viewer still loading for the previous tab
      [...tabs.children].forEach((tab, i) => tab.setAttribute('aria-selected', String(i === index)));
      body.replaceChildren(this.scenarioSection(report, scenario, findingOf(scenario), sources));
      if (report.mesh_available) this.mountViewer(report, scenario);
    };
    scenarios.forEach((scenario, index) => {
      const finding = findingOf(scenario);
      const tab = create('button', 'scenario-tab');
      tab.type = 'button';
      tab.setAttribute('role', 'tab');
      tab.append(
        create('span', 'tab-id', `${finding.id} · ${finding.room_name}`),
        create('span', 'tab-title', finding.title),
        create('span', 'tab-meta', `${categories[finding.category] || 'Visible condition'} · ${finding.confidence} confidence`),
      );
      tab.addEventListener('click', () => this.showScenario(index));
      tabs.append(tab);
    });
    if (scenarios.length > 1) {
      const intro = create('div', 'deck-intro');
      intro.append(create('p', 'eyebrow', `${scenarios.length} CONFIRMED FINDINGS`), create('h3', '', 'Pick a finding to see what could happen, and how to fix it'));
      deck.append(intro, tabs);
    }
    deck.append(body);
    return deck;
  }

  findingCard(finding) {
    const card = create('article', 'finding');
    const media = create('button', 'finding-media');
    media.type = 'button';
    media.setAttribute('aria-label', `Enlarge the scan view for ${finding.title}`);
    media.append(this.boxedImage(finding.view_id, finding.box, ''));
    media.addEventListener('click', () => this.openEvidence(finding));
    const text = create('div', 'finding-text');
    const top = create('div', 'finding-top');
    top.append(create('span', 'finding-number', finding.id), create('span', 'finding-category', categories[finding.category] || 'Visible condition'), create('span', 'confidence', `${finding.confidence} confidence in what is visible`));
    text.append(top, create('h4', '', finding.title), create('p', 'finding-location', finding.view_label));
    text.append(this.textSection('WHAT THE SURVEYOR SAW', finding.observation));
    const verification = this.textSection('WHY THE SKEPTIC AGREED', finding.verification);
    if (finding.second_view_id) {
      const link = create('button', 'link-button', 'See the second angle');
      link.type = 'button';
      link.addEventListener('click', () => this.openEvidence(finding, finding.second_view_id));
      verification.append(link);
    }
    text.append(verification, this.textSection('WHY IT MATTERS', finding.possible_consequence));
    card.append(media, text);
    return card;
  }

  scenarioSection(report, scenario, finding, sources) {
    const section = create('section', 'scenario');
    section.append(create('p', 'eyebrow', 'WHAT COULD HAPPEN'), create('h3', '', scenario.title), create('p', 'premise', `${scenario.premise}.`));

    const stage = create('div', 'stage-grid');
    if (finding) {
      const evidence = create('div', 'stage-evidence');
      evidence.append(create('span', 'field-label', `THE EVIDENCE · ${finding.id}`));
      const media = create('button', 'finding-media');
      media.type = 'button';
      media.setAttribute('aria-label', `Enlarge the scan view for ${finding.title}`);
      media.append(this.boxedImage(finding.view_id, finding.box, finding.title));
      media.addEventListener('click', () => this.openEvidence(finding));
      evidence.append(media, create('h4', '', finding.title), create('p', 'finding-location', finding.view_label), create('p', 'evidence-text', finding.observation));
      const verification = this.textSection('WHY THE SKEPTIC AGREED', finding.verification);
      if (finding.second_view_id) {
        const link = create('button', 'link-button', 'See the second angle');
        link.type = 'button';
        link.addEventListener('click', () => this.openEvidence(finding, finding.second_view_id));
        verification.append(link);
      }
      evidence.append(verification);
      stage.append(evidence);
    }

    const model = create('div', 'stage-model');
    model.innerHTML = `
      <div class="model-top"><span class="field-label">THE SCANNED ROOM, IN 3D</span>
        <div class="view-toggle mode-toggle" role="group" aria-label="What the 3D view plays"><button type="button" data-mode="failure" aria-pressed="true">What could happen</button><button type="button" data-mode="repair" aria-pressed="false">The fix</button></div>
        <div class="view-toggle" role="group" aria-label="Camera"><button type="button" data-view="room" aria-pressed="true">In the room</button><button type="button" data-view="dollhouse" aria-pressed="false">From above</button></div></div>
      <div class="canvas"><p class="canvas-status" role="status">Loading the building’s mesh…</p></div>
      <div class="stages" role="group" aria-label="Scenario stage"></div>
      <div class="scrub"><button type="button" class="play" disabled>▶ Play</button><input type="range" min="0" max="5" step="0.01" value="0" aria-label="Scenario timeline" disabled></div>
      <p class="stage-title"></p><p class="stage-caption"></p>
      <p class="disclaimer"></p>`;
    if (!Array.isArray(scenario.repair_plan?.stages)) model.querySelector('.mode-toggle').hidden = true;
    model.querySelector('.disclaimer').textContent = report.mesh_available
      ? `${scenario.disclaimer} The room is the scan’s own mesh, with its panoramas projected from scan position${scenario.projectors.length > 1 ? 's' : ''} ${scenario.projectors.map((item) => item.position_id).join(' and ')}.${scenario.zone.widened ? ' The flagged section is drawn one framing bay wide.' : ''}`
      : scenario.disclaimer;
    stage.append(model);
    section.append(stage);

    const calc = scenario.calc;
    const numbers = create('div', 'calc');
    numbers.append(create('span', 'field-label', 'CALCULATED FROM THE SCAN’S GEOMETRY'));
    const tiles = create('div', 'tiles');
    const tile = (value, unit, label) => {
      const node = create('div', 'tile');
      const figure = create('p', 'tile-value', value);
      figure.append(create('span', 'tile-unit', unit));
      node.append(figure, create('p', 'tile-label', label));
      return node;
    };
    if (Array.isArray(calc.tiles)) {
      for (const item of calc.tiles) tiles.append(tile(number(item.value, item.digits), item.unit, item.label));
    } else {
      tiles.append(
        tile(number(calc.area_m2, 2), ' m²', 'section that could detach'),
        tile(number(calc.drop_height_m, 2), ' m', 'ceiling to floor'),
        tile(number(calc.fall_time_s, 2), ' s', 'time to reach the floor'),
        tile(number(calc.speed_at_landing_m_s), ' m/s', `speed reaching ${calc.landing_label}`),
      );
      for (const mass of calc.masses) tiles.append(tile(number(mass.mass_kg), ' kg', `dry weight if ${mass.label}`));
    }
    numbers.append(tiles);
    const inputs = create('ul', 'calc-inputs');
    for (const line of strings(calc.inputs)) inputs.append(create('li', '', line));
    if (!calc.finish_known) inputs.append(create('li', '', 'The finish could not be identified from the images, so both common finishes are shown.'));
    numbers.append(inputs);
    section.append(numbers);
    const fix = this.fixSection(scenario);
    if (fix) section.append(fix);
    const cost = this.repairCostSection(scenario.repair_cost);
    if (cost) section.append(cost);

    const advice = create('div', 'advice');
    advice.append(this.textSection('WHY THIS COULD FAIL', scenario.why));
    if (scenario.written_by_analyst === false) advice.firstChild.append(create('p', 'mechanism-note', 'The failure analyst wrote up the lead finding. This one uses the standard description of the mechanism for this kind of damage.'));
    const signs = create('div', 'text-section');
    signs.append(create('h5', 'field-label', 'EARLY WARNING SIGNS TO WATCH'));
    const list = create('ul', 'signs');
    for (const sign of strings(scenario.early_warning_signs)) list.append(create('li', '', sign));
    signs.append(list);
    const action = create('div', 'recommended-action');
    action.append(this.textSection('NEXT STEP', scenario.recommended_action), this.textSection('WHO SHOULD DO IT', scenario.who_to_call));
    advice.append(signs, action);
    section.append(advice);
    this.addReferences(section, ['nps_plaster', 'asce7_dead_loads', 'epa_moisture', ...strings(scenario.repair_cost?.reference_ids)], sources);
    return section;
  }

  // Two ways to fix one finding, priced for the same job. Links open the
  // retailer's listing or a public directory; nothing is bought from this page.
  fixSection(scenario) {
    const plan = scenario.repair_plan;
    if (!plan?.diy || !plan?.professional) return null;
    const money = (value) => `$${Number(value).toLocaleString('en-US', { minimumFractionDigits: Number.isInteger(value) ? 0 : 2, maximumFractionDigits: 2 })}`;
    const range = (low, high) => (low === high ? money(low) : `${money(low)} – ${money(high)}`);
    const external = (label, url, className = '') => {
      try {
        const parsed = new URL(url);
        if (parsed.protocol !== 'https:') return null;
        const link = create('a', className, label);
        link.href = parsed.href;
        link.target = '_blank';
        link.rel = 'noopener noreferrer';
        return link;
      } catch { return null; }
    };
    const section = create('div', 'fix');
    section.append(create('span', 'field-label', 'HOW TO FIX IT: TWO OPTIONS'));
    const work = plan.surface_work;
    if (work && Number.isFinite(work.diy_usd)) {
      const compare = create('p', 'fix-compare', 'For the visible repair alone: ');
      compare.append(create('strong', '', `about ${money(work.diy_usd)} in materials`), ' if you do it yourself, against ', create('strong', '', range(work.professional_low, work.professional_high)), ' for a professional.');
      if (plan.source_work) compare.append(` Either way, finding and fixing the moisture source is a professional’s job: ${range(plan.source_work.low, plan.source_work.high)}.`);
      section.append(compare);
    }
    const grid = create('div', 'fix-grid');

    const diy = create('article', 'fix-card fix-diy');
    const diyHead = create('div', 'fix-head');
    diyHead.append(create('p', 'fix-kind', plan.diy.label), create('p', 'fix-total', money(plan.diy.materials_total_usd)), create('p', 'fix-sub', `materials for the visible repair · ${plan.diy.difficulty} · ${plan.diy.time}`));
    if (plan.source_work) diyHead.append(create('p', 'fix-sub fix-plus', `+ ${range(plan.source_work.low, plan.source_work.high)} for a professional to find and fix the moisture source`));
    diy.append(diyHead);
    const shopping = (title, items) => {
      if (!items?.length) return;
      diy.append(create('h5', 'field-label', title));
      const list = create('ul', 'fix-items');
      for (const item of items) {
        const row = create('li');
        const line = create('p', 'cost-line');
        const name = create('span', '', item.quantity > 1 ? `${item.quantity} × ${item.name}` : item.name);
        line.append(name, create('span', 'cost-amount', item.total_usd === null || item.total_usd === undefined ? 'price in store' : money(item.total_usd)));
        row.append(line);
        const label = item.link_kind === 'product' ? `Buy at ${item.retailer} ↗` : item.link_kind === 'category' ? `See it on the shelf at ${item.retailer} ↗` : `Search ${item.retailer} ↗`;
        const link = external(label, item.url, 'buy-link');
        if (link) row.append(link);
        list.append(row);
      }
      diy.append(list);
    };
    shopping('MATERIALS', plan.diy.materials);
    shopping('TOOLS YOU MAY ALREADY OWN', plan.diy.tools);
    diy.append(create('p', 'cost-basis', plan.diy.price_note));
    diy.append(create('h5', 'field-label', 'STEPS'));
    const steps = create('ol', 'fix-steps');
    for (const step of strings(plan.diy.steps)) steps.append(create('li', '', step));
    diy.append(steps);
    diy.append(create('h5', 'field-label', 'BEFORE YOU START'));
    const cautions = create('ul', 'fix-cautions');
    for (const caution of strings(plan.diy.cautions)) cautions.append(create('li', '', caution));
    diy.append(cautions);
    const safety = create('p', 'fix-links');
    for (const item of plan.safety_links || []) {
      const link = external(`${item.label} ↗`, item.url);
      if (link) safety.append(link);
    }
    if (safety.childElementCount) diy.append(safety);

    const pro = create('article', 'fix-card fix-pro');
    const proHead = create('div', 'fix-head');
    const visible = work && plan.source_work ? [work.professional_low, work.professional_high] : [plan.professional.low, plan.professional.high];
    proHead.append(create('p', 'fix-kind', plan.professional.label), create('p', 'fix-total', range(...visible)), create('p', 'fix-sub', 'guide price for the visible repair'));
    if (plan.source_work) proHead.append(create('p', 'fix-sub fix-plus', `+ ${range(plan.source_work.low, plan.source_work.high)} to find and fix the moisture source · ${range(plan.professional.low, plan.professional.high)} in all`));
    pro.append(proHead);
    pro.append(create('h5', 'field-label', 'WHAT YOU PAY FOR'));
    const lines = create('ul', 'fix-items');
    for (const item of plan.professional.items || []) {
      const row = create('li');
      const line = create('p', 'cost-line');
      line.append(create('span', '', item.label), create('span', 'cost-amount', range(item.low, item.high)));
      row.append(line, create('p', 'cost-basis', item.basis));
      lines.append(row);
    }
    pro.append(lines, this.textSection('WHO TO CALL', scenario.who_to_call));
    if (plan.professional.links?.length) {
      pro.append(create('h5', 'field-label', 'FIND ONE'));
      const directory = create('div', 'pro-links');
      for (const item of plan.professional.links) {
        const link = external(`${item.label} ↗`, item.url, 'pro-link');
        if (link) directory.append(link);
      }
      pro.append(directory, create('p', 'cost-basis', plan.professional.link_note));
    }

    const show = create('button', 'secondary show-fix', '▶ Watch the fix in 3D');
    show.type = 'button';
    show.hidden = true; // shown once the 3D view is ready
    grid.append(diy, pro);
    section.append(grid, show);
    return section;
  }

  // Repair now against repair after the section has fallen. Reviews saved
  // before this comparison existed have no repair_cost and show nothing here.
  repairCostSection(cost) {
    const bills = [cost?.now, cost?.after];
    if (!bills.every((bill) => bill && Number.isFinite(bill.low) && Number.isFinite(bill.high) && Array.isArray(bill.items))) return null;
    const money = (value) => `$${Math.round(value).toLocaleString('en-US')}`;
    const range = (low, high) => (low === high ? money(low) : `${money(low)} – ${money(high)}`);
    const section = create('div', 'repair-cost');
    section.append(create('span', 'field-label', cost.heading || 'WHAT THE REPAIR COSTS: NOW, OR AFTER IT FALLS'));
    const grid = create('div', 'cost-grid');
    bills.forEach((bill, index) => {
      const card = create('div', index ? 'cost-card cost-after' : 'cost-card');
      card.append(create('p', 'cost-when', bill.label), create('p', 'cost-total', range(bill.low, bill.high)));
      const list = create('ul', 'cost-items');
      for (const item of bill.items) {
        const row = create('li');
        const head = create('p', 'cost-line');
        head.append(create('span', '', item.label), create('span', 'cost-amount', range(item.low, item.high)));
        row.append(head, create('p', 'cost-basis', item.basis));
        list.append(row);
      }
      card.append(list);
      grid.append(card);
    });
    section.append(grid);
    if (Number.isFinite(cost.added_low) && Number.isFinite(cost.added_high)) {
      const [before, after] = strings(cost.added_text).length === 2 ? cost.added_text : ['Waiting until it falls adds ', ' to the bill, for the same leak and the same ceiling.'];
      const added = create('p', 'cost-added', before);
      added.append(create('strong', '', range(cost.added_low, cost.added_high)), after);
      section.append(added);
    }
    const notes = create('ul', 'calc-inputs');
    if (cost.context?.statement && Number.isFinite(cost.context.amount)) notes.append(create('li', '', `${cost.context.statement} ${money(cost.context.amount)}.`));
    for (const note of strings(cost.notes)) notes.append(create('li', '', note));
    section.append(notes);
    return section;
  }

  async mountViewer(report, scenario) {
    const token = this.viewerToken;
    const root = this.$('.stage-model');
    const canvas = root.querySelector('.canvas');
    const range = root.querySelector('input[type="range"]');
    const play = root.querySelector('.play');
    const stageHost = root.querySelector('.stages');
    const timelines = { failure: scenario.stages, repair: scenario.repair_plan?.stages };
    let stages = timelines.failure;
    let stageButtons = [];
    const show = (progress) => {
      const index = Math.min(Math.max(Math.ceil(progress) - 1, 0), stages.length - 1);
      const active = progress === 0 ? 0 : index;
      stageButtons.forEach((button, i) => button.setAttribute('aria-pressed', String(i === active)));
      root.querySelector('.stage-title').textContent = `${active + 1} of ${stages.length} · ${stages[active].title}`;
      root.querySelector('.stage-caption').textContent = stages[active].caption;
      range.value = String(progress);
    };
    let onStage = () => {};
    const buildStages = (ready) => {
      stageHost.replaceChildren();
      stageButtons = stages.map((item, index) => {
        const button = create('button', '', item.title);
        button.type = 'button';
        button.disabled = !ready;
        button.addEventListener('click', () => onStage(index));
        stageHost.append(button);
        return button;
      });
      range.max = String(stages.length);
      show(0);
    };
    buildStages(false);
    try {
      const { createForecastViewer } = await import('/static/forecast3d.js');
      const viewer = await createForecastViewer(canvas, {
        meshUrl: `${this.api}/models/${encodeURIComponent(report.model_id)}/mesh`,
        scenario,
        evidenceUrl: (viewId) => this.imageUrl(viewId),
      });
      if (token !== this.viewerToken || !this.isConnected) { viewer.dispose(); return; }
      this.viewer3d = viewer;
      canvas.querySelector('.canvas-status')?.remove();
      range.disabled = false;
      play.disabled = false;
      const setPlaying = (on) => { play.textContent = on ? '❚❚ Pause' : '▶ Play'; };
      const start = () => { setPlaying(true); viewer.play(show, () => setPlaying(false)); };
      // A stage button shows that stage fully developed; the first shows the scan as found.
      onStage = (index) => { viewer.setProgress(index === 0 ? 0 : index + 1); setPlaying(false); show(viewer.progress); };
      const setMode = (name) => {
        if (!timelines[name]) return;
        viewer.setMode(name);
        stages = timelines[name];
        root.dataset.mode = name;
        root.querySelectorAll('[data-mode]').forEach((button) => button.setAttribute('aria-pressed', String(button.dataset.mode === name)));
        setPlaying(false);
        buildStages(true);
      };
      buildStages(true);
      play.addEventListener('click', () => {
        if (viewer.playing) { viewer.pause(); setPlaying(false); return; }
        start();
      });
      range.addEventListener('input', () => { viewer.setProgress(Number(range.value)); setPlaying(false); show(viewer.progress); });
      root.querySelectorAll('[data-mode]').forEach((button) => button.addEventListener('click', () => setMode(button.dataset.mode)));
      root.querySelectorAll('[data-view]').forEach((button) => button.addEventListener('click', () => {
        viewer.setView(button.dataset.view);
        root.querySelectorAll('[data-view]').forEach((other) => other.setAttribute('aria-pressed', String(other === button)));
      }));
      const watch = this.$('.show-fix');
      if (watch && timelines.repair) {
        watch.hidden = false;
        watch.addEventListener('click', () => {
          setMode('repair');
          root.scrollIntoView({ behavior: reducedMotion() ? 'instant' : 'smooth', block: 'center' });
          start();
        });
      }
    } catch (error) {
      const status = canvas.querySelector('.canvas-status');
      if (status) status.textContent = error.message || 'The 3D view could not be loaded.';
    }
  }

  coverageSection(report, sources) {
    const section = create('section', 'coverage');
    section.append(create('p', 'eyebrow', 'KNOW THE BOUNDARIES'), create('h3', '', 'What this review covered'));
    const rooms = Array.isArray(report.rooms) ? report.rooms : [];
    if (rooms.length) {
      const wrap = create('div', 'table-wrap');
      const table = create('table');
      const head = create('tr');
      for (const label of ['Room', 'Floor area', 'Ceiling height', 'Scan positions opened', 'Views zoomed']) head.append(create('th', '', label));
      table.append(head);
      for (const room of rooms) {
        const row = create('tr');
        row.append(
          create('td', '', room.surveyed ? room.name : `${room.name} (not examined)`),
          create('td', 'numeric', room.area_m2 ? `${number(room.area_m2)} m²` : '–'),
          create('td', 'numeric', room.height_m ? `${number(room.height_m, 2)} m` : '–'),
          create('td', 'numeric', `${room.scan_positions_used} of ${room.scan_positions_available}`),
          create('td', 'numeric', room.views_zoomed),
        );
        table.append(row);
      }
      wrap.append(table);
      section.append(wrap, create('p', 'section-description', 'Room names and dimensions come from the Matterport Model API.'));
    }
    const missed = strings(report.not_surveyed);
    if (missed.length) section.append(this.textSection('NOT EXAMINED', missed.join(' · ')));
    const limits = create('ul', 'limitations');
    for (const item of strings(report.limitations)) limits.append(create('li', '', item));
    section.append(limits);
    if (report.prevalence?.statement) {
      const prevalence = create('p', 'prevalence', `${report.prevalence.statement} `);
      prevalence.append(create('span', '', report.prevalence.basis));
      section.append(prevalence);
    }
    this.addReferences(section, [...sources.keys()], sources);
    if (report.engine) section.append(create('p', 'engine', report.engine));
    return section;
  }

  textSection(label, text) {
    const section = create('div', 'text-section');
    section.append(create('h5', 'field-label', label), create('p', '', text || 'Not specified in this review.'));
    return section;
  }

  addReferences(root, ids, sources) {
    const references = create('div', 'references');
    for (const id of strings(ids)) {
      const source = sources.get(id);
      if (!source) continue;
      try {
        const url = new URL(source.url);
        if (url.protocol !== 'https:') continue;
        const link = create('a', '', source.title || id);
        link.href = url.href;
        link.target = '_blank';
        link.rel = 'noopener noreferrer';
        references.append(link);
      } catch { /* Ignore invalid source links. */ }
    }
    if (references.childElementCount) {
      references.prepend(create('span', '', 'Sources: '));
      root.append(references);
    }
  }
}

customElements.define('field-note-leads', FieldNoteLeads);
customElements.define('house-analysis', HouseAnalysis);
