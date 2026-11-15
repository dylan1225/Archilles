// Archilles page shell: header state, custom cursor, scroll reveals, intro gate.
// Pure presentation. Every page works the same with this file missing.

const reducedMotion = window.matchMedia("(prefers-reduced-motion: reduce)").matches;
const finePointer = window.matchMedia("(pointer: fine)").matches;
const INTRO_KEY = "archilles-entered";
const INTRO_MIN_MS = 1100;
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

/** Loading counter, then an Enter button. Skipped once per browser session. */
function entered() {
  document.documentElement.classList.remove("gated");
  document.documentElement.dataset.entered = "true";
  window.dispatchEvent(new Event("archilles:enter"));
  reveals();
}

function introGate() {
  const gate = document.getElementById("intro");
  if (!gate) { entered(); return; }
  const open = () => {
    gate.classList.add("gone");
    writeFlag();
    entered();
    setTimeout(() => gate.remove(), 1400);
  };
  if (readFlag() || reducedMotion) {
    gate.remove();
    entered();
    return;
  }
  const counter = gate.querySelector("[data-count]");
  const enter = gate.querySelector("[data-enter]");
  const started = performance.now();
  let ready = document.readyState === "complete";
  window.addEventListener("load", () => { ready = true; }, { once: true });
  const tick = (now) => {
    const progress = Math.min(1, (now - started) / INTRO_MIN_MS);
    const shown = ready ? progress : Math.min(progress, 0.9);
    counter.textContent = String(Math.round(shown * 100)).padStart(3, "0");
    if (shown < 1) { requestAnimationFrame(tick); return; }
    gate.classList.add("ready");
    enter.focus({ preventScroll: true });
  };
  requestAnimationFrame(tick);
  enter.addEventListener("click", open);
}

watchHeader();
customCursor();
introGate();
