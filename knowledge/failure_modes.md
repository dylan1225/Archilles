# Failure modes knowledge base

This file is inserted verbatim into the diagnosis prompt.

Format rules:
- One `## Mode: <id>` block per failure mode. The `<id>` is a lowercase slug and is
  the exact string the system returns as `primary_mode` and the exact string to use
  as `true_mode` in `eval/labels.csv`.
- Every block has the same four sections, in this order.
- 3 of 11 modes are filled in as format examples. The other 8 are TODO.

---

## Mode: fatigue_metal

- **Name:** Fatigue fracture
- **Material:** metal

### Visual signature
- Two distinct zones on one fracture surface: a smooth, flat, often dull or
  burnished zone (slow crack growth) and a smaller rough, bright, granular zone
  (final fast fracture).
- Beach marks: curved, roughly concentric arcs in the smooth zone that radiate
  from a single point.
- That point (the origin) sits at the surface, usually at a sharp corner, thread
  root, hole, keyway, weld toe, or machining mark.
- Ratchet marks (small steps along the edge) where several cracks started side
  by side.
- Little or no overall distortion: the two halves fit back together cleanly, no
  necking, no visible bend.

### Typical cause
Repeated loading well below the load that would break the part in one go. A crack
starts at a stress concentration and grows a little each cycle until the remaining
section is too small and snaps.

### What to change
- Remove or soften the stress concentration at the origin: larger fillet radius,
  no sharp internal corners, relocate holes away from high-stress regions.
- Improve surface finish at the origin (machining marks and scratches start cracks).
- Reduce the stress amplitude: larger section, lower load, or proper preload on
  bolted joints.
- Surface treatment that puts the surface in compression (shot peening).

### Distinguish from: single-event overload in metal
Overload has one texture across the whole surface and no beach marks. Ductile
overload shows necking, shear lips, and visible stretching or bending of the part.
Brittle overload shows chevron marks pointing back to the origin and a uniformly
granular surface. Fatigue shows two zones, beach marks, and almost no distortion.

---

## Mode: layer_delamination_fdm

- **Name:** Layer delamination
- **Material:** fdm_print

### Visual signature
- The break runs parallel to the print layers, along a single layer boundary.
- The exposed face is flat and shows intact, rounded extrusion lines (bead tops)
  in the pattern the nozzle laid them down.
- Clean separation: little tearing, little stress-whitening, almost no stretched
  or stringy plastic.
- Beads look glossy and unfused to the layer above, sometimes with visible gaps
  between adjacent lines.

### Typical cause
The bond between two layers was the weakest path and the load pulled or bent
across it. Either the part was oriented so the main load acts across the layer
lines, or the layers never fused well (nozzle too cold, too much part cooling,
under-extrusion, layer height too large for the nozzle, wet filament, drafts).

### What to change
- Reorient the part on the bed so the main load runs along the layers, not across.
- Raise nozzle temperature 5 to 10 °C and reduce the part-cooling fan.
- Increase extrusion width or flow; lower the layer height.
- Enclose the printer for materials that warp (ABS, ASA).
- Add a fillet where a thin feature meets a base, so the change in section is not
  on one layer line.

### Distinguish from: cross-layer fracture in FDM
A cross-layer fracture cuts through the layers: the surface is rough, shows the
cross-section of perimeters and infill, and often has stress-whitening. Layer
delamination shows the face of one layer with the bead pattern intact.

---

## Mode: bending_overload_wood

- **Name:** Bending overload
- **Material:** wood

### Visual signature
- Long, splintered, fibrous break on one side (the tension side) with fibres
  pulled out along the grain.
- The fracture is not flat: it runs along the grain for some distance in jagged
  steps before crossing it.
- The opposite side (compression) may show a crease, wrinkle, or crushed band
  across the grain.
- The break often passes through or starts at a knot, a notch, a hole, or a
  region where the grain runs out of the edge of the piece.

### Typical cause
A single bending load larger than the piece could carry. Common contributors: a
section too shallow for the span, a knot or notch on the tension face, or grain
that slopes across the piece instead of running along it.

### What to change
- Increase the depth of the section in the bending direction (bending strength
  rises with depth squared, stiffness with depth cubed).
- Shorten the unsupported span or add a support.
- Use straight-grained stock with no knots near the point of highest bending.
- Keep notches and holes off the tension face; laminate if a deeper section is
  not possible.

### Distinguish from: brash (brittle) failure in wood
A brash failure is abrupt and nearly flat across the grain, with short splinters
or none, and may show discolouration or soft, crumbly wood. It points to a
material problem (decay, compression wood, severe cross-grain), not to overload
of sound wood. Bending overload of sound wood gives long splinters.

---

## Mode: TODO_04

- **Name:** TODO
- **Material:** TODO (fdm_print / metal / wood / other)

### Visual signature
TODO

### Typical cause
TODO

### What to change
TODO

### Distinguish from: TODO (the mode this is most confused with)
TODO

---

## Mode: TODO_05

- **Name:** TODO
- **Material:** TODO (fdm_print / metal / wood / other)

### Visual signature
TODO

### Typical cause
TODO

### What to change
TODO

### Distinguish from: TODO (the mode this is most confused with)
TODO

---

## Mode: TODO_06

- **Name:** TODO
- **Material:** TODO (fdm_print / metal / wood / other)

### Visual signature
TODO

### Typical cause
TODO

### What to change
TODO

### Distinguish from: TODO (the mode this is most confused with)
TODO

---

## Mode: TODO_07

- **Name:** TODO
- **Material:** TODO (fdm_print / metal / wood / other)

### Visual signature
TODO

### Typical cause
TODO

### What to change
TODO

### Distinguish from: TODO (the mode this is most confused with)
TODO

---

## Mode: TODO_08

- **Name:** TODO
- **Material:** TODO (fdm_print / metal / wood / other)

### Visual signature
TODO

### Typical cause
TODO

### What to change
TODO

### Distinguish from: TODO (the mode this is most confused with)
TODO

---

## Mode: TODO_09

- **Name:** TODO
- **Material:** TODO (fdm_print / metal / wood / other)

### Visual signature
TODO

### Typical cause
TODO

### What to change
TODO

### Distinguish from: TODO (the mode this is most confused with)
TODO

---

## Mode: TODO_10

- **Name:** TODO
- **Material:** TODO (fdm_print / metal / wood / other)

### Visual signature
TODO

### Typical cause
TODO

### What to change
TODO

### Distinguish from: TODO (the mode this is most confused with)
TODO

---

## Mode: TODO_11

- **Name:** TODO
- **Material:** TODO (fdm_print / metal / wood / other)

### Visual signature
TODO

### Typical cause
TODO

### What to change
TODO

### Distinguish from: TODO (the mode this is most confused with)
TODO
