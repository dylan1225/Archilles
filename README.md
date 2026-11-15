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

## Pre-failure interior review

Open **http://localhost:8010/interiors** (or the **Explore building interiors**
link in the header). The page reviews scanned interiors for early, visible
signs of a weakness, before anything has failed, and shows in 3D what that
weakness could lead to.

The scans come from the Matterport Model API (the hackathon's CoStar /
Matterport access): the public **Houston** and **Los Angeles** models available
to your account.

### What the page does

1. **Flagged by the people who scanned them.** Scan technicians leave notes for
   the listing team in the model description: "avoid master closet (ceiling
   leak)", "mold, water damage". Those notes are searchable. The page searches
   them across the whole collection and lists the buildings where a technician
   wrote down an interior problem. Pick one, or any building in the collection.
2. **A team of agents reviews the scan.** Built on the open source
   [Deep Agents](https://github.com/langchain-ai/deepagents) framework:

   ```
   lead
    ├─ room-surveyor  (one per room, in parallel)  opens each scan position,
    │                 looks at all six directions including straight up,
    │                 zooms where something looks wrong, flags candidates
    ├─ skeptic        re-opens every flagged view without the surveyor's
    │                 reasoning, checks a second scan position, confirms or rejects
    └─ failure-analyst  ties the most serious confirmed ceiling finding to a
                      modelled failure mechanism
   ```

   The technician's note is never shown to the agents, so when they confirm the
   same kind of problem from the images, that is independent agreement.
3. **The finding is placed on the building's real 3D model.** Each panorama has a
   pose and the building has a downloadable mesh, in the same coordinates. A
   flagged region of a ceiling photo is ray-cast onto the mesh, which gives its
   true position, area and height above the floor.
4. **What could happen, in 3D.** The room is the scan's own mesh with its
   panoramas projected back onto it, so you are looking at the real surfaces.
   A five-stage scenario plays on that geometry: as scanned, repeated wetting,
   sag, detachment, aftermath. The numbers beside it (section area, drop, fall
   time, speed, dry weight) are calculated from the scan's geometry by
   `failure_scenarios.py`, with no model call.
5. **What waiting costs.** Two bills side by side: the repair now (find the
   leak, patch the flagged section) and the repair after the section falls
   (the same two lines, plus drying the room, restoring the floor and possible
   mould removal). The difference is the cost of waiting. Each line is a
   published US cost-guide range applied to the scan's own areas, with its
   source linked; it is a rough range, not a quote, and prices no injury or
   damaged belongings.

### Rules the agents cannot break

The agents see pixels only through tools and change the review only through
tools, and the tools hold the rules:

- a finding must cite a view an agent actually zoomed into;
- only the skeptic can confirm, only after opening the view itself, and it can
  lower a finding's confidence but never raise it;
- a forecast needs a confirmed moisture or structure finding located on a ceiling;
- the report is assembled by code from what the tools recorded, never from an
  agent's prose, and a half-finished review is not published.

No agent has a filesystem, shell, network or credential tool.

### Setup

Add these to the server's `.env` (see `.env.example`):

```dotenv
MATTERPORT_API_KEY=your-token-id
MATTERPORT_API_TOKEN=your-token-secret
MATTERPORT_SDK_KEY=your-sdk-application-key
MATTERPORT_SDK_DOMAINS=localhost

# Vision model for the review. Every call goes through OpenRouter.
OPENROUTER_API_KEY=your-openrouter-key
```

`HOUSE_ANALYSIS_MODEL` overrides the model. It accepts one model or a
comma-separated list; later models take over when an earlier one runs out of
quota or credit. Model ids are OpenRouter's `vendor/model` form. A full review
makes roughly 45 model calls. When OpenRouter reports a per-minute limit, the
agents queue behind it instead of failing.

The Matterport key and token authenticate **only on the server**. The SDK
application key is public and must be registered for the browser's hostname;
the hackathon key supports `localhost` (use `localhost`, not `127.0.0.1`, for
the Walkthrough, Dollhouse and Floor plan buttons).

### Saved reviews

When a live review completes, its report, the agents' activity log, the cited
scan images and the trimmed mesh are written to `cache/` (git-ignored).
Selecting that building again replays the saved review instantly with no model
or Matterport call, which is what makes a demo dependable. **Run a fresh live
review** replaces it.

The completed review for **1649 W 56th St, Los Angeles** (`nFKebdbTD5U`, House
One) is also shipped in `bundled/interiors/`: its five confirmed findings,
scenarios, repair plans, agent activity, cited images and interior mesh. These
tracked files are included in a normal deployment, so selecting that building
shows its analysis even with an empty runtime cache and no vision API key.
A runtime review takes precedence over the bundled review; fresh reviews
continue to be saved in `cache/` (or `POSTMORTEM_CACHE_DIR`). The building
collection and embedded tour still use Matterport as described above.

### What this is not

A **screening of scan images on their capture date**, not a home inspection,
engineering assessment or safety certificate. Nothing inside a ceiling or wall
is seen. The 3D scenario illustrates a known failure mechanism at the flagged
location; it is not a prediction that it will happen, or when. No finding in a
room does not establish that it is sound.

### API

- `GET /api/interiors/models?q=&region=&offset=&page_size=`: the collection.
- `GET /api/interiors/leads`: buildings flagged in technicians' field notes.
- `GET /api/interiors/analysis-config`: provider, models, whether a key is set.
- `POST /api/interiors/assessments` with `{ "model_id": "..." }`: start a live review.
- `GET /api/interiors/assessments/{id}`: progress, live agent activity, report.
- `GET /api/interiors/reviews/{model_id}`: the saved review, if any.
- `GET /api/interiors/reviews/{model_id}/evidence/{view_id}`: a cited scan view.
- `GET /api/interiors/models/{model_id}/mesh`: the cached interior mesh.

### Files

| File | Role |
|---|---|
| `interiors.py` | Collection search and field-note leads |
| `building_evidence.py` | One building's survey: rooms, panoramas, mesh |
| `scan_geometry.py` | Panorama pixel to 3D point: face convention, ray casting |
| `house_analysis.py` | The Deep Agents team, its tools and rules, the report |
| `failure_scenarios.py` | The ceiling scenario and its calculated numbers |
| `house_inspections.py` | HTTP jobs, saved reviews, mesh and evidence endpoints |
| `static/house-analysis.js` | Leads strip and review UI |
| `static/forecast3d.js` | The 3D scenario view (three.js) |

Jobs run in this server process (two at a time, 30 minute limit) and are lost
on restart; saved reviews are not. Run one application worker.

## How it works

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
| `mechanics.py` | Stress concentration lookup, fillet improvement, section weight/strength ratios, FDM wall check. No model calls. |
| `materials.py` + `knowledge/materials.csv` | Reference material properties with sources; material comparisons. |
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

322 offline tests with a fake model client cover the mechanics tables, schema and
rule enforcement, retries and timeouts, the cache, the 3D fields and the bundled
examples. The eval needs `eval/labels.csv` and `eval/images/`.

### Update log

#### 2026-11-15 — UI version 2.0: Archilles theme with the newer simulation
- `main`'s Archilles page is kept as it was: intro gate, 3D hero, the two
  modes, the photo bench with the collection, Method and footer, all in its
  pastel-blue theme (`theme.css`, `postmortem.css`).
- The results area is the newer one from the redesign branch, restyled to
  match (`static/results.css`): the summary strip with a one-sentence verdict,
  the 3D simulation (now a pale-blue studio; the change in Archilles blue, loads
  in coral), the fix list, and Details as drop-down rows with a + / −.
- A selected fix is now **highlighted** (filled in blue), not underlined, so it
  is clear which fix is applied to the model. Hovering tints a fix.
- Material picker and the Deep Agents options sit in step 02 of the bench.
- The three bundled examples were re-run through the current pipeline, whose
  prompt bans vague size words, so their pros and cons now carry numbers with
  units and the new section, span and material calculations.

#### 2026-11-15 — Drawing-room redesign and supported numbers
- (The drawing-room look was not carried into `main`; UI version 2.0 above
  keeps its results features in the Archilles theme.)
- New look: white page, one grotesque typeface (Helvetica Neue stack), pencil
  rules, monochrome throughout. The landing page is an original pencil-sketch
  drawing board; click it and it lifts to invite an upload, a camera photo or
  one of the collection pieces.
- Results: verdict, then the 3D simulation in a white studio, then a plain list
  of suggested fixes that underline on hover and apply to the model on click.
  Details sit at the bottom as headers with a + that opens each one.
- Every calculated number shows its working on hover, focus or tap: formula,
  values substituted, result and source (`working` steps from `mechanics.py`
  and `materials.py`; tests check the steps equal the reported numbers).
- New numbers: grams for a material swap (part volume × the two densities) and
  a span calculation for fixes that add a support (`mechanics.span_change`,
  `GET /mechanics/span`).

#### 2026-11-14 — Readability redesign
- Results lead with a **verdict card**: the failure mode, a one-sentence summary
  (new `summary` field), confidence and material.
- Everything else sits in **drop-down rows** that open in place, each showing a
  count or preview while closed: what the photo shows, why it broke, other causes,
  also considered, reference, analysis details.
- **Glossary**: 38 hand-written terms in `knowledge/glossary.json`. Terms in the
  results are coloured; hover (or tap, or keyboard focus) shows a definition and
  a short explanation.
- One type scale, base 16 px, nothing smaller than 13 px.
- Fix buttons are labelled ("1 · Add support") and scroll sideways on phones.
- The left column folds away once a result is showing.

#### 2026-11-14 — Knowledge base and materials
- All 11 failure modes written, each with a cited source; the page shows the
  mode's name and a Reference line. Every block is marked draft until checked.
- `knowledge/materials.csv`: 12 common robotics materials (printed plastics,
  plastic sheet, aluminium, steel, four woods), each value with its source.
- Optional **Material** picker in step 2; a **Reference data** card shows the
  chosen material, and "switch material" fixes get a side-by-side comparison.
- "Thicker section" fixes get a Calculated card with exact weight, bending
  strength and stiffness ratios (e.g. a 12 → 16 mm dowel is ×1.78 heavier and
  ×2.37 stronger), plus grams when the material and length are known.

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

## Optional Deep Agents redesign workflow

Uses **LangChain Deep Agents** with OpenRouter's
`nvidia/nemotron-3.5-super-vl:free`. The existing Gemini pipeline and bundled
examples remain available without these optional packages.

```bash
# Run in your own terminal: this editing session could not write Git metadata.
git switch -c deep-agent-implementation
venv/bin/pip install -r requirements-agents.txt
# OPENROUTER_API_KEY belongs in the ignored .env, never in source code.
venv/bin/python -m uvicorn main:app --port 8010
```

Stop the previous server before starting the updated one on the same port.
Use **Design constraints & analysis mode** to select the pipeline. Deep Agents
becomes selectable only when dependencies and the key are configured.

- **New photo:** Nemotron performs a concise structured evidence extraction,
  followed by a Deep Agent coordinator and two compiled specialist subagents.
- **Existing result:** **Explore designs with Deep Agents** skips photo analysis.
  **Refine these fixes** carries forward the current diagnosis, proposed fixes and
  retained constraint controls. Edit the constraint note when changing a requirement;
  there is no hidden long-term conversational memory.
- **Minimal-change specialist:** load placement, supports, assembly and print orientation.
- **Geometry/material specialist:** section changes, local reinforcement and permitted
  material changes. Both must actually submit candidates before a result is accepted.
- **Tools:** material-reference lookup and candidate evaluation. Python computes
  section ratios, flags unverified assumptions and rejects explicit constraint violations.
  Free-text constraints are reviewed by the models and explicitly marked as not
  deterministically verified. Draft reference data retains its draft status.
- **Coordinator:** receives tool results and selects registered candidate IDs. Unknown,
  duplicate or explicitly rejected IDs are blocked in code. Conditional candidates
  are proposals for testing, not verified designs.
- **3D:** reuses the existing schematic builders, not CAD reconstruction or FEA.
  Arbitrary fillet calculations are intentionally omitted from agent candidates.
- **Progress:** newline-delimited JSON from `/diagnose/deep` or `/redesign` displays
  actual observation, delegation and tool events. A failed redesign keeps the last result.

The coordinator is instructed to call both specialist tasks in one response;
`max_concurrency=2` allows their tools/tasks to overlap. The model still controls
its delegation, so parallel execution must be confirmed in a live trace. Calls
use a small output budget, disabled reasoning, no SDK retries, an 18-step graph
limit and a 90-second whole-workflow timeout. The backend makes no automatic paid
fallback call. Free endpoints can rate-limit multi-call runs.

### Timing and verification

```bash
venv/bin/python -m pytest -q
# Real API calls, bypassing caches; do this after installing dependencies.
venv/bin/python eval/benchmark_agents.py static/examples/1.jpg --runs 3 \
  --context "Wooden rod. Use the actual measured dimensions here."
```

The benchmark alternates the two pipelines, reports success counts, wall times and
median speedup only when all runs succeed, and saves actual outputs in ignored
`cache/agent-benchmark.json`. Review evidence accuracy, tool calls and usefulness
alongside timing. An existing-result redesign cannot be compared fairly with a
fresh full Gemini photo analysis.

**Current verification:** offline tests only; SDK installation, real model tool
calling, provider support and speedup could not be verified in the restricted
editing session. No claim that this workflow is faster has been established.

References: [Deep Agents documentation](https://docs.langchain.com/oss/python/deepagents/quickstart),
[compiled subagents](https://docs.langchain.com/oss/python/deepagents/subagents),
[OpenRouter model](https://openrouter.ai/nvidia/nemotron-3.5-super-vl:free),
[HackPrinceton workshop repository](https://github.com/langchain-samples/hack-princeton-intro-to-deep-agents).
The workshop repository could not be retrieved in this session; implementation
follows the official SDK documentation. Postmortem's contribution is the engineering
workflow, specialist objectives, deterministic candidate checks and 3D integration.

---
