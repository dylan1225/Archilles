// Archilles page shell: header state, custom cursor, scroll reveals, intro gate.
// Pure presentation. Every page works the same with this file missing.

const reducedMotion = window.matchMedia("(prefers-reduced-motion: reduce)").matches;
const finePointer = window.matchMedia("(pointer: fine)").matches;
const INTRO_KEY = "archilles-entered";
const INTRO_DOCK_MS = 5300;   // when the built logo moves into the header
const INTRO_GLYPH_PX = 300;   // height of the arch "A" while it builds
const INTERACTIVE = "a, button, [role='button'], label, input, textarea, summary, .drop, canvas";

function watchHeader() {
  const header = document.querySelector(".site-header");
  if (!header) return;
  const update = () => { header.dataset.scrolled = String(window.scrollY > 24); };
  update();
  window.addEventListener("scroll", update, { passive: true });
}

function customCursor() {
  if (!finePointer || reducedMotion) return;
  const dot = document.createElement("div");
  dot.className = "cursor";
  dot.setAttribute("aria-hidden", "true");
  document.body.append(dot);
  let x = -100, y = -100, cx = -100, cy = -100;
  window.addEventListener("pointermove", (event) => {
    x = event.clientX; y = event.clientY;
    dot.dataset.visible = "true";
    dot.dataset.hover = String(Boolean(event.target.closest && event.target.closest(INTERACTIVE)));
  }, { passive: true });
  document.addEventListener("pointerleave", () => { dot.dataset.visible = "false"; });
  const follow = () => {
    cx += (x - cx) * 0.22; cy += (y - cy) * 0.22;
    dot.style.transform = `translate3d(${cx}px, ${cy}px, 0)`;
    requestAnimationFrame(follow);
  };
  follow();
}

function reveals() {
  const items = document.querySelectorAll(".reveal, .line-mask");
  if (reducedMotion || !("IntersectionObserver" in window)) {
    items.forEach((item) => item.classList.add("in"));
    return;
  }
  const observer = new IntersectionObserver((entries) => {
    entries.forEach((entry) => {
      if (!entry.isIntersecting) return;
      entry.target.classList.add("in");
      observer.unobserve(entry.target);
    });
  }, { rootMargin: "0px 0px -8% 0px", threshold: 0.12 });
  items.forEach((item) => observer.observe(item));
}

function readFlag() {
  try { return sessionStorage.getItem(INTRO_KEY) === "1"; } catch { return false; }
}

function writeFlag() {
  try { sessionStorage.setItem(INTRO_KEY, "1"); } catch { /* private mode: the gate just shows again */ }
}

/** The visitor is in: lift the gate state, start reveals, tell the hero. */
function entered() {
  document.documentElement.classList.remove("gated");
  document.documentElement.dataset.entered = "true";
  window.dispatchEvent(new Event("archilles:enter"));
  reveals();
}

/** Scale and move the logo from its header slot to the middle of the screen. */
function centreBrand(brand) {
  brand.style.transition = "none";
  brand.style.transform = "none";
  const box = brand.getBoundingClientRect();
  const glyph = brand.querySelector(".brand-glyph").getBoundingClientRect();
  const scale = Math.min(INTRO_GLYPH_PX / glyph.height, (window.innerWidth * 0.78) / box.width);
  const x = window.innerWidth / 2 - box.left - (scale * box.width) / 2;
  const y = window.innerHeight / 2 - box.top - (scale * box.height) / 2;
  brand.style.transform = `translate(${x}px, ${y}px) scale(${scale})`;
}

/**
 * Intro from the intro/logo branch: the arch "A" builds, an arrow strikes its
 * heel, "rchilles" types in, then the logo docks into the header. Click or any
 * key skips it. Shown once per browser session; never with reduced motion.
 */
function introGate() {
  const veil = document.getElementById("intro");
  const brand = document.getElementById("brand");
  if (!veil || !brand) { entered(); return; }
  if (readFlag() || reducedMotion) {
    veil.remove();
    entered();
    return;
  }
  let timer = 0;
  let docked = false;
  const dock = () => {
    if (docked) return;
    docked = true;
    clearTimeout(timer);
    brand.style.transition = "transform 1.1s cubic-bezier(.65, 0, .25, 1)";
    brand.style.transform = "none";
    veil.classList.add("gone");
    writeFlag();
    entered();
    setTimeout(() => { veil.remove(); brand.style.transition = ""; brand.style.transform = ""; }, 1300);
  };
  const skip = () => { brand.classList.add("skip"); dock(); };
  const play = () => {
    // No layout yet (hidden or zero-size window): show the finished logo instead.
    if (!brand.querySelector(".brand-glyph").getBoundingClientRect().height) { skip(); return; }
    centreBrand(brand);
    brand.classList.add("placed");
    void brand.offsetWidth; // restart the CSS animations from the first frame
    brand.classList.add("playing");
    timer = setTimeout(dock, INTRO_DOCK_MS);
  };
  veil.addEventListener("click", skip);
  window.addEventListener("keydown", () => { if (!docked) skip(); });
  (document.fonts ? document.fonts.ready : Promise.resolve()).then(play);
}

watchHeader();
customCursor();
introGate();
