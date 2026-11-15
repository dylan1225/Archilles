# Archilles

The hackathon project lives in this file and includes 2 modes:
- **[Postmortem](#postmortem)**: photograph a broken mechanical part and get back
  why it failed and what to change. Python app at the repository root.
- **[Forecast](#forecast)**: photograph a place or an object and get back how it could fail
  and what needs to be changed.

---

## Postmortem

Photograph a broken part and find out how it failed and what to change.

Postmortem reads the fracture surface in a photo, names the failure mode from a
knowledge base, and ranks three or four fixes with their pros and cons. An
interactive 3D exhibit shows the part as found and with each of the top three
fixes built in.

- **Evidence first.** The model must list what it can actually see before it
  names a mode. If the photo isn't good enough, it says so instead of guessing.
- **The model never does the maths.** Stress concentration factors and stress
  reductions come from `mechanics.py` (Peterson chart values) and are marked
  *Calculated* in the UI.
- **The 3D model is a schematic.** One photo can't give real geometry, so the
  part is rebuilt from a simple shape with estimated dimensions, and the page
  says so.

### Quick start

Needs Python 3.12+ and a Gemini API key.

```bash
python3 -m venv venv
venv/bin/pip install -r requirements.txt
echo "GEMINI_API_KEY=your-key-here" > .env
venv/bin/python -m uvicorn main:app --port 8010
```

Open http://127.0.0.1:8010. The three pieces under **From the collection** use
saved results, so they work without a key or a network connection.

Command line:

```bash
venv/bin/python diagnose.py photo.jpg --context "PETG bracket, 3 mm wall, snapped after two weeks"
```

### Using it

1. **Photograph the break.** Drop a photo or use the camera button: close up,
   sharp and well lit. JPEG, PNG and HEIC all work.
2. **Describe it (optional).** Material, dimensions and how it broke. Dimensions
   given here are used instead of estimates.
3. **Analyse.** A live analysis takes about 40–90 s. The same photo and note are
   only sent to the model once; repeat requests come from `cache/`.

The result shows:

- a summary: material, failure mode, confidence, analysis time
- **the exhibit**: a 3D vitrine with an *As found · 1 · 2 · 3* selector
  - *As found* shows the part broken at the fracture, with its supports and load
  - *1–3* show the part whole with that fix drawn in orange, next to a placard
    with the fix's description, pros, cons and any calculated numbers
  - drag to rotate, scroll or pinch to zoom; arrow keys switch between options
- the visual evidence, probable cause and alternatives the model considered
- a fourth fix, when there is one, under *Also considered*

### How it works

```
photo + note ─► diagnose.py   one Gemini call with the knowledge base, a strict JSON schema,
                              and rules checked in code (one retry on a bad answer)
                    │          → mode, evidence, cause, solutions, 3D shape hint
                    ├─► mechanics.py   Kt and stress reduction for any fillet change
                    ▼
               main.py (FastAPI) ─► static/index.html + static/viewer3d.js (three.js)
```

| Path | What it does |
|---|---|
| `main.py` | FastAPI app: `/`, `POST /diagnose`, `GET /examples/{n}`, `GET /mechanics/fillet` (recompute from typed measurements). Errors are always JSON. |
| `diagnose.py` | Image prep (HEIC/EXIF), prompt, Gemini call with a time limit, validation, cache. |
| `mechanics.py` | Stress concentration lookup, fillet improvement, FDM wall check. No model calls. |
| `knowledge/failure_modes.md` | Failure-mode knowledge base, inserted into the prompt verbatim. |
| `knowledge/examples/` | Optional few-shot photos with `.json` labels. |
| `static/index.html` | The single-page UI. |
| `static/viewer3d.js` | The 3D exhibit: gallery scene, part builders, one builder per kind of fix. |
| `static/vendor/` | three.js 0.170.0 and OrbitControls (MIT), served locally so the demo works offline. |
| `static/examples/` | Bundled demo photos with their real, pre-computed Gemini results. |
| `docs/demo-photo-guide.md` | How to shoot and bundle a fourth example that shows the calculated card. |
| `eval/run_eval.py` | Accuracy run over `eval/labels.csv` + `eval/images/`, cached, with a few-shot leak check. |

#### 3D exhibit data

Alongside the diagnosis, the model describes the part as a `specimen`
(`round_rod`, `rect_bar`, `flat_plate` or `l_bracket`, plus dimensions, where it
broke, the feature at the break and how it was loaded). Each solution also gets
a `visual` field: one of `add_support`, `move_load`, `thicken_section`,
`change_material`, `add_fillet`, `reinforce_feature`, `relocate_feature`,
`densify_infill`, `reorient_layers`, `add_rib`, `surface_treatment` or `other`,
plus a short label. Out-of-range values are clamped, never retried. Saved
results without these fields still load and show the plain text list.

### Tests and evaluation

```bash
venv/bin/python -m pytest
venv/bin/python eval/run_eval.py
```

117 offline tests with a fake model client cover the mechanics tables, schema and
rule enforcement, retries and timeouts, the cache, the 3D fields and the bundled
examples. The eval needs `eval/labels.csv` and `eval/images/`.

### Update log

#### 2026-11-14 — Uncertainty handling
Defences against a confident failure story the photo does not support
(`uncertainty.py`, plus schema and prompt changes in `diagnose.py`).
- **Self-consistency:** each photo is diagnosed 3 times in parallel at
  temperature 0.7 (`diagnose(image, n_samples=3)`). The majority answer is
  returned with a `consistency` block (`runs`, `agreement`, `modes_seen`,
  `consistent`, `failed_runs`). Any disagreement, or fewer than 2 completed
  runs, drops confidence to low and is shown on the page. A three-way split
  declines rather than picking a failure. Results are cached by image hash.
- **Null detection:** `is_failure_analysis_applicable` is false for an intact
  part, something that is not a part, or anything that is not a fracture. The
  prompt states that diagnosing a failure in something unbroken is the worst
  error the system can make. The page then says plainly what it sees and that
  there is nothing to analyse, with no mode, cause or fixes.
- **Rejected alternative:** the model must name the second most likely mode and
  the observed feature that rules it out. Code rejects (and retries) a missing,
  vague or unobserved citation. Shown as "Considered and rejected".
- **Geometry cross-check:** a stress-raiser mode (`fatigue_metal`, see
  `STRESS_CONCENTRATION_MODES`) or a fillet fix with no hole, notch or sharp
  corner in `geometric_features` is flagged as an internal inconsistency and
  confidence drops to low.
- **Eval:** accuracy by stated confidence, and a null-specimen table. Label a
  null specimen `true_mode = not_applicable`; the run exits 1 unless every null
  specimen was declined. The eval now shares the app's diagnosis cache.

#### 2026-11-14 — Calculated before/after for geometry fixes
- Fixes that add or enlarge a fillet, increase a radius or soften a corner now
  carry a `geometry` block (current radius, proposed radius, section depth). The
  model must leave a value null rather than invent it.
- `mechanics.fillet_improvement()` turns that into `computed`: `kt_before`,
  `kt_after`, `stress_reduction_pct`, `basis`, `is_extrapolated`, `is_capped`.
- New card, visibly separate from the model's text: green *Calculated* band,
  large monospace figures, e.g. *Sharp corner, r ≈ 0 · Kt ≈ 3.3 → add a 3 mm
  fillet · Kt ≈ 1.45 · Peak stress drops ≥ 56%*, the method underneath, and an
  "estimated, outside tabulated range" note when a value is extrapolated.
- "Try your own measurements": type the measured radius and section depth and the
  numbers recompute live through `GET /mechanics/fillet`.
- Fixes with no geometry show no card at all. Thicker walls or sections are not
  calculated: `mechanics.py` has no model for them yet.

#### 2026-11-14 — 3D exhibit and new UI (`655b3d8`)
- Interactive three.js exhibit: the part *as found* (broken) or with solution 1, 2
  or 3 applied, with orange callouts, load arrows and supports, and the source
  photo framed on the gallery wall.
- Placard beside the model with the selected fix's description, pros, cons and
  calculated numbers.
- New `specimen` and per-solution `visual` fields in the model output, clamped
  into range; older saved results still load.
- Museum-inspired redesign: limestone and parchment palette, serif display type,
  numbered input steps, an arched vitrine, keyboard-accessible selector and a
  phone layout.
- three.js vendored for offline use; bundled examples re-generated with 3D data;
  new tests for the 3D fields and bundled examples.

#### 2026-11-14 — Demo examples (`6ee41c4`, `f3737c8`)
- Three bundled examples with real results: wooden dowel (bending overload),
  printed bars (fracture through a hole), craft stick (photo insufficient).
- `GET /examples/{n}` serves them with no network access; demo hardening and tests.

#### 2026-11-14 — Gemini (`40a6af5`)
- Model call moved from Anthropic to Gemini, with a hard wall-clock limit and
  clear messages for timeouts, rate limits and rejected keys.

#### 2026-11-14 — Evaluation (`7f30f8c`, `54a0eaf`)
- `eval/run_eval.py` with a result cache, per-mode accuracy and a confusion
  matrix; aborts if an eval photo is also a few-shot example.
- Clear error when `.env` is a folder instead of a file.

#### 2026-11-14 — First working version (`a168f9f` … `2314760`)
- Project scaffold and failure-mode knowledge base template.
- `mechanics.py`: stress concentration, fillet improvement, wall check.
- `diagnose.py`: evidence-first vision diagnosis with schema and rule checks.
- FastAPI app and single-file UI.

---
