# Adding a fourth collection example (to show the calculated card)

None of the three bundled examples has a fillet or radius fix, so the
**Calculated** before/after card never appears for them. A fourth example of a
part broken at a sharp corner fixes that. The card only shows if Gemini proposes
a fillet as a top fix, so the photo has to make the sharp corner the obvious
cause.

## The part

**Best: a printed L-bracket snapped at a sharp inside corner.** This is the textbook
stress-concentration failure, so a fillet is almost certain to be suggested. The
3D exhibit also has a dedicated L-bracket shape that draws the fillet as an
orange curve in the corner.

- Print a small L-bracket with a **sharp inside corner, no fillet**: about
  40 × 40 mm legs, 12 mm wide, 3–4 mm thick, in PLA or PETG.
- Clamp one leg and push the other until it snaps at the corner.

Alternative: a printed flat bar with a **V-notch** cut into one edge, snapped
through the notch.

## The photo

- **Show the broken face:** point the fracture surface toward the camera, not
  side-on. If it can't see the surface, the model says the photo isn't good
  enough (that's what happened with the craft stick, example III).
- **Include the corner:** keep both halves in frame so the sharp corner the crack
  started from is visible.
- **Get close and sharp:** tap to focus on the break. The part should fill most
  of the frame.
- **Use even light:** daylight or a desk lamp from the side, so the surface
  texture shows. Avoid glare and dark shadows.
- **Add a ruler or coin** in frame so sizes are easier to judge.
- **Use a plain background:** a sheet of paper is better than a patterned desk.

## The note

Type the dimensions in **Describe it** so the numbers come from you, not
estimates. For example:

> PLA L-bracket, 4 mm thick, 12 mm wide, sharp inside corner (no fillet),
> snapped at the corner when I bent it by hand.

With that note, Gemini should suggest adding a fillet and use 4 mm as the section
depth. The card then shows the sharp-corner Kt dropping to the filleted value.

## Bundling it

1. Save the photo as `static/examples/4.jpg`.
2. Run it through Gemini once, with the same note:

   ```bash
   venv/bin/python diagnose.py static/examples/4.jpg --context "PLA L-bracket, 4 mm thick, ..."
   ```

3. Check the output: at least one of the top three solutions must have a
   `computed` block with `kt_before` and `kt_after`. If not, retake the photo or
   sharpen the note. **Never hand-edit the result**: the examples are real model
   output.
4. Save `{"diagnosis": ..., "meta": ...}` from the output as `static/examples/4.json`.
5. In `main.py`, set `EXAMPLE_COUNT = 4`.
6. In `static/index.html`, add a fourth button to *From the collection*:
   `data-example="4"`, numeral **IV**, e.g. "Printed L-bracket · Snapped at a
   sharp corner".
7. Run `venv/bin/python -m pytest`. `tests/test_examples.py` checks every bundled
   example automatically.
