# Postmortem — plan: knowledge, proof and readability

## Context

Postmortem works end to end, but a review against the judging criteria (Creativity,
Utility, Charity, Avidity) found four gaps:

1. **Thin knowledge.** Only 3 of 11 failure modes are written, none cites a source,
   and there is no material data at all. The printed-bars example comes back as
   "Other" because its mode doesn't exist.
2. **The calculated before/after card is invisible in the demo.** None of the three
   bundled examples has a fillet fix.
3. **No accuracy number.** `eval/run_eval.py` exists, but there are no labelled photos.
4. **Hard to read from a distance.** Much of the page is 11–12 px text, results are
   long, the failure mode shows as a code id, and fix buttons say "Solution 1 / 2 / 3".

Decisions already made: more than 8 hours are available; the database covers **both**
failure modes and a materials table; all four readability changes are wanted.
**Jargon stays in the text**: glossary terms are coloured, and hovering over one
shows its definition and explanation. A fuller teaching mode comes later.

**Branch:** all of this work goes on a new branch, `ui-and-ai-assistant-dev`
(git branch names can't contain spaces), to be merged later. On approval: create
it from `postmortem` (at `7fd796b`), which stays unchanged. All parts are
committed there.

Status (2026-11-14): Parts 1 and 2 built. Part 1: 11 failure modes drafted with
sources and shown on the page. Part 2: materials table (12 rows), material picker,
Reference data card, material comparison, and the weight and strength card for
thicker sections. Every mode and every material row is `draft` until a team member
or mentor checks it against its source. Parts 3–5 not started.

## Who does what

Work that needs real parts and photos is yours; everything else can start at once.

| You (team) | Claude |
|---|---|
| Print and break parts; take photos (Parts 3 and 4) | Draft the 8 failure modes with sources (Part 1) |
| Check each drafted mode against its source (or ask a mentor) | Materials table, module and wiring (Part 2) |
| Fill in `eval/labels.csv` | Readability changes and glossary draft (Part 5) |
| Check the glossary entries read right for students | |
| | Bundle example IV, run the eval, publish the number |

## Part 1 — Failure-mode knowledge base

File: `knowledge/failure_modes.md` (pasted verbatim into the prompt; a filled block
becomes a valid answer automatically via `diagnose.mode_ids()`).

- Fill the 8 TODO blocks. The first four are already named as neighbours by the
  existing blocks:
  `cross_layer_fracture_fdm`, `ductile_overload_metal`, `brittle_overload_metal`,
  `brash_failure_wood`, `bolt_hole_tearout`, `torsion_overload_shaft`,
  `gear_tooth_shear`, `split_along_grain_wood`.
- Same four sections as now. Write each mode together with its most-confused
  neighbour; "Distinguish from" decides accuracy. "What to change" names a fillet
  or radius fix wherever one applies, since that triggers the calculated card.
- Add `- **Source:**` under Name/Material in all 11 blocks (ASM Handbook vols. 11/12
  for metals, USDA *Wood Handbook* for wood, Peterson for stress concentration,
  published FDM strength studies and printer-maker guides for prints).
- **Review gate:** Claude's drafts are model-written. A block counts as done only
  when a team member or mentor has checked it against its source.
- `diagnose.py`: new `mode_info()` parses Name and Source per mode; code (not the
  model) attaches `mode_name` and `mode_source` to `Diagnosis` (optional fields, so
  old saved results still load). The page shows the proper name and
  "Reference: …" under it.
- Tests: every filled block has Name, Material, Source and all four sections;
  `mode_name` / `mode_source` are attached from the file.

## Part 2 — Materials table

- New `knowledge/materials.csv`, about 12 rows: PLA, PETG, ABS, nylon,
  polycarbonate sheet, acrylic, 6061-T6 aluminium, mild steel, birch plywood,
  basswood/pine, hardwood dowel. Columns: `id, name, family, tensile_strength_mpa`
  (range), `modulus_gpa`, `elongation_pct`, `density_g_cm3`, `note`, `source`.
  Every value comes from a cited datasheet or handbook; none is estimated by a
  model. Printed plastics carry a note that strength across layers is lower.
- New `materials.py`, a pure module in the style of `mechanics.py`:
  `load_materials()` (rejects a row with a missing source or a non-positive value),
  `get(id)`, `compare(from_id, to_id)` returning the ratios plus a `basis` string.
- Wiring:
  - `GET /materials` feeds a new optional **Material** dropdown in step 2. A photo
    can't tell PLA from PETG, so the user says which; it is added to the note sent
    to the model and returned as `material_facts`.
  - For "change material" fixes the model fills `proposed_material_id` (one of the
    table ids, or null). Code attaches a current-vs-proposed comparison when both
    are known.
  - Page: a **Reference data** card (strength, stiffness, stretch before breaking,
    source), styled apart from both model text and the Calculated card.
- **Weight and strength card for "thicker section" fixes** (added 2026-11-14).
  The model's cons say things like "increases weight slightly"; a 12 → 16 mm dowel
  is actually 78% heavier. Same pattern as the fillet card:
  - The model fills a `section_change` block for thicker-section fixes: shape
    (round or rectangular), current and proposed diameter or width × thickness, and
    length, in mm. Anything it can't estimate is null, never invented.
  - New `mechanics.section_change()` computes the **weight ratio** (area ratio) and
    the **bending strength ratio** (section modulus: d³ for round, b·h² for
    rectangular), both exact for the same material, length and load. It is a plain
    ratio, not a stress-concentration figure, and the `basis` text says so.
  - **Grams** use length and the density from `materials.csv`, shown as a range
    (for example "≈ +9 to +14 g"); with no material chosen, only the ratios show.
  - Shown on the Calculated card: "12 → 16 mm · strength ×2.4 · weight ×1.8
    (≈ +9 to +14 g)", with inputs to type real dimensions, recomputed through
    `GET /mechanics/section`, like `GET /mechanics/fillet`.
- Tests: table loads and validates, `compare()` arithmetic, endpoint, unknown id;
  `section_change()` for the 12 → 16 mm dowel (weight ×1.78, strength ×2.37),
  a rectangular case, missing dimensions ask for input, grams range from density.

## Part 3 — Fourth collection example

Follow `docs/demo-photo-guide.md`: a printed L-bracket snapped at a sharp inside
corner, fracture face toward the camera, with a note giving its dimensions.

- You: photo saved as `static/examples/4.jpg`, plus the note.
- Claude: run it once, confirm a top-three fix carries `kt_before` / `kt_after`
  (retake if not; results are never hand-edited), save `4.json`, set
  `EXAMPLE_COUNT = 4` in `main.py`, add button **IV**. `tests/test_examples.py`
  covers it automatically.
- Done after Part 1 is reviewed, so all four examples are re-run only once. Example
  II should then stop showing "Other".

## Part 4 — Accuracy number

- You: 25–30 photos in `eval/images/`, at least 2 per mode, plus 3 deliberately bad
  photos labelled `undetermined` (the right answer is "photo not good enough").
  One row each in `eval/labels.csv`:
  `filename,true_mode,material,how_it_broke,difficulty`. Own photos preferred; for
  metal modes that can't be made in time, openly licensed photos, marked as such.
  Never reuse a bundled example or a few-shot photo.
- Claude: `venv/bin/python eval/run_eval.py`. About a minute and a few cents per
  photo; run one at a time (three parallel calls hit rate limits earlier). Results
  are cached, but any knowledge-base edit re-bills the whole set.
- **Honest reporting:** the first full run is the headline number, quoted as a
  count ("21 of 28"), not only a percentage. If "Distinguish from" text is sharpened
  after seeing the confusion matrix, the second number is reported as "after
  tuning on the same photos".
- Small code changes in `eval/run_eval.py`: stop warning about `other` /
  `undetermined` labels, and write `static/eval_summary.json` (count, total, date,
  per-mode). The page shows "Tested on N labelled photos: X correct" when that
  file exists and nothing when it doesn't. README gets the full report.

## Part 5 — Readability (`static/index.html`)

1. **Jargon stays; hover over a word for its meaning.** The model's wording is
   not simplified. Instead:
   - New `knowledge/glossary.json`, about 30 terms (stress concentration / Kt,
     fillet, fatigue, beach marks, ductile, brittle, necking, delamination, infill,
     perimeter, bending moment, section depth, r/d, nominal and peak stress,
     tensile strength, modulus, torsion, grain, cantilever, tear-out, shot
     peening…). Each entry: the term, other spellings, a one-line definition, and
     a 2–3 sentence explanation written for a student. Hand-written and reviewed
     like the failure modes; never generated at run time. Served by `GET /glossary`.
   - A helper in `static/index.html` finds glossary terms in any result text and
     wraps them (built as DOM nodes, never as HTML strings). Glossary terms are
     shown in a **different colour** from the surrounding text (the dark orange
     accent, with a fine dotted underline so colour is not the only cue).
   - **Hover** over a term (revised 2026-11-14, replacing long press) opens a small
     pop-up with the definition and explanation; it closes when the pointer leaves.
     Keyboard focus opens it too, and Escape closes it. Phones have no hover, so
     there a tap on the term opens it and a tap elsewhere closes it.
   - One hint line above the results: "Hover over a coloured word for its
     meaning." ("Tap" on touch screens.)
   - The later teaching mode can reuse the same glossary file.
   - The page also shows the failure mode's proper name instead of its code id.
2. **Bigger, clearer text.** Base 16 px; nothing below 13 px (today 10.5–12 px is
   used in about 20 places); one type scale as CSS variables instead of ad-hoc
   sizes; text lines capped near 65 characters.
3. **Decluttered, restructured results** (revised 2026-11-14), using the
   drop-down pattern of princeton.edu's menu: clean full-width rows with a title
   and a chevron that open in place, generous spacing, strong black / white /
   orange contrast. Only the layout pattern is borrowed; no Princeton name,
   shield, logo or fonts are used.
   - Always visible: a verdict card (new one-sentence `summary` field from the
     model, mode name, confidence) and the 3D exhibit with its fix selector.
   - Everything else becomes a stack of drop-down rows, closed by default, each
     showing a count or one-line preview so nothing is hidden blind:
     "What the photo shows (5)", "Why it broke", "Other causes considered (1)",
     "Also considered" (4th fix), "Reference", "Analysis details" (model, time).
   - Inside the exhibit's description panel, Pros, Cons and the Calculated /
     Reference data cards are the same kind of rows; Pros and Cons start open.
   - Built on native `<details>` / `<summary>` so keyboard and screen readers work;
     the chevron rotates when open; reduced-motion users get no animation.
   - The left column gets the same treatment: "Describe it" and "From the
     collection" collapse to rows once a result is showing.
   - Summary strip removed; its four boxes fold into the verdict card and
     "Analysis details".
4. **Labelled fix buttons.** "Solution 1" becomes "1 · Add support" using each
   fix's short label, full title on hover; on phones the row scrolls sideways.

## Order of work

1. Save `plan.md`. Claude drafts the 8 modes; you start printing and photographing.
2. Claude builds Part 2 and Part 5 while modes are reviewed and photos come in.
3. Knowledge base frozen → re-run examples I–IV → run the eval → publish the number.
4. Optional tuning pass on confused pairs, reported as tuned.

## Files

- Modify: `knowledge/failure_modes.md`, `diagnose.py`, `main.py`,
  `static/index.html`, `eval/run_eval.py`, `README.md`, `tests/test_diagnose.py`,
  `tests/test_run_eval.py`, `static/examples/*.json`
- Add: `plan.md`, `knowledge/materials.csv`, `materials.py`,
  `knowledge/glossary.json`, `tests/test_materials.py`, `tests/test_glossary.py`
  (every entry has a definition and explanation; endpoint works),
  `static/examples/4.jpg` + `4.json`,
  `eval/labels.csv`, `eval/images/`, `static/eval_summary.json`

## Verification

- `venv/bin/python -m pytest`: all existing tests plus the new ones pass.
- Run the app at http://127.0.0.1:8010 and check in the browser: examples I–IV;
  example IV shows the Calculated card; a "change material" fix shows the Reference
  data card; mode name and reference appear; glossary words are coloured and hovering
  (or keyboard focus, or a tap at phone size) opens the definition; a thicker-section fix shows the weight and strength card
  and recomputes on typed dimensions; drop-down rows open and close by click and
  keyboard, with counts shown while closed; fix buttons
  are labelled; nothing smaller than 13 px; phone width has no sideways page scroll;
  no console errors.
- `venv/bin/python eval/run_eval.py` prints the report; the page shows the tested-on
  line from `static/eval_summary.json`.
- Commits go to `ui-and-ai-assistant-dev` only; nothing is merged into
  `postmortem` or `main`.

## Not in this plan

Teaching mode, multi-photo upload, and stress-concentration (Kt) calculations for
thicker sections (the new card gives plain strength and weight ratios only).
