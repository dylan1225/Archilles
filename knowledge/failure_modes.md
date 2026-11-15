# Failure modes knowledge base

This file is inserted verbatim into the diagnosis prompt.

Format rules:
- One `## Mode: <id>` block per failure mode. The `<id>` is a lowercase slug and is
  the exact string the system returns as `primary_mode` and the exact string to use
  as `true_mode` in `eval/labels.csv`.
- Every block has the same four sections, in this order.
- Under the name, every block has `Material`, `Source` (the reference the content
  was checked against) and `Status` (`draft` until a person has checked the block
  against that source, then `reviewed`). Code reads `Name` and `Source` and shows
  them on the result page; the model never writes them.

---

## Mode: fatigue_metal

- **Name:** Fatigue fracture
- **Material:** metal
- **Source:** ASM Handbook, Vol. 12: Fractography (ASM International, 1987); ASM Handbook, Vol. 11: Failure Analysis and Prevention (ASM International, 2002)
- **Status:** draft

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
- **Source:** Ahn, Montero, Odell, Roundy & Wright, "Anisotropic material properties of fused deposition modeling ABS", Rapid Prototyping Journal 8(4), 2002
- **Status:** draft

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
- **Source:** USDA Forest Products Laboratory, Wood Handbook: Wood as an Engineering Material, FPL-GTR-282 (2021)
- **Status:** draft

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

## Mode: cross_layer_fracture_fdm

- **Name:** Cross-layer fracture
- **Material:** fdm_print
- **Source:** Ahn, Montero, Odell, Roundy & Wright, "Anisotropic material properties of fused deposition modeling ABS", Rapid Prototyping Journal 8(4), 2002
- **Status:** draft

### Visual signature
- The break cuts through the layers instead of running along one of them.
- The surface is rough and uneven, often stepped where the crack jumped between
  layers.
- The cut-open section shows the structure of the print: solid outer walls
  (perimeters) as a ring, and the infill pattern (lines, grid, triangles, gyroid)
  with empty space between.
- Stress-whitening: pale, cloudy plastic near the break, especially on the side
  that was in tension.
- In tough filaments (PETG, nylon, TPU) there are stretched, stringy strands; in
  PLA the break is crisper and more brittle-looking.
- It often starts at a hole, a notch, a sharp inside corner or a thin wall.

### Typical cause
A single load, usually bending or an impact, larger than the section could carry.
Most of the strength of a printed part is in its walls, so parts with few
perimeters and sparse infill break across the layers at loads far below what the
same shape would carry if printed solid. A hole or sharp corner at the break
point concentrates the stress and decides where it starts.

### What to change
- Add perimeters (4 to 6 walls). Walls carry far more load than infill.
- Raise infill density where it broke, with a slicer modifier, rather than for
  the whole part.
- Add a fillet at sharp inside corners; add a boss or extra walls around holes, or
  move holes out of the most-loaded region.
- Increase the section depth in the bending direction.
- Use a tougher filament (PETG, nylon, polycarbonate) for parts that take impacts.

### Distinguish from: layer delamination in FDM
Layer delamination separates cleanly along one layer: the face is flat and shows
the tops of the extruded lines with their pattern intact, with little whitening.
A cross-layer fracture cuts through many layers and shows the walls and infill in
cross-section, usually with whitening or stretched plastic.

---

## Mode: ductile_overload_metal

- **Name:** Ductile overload
- **Material:** metal
- **Source:** ASM Handbook, Vol. 12: Fractography (ASM International, 1987); ASM Handbook, Vol. 11: Failure Analysis and Prevention (ASM International, 2002)
- **Status:** draft

### Visual signature
- Obvious permanent deformation before the break: the part is bent, twisted or
  stretched.
- Necking: the metal thins locally right at the break, like taffy pulled apart.
- In round bars, a cup-and-cone break: one half has a raised rim, the other a
  matching dish.
- The surface is dull, grey and fibrous or matte, not sparkly.
- Shear lips: angled bands (about 45°) along the edges of the break.
- The two halves no longer fit back together cleanly.

### Typical cause
One load event well above what the part could carry, from a collision, a jammed
mechanism, a stalled motor or a fall. The metal yielded, deformed and then tore.
The part was too small for the load, or the load was much higher than designed for.

### What to change
- Increase the section where it broke; in bending, depth in the bending direction
  matters most.
- Use a stronger alloy or temper (for example aluminium 6061-T6 or 7075-T6 rather
  than soft or annealed stock).
- Limit the load: mechanical hard stops, a slip clutch, motor current limits.
- Shorten lever arms so the same force makes a smaller bending moment.

### Distinguish from: fatigue in metal
Fatigue leaves almost no deformation and shows two zones: a smooth area with
curved beach marks and a smaller rough area. Ductile overload shows heavy bending,
stretching or necking and one dull, fibrous texture across the whole break.

---

## Mode: brittle_overload_metal

- **Name:** Brittle overload
- **Material:** metal
- **Source:** ASM Handbook, Vol. 12: Fractography (ASM International, 1987); ASM Handbook, Vol. 11: Failure Analysis and Prevention (ASM International, 2002)
- **Status:** draft

### Visual signature
- Little or no deformation: the pieces fit back together tightly.
- A flat break, roughly at right angles to the main load.
- A bright, sparkly, granular (crystalline-looking) surface.
- Chevron marks: V-shaped ridges that point back to where the crack started, or
  lines radiating from that point.
- Common in cast metal (die-cast zinc, cast iron), hardened steel, and near welds.

### Typical cause
A single load, often an impact, in a metal with little toughness: a casting, a
hardened or heat-treated part, a weld zone, or metal used in the cold. Instead of
bending first, it cracked suddenly, usually starting at a notch or sharp corner.

### What to change
- Use a tougher material: wrought (machined) metal instead of a casting, or a
  tempered rather than fully hardened steel.
- Add generous fillets and remove notches, sharp corners and deep scratches near
  the break.
- Reduce impact loads with bumpers or some compliance in the mechanism.
- Keep welds away from the most-loaded region.

### Distinguish from: fatigue in metal
Fatigue shows a smooth zone with curved beach marks plus a smaller rough zone.
Brittle overload is one bright, granular texture across the whole surface,
often with chevrons pointing to the origin, and no smooth zone.

---

## Mode: brash_failure_wood

- **Name:** Brash (brittle) failure
- **Material:** wood
- **Source:** USDA Forest Products Laboratory, Wood Handbook: Wood as an Engineering Material, FPL-GTR-282 (2021)
- **Status:** draft

### Visual signature
- An abrupt break almost straight across the grain, like a carrot snapping.
- Short splinters or none; the surface is fairly flat.
- The wood may show discolouration or dark streaks, feel soft or crumbly (decay),
  feel unusually light, or have a dense reddish band (compression wood).
- The grain may run steeply across the piece instead of along it.

### Typical cause
The wood itself was weak, not the design: decay, compression wood, very low
density wood, damage from overheating when it was dried, or severe cross-grain.
Brash wood breaks at loads well below what sound wood of the same size carries.

### What to change
- Replace it with sound, straight-grained stock; reject pieces that are
  discoloured, light for their size, or soft.
- Keep wood parts dry, and store them so they can't rot.
- Changing the shape will not help much until the material is sound.

### Distinguish from: bending overload in wood
Sound wood broken in bending gives long splinters and a jagged break that runs
along the grain before crossing it. A brash failure is flat with short splinters
or none, and points to a problem with the wood itself.

---

## Mode: bolt_hole_tearout

- **Name:** Bolt-hole tear-out
- **Material:** any (fdm_print, metal, wood)
- **Source:** Budynas & Nisbett, Shigley's Mechanical Engineering Design (McGraw-Hill), chapter on bolted joints
- **Status:** draft

### Visual signature
- The damage starts at a fastener hole and runs to the nearest edge.
- Shear-out: a U-shaped or rectangular piece torn out between the hole and the
  edge, in the direction the load pulled.
- Net-section break: a crack straight across the narrowest material beside the
  hole.
- Bearing damage: the hole stretched into an oval in the load direction.
- In prints, the walls around the hole split from the hole toward the edge. In
  wood, a plug between the hole and the end is pushed out along the grain.

### Typical cause
Too little material around the hole: the hole is too close to an edge or end, the
plate is too thin, or one fastener carries the whole load. A loose fit lets the
fastener hammer back and forth and makes it worse. A common guideline is to keep
at least about twice the hole diameter from the hole's centre to the edge.

### What to change
- Move the hole further from the edge, or extend the edge.
- Thicken or widen the material around the hole; add a boss.
- Share the load between more fasteners; use washers to spread it.
- In prints, add perimeters (they wrap around holes) or use heat-set inserts.
- In wood, keep holes away from ends and use larger washers.

### Distinguish from: cross-layer fracture in FDM
Tear-out is local: it starts at the hole and runs to the nearest edge, and the
rest of the part is intact. A cross-layer fracture runs across the whole section,
sometimes through a hole, but not out to an edge from it.

---

## Mode: torsion_overload_shaft

- **Name:** Torsion overload
- **Material:** metal
- **Source:** ASM Handbook, Vol. 11: Failure Analysis and Prevention (ASM International, 2002), article on failures of shafts; Budynas & Nisbett, Shigley's Mechanical Engineering Design (McGraw-Hill)
- **Status:** draft

### Visual signature
- The shaft is visibly twisted: machining marks, flats, hex edges or a paint line
  now run in a spiral.
- A ductile shaft breaks flat, at right angles to its axis, with swirl marks
  smeared around the centre.
- A brittle shaft (cast, hardened or printed) breaks along a 45° spiral.
- Rounded hex corners or a shiny polished bore mean the hub slipped on the shaft
  rather than the shaft breaking.
- The break is often at a keyway, snap-ring groove, set-screw flat or shoulder.

### Typical cause
More torque than the shaft could carry: a jammed mechanism, a stalled motor, a
gear lock-up or an impact on an arm. Small diameter, or a groove or sharp shoulder
at the break point, makes it worse.

### What to change
- Increase the diameter: for a solid round shaft, torsional strength rises with
  the diameter cubed.
- Use a stronger material or temper.
- Put a radius at shoulders and grooves; keep set-screw flats and snap-ring grooves
  away from the most-loaded section.
- Limit torque with a slip clutch, motor current limits or stall detection.

### Distinguish from: fatigue in metal
Torsional fatigue cracks can also spiral at 45°, but they show beach marks and the
shaft is not twisted overall. Torsion overload shows visible permanent twist (if
ductile) and no beach marks.

---

## Mode: gear_tooth_shear

- **Name:** Gear tooth breakage
- **Material:** any (fdm_print, metal, other)
- **Source:** ASM Handbook, Vol. 11: Failure Analysis and Prevention (ASM International, 2002), article on gear failures
- **Status:** draft

### Visual signature
- One or more teeth broken off at the root, where the tooth meets the gear body.
- Often several teeth in a row are missing, with the next teeth bent or cracked
  at the root.
- Teeth nearby may have flattened or chipped tips.
- On plastic gears, smeared or melted tooth surfaces point to heat from running
  too fast or under too much load.
- On printed gears, teeth snapped cleanly along a layer line suggest the gear was
  printed on its side.

### Typical cause
Each tooth acts like a short cantilever beam. A shock load (sudden stop, jam,
collision) or a steady load too large for the tooth size bent the tooth past its
strength at the root. Small teeth, a narrow gear, wrong centre distance (load only
on the tips) or a sharp root make it worse.

### What to change
- Use bigger teeth (larger module) or a wider gear.
- Use a metal gear for the most-loaded stage.
- Set the correct centre distance so teeth mesh fully.
- Use a gear profile with a rounded (full radius) tooth root.
- Print gears lying flat, so no layer line crosses the tooth root.
- Add torque limiting to absorb shocks.

### Distinguish from: gear wear (not a separate mode here)
Wear removes material gradually: teeth become thin, pointed or polished but stay
attached. Tooth breakage removes whole teeth, broken at the root.

---

## Mode: split_along_grain_wood

- **Name:** Split along the grain
- **Material:** wood
- **Source:** USDA Forest Products Laboratory, Wood Handbook: Wood as an Engineering Material, FPL-GTR-282 (2021)
- **Status:** draft

### Visual signature
- A crack running straight along the grain, parallel to the wood fibres, often for
  a long distance.
- The split faces are fairly smooth and fibrous along their length, with no
  fibres pulled across the crack.
- It often starts at an end, a screw, a nail or a hole.
- The piece may still hold together; it is split lengthwise, not broken across.
- Common in thin strips: craft sticks, dowels, and the face layer of plywood.

### Typical cause
Wood is much weaker across the grain than along it. Something pulled the fibres
apart sideways: a screw or nail driven without a pilot hole, a fastener too close
to an end, a clamp, a wedging or twisting load, or shrinkage as the wood dried.

### What to change
- Drill pilot holes, and keep fasteners away from ends and edges.
- Use plywood, whose layers have crossing grain, instead of a solid strip.
- Avoid loads that pull across the grain; glue and clamp joints instead of
  wedging them.
- Seal the ends of solid wood so it dries evenly.

### Distinguish from: bending overload in wood
Bending overload breaks the piece across, with long splinters and a jagged break.
A split runs along the grain and leaves the piece in one length, separated
lengthwise.
