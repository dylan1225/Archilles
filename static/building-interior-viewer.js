/**
 * Standalone, framework-independent browser for the Houston and Los Angeles scan collection.
 * Usage: <building-interior-viewer api-base="/api/interiors">
 * API secrets stay on the server. Only the domain-restricted SDK key is public.
 */
const SDK_URL = 'https://api.matterport.com/sdk/bootstrap/3.1.0-0-g1cd7e0997a/sdk.es6.js';
const sdkModules = new Map();
const icon = (path) => `<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">${path}</svg>`;
const cube = icon('<path d="m12 3 9 5v8l-9 5-9-5V8l9-5Z M3 8l9 5 9-5 M12 13v8"/>');
const walk = icon('<circle cx="14" cy="4" r="2"/><path d="m7 21 4-7-2-4 3-3 3 5 4 1 M11 14l5 7 M5 12l4-2"/>');
const plan = icon('<path d="M3 3h18v18H3z M3 12h7V3 M10 12v4 M10 21v-2 M16 12h5"/>');
const arrow = icon('<path d="m9 5 7 7-7 7"/>');
const search = icon('<circle cx="10.5" cy="10.5" r="6.5"/><path d="m16 16 5 5"/>');

class BuildingInteriorViewer extends HTMLElement {
  constructor() {
    super();
    this.attachShadow({ mode: 'open' });
    this.models = [];
    this.offsets = [null];
    this.page = 0;
    this.search = '';
    this.selectionVersion = 0;
    this.subscriptions = [];
  }

  connectedCallback() {
    if (this.initialized) return;
    this.initialized = true;
    this.search = (this.getAttribute('initial-search') || '').slice(0, 120);
    this.shadowRoot.innerHTML = `
      <link rel="stylesheet" href="/static/building-interior-viewer.css">
      <div class="explorer">
        <aside class="catalog" aria-label="Building collection">
          <div class="catalog-head">
            <div class="section-label">FIND YOUR SPACE <span class="count">—</span></div>
            <form role="search">
              <label class="sr-only" for="building-search">Search buildings by name or address</label>
              <div class="search-box"><input id="building-search" type="search" maxlength="120" placeholder="Search a name or address" autocomplete="off"><button class="search-button" title="Search buildings" aria-label="Search buildings">${search}</button></div>
            </form>
            <div class="catalog-meta"><span>HOUSTON · LOS ANGELES</span><span class="result-count" aria-live="polite"></span></div>
          </div>
          <div class="building-list" aria-label="Available buildings" aria-busy="true"></div>
          <div class="pagination"><button type="button" class="previous" aria-label="Previous page" disabled>←</button><span class="page-info">Loading collection</span><button type="button" class="next" aria-label="Next page" disabled>→</button></div>
          <div class="catalog-footer"><i></i> Published tours · Matterport</div>
        </aside>
        <section class="tour" aria-label="Selected building interior">
          <div class="tour-head">
            <div class="tour-label">${cube}<span>INTERIOR EXPLORER</span></div>
            <span class="tour-state" role="status">Finding spaces</span>
          </div>
          <div class="tour-media">
            <div class="placeholder"><div class="placeholder-icon">${cube}</div><span class="section-label">A CLOSER LOOK</span><h2>Every space has a story.</h2><p>Select a building to explore its interior in 3D.</p></div>
            <div class="tour-loading" hidden><span class="spinner"></span><span>Opening your space…</span></div>
          </div>
          <div class="tour-toolbar">
            <div class="view-modes" role="group" aria-label="Tour view">
              <button type="button" data-mode="INSIDE" aria-pressed="false" disabled>${walk}<span>Walkthrough</span></button>
              <button type="button" data-mode="DOLLHOUSE" aria-pressed="false" disabled>${cube}<span>Dollhouse</span></button>
              <button type="button" data-mode="FLOORPLAN" aria-pressed="false" disabled>${plan}<span>Floor plan</span></button>
            </div>
            <div class="tour-actions">
              <button type="button" class="reload" aria-label="Restart tour" title="Restart tour" disabled>${icon('<path d="M3 11a9 9 0 1 1 2.6 7 M3 4v7h7"/>')}</button>
              <button type="button" class="fullscreen" aria-label="Enter fullscreen" title="Fullscreen" disabled>${icon('<path d="M8 3H3v5 M16 3h5v5 M21 16v5h-5 M8 21H3v-5"/>')}</button>
            </div>
          </div>
          <div class="building-detail">
            <div class="detail-kicker">THE SELECTED SPACE <span class="floor-count"></span></div>
            <div class="detail-row"><h2 class="building-title">Your next perspective awaits.</h2><a class="open-tour" target="_blank" rel="noopener noreferrer" hidden>Open tour ↗</a></div>
            <p class="building-address">Browse the collection to get started.</p>
          </div>
          <div class="tour-hint">${icon('<path d="m9 10 2-7a1 1 0 0 1 2 0v7l2-1 2 1 2 1v5l-4 5h-5l-5-6a1.5 1.5 0 0 1 2-2l2 2Z"/>')}<span class="hint-text">Drag to look around · Click the floor to move · Scroll to zoom</span></div>
        </section>
      </div>`;
    this.$('input').value = this.search;
    this.$('form').addEventListener('submit', (event) => {
      event.preventDefault();
      this.search = this.$('input').value.trim();
      this.offsets = [null];
      this.page = 0;
      this.loadModels();
    });
    this.$('input').addEventListener('search', () => {
      if (!this.$('input').value) this.$('form').requestSubmit();
    });
    this.$('.previous').addEventListener('click', () => {
      if (this.page > 0) { this.page--; this.loadModels(); }
    });
    this.$('.next').addEventListener('click', () => {
      if (this.nextOffset) {
        this.offsets[this.page + 1] = this.nextOffset;
        this.page++;
        this.loadModels();
      }
    });
    this.$('.reload').addEventListener('click', () => this.selectModel(this.selected));
    this.$('.fullscreen').addEventListener('click', async () => {
      try {
        if (document.fullscreenElement) await document.exitFullscreen();
        else await this.$('.tour-media').requestFullscreen();
      } catch { this.$('.hint-text').textContent = 'Fullscreen is unavailable here. Use “Open tour” for a larger view.'; }
    });
    this.shadowRoot.querySelectorAll('[data-mode]').forEach((button) => {
      button.addEventListener('click', () => this.changeMode(button.dataset.mode));
    });
    this.loadModels();
  }

  disconnectedCallback() {
    this.request?.abort();
    this.clearConnection();
    this.$('iframe')?.remove();
    this.initialized = false;
  }

  $(selector) { return this.shadowRoot.querySelector(selector); }

  async loadModels() {
    this.request?.abort();
    const controller = new AbortController();
    this.request = controller;
    const list = this.$('.building-list');
    list.setAttribute('aria-busy', 'true');
    list.innerHTML = '<div class="list-message" role="status"><span class="spinner"></span><p>Finding spaces…</p></div>';
    this.$('.previous').disabled = true;
    this.$('.next').disabled = true;
    this.$('.result-count').textContent = 'Loading…';
    const params = new URLSearchParams({ q: this.search, page_size: '12' });
    if (this.offsets[this.page]) params.set('offset', this.offsets[this.page]);
    try {
      const response = await fetch(`${this.getAttribute('api-base') || '/api/interiors'}/models?${params}`, { signal: controller.signal });
      const data = await response.json();
      if (!response.ok) throw new Error(data.error || 'Could not load buildings. Please try again.');
      if (controller.signal.aborted || !this.isConnected) return;
      this.models = data.models;
      this.sdkKey = data.sdkKey;
      this.sdkDomains = data.sdkDomains || ['localhost'];
      this.nextOffset = data.nextOffset;
      this.$('.count').textContent = Number(data.total).toLocaleString();
      this.$('.result-count').textContent = `${Number(data.total).toLocaleString()} spaces`;
      this.$('.page-info').textContent = data.total ? `Page ${this.page + 1}` : 'No results';
      this.$('.previous').disabled = this.page === 0;
      this.$('.next').disabled = !this.nextOffset;
      this.renderModels();
      if (!this.models.length) this.showEmpty();
      else this.selectModel(this.models.find((model) => model.id === this.selected?.id) || this.models[0]);
    } catch (error) {
      if (controller.signal.aborted) return;
      list.replaceChildren();
      const message = document.createElement('div');
      message.className = 'list-message';
      message.setAttribute('role', 'alert');
      const title = document.createElement('strong');
      title.textContent = 'The collection couldn’t load.';
      const detail = document.createElement('p');
      detail.textContent = error.message;
      const retry = document.createElement('button');
      retry.type = 'button';
      retry.className = 'retry';
      retry.textContent = 'Try again';
      retry.addEventListener('click', () => this.loadModels());
      message.append(title, detail, retry);
      list.append(message);
      this.$('.result-count').textContent = 'Connection unavailable';
      this.$('.page-info').textContent = 'Collection unavailable';
      this.$('.previous').disabled = this.page === 0;
      if (!this.selected) this.$('.tour-state').textContent = 'Waiting for collection';
    } finally {
      if (this.request === controller) list.setAttribute('aria-busy', 'false');
    }
  }

  renderModels() {
    const list = this.$('.building-list');
    list.replaceChildren();
    if (!this.models.length) {
      const message = document.createElement('div');
      message.className = 'list-message';
      message.textContent = 'No published tours match this search. Try another name or address.';
      list.append(message);
      return;
    }
    for (const [index, model] of this.models.entries()) {
      const button = document.createElement('button');
      button.type = 'button';
      button.className = 'building-card';
      button.dataset.id = model.id;
      button.setAttribute('aria-pressed', 'false');
      const image = document.createElement('div');
      image.className = 'building-image';
      image.innerHTML = cube;
      if (model.thumbnail) {
        const url = new URL(model.thumbnail, window.location.origin);
        if (url.protocol === 'https:') {
          const img = document.createElement('img');
          img.src = url.href;
          img.alt = '';
          img.loading = 'lazy';
          img.addEventListener('error', () => img.remove(), { once: true });
          image.append(img);
        }
      }
      const text = document.createElement('span');
      text.className = 'building-card-text';
      const number = document.createElement('span');
      number.className = 'building-number';
      number.textContent = String(this.page * 12 + index + 1).padStart(2, '0');
      const title = document.createElement('span');
      title.className = 'building-name';
      title.textContent = model.name;
      const address = document.createElement('span');
      address.className = 'building-card-address';
      address.textContent = model.address || 'Address not published';
      text.append(number, title, address);
      const end = document.createElement('span');
      end.className = 'card-arrow';
      end.innerHTML = arrow;
      button.append(image, text, end);
      button.addEventListener('click', () => this.selectModel(model));
      list.append(button);
    }
    list.scrollTop = 0;
  }

  clearConnection() {
    this.selectionVersion++;
    clearTimeout(this.tourTimeout);
    for (const subscription of this.subscriptions) subscription.cancel();
    this.subscriptions = [];
    this.sdk = null;
    this.shadowRoot.querySelectorAll('[data-mode]').forEach((button) => {
      button.disabled = true;
      button.setAttribute('aria-pressed', 'false');
    });
  }

  showEmpty() {
    this.clearConnection();
    this.selected = null;
    this.$('iframe')?.remove();
    this.$('.placeholder').hidden = false;
    this.$('.tour-loading').hidden = true;
    this.$('.tour-state').textContent = 'No space selected';
    this.$('.building-title').textContent = 'Find another perspective.';
    this.$('.building-address').textContent = 'Try a different name or street address.';
    this.$('.floor-count').textContent = '';
    this.$('.open-tour').hidden = true;
    this.$('.reload').disabled = true;
    this.$('.fullscreen').disabled = true;
    this.dispatchEvent(new CustomEvent('building-selected', { detail: null, bubbles: true, composed: true }));
  }

  selectModel(model) {
    if (!model) return;
    this.clearConnection();
    const version = this.selectionVersion;
    this.selected = model;
    this.shadowRoot.querySelectorAll('.building-card').forEach((button) => {
      button.setAttribute('aria-pressed', String(button.dataset.id === model.id));
    });
    this.$('.building-title').textContent = model.name;
    this.$('.building-address').textContent = model.address || 'Address not published';
    this.$('.floor-count').textContent = model.floors ? `${model.floors} ${model.floors === 1 ? 'floor' : 'floors'}` : '';
    const open = this.$('.open-tour');
    open.href = `https://my.matterport.com/show/?${new URLSearchParams({ m: model.id })}`;
    open.hidden = false;
    this.$('.reload').disabled = false;
    this.$('.fullscreen').disabled = !this.$('.tour-media').requestFullscreen;
    this.$('.tour-state').textContent = 'Opening tour';
    this.$('.hint-text').textContent = 'Drag to look around · Click the floor to move · Scroll to zoom';
    this.$('.placeholder').hidden = true;
    this.$('.tour-loading').hidden = false;
    this.$('iframe')?.remove();
    const iframe = document.createElement('iframe');
    iframe.title = `3D interior tour of ${model.name}`;
    iframe.allow = 'fullscreen; xr-spatial-tracking; accelerometer; gyroscope';
    iframe.allowFullscreen = true;
    const params = new URLSearchParams({ m: model.id, play: '1', qs: '1', title: '0' });
    const useSdk = Boolean(this.sdkKey && this.sdkDomains.includes(window.location.hostname));
    if (useSdk) params.set('applicationKey', this.sdkKey);
    iframe.src = `https://my.matterport.com/show/?${params}`;
    iframe.addEventListener('load', () => {
      if (version !== this.selectionVersion) return;
      clearTimeout(this.tourTimeout);
      this.$('.tour-loading').hidden = true;
      this.$('.tour-state').textContent = 'Embedded tour';
      if (useSdk) this.connectSdk(iframe, version);
      else this.$('.hint-text').textContent = 'Drag to look around · Use the tour’s built-in buttons to change your view';
    }, { once: true });
    iframe.addEventListener('error', () => this.tourUnavailable(version), { once: true });
    this.tourTimeout = setTimeout(() => this.tourUnavailable(version), 35000);
    this.$('.tour-media').append(iframe);
    this.dispatchEvent(new CustomEvent('building-selected', { detail: { ...model }, bubbles: true, composed: true }));
  }

  tourUnavailable(version) {
    if (version !== this.selectionVersion) return;
    this.$('.tour-loading').hidden = true;
    this.$('.tour-state').textContent = 'Tour may be unavailable';
    this.$('.hint-text').textContent = 'Having trouble loading? Restart the tour, choose another building, or use “Open tour”.';
  }

  async connectSdk(iframe, version) {
    let timeout;
    try {
      if (!sdkModules.has(this.sdkKey)) {
        const url = `${SDK_URL}?${new URLSearchParams({ applicationKey: this.sdkKey })}`;
        const promise = import(url).catch((error) => { sdkModules.delete(this.sdkKey); throw error; });
        sdkModules.set(this.sdkKey, promise);
      }
      const connect = async () => {
        const module = await sdkModules.get(this.sdkKey);
        if (version !== this.selectionVersion) return null;
        const sdk = await module.connect(iframe);
        await sdk.App.state.waitUntil((state) => state.phase === sdk.App.Phase.PLAYING);
        return sdk;
      };
      const sdk = await Promise.race([
        connect(),
        new Promise((_, reject) => { timeout = setTimeout(() => reject(new Error('SDK timed out')), 30000); }),
      ]);
      if (!sdk || version !== this.selectionVersion || !this.isConnected) return;
      this.sdk = sdk;
      this.$('.tour-state').textContent = 'Ready to explore';
      this.shadowRoot.querySelectorAll('[data-mode]').forEach((button) => { button.disabled = false; });
      this.subscriptions.push(sdk.Mode.current.subscribe((mode) => {
        if (version !== this.selectionVersion) return;
        this.shadowRoot.querySelectorAll('[data-mode]').forEach((button) => {
          button.setAttribute('aria-pressed', String(sdk.Mode.Mode[button.dataset.mode] === mode));
        });
      }));
    } catch {
      if (version !== this.selectionVersion) return;
      this.$('.hint-text').textContent = 'Use the tour’s controls. If loading or verification is required, choose “Open tour” to continue in a new tab.';
      this.$('.tour-state').textContent = 'Embedded tour';
    } finally { clearTimeout(timeout); }
  }

  async changeMode(mode) {
    const sdk = this.sdk;
    if (!sdk) return;
    const version = this.selectionVersion;
    try {
      await sdk.Mode.moveTo(sdk.Mode.Mode[mode]);
    } catch {
      if (version === this.selectionVersion) this.$('.hint-text').textContent = 'This view isn’t available for this space. Try another view in the tour.';
    }
  }

  async focusEvidence(evidence) {
    if (!this.sdk || !this.selected || !evidence?.sweep_id) return false;
    const version = this.selectionVersion;
    await this.sdk.Mode.moveTo(this.sdk.Mode.Mode.INSIDE);
    if (version !== this.selectionVersion) return false;
    await this.sdk.Sweep.moveTo(evidence.sweep_id, {});
    return version === this.selectionVersion;
  }
}

customElements.define('building-interior-viewer', BuildingInteriorViewer);
