"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import SignaturePad from "signature_pad";
import { ArrowDownToLine, ArrowRight, BookOpen, Check, CheckCheck, ChevronDown, CircleHelp, Eraser, Lightbulb, LoaderCircle, Pencil, RotateCcw, RotateCw, Sparkles, Trash2, X } from "lucide-react";
import type { ReviewResult } from "../lib/review";

type Strokes = ReturnType<SignaturePad["toData"]>;
type InputMode = "draw" | "type";
const COLORS = ["#30323b", "#7662c6", "#38866b", "#d97a5b"];
const PRACTICE = [
  { label: "One step at a time", subject: "Algebra", question: "Solve for x: 2x + 6 = 14", tag: "Linear equations" },
  { label: "Make it add up", subject: "Fractions", question: "Simplify: 1/2 + 1/3", tag: "Adding fractions" },
  { label: "Find the roots", subject: "Algebra", question: "Solve: x² − 5x + 6 = 0", tag: "Quadratic equations" },
  { label: "A change of pace", subject: "Calculus", question: "Find the derivative of f(x) = 3x² + 2x − 4", tag: "Differentiation" },
];
const STORE = "margin-whiteboard-v1";

export default function Home() {
  const canvasRef = useRef<HTMLCanvasElement>(null);
  const padRef = useRef<SignaturePad | null>(null);
  const timerRef = useRef<ReturnType<typeof setTimeout> | null>(null);
  const requestRef = useRef<AbortController | null>(null);
  const revisionRef = useRef(0);
  const busyRef = useRef(false);
  const redoRef = useRef<Strokes>([]);
  const [ready, setReady] = useState(false);
  const [hasInk, setHasInk] = useState(false);
  const [boardVersion, setBoardVersion] = useState(0);
  const [mode, setMode] = useState<InputMode>("draw");
  const [typed, setTyped] = useState("");
  const [problem, setProblem] = useState("");
  const [pen, setPen] = useState<"pen" | "eraser">("pen");
  const [color, setColor] = useState(COLORS[0]);
  const [size, setSize] = useState(2.5);
  const [live, setLive] = useState(true);
  const [busy, setBusy] = useState(false);
  const [waiting, setWaiting] = useState(false);
  const [result, setResult] = useState<ReviewResult | null>(null);
  const [error, setError] = useState("");
  const [configured, setConfigured] = useState<boolean | null>(null);
  const [practiceOpen, setPracticeOpen] = useState(false);
  const [helpOpen, setHelpOpen] = useState(false);
  const [details, setDetails] = useState(false);
  const [grid, setGrid] = useState(true);
  const [saved, setSaved] = useState(false);
  const [notice, setNotice] = useState("");
  const stateRef = useRef({ mode, typed, problem, live });
  stateRef.current = { mode, typed, problem, live };
  const checkRef = useRef<(explain?: boolean) => Promise<void>>(async () => {});

  const invalidate = useCallback(() => {
    revisionRef.current++;
    if (timerRef.current) clearTimeout(timerRef.current);
    requestRef.current?.abort();
    busyRef.current = false;
    setBusy(false); setWaiting(false); setResult(null); setDetails(false); setError("");
  }, []);

  const schedule = useCallback(() => {
    invalidate(); setSaved(false); setBoardVersion(v => v + 1);
    if (stateRef.current.live) {
      setWaiting(true);
      timerRef.current = setTimeout(() => { void checkRef.current(); }, 1600);
    }
  }, [invalidate]);

  useEffect(() => {
    const canvas = canvasRef.current;
    if (!canvas) return;
    let previousWidth = canvas.getBoundingClientRect().width;
    let previousHeight = canvas.getBoundingClientRect().height;
    const ratio = Math.min(window.devicePixelRatio || 1, 2);
    canvas.width = previousWidth * ratio; canvas.height = previousHeight * ratio;
    canvas.getContext("2d")?.scale(ratio, ratio);
    const pad = new SignaturePad(canvas, { minWidth: 1.2, maxWidth: 2.5, penColor: COLORS[0], backgroundColor: "#ffffff", throttle: 8 });
    padRef.current = pad;
    const resizeObserver = new ResizeObserver(() => {
      const rect = canvas.getBoundingClientRect();
      if (!rect.width || !rect.height) return;
      const sx = rect.width / (previousWidth || rect.width), sy = rect.height / (previousHeight || rect.height);
      const data = pad.toData().map(g => ({ ...g, points: g.points.map(p => ({ ...p, x: p.x * sx, y: p.y * sy })) }));
      const ratio = Math.min(window.devicePixelRatio || 1, 2);
      canvas.width = rect.width * ratio; canvas.height = rect.height * ratio;
      canvas.getContext("2d")?.setTransform(ratio, 0, 0, ratio, 0, 0);
      pad.clear(); pad.fromData(data);
      previousWidth = rect.width; previousHeight = rect.height;
    });
    resizeObserver.observe(canvas);
    pad.addEventListener("beginStroke", invalidate);
    pad.addEventListener("endStroke", () => { setHasInk(!pad.isEmpty()); redoRef.current = []; schedule(); });
    try {
      const raw = localStorage.getItem(STORE);
      if (raw) {
        const data = JSON.parse(raw);
        if (typeof data.typed === "string") setTyped(data.typed.slice(0, 6000));
        if (typeof data.problem === "string") setProblem(data.problem.slice(0, 2000));
        if (data.mode === "draw" || data.mode === "type") setMode(data.mode);
        if (Array.isArray(data.strokes) && data.strokes.length) {
          const sx = previousWidth / (data.width || previousWidth), sy = previousHeight / (data.height || previousHeight);
          pad.fromData(data.strokes.map((g: Strokes[number]) => ({ ...g, points: g.points.map(p => ({ ...p, x: p.x * sx, y: p.y * sy })) })));
          setHasInk(!pad.isEmpty());
        }
      }
    } catch { pad.clear(); setNotice("Your saved board could not be restored. Start a fresh one here."); }
    setReady(true);
    fetch("/api/status").then(r => r.json()).then(d => setConfigured((d as { configured: boolean }).configured)).catch(() => setConfigured(false));
    return () => { pad.off(); resizeObserver.disconnect(); requestRef.current?.abort(); if (timerRef.current) clearTimeout(timerRef.current); };
  }, [invalidate, schedule]);

  useEffect(() => {
    const pad = padRef.current; if (!pad) return;
    pad.penColor = pen === "eraser" ? "#ffffff" : color;
    pad.minWidth = pen === "eraser" ? 14 : size * 0.55;
    pad.maxWidth = pen === "eraser" ? 18 : size;
  }, [pen, color, size]);

  useEffect(() => {
    if (!ready) return;
    const save = setTimeout(() => {
      try {
        const rect = canvasRef.current?.getBoundingClientRect();
        localStorage.setItem(STORE, JSON.stringify({ mode, typed, problem, strokes: padRef.current?.toData() || [], width: rect?.width, height: rect?.height }));
        setSaved(true);
      } catch { setNotice("Browser storage is full. Export your board to keep a copy."); }
    }, 600);
    return () => clearTimeout(save);
  }, [mode, typed, problem, boardVersion, ready]);

  const snapshot = useCallback(() => {
    if (stateRef.current.mode === "draw") {
      if (!padRef.current || padRef.current.isEmpty()) return null;
      return padRef.current.toDataURL("image/png");
    }
    const text = stateRef.current.typed.trim(); if (!text) return null;
    const c = document.createElement("canvas"); c.width = 1200;
    const lines = text.split("\n").flatMap(l => l.match(/.{1,64}/g) || [""]);
    if (lines.length > 100) { setNotice("Check up to 100 lines at a time. Start a fresh board for the next part."); return null; }
    c.height = Math.max(500, lines.length * 48 + 100);
    const ctx = c.getContext("2d")!;
    ctx.fillStyle = "#ffffff"; ctx.fillRect(0, 0, c.width, c.height);
    ctx.fillStyle = "#242731"; ctx.font = "30px monospace";
    lines.forEach((line, i) => ctx.fillText(line, 48, 68 + i * 48));
    return c.toDataURL("image/png");
  }, []);

  checkRef.current = async (explain = false) => {
    if (busyRef.current) return;
    if (timerRef.current) clearTimeout(timerRef.current);
    setWaiting(false);
    const image = snapshot(); if (!image) { setResult(null); return; }
    const revision = revisionRef.current;
    const controller = new AbortController(); requestRef.current = controller;
    busyRef.current = true; setBusy(true); setError("");
    try {
      const response = await fetch("/api/review", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ image, problem: stateRef.current.problem, mode: explain ? "explain" : "hint" }), signal: controller.signal });
      const data = await response.json() as ReviewResult & { error?: string };
      if (!response.ok) throw new Error(data.error || "Could not check your work. Please try again.");
      if (revision !== revisionRef.current) return;
      setResult(data); if (explain) setDetails(true);
    } catch (e) {
      if (!controller.signal.aborted && revision === revisionRef.current) setError(e instanceof Error ? e.message : "Could not reach your math coach. Please try again.");
    } finally {
      if (requestRef.current === controller) { busyRef.current = false; setBusy(false); }
    }
  };

  const clearBoard = (confirm = true) => {
    if (confirm && (hasInk || typed.trim()) && !window.confirm("Clear this board? Export it first if you want to keep a copy.")) return false;
    invalidate(); padRef.current?.clear(); redoRef.current = []; setHasInk(false); setTyped(""); setSaved(false); setBoardVersion(v => v + 1); return true;
  };
  const undo = () => {
    const pad = padRef.current; if (!pad) return;
    const data = [...pad.toData()]; const stroke = data.pop();
    if (stroke) { redoRef.current.push(stroke); pad.fromData(data); setHasInk(!pad.isEmpty()); schedule(); }
  };
  const redo = () => {
    const pad = padRef.current; const stroke = redoRef.current.pop();
    if (pad && stroke) { pad.fromData([...pad.toData(), stroke]); setHasInk(true); schedule(); }
  };
  const exportBoard = () => {
    const image = snapshot(); if (!image) { setNotice("Add a little math to your board first."); return; }
    const a = document.createElement("a"); a.download = "margin-whiteboard.png"; a.href = image; a.click(); setNotice("Your board has been exported.");
  };
  const loadExample = () => {
    if (!clearBoard()) return;
    setProblem("Solve for x: 2x + 6 = 14"); setMode("type"); setTyped("2x + 6 = 14\n2x = 14 + 6\n2x = 20\nx = 10");
    setPracticeOpen(false); schedule();
  };
  useEffect(() => { if (!notice) return; const t = setTimeout(() => setNotice(""), 5000); return () => clearTimeout(t); }, [notice]);
  useEffect(() => { const escape = (e: KeyboardEvent) => { if (e.key === "Escape") { setHelpOpen(false); setPracticeOpen(false); } }; window.addEventListener("keydown", escape); return () => window.removeEventListener("keydown", escape); }, []);
  const content = mode === "draw" ? hasInk : !!typed.trim();
  const status = busy ? "Reading your work…" : waiting ? "Waiting for a pause…" : result ? "Feedback is up to date" : live ? "Ready when you are" : "Live feedback paused";

  return <div className="app-shell">
    <header className="site-header">
      <a className="brand" href="/" aria-label="Margin home"><span className="brand-mark"><svg viewBox="0 0 30 30" fill="none"><path d="M5 23 10 7l4 14 6-16 5 18M5 25h21" stroke="currentColor" strokeWidth="2.6" strokeLinecap="round" strokeLinejoin="round" /></svg></span>margin<span className="brand-dot">.</span></a>
      <nav className="main-nav" aria-label="Main navigation"><button className={!practiceOpen ? "nav-item active" : "nav-item"} onClick={() => setPracticeOpen(false)}><Pencil size={15}/> Whiteboard</button><button className={practiceOpen ? "nav-item active" : "nav-item"} onClick={() => setPracticeOpen(true)}><BookOpen size={16}/> Practice <span className="new-tag">NEW</span></button></nav>
      <div className="header-right"><button className="help-button" onClick={() => setHelpOpen(true)}><CircleHelp size={17}/><span>How it works</span></button><span className="header-divider"/><span className="avatar" title="Your personal workspace">S</span></div>
    </header>
    <main className="workspace">
      <section className="workspace-heading"><div><div className="eyebrow">A LITTLE SPACE FOR BIG IDEAS</div><h1>Think it through<span>.</span></h1><p>Make mistakes. Find your way. We’ll be right here.</p></div><button className="secondary-button practice-button" onClick={() => setPracticeOpen(true)}><BookOpen size={16}/> Find a practice problem <ArrowRight size={16}/></button></section>
      <div className="studio-layout">
        <section className="board-section" aria-label="Math whiteboard">
          <div className="board-top"><div className="board-title"><span className="board-indicator"/> Your working space <span className="save-status">{saved && <><CheckCheck size={13}/> Saved on this device</>}</span></div><div className="board-actions"><button aria-label="Toggle dotted grid" title="Toggle dotted grid" className={grid ? "icon-button selected" : "icon-button"} onClick={() => setGrid(!grid)}><svg width="17" height="17" viewBox="0 0 18 18" fill="currentColor">{[3,9,15].flatMap(x => [3,9,15].map(y => <circle key={`${x}-${y}`} cx={x} cy={y} r="1.1"/>))}</svg></button><button className="icon-button" title="Export board as PNG" aria-label="Export board as PNG" onClick={exportBoard}><ArrowDownToLine size={17}/></button><span className="mini-divider"/><button className="icon-button" title="Clear board" aria-label="Clear board" onClick={() => clearBoard()}><Trash2 size={16}/></button></div></div>
          <div className="problem-strip"><span className="problem-label">PROBLEM</span><input value={problem} onChange={e => { setProblem(e.target.value); schedule(); }} placeholder="Write your question below, or add it here…" aria-label="Math problem" maxLength={2000}/><div className="input-tabs" aria-label="Input method"><button className={mode === "draw" ? "chosen" : ""} onClick={() => { setMode("draw"); schedule(); }}>Draw</button><button className={mode === "type" ? "chosen" : ""} onClick={() => { setMode("type"); schedule(); }}>Type</button></div></div>
          <div className={`paper ${grid ? "dotted" : ""}`}>
            <canvas ref={canvasRef} className={mode === "draw" ? "writing-canvas" : "writing-canvas canvas-hidden"} aria-label="Draw your math question and working here. Switch to Type for keyboard input." style={{ cursor: pen === "eraser" ? "cell" : "crosshair" }}/>
            {mode === "type" && <textarea className="typed-board" aria-label="Type your math working" placeholder={"Write your question and work here.\nOne step per line is a good place to start."} value={typed} maxLength={6000} onChange={e => { setTyped(e.target.value); schedule(); }} spellCheck={false}/>}
            {!hasInk && mode === "draw" && <div className="blank-board"><div className="sketch"><svg width="77" height="65" viewBox="0 0 77 65" fill="none"><path d="m13 45 10-27 9 26m-14-9h12M43 21h20m-20 9h20m-17 16 8-6 8 6M6 56c20 4 42 4 66-1" stroke="currentColor" strokeWidth="1.6" strokeLinecap="round"/><path d="m63 4 1 7m8-2-5 4" stroke="#ab9ed3" strokeWidth="1.5" strokeLinecap="round"/></svg></div><h2>Your next “aha!” starts here.</h2><p>Pick up your pen and work through a problem.<br/>Your AI math coach will follow along.</p><button onClick={loadExample}>Or try an example <ArrowRight size={14}/></button></div>}
            {mode === "draw" && <div className="drawing-toolbar" aria-label="Drawing tools"><div className="tool-group"><button className={`tool-button ${pen === "pen" ? "active" : ""}`} onClick={() => setPen("pen")} aria-label="Pen" title="Pen"><Pencil size={19}/></button><button className={`tool-button ${pen === "eraser" ? "active" : ""}`} onClick={() => setPen("eraser")} aria-label="Eraser" title="Eraser"><Eraser size={19}/></button></div><span className="tool-divider"/><div className="color-group">{COLORS.map((c, i) => <button key={c} aria-label={`Use ${["graphite","purple","green","orange"][i]} ink`} aria-pressed={c === color && pen === "pen"} className={`color-button ${color === c && pen === "pen" ? "active" : ""}`} onClick={() => { setColor(c); setPen("pen"); }}><span style={{ background: c }}/></button>)}</div><span className="tool-divider"/><button className="tool-button stroke-button" aria-label="Change pen thickness" title="Change pen thickness" onClick={() => setSize(size === 2.5 ? 4.5 : size === 4.5 ? 1.5 : 2.5)}><span style={{ height: size + 1 }}/><ChevronDown size={10}/></button><span className="tool-divider"/><div className="tool-group"><button className="tool-button" aria-label="Undo stroke" title="Undo stroke" onClick={undo} disabled={!hasInk}><RotateCcw size={18}/></button><button className="tool-button" aria-label="Redo stroke" title="Redo stroke" onClick={redo}><RotateCw size={18}/></button></div></div>}
            <div className="paper-corner">{mode === "draw" ? "A pen, a mouse, or your fingertip. Make it yours." : "Your thinking, one line at a time."}</div>
          </div>
          <div className="board-bottom"><div className="live-control"><button role="switch" aria-checked={live} aria-label="Live feedback" className={`toggle ${live ? "on" : ""}`} onClick={() => { setLive(!live); if (live) { if (timerRef.current) clearTimeout(timerRef.current); setWaiting(false); } else { timerRef.current = setTimeout(() => void checkRef.current(), 300); } }}><span/></button><span>Live feedback</span><span className="live-help">A nudge when you need it.</span></div><button className="check-button" onClick={() => void checkRef.current()} disabled={!content || busy}>{busy ? <LoaderCircle size={15} className="spin"/> : <Sparkles size={15}/>} Check my work <span>↗</span></button></div>
        </section>
        <aside className="coach-panel" aria-label="AI math feedback"><div className="coach-header"><div className="coach-icon"><Sparkles size={19}/></div><div><h2>Your math coach</h2><span><span className={`status-dot ${busy ? "pulsing" : ""}`}/>{status}</span></div><span className="ai-tag">AI</span></div>
          <div className="coach-content" aria-live="polite" aria-busy={busy}>
            {configured === false && <div className="error-card"><strong>Coach needs a connection</strong><p>Add GEMINI_API_KEY to the server environment to enable feedback.</p></div>}
            {error && <div className="error-card"><strong>Let’s try that again</strong><p>{error}</p><button onClick={() => void checkRef.current()}>Retry <RotateCw size={13}/></button></div>}
            {busy ? <div className="coach-loading"><div className="loading-orb"><Sparkles size={27} className="pulsing"/></div><h3>Following your thinking…</h3><p>I’m reading your steps and checking how they connect.</p><div className="skeleton-line"/><div className="skeleton-line short"/></div> : result ? <div className="feedback-result"><div className={`result-label ${result.status}`}>{result.status === "mistake" ? <Lightbulb size={16}/> : result.status === "correct" ? <Check size={16}/> : <Pencil size={16}/>} {result.status === "mistake" ? "LET’S TAKE ANOTHER LOOK" : result.status === "correct" ? "LOOKING GOOD" : "KEEP THINKING"}</div><h3>{result.title}</h3><p className="result-hint">{result.hint}</p>{result.steps.length > 0 && <div className="steps-list">{result.steps.map((step, i) => <div className={`step-row ${step.status}`} key={i}><span className="step-number">{step.status === "correct" ? <Check size={12}/> : i + 1}</span><div><code>{step.line}</code>{step.feedback && <p>{step.feedback}</p>}</div></div>)}</div>}<button className="explain-button" onClick={() => { if (details) setDetails(false); else if (result.explanation) setDetails(true); else void checkRef.current(true); }}><Lightbulb size={16}/>{details ? "Hide explanation" : "Help me understand"}<ChevronDown size={15}/></button>{details && <div className="explanation">{result.explanation}</div>}<details className="transcription"><summary>What I read from your board</summary><pre>{result.transcription}</pre></details></div> : <div className="coach-empty"><div className="coach-illustration"><span className="orbit one"/><span className="orbit two"/><Lightbulb size={37} strokeWidth={1.3}/><span className="little-spark">✦</span></div><h3>A second pair of eyes.</h3><p>You do the thinking.<br/>I’ll help you spot the little things.</p><div className="coach-features"><div><span className="feature-icon"><Pencil size={15}/></span><div><strong>Write naturally</strong><p>No special symbols or typing needed.</p></div></div><div><span className="feature-icon"><Sparkles size={15}/></span><div><strong>Get a gentle nudge</strong><p>Catch a missed sign or a tricky step.</p></div></div><div><span className="feature-icon"><Lightbulb size={15}/></span><div><strong>Understand the why</strong><p>Hints that help you figure it out.</p></div></div></div></div>}
          </div><div className="coach-footnote"><span>✧</span> Room to make mistakes. Space to grow.<p>AI can make mistakes, too. Always check your reasoning.</p></div>
        </aside>
      </div>
      <footer className="workspace-footer"><span><span className="footer-spark">✦</span> A little guidance. A lot of possibility.</span><span>Powered by Gemini <span className="gemini-spark">✦</span><i/>Your board stays in your browser; snapshots go to Gemini for feedback.</span></footer>
    </main>
    {notice && <div role="status" className="toast"><Check size={16}/>{notice}<button aria-label="Dismiss notice" onClick={() => setNotice("")}><X size={14}/></button></div>}
    {practiceOpen && <div className="modal-backdrop" onClick={() => setPracticeOpen(false)}><section className="modal practice-modal" role="dialog" aria-modal="true" aria-labelledby="practice-title" onClick={e => e.stopPropagation()}><button className="modal-close icon-button" aria-label="Close practice" onClick={() => setPracticeOpen(false)}><X size={19}/></button><div className="eyebrow">LET’S GET YOUR IDEAS MOVING</div><h2 id="practice-title">A fresh place to start.</h2><p>Pick a problem, show your steps, and see where it takes you.</p><div className="practice-grid">{PRACTICE.map(p => <button key={p.question} onClick={() => { if (!clearBoard()) return; setProblem(p.question); setPracticeOpen(false); }}><span className="practice-tag">{p.subject}</span><h3>{p.label}</h3><p>{p.question}</p><span className="practice-bottom">{p.tag}<ArrowRight size={16}/></span></button>)}</div><button className="example-link" onClick={loadExample}><Sparkles size={15}/> See how feedback catches an example mistake <ArrowRight size={15}/></button></section></div>}
    {helpOpen && <div className="modal-backdrop" onClick={() => setHelpOpen(false)}><section className="modal help-modal" role="dialog" aria-modal="true" aria-labelledby="help-title" onClick={e => e.stopPropagation()}><button className="modal-close icon-button" aria-label="Close help" onClick={() => setHelpOpen(false)}><X size={19}/></button><div className="coach-icon"><Sparkles size={24}/></div><h2 id="help-title">Meet your thinking partner.</h2><p>Write a math question and show your working on the board. Use a stylus, mouse, or touch—or switch to Type.</p><ol><li><strong>Take it one step at a time.</strong> Put each step on a new line so it’s easy to follow.</li><li><strong>Pause for a little guidance.</strong> With live feedback on, Gemini reads a snapshot after you stop writing for about 1.6 seconds.</li><li><strong>Try, adjust, and keep going.</strong> The coach points out the first clear mistake and gives you a hint. Unclear handwriting gets a second look.</li></ol><p className="privacy-note">Your work saves on this device. Only board snapshots and the problem are sent to Gemini when a check runs. Turn off live feedback to check only when you choose.</p><button className="primary-button" onClick={() => setHelpOpen(false)}>Let’s work it out <ArrowRight size={16}/></button></section></div>}
  </div>;
}
