/**
 * Interactive 3D exhibit of the diagnosed part.
 *
 * The part is a schematic reconstruction built from the model's `specimen`
 * description (simple shape, estimated dimensions, break position), not a scan.
 * "As found" shows it broken; selecting a solution shows it whole with that
 * solution's change drawn in orange.
 *
 *   const viewer = createViewer(container, { specimen, material, solutions, photoUrl });
 *   viewer.select(null);   // as found
 *   viewer.select(0);      // solution 1
 *   viewer.resetView();
 *   viewer.dispose();
 */

import * as THREE from "three";
import { OrbitControls } from "three/addons/controls/OrbitControls.js";

const ORANGE = 0x3d7fc4; // accent: marks the change (name kept for history)
const LOAD_RED = 0xe0604c;
const HOLE_DARK = 0x0f1a26;
const FIXTURE = 0x2c3a4a;
const BRASS = 0x8fa9c4;
const GHOST = 0x22303e;
const WALL = 0xe6eff8;
const NICHE = 0xd6e5f3;
const STONE = 0xdfe9f3;

const MATERIAL_LOOKS = {
  wood: { color: 0xc9a576, roughness: 0.78, metalness: 0.0 },
  fdm_print: { color: 0x7b8794, roughness: 0.55, metalness: 0.0 },
  // Moderate metalness: the scene has no environment map, so a fully metallic
  // surface has nothing to reflect and renders near-black.
  metal: { color: 0xbcc1c8, roughness: 0.38, metalness: 0.45 },
  other: { color: 0xb3bcc7, roughness: 0.6, metalness: 0.05 },
  unclear: { color: 0xb3bcc7, roughness: 0.6, metalness: 0.05 },
};

const SPECIMEN_LENGTH = 4;
const MOUNT_HEIGHT = 0.35;
const WALL_Z = -3.2;
const FLOOR_Y = -1.4;
const CAMERA_HOME = new THREE.Vector3(3.1, 3.3, 7.0);
const TARGET_HOME = new THREE.Vector3(0, 1.05, -0.6);
const TWEEN_MS = 650;

const clamp = (value, low, high) => Math.min(Math.max(value, low), high);
const lerp = (a, b, t) => a + (b - a) * t;
const easeOut = (t) => 1 - Math.pow(1 - t, 3);

// --- Layout -----------------------------------------------------------------

/** Turn the specimen (millimetres) into scene units. Length is always 4 units. */
function computeLayout(spec) {
  const scale = SPECIMEN_LENGTH / spec.length_mm;
  const round = spec.shape === "round_rod";
  const bracket = spec.shape === "l_bracket";
  const rawW = spec.width_mm * scale;
  const rawT = (round ? spec.width_mm : spec.thickness_mm) * scale;
  const w = clamp(rawW, 0.16, spec.shape === "flat_plate" ? 2.2 : 1.0);
  const t = round ? w : clamp(rawT, 0.09, bracket ? 0.5 : 0.8);
  const lay = {
    shape: spec.shape,
    round,
    bracket,
    feature: spec.feature,
    load: spec.load_case,
    breakPosition: spec.break_position,
    exaggerated: Math.abs(w - rawW) > 1e-6 || Math.abs(t - rawT) > 1e-6,
    base: MOUNT_HEIGHT,
    x0: bracket ? -1.4 : -SPECIMEN_LENGTH / 2,
    x1: bracket ? 1.0 : SPECIMEN_LENGTH / 2,
    legH: 1.6,
  };
  return withSection(lay, t, w);
}

/** Same layout with a different section (used when a solution thickens it). */
function withSection(lay, t, w) {
  const next = { ...lay, t, w, r: w / 2, top: lay.base + t };
  next.holeR = Math.min(0.3 * w, 0.35);
  next.notchW = Math.max(0.06, t * 0.35);
  next.notchD = t * 0.38;
  if (lay.bracket) {
    next.cornerX = next.x1 - t;
    next.breakX = next.cornerX;
    next.featureX = lay.feature === "hole" || lay.feature === "notch" ? next.cornerX - 0.45 : next.cornerX;
  } else {
    next.breakX = next.x0 + SPECIMEN_LENGTH * lay.breakPosition;
    next.featureX = next.breakX;
  }
  next.centerY = next.base + t / 2;
  return next;
}

// --- Materials and small builders -------------------------------------------

function partMaterial(look, { tint = 0, opacity = 1 } = {}) {
  const color = new THREE.Color(look.color).lerp(new THREE.Color(ORANGE), tint);
  return new THREE.MeshStandardMaterial({
    vertexColors: true,
    color: 0xffffff,
    roughness: look.roughness,
    metalness: look.metalness,
    transparent: opacity < 1,
    opacity,
    depthWrite: opacity >= 1,
    userData: { base: color },
  });
}

function solid(color, extra = {}) {
  return new THREE.MeshStandardMaterial({ color, roughness: 0.55, metalness: 0.1, ...extra });
}

function accent(extra = {}) {
  return new THREE.MeshStandardMaterial({
    color: ORANGE,
    roughness: 0.4,
    metalness: 0.15,
    emissive: ORANGE,
    emissiveIntensity: 0.18,
    ...extra,
  });
}

function mesh(geometry, material, { cast = true, receive = false } = {}) {
  const node = new THREE.Mesh(geometry, material);
  node.castShadow = cast;
  node.receiveShadow = receive;
  return node;
}

/** Ridged fracture surface. The same function for both halves, so they interlock. */
function fractureNoise(y, z, amp) {
  return amp * (0.5 * Math.sin(y * 37 + z * 11 + 1.3)
    + 0.3 * Math.sin(z * 53 - y * 23 + 2.1)
    + 0.2 * Math.sin((y + z) * 91 + 0.7));
}

function boxGeometry(xa, xb, ya, yb, w) {
  const len = xb - xa;
  const height = yb - ya;
  const geometry = new THREE.BoxGeometry(
    len, height, w,
    Math.max(2, Math.round(len / 0.08)),
    clamp(Math.round(height / 0.03), 6, 48),
    12,
  );
  geometry.translate((xa + xb) / 2, (ya + yb) / 2, 0);
  return geometry;
}

function rodGeometry(xa, xb, r, yc) {
  const len = xb - xa;
  const geometry = new THREE.CylinderGeometry(r, r, len, 40, Math.max(2, Math.round(len / 0.08)));
  geometry.rotateZ(-Math.PI / 2); // axis +y -> +x
  geometry.translate((xa + xb) / 2, yc, 0);
  return geometry;
}

/** Jag the fracture planes and paint vertex colours (fracture faces darker). */
function finishPart(geometry, planes, material, lay) {
  const position = geometry.attributes.position;
  const colors = new Float32Array(position.count * 3);
  const base = material.userData.base;
  const fracture = base.clone().lerp(new THREE.Color(0x3b2a1e), 0.5);
  const amp = Math.min(0.12, 0.35 * Math.min(lay.t, lay.w));
  for (let i = 0; i < position.count; i++) {
    const x = position.getX(i);
    const y = position.getY(i);
    const z = position.getZ(i);
    let color = base;
    for (const plane of planes) {
      const onPlane = Math.abs(x - plane.x) < 1e-6 && (plane.yMax === undefined || y <= plane.yMax + 1e-6);
      if (onPlane) {
        position.setX(i, x + fractureNoise(y, z, amp));
        color = fracture;
      }
    }
    color.toArray(colors, i * 3);
  }
  geometry.setAttribute("color", new THREE.BufferAttribute(colors, 3));
  if (planes.length) geometry.computeVertexNormals();
  return mesh(geometry, material);
}

/** The intact part's geometries, before any fracture. */
function partSegments(lay) {
  if (lay.bracket) {
    return [
      { geometry: boxGeometry(lay.x0, lay.cornerX, lay.base, lay.top, lay.w), side: "a" },
      { geometry: boxGeometry(lay.cornerX, lay.x1, lay.base, lay.base + lay.legH, lay.w), side: "b" },
    ];
  }
  const make = (xa, xb) => (lay.round
    ? rodGeometry(xa, xb, lay.r, lay.centerY)
    : boxGeometry(xa, xb, lay.base, lay.top, lay.w));
  return [{ geometry: make(lay.x0, lay.x1), side: "a" }];
}

function intactPart(lay, look, options) {
  const material = partMaterial(look, options);
  const group = new THREE.Group();
  partSegments(lay).forEach(({ geometry }) => group.add(finishPart(geometry, [], material, lay)));
  return group;
}

function arrow(tip, direction, length, color, opacity = 1) {
  const material = new THREE.MeshStandardMaterial({
    color, roughness: 0.45, metalness: 0.1, transparent: opacity < 1, opacity, depthWrite: opacity >= 1,
  });
  const headLength = 0.22;
  const group = new THREE.Group();
  const shaft = mesh(new THREE.CylinderGeometry(0.03, 0.03, length - headLength, 16), material);
  shaft.position.y = headLength + (length - headLength) / 2;
  const head = mesh(new THREE.ConeGeometry(0.09, headLength, 24), material);
  head.rotation.x = Math.PI;
  head.position.y = headLength / 2;
  group.add(shaft, head);
  group.position.copy(tip);
  group.quaternion.setFromUnitVectors(new THREE.Vector3(0, -1, 0), direction.clone().normalize());
  return group;
}

/** A thin ring around the section at x: a torus for a rod, four bars for a box. */
function sectionRing(lay, x, pad, thickness, material) {
  if (lay.round) {
    const ring = mesh(new THREE.TorusGeometry(lay.r + pad, thickness, 12, 56), material);
    ring.rotation.y = Math.PI / 2;
    ring.position.set(x, lay.centerY, 0);
    return ring;
  }
  const group = new THREE.Group();
  const outerW = lay.w + 2 * pad + 2 * thickness;
  const outerT = lay.t + 2 * pad + 2 * thickness;
  const top = mesh(new THREE.BoxGeometry(thickness, thickness, outerW), material);
  top.position.set(x, lay.top + pad + thickness / 2, 0);
  const bottom = top.clone();
  bottom.position.y = lay.base - pad - thickness / 2;
  const side = mesh(new THREE.BoxGeometry(thickness, outerT, thickness), material);
  side.position.set(x, lay.centerY, lay.w / 2 + pad + thickness / 2);
  const otherSide = side.clone();
  otherSide.position.z = -side.position.z;
  group.add(top, bottom, side, otherSide);
  return group;
}

function collar(lay, x, length, pad, material) {
  const geometry = lay.round
    ? rodGeometry(x - length / 2, x + length / 2, lay.r + pad, lay.centerY)
    : boxGeometry(x - length / 2, x + length / 2, lay.base - pad, lay.top + pad, lay.w + 2 * pad);
  return mesh(geometry, material);
}

// --- Features (hole, notch, sharp corner) -----------------------------------

function holeHeight(lay) {
  return (lay.round ? 2 * lay.r : lay.t) * 1.06;
}

/** half: "left", "right" or null for the whole hole. */
function holeMesh(lay, x, half = null, radius = lay.holeR) {
  const thetaStart = half === "left" ? Math.PI : 0;
  const thetaLength = half ? Math.PI : Math.PI * 2;
  const geometry = new THREE.CylinderGeometry(radius, radius, holeHeight(lay), 40, 1, false, thetaStart, thetaLength);
  const hole = mesh(geometry, solid(HOLE_DARK, { roughness: 1, metalness: 0 }), { cast: false });
  hole.position.set(x, lay.centerY, 0);
  return hole;
}

function holeRim(lay, x, material, radius = lay.holeR) {
  const rim = mesh(new THREE.TorusGeometry(radius, 0.022, 10, 48), material, { cast: false });
  rim.rotation.x = Math.PI / 2;
  rim.position.set(x, lay.centerY + holeHeight(lay) / 2, 0);
  return rim;
}

function notchMesh(lay, x, half = null) {
  const material = solid(HOLE_DARK, { roughness: 1, metalness: 0 });
  if (lay.round) {
    const groove = mesh(new THREE.TorusGeometry(lay.r, lay.r * 0.16, 10, 48), material, { cast: false });
    groove.rotation.y = Math.PI / 2;
    groove.position.set(x, lay.centerY, 0);
    return groove;
  }
  const top = lay.top + 0.002;
  const shape = new THREE.Shape();
  const left = half === "right" ? x : x - lay.notchW;
  const right = half === "left" ? x : x + lay.notchW;
  shape.moveTo(left, top);
  shape.lineTo(right, top);
  shape.lineTo(x, top - lay.notchD);
  shape.closePath();
  const geometry = new THREE.ExtrudeGeometry(shape, { depth: lay.w * 1.02, bevelEnabled: false });
  geometry.translate(0, 0, -lay.w * 0.51);
  return mesh(geometry, material, { cast: false });
}

/** Rounded groove replacing a sharp notch: dark half-cylinder with orange edges. */
function roundedGroove(lay, x) {
  const group = new THREE.Group();
  const radius = Math.max(lay.notchW, lay.notchD) * 1.1;
  const groove = mesh(
    new THREE.CylinderGeometry(radius, radius, lay.w * 1.02, 40, 1, false, Math.PI / 2, Math.PI),
    solid(HOLE_DARK, { roughness: 1, metalness: 0 }), { cast: false },
  );
  groove.rotation.x = Math.PI / 2;
  groove.position.set(x, lay.top + 0.002, 0);
  group.add(groove);
  [1, -1].forEach((sign) => {
    const edge = mesh(new THREE.TorusGeometry(radius, 0.018, 8, 40, Math.PI), accent(), { cast: false });
    edge.rotation.z = Math.PI;
    edge.position.set(x, lay.top, sign * (lay.w / 2 + 0.004));
    group.add(edge);
  });
  return group;
}

function sharpCornerMarker(lay) {
  const material = solid(LOAD_RED, { emissive: LOAD_RED, emissiveIntensity: 0.3 });
  if (lay.bracket) {
    const line = mesh(new THREE.BoxGeometry(0.035, 0.035, lay.w * 1.04), material, { cast: false });
    line.position.set(lay.cornerX, lay.top, 0);
    return line;
  }
  return sectionRing(lay, lay.breakX, 0.01, 0.018, material);
}

// --- Fixtures and loads -----------------------------------------------------

function defaultLoads(lay) {
  const down = new THREE.Vector3(0, -1, 0);
  if (lay.bracket) {
    return [{ tip: new THREE.Vector3(lay.cornerX - 0.03, lay.base + lay.legH - 0.15, 0), dir: new THREE.Vector3(1, 0, 0), length: 0.9 }];
  }
  switch (lay.load) {
    case "bending_span":
      return [{ tip: new THREE.Vector3(lay.breakX, lay.top + 0.02, 0), dir: down, length: 0.95 }];
    case "cantilever":
      return [{ tip: new THREE.Vector3(lay.x1 - 0.2, lay.top + 0.02, 0), dir: down, length: 0.95 }];
    case "tension":
      return [
        { tip: new THREE.Vector3(lay.x1 + 0.93, lay.centerY, 0), dir: new THREE.Vector3(1, 0, 0), length: 0.85 },
        { tip: new THREE.Vector3(lay.x0 - 0.93, lay.centerY, 0), dir: new THREE.Vector3(-1, 0, 0), length: 0.85 },
      ];
    default:
      return [];
  }
}

function supportXs(lay) {
  if (lay.bracket) return [lay.x0 + 0.4, lay.cornerX - 0.7];
  if (lay.load === "bending_span") return [lay.x0 + 0.3, lay.x1 - 0.3];
  return [lay.x0 + 0.7, lay.x1 - 0.7];
}

function wedge(x, lay, material) {
  const shape = new THREE.Shape();
  shape.moveTo(x - 0.2, 0);
  shape.lineTo(x + 0.2, 0);
  shape.lineTo(x, lay.base);
  shape.closePath();
  const depth = lay.w + 0.3;
  const geometry = new THREE.ExtrudeGeometry(shape, { depth, bevelEnabled: false });
  geometry.translate(0, 0, -depth / 2);
  return mesh(geometry, material, { receive: true });
}

function post(x, lay, material) {
  const group = new THREE.Group();
  const rod = mesh(new THREE.CylinderGeometry(0.035, 0.05, lay.base, 20), material);
  rod.position.set(x, lay.base / 2, 0);
  const saddle = mesh(new THREE.BoxGeometry(0.16, 0.03, Math.min(lay.w * 0.8, 0.5)), material);
  saddle.position.set(x, lay.base - 0.015, 0);
  group.add(rod, saddle);
  return group;
}

function fixtures(lay) {
  const group = new THREE.Group();
  if (lay.load === "cantilever") {
    const height = lay.top + 0.28;
    const clampBlock = mesh(new THREE.BoxGeometry(0.75, height, lay.w + 0.5), solid(FIXTURE, { roughness: 0.7 }), { receive: true });
    clampBlock.position.set(lay.x0 + 0.075, height / 2, 0);
    group.add(clampBlock);
    return group;
  }
  const xs = supportXs(lay);
  if (lay.load === "bending_span" && !lay.bracket) {
    xs.forEach((x) => group.add(wedge(x, lay, solid(FIXTURE, { roughness: 0.7 }))));
  } else {
    xs.forEach((x) => group.add(post(x, lay, solid(BRASS, { metalness: 0.8, roughness: 0.35 }))));
  }
  return group;
}

function loadArrows(loads, color, opacity = 1) {
  const group = new THREE.Group();
  loads.forEach(({ tip, dir, length }) => group.add(arrow(tip, dir, length, color, opacity)));
  return group;
}

// --- States -----------------------------------------------------------------

function pivot(px, py) {
  const outer = new THREE.Group();
  const inner = new THREE.Group();
  outer.position.set(px, py, 0);
  inner.position.set(-px, -py, 0);
  outer.add(inner);
  return { outer, inner, px, py };
}

function buildAsFound(lay, look) {
  const group = new THREE.Group();
  const material = partMaterial(look);
  const planes = lay.bracket
    ? { a: [{ x: lay.cornerX }], b: [{ x: lay.cornerX, yMax: lay.top }] }
    : { a: [{ x: lay.breakX }], b: [{ x: lay.breakX }] };

  let pieceA;
  let pieceB;
  if (lay.bracket) {
    pieceA = pivot(lay.x0, lay.base);
    pieceB = pivot(lay.x1, lay.base);
    pieceA.inner.add(finishPart(boxGeometry(lay.x0, lay.cornerX, lay.base, lay.top, lay.w), planes.a, material, lay));
    pieceB.inner.add(finishPart(boxGeometry(lay.cornerX, lay.x1, lay.base, lay.base + lay.legH, lay.w), planes.b, material, lay));
  } else {
    const make = (xa, xb) => (lay.round
      ? rodGeometry(xa, xb, lay.r, lay.centerY)
      : boxGeometry(xa, xb, lay.base, lay.top, lay.w));
    pieceA = pivot(lay.x0, lay.base);
    pieceB = pivot(lay.load === "cantilever" ? lay.breakX : lay.x1, lay.base);
    pieceA.inner.add(finishPart(make(lay.x0, lay.breakX), planes.a, material, lay));
    pieceB.inner.add(finishPart(make(lay.breakX, lay.x1), planes.b, material, lay));
  }

  // The feature at the break, split between the two halves where it was cut.
  const split = Math.abs(lay.featureX - lay.breakX) < 1e-6;
  if (lay.feature === "hole") {
    if (split) {
      pieceA.inner.add(holeMesh(lay, lay.featureX, "left"));
      pieceB.inner.add(holeMesh(lay, lay.featureX, "right"));
    } else {
      pieceA.inner.add(holeMesh(lay, lay.featureX));
    }
  } else if (lay.feature === "notch") {
    if (split && !lay.round) {
      pieceA.inner.add(notchMesh(lay, lay.featureX, "left"));
      pieceB.inner.add(notchMesh(lay, lay.featureX, "right"));
    } else {
      pieceA.inner.add(notchMesh(lay, lay.featureX));
    }
  } else if (lay.feature === "sharp_corner") {
    pieceA.inner.add(sharpCornerMarker(lay));
  }

  // How the halves come to rest after the break.
  let motionA = { dx: -0.08, dy: 0, rot: 0 };
  let motionB = { dx: 0.08, dy: 0, rot: 0 };
  if (lay.bracket) {
    motionA = { dx: -0.04, dy: 0, rot: 0 };
    motionB = { dx: 0.14, dy: 0, rot: -0.14 };
  } else if (lay.load === "bending_span") {
    motionA = { dx: -0.05, dy: 0, rot: -0.035 };
    motionB = { dx: 0.05, dy: 0, rot: 0.035 };
  } else if (lay.load === "cantilever") {
    motionA = { dx: 0, dy: 0, rot: 0 };
    motionB = { dx: 0.1, dy: -0.04, rot: -Math.min(0.1, 0.25 / Math.max(lay.x1 - lay.breakX, 0.1)) };
  }

  group.add(pieceA.outer, pieceB.outer, fixtures(lay), loadArrows(defaultLoads(lay), LOAD_RED));
  const pieces = [[pieceA, motionA], [pieceB, motionB]];
  const anchor = lay.bracket
    ? new THREE.Vector3(lay.cornerX, lay.top + 0.35, 0)
    : new THREE.Vector3(lay.breakX, lay.top + 0.45, 0);
  return {
    group,
    anchor,
    label: "Fracture · as found",
    tone: "fracture",
    animate(progress) {
      pieces.forEach(([piece, motion]) => {
        piece.outer.position.set(piece.px + motion.dx * progress, piece.py + motion.dy * progress, 0);
        piece.outer.rotation.z = motion.rot * progress;
      });
    },
  };
}

function nearestEnd(lay, x) {
  return Math.abs(x - lay.x0) < Math.abs(x - lay.x1) ? lay.x0 : lay.x1;
}

function ghostEdges(lay) {
  const group = new THREE.Group();
  const material = new THREE.LineBasicMaterial({ color: GHOST, transparent: true, opacity: 0.45, depthTest: false });
  partSegments(lay).forEach(({ geometry }) => {
    const lines = new THREE.LineSegments(new THREE.EdgesGeometry(geometry, 25), material);
    lines.renderOrder = 10;
    group.add(lines);
    geometry.dispose();
  });
  return group;
}

function gusset(lay, size, thickness) {
  const cx = lay.cornerX;
  const cy = lay.top;
  const shape = new THREE.Shape();
  shape.moveTo(cx, cy);
  shape.lineTo(cx - size, cy);
  shape.lineTo(cx, cy + size);
  shape.closePath();
  const geometry = new THREE.ExtrudeGeometry(shape, { depth: thickness, bevelEnabled: false });
  geometry.translate(0, 0, -thickness / 2);
  return mesh(geometry, accent());
}

function topRib(lay) {
  const half = 0.9;
  const height = Math.max(lay.t * 0.8, 0.12);
  const x = clamp(lay.breakX, lay.x0 + half, lay.x1 - half);
  const y = lay.round ? lay.top - lay.r * 0.3 : lay.top;
  const shape = new THREE.Shape();
  shape.moveTo(x - half, y);
  shape.lineTo(x + half, y);
  shape.lineTo(x + half * 0.55, y + height);
  shape.lineTo(x - half * 0.55, y + height);
  shape.closePath();
  const thickness = clamp(lay.w * 0.3, 0.05, 0.14);
  const geometry = new THREE.ExtrudeGeometry(shape, { depth: thickness, bevelEnabled: false });
  geometry.translate(0, 0, -thickness / 2);
  return { node: mesh(geometry, accent()), anchor: new THREE.Vector3(x, y + height + 0.3, 0) };
}

/** Grid of struts through the part: sparse grey, dense orange near the break. */
function infill(lay) {
  const group = new THREE.Group();
  const halfW = (lay.round ? lay.r * 0.7 : lay.w / 2) * 0.92;
  const levels = lay.round ? [lay.centerY] : [lay.base + lay.t * 0.33, lay.base + lay.t * 0.67];
  const xEnd = lay.bracket ? lay.cornerX : lay.x1;
  const strut = new THREE.BoxGeometry(1, 0.014, 0.014);
  const add = (material, step, keep) => {
    const transforms = [];
    for (let x = lay.x0 + 0.02; x + step <= xEnd; x += step) {
      if (!keep(x + step / 2)) continue;
      levels.forEach((y) => {
        [1, -1].forEach((sign) => transforms.push({ x: x + step / 2, y, angle: sign * Math.atan2(2 * halfW, step) }));
      });
    }
    const instanced = new THREE.InstancedMesh(strut, material, transforms.length);
    const matrix = new THREE.Matrix4();
    const length = Math.hypot(step, 2 * halfW);
    transforms.forEach(({ x, y, angle }, index) => {
      matrix.compose(
        new THREE.Vector3(x, y, 0),
        new THREE.Quaternion().setFromAxisAngle(new THREE.Vector3(0, 1, 0), angle),
        new THREE.Vector3(length, 1, 1),
      );
      instanced.setMatrixAt(index, matrix);
    });
    group.add(instanced);
  };
  const zone = 0.7;
  add(solid(0x8b8577), 0.32, (x) => Math.abs(x - lay.breakX) > zone);
  add(accent(), 0.07, (x) => Math.abs(x - lay.breakX) <= zone);
  return group;
}

/** Layer lines running along the length, on both side faces. */
function lengthwiseLayers(lay) {
  const group = new THREE.Group();
  const material = accent();
  const xEnd = lay.bracket ? lay.cornerX : lay.x1;
  const length = xEnd - lay.x0;
  const count = 6;
  const line = new THREE.BoxGeometry(length, 0.012, 0.006);
  const instanced = new THREE.InstancedMesh(line, material, count * 2);
  const matrix = new THREE.Matrix4();
  let index = 0;
  for (let i = 1; i <= count; i++) {
    const y = lay.base + (lay.t * i) / (count + 1);
    const offset = lay.round ? Math.sqrt(Math.max(lay.r ** 2 - (y - lay.centerY) ** 2, 0)) + 0.004 : lay.w / 2 + 0.004;
    [1, -1].forEach((sign) => {
      matrix.makeTranslation(lay.x0 + length / 2, y, sign * offset);
      instanced.setMatrixAt(index++, matrix);
    });
  }
  group.add(instanced);
  return group;
}

/** Faint layer lines across the length: how an upright print is stacked. */
function acrossLayers(lay) {
  const points = [];
  const xEnd = lay.bracket ? lay.cornerX : lay.x1;
  const hw = lay.w / 2 + 0.003;
  for (let x = lay.x0 + 0.05; x < xEnd; x += 0.06) {
    if (lay.round) {
      for (let k = 0; k < 24; k++) {
        const a = (k / 24) * Math.PI * 2;
        const b = ((k + 1) / 24) * Math.PI * 2;
        const r = lay.r + 0.003;
        points.push(x, lay.centerY + r * Math.sin(a), r * Math.cos(a), x, lay.centerY + r * Math.sin(b), r * Math.cos(b));
      }
    } else {
      const y0 = lay.base - 0.003;
      const y1 = lay.top + 0.003;
      points.push(x, y0, hw, x, y1, hw, x, y1, hw, x, y1, -hw, x, y1, -hw, x, y0, -hw);
    }
  }
  const geometry = new THREE.BufferGeometry();
  geometry.setAttribute("position", new THREE.Float32BufferAttribute(points, 3));
  return new THREE.LineSegments(geometry, new THREE.LineBasicMaterial({ color: GHOST, transparent: true, opacity: 0.35 }));
}

/** Whole part with one solution's change applied. */
function buildSolution(lay, look, solution) {
  const kind = (solution.visual && solution.visual.kind) || "other";
  const group = new THREE.Group();
  const decor = [];
  let partLay = lay;
  let partLook = look;
  let partOptions = {};
  let showFeature = true;
  let featureX = lay.featureX;
  let loads = defaultLoads(lay);
  let anchor = new THREE.Vector3(lay.breakX, lay.top + 0.45, 0);

  switch (kind) {
    case "add_support": {
      if (lay.bracket) {
        const node = gusset(lay, Math.min(0.75, lay.legH * 0.5), Math.min(lay.w * 0.25, 0.12));
        decor.push(node);
        anchor = new THREE.Vector3(lay.cornerX - 0.3, lay.top + 0.55, 0);
      } else if (lay.load === "bending_span") {
        decor.push(wedge(lay.breakX, lay, accent()));
        anchor = new THREE.Vector3(lay.breakX, lay.base * 0.5, lay.w / 2 + 0.3);
      } else {
        const x = lay.load === "cantilever" ? lerp(lay.breakX, lay.x1, 0.5) : lay.breakX;
        decor.push(post(x, lay, accent()));
        anchor = new THREE.Vector3(x, lay.base * 0.5, lay.w / 2 + 0.3);
      }
      break;
    }
    case "move_load": {
      decor.push(loadArrows(loads, LOAD_RED, 0.22));
      if (lay.bracket) {
        const tip = new THREE.Vector3(lay.cornerX - 0.03, lay.top + 0.3, 0);
        loads = [{ tip, dir: new THREE.Vector3(1, 0, 0), length: 0.9 }];
      } else if (lay.load === "cantilever") {
        const x = lerp(lay.x1 - 0.2, lay.x0 + 0.5, 0.55);
        loads = [{ tip: new THREE.Vector3(x, lay.top + 0.02, 0), dir: new THREE.Vector3(0, -1, 0), length: 0.95 }];
      } else {
        const from = loads.length && lay.load === "bending_span" ? loads[0].tip.x : lay.breakX;
        const xs = supportXs(lay);
        const target = Math.abs(from - xs[0]) < Math.abs(from - xs[1]) ? xs[0] : xs[1];
        const x = lerp(from, target, 0.7);
        loads = [{ tip: new THREE.Vector3(x, lay.top + 0.02, 0), dir: new THREE.Vector3(0, -1, 0), length: 0.95 }];
      }
      decor.push(loadArrows(loads, ORANGE));
      anchor = loads[0].tip.clone().add(new THREE.Vector3(0, lay.bracket ? 0.35 : 1.15, 0));
      loads = [];
      break;
    }
    case "thicken_section": {
      partLay = withSection(lay, lay.round ? lay.t * 1.4 : lay.t * 1.5, lay.w * (lay.round ? 1.4 : 1.3));
      partOptions = { tint: 0.3 };
      decor.push(ghostEdges(lay));
      anchor = new THREE.Vector3(partLay.breakX, partLay.top + 0.45, 0);
      break;
    }
    case "change_material": {
      partLook = { color: new THREE.Color(look.color).lerp(new THREE.Color(ORANGE), 0.5).getHex(), roughness: 0.35, metalness: 0.35 };
      break;
    }
    case "add_fillet": {
      if (lay.bracket) {
        const radius = Math.min(lay.t * 1.4, 0.45);
        const cx = lay.cornerX;
        const cy = lay.top;
        const shape = new THREE.Shape();
        shape.moveTo(cx, cy);
        shape.lineTo(cx - radius, cy);
        shape.absarc(cx - radius, cy + radius, radius, -Math.PI / 2, 0, false);
        shape.closePath();
        const geometry = new THREE.ExtrudeGeometry(shape, { depth: lay.w, bevelEnabled: false, curveSegments: 24 });
        geometry.translate(0, 0, -lay.w / 2);
        decor.push(mesh(geometry, accent()));
        anchor = new THREE.Vector3(cx - radius * 0.4, cy + radius + 0.35, 0);
      } else if (lay.feature === "notch" && !lay.round) {
        showFeature = false;
        decor.push(roundedGroove(lay, lay.featureX));
      } else if (lay.feature === "hole") {
        decor.push(holeRim(lay, lay.featureX, accent()));
      } else {
        decor.push(sectionRing(lay, lay.breakX, 0.0, lay.round ? lay.r * 0.18 : 0.03, accent()));
      }
      break;
    }
    case "reinforce_feature": {
      if (lay.feature === "hole") {
        const boss = mesh(new THREE.CylinderGeometry(lay.holeR * 1.9, lay.holeR * 1.9, holeHeight(lay) * 1.25, 48), accent());
        boss.position.set(lay.featureX, lay.centerY, 0);
        decor.push(boss, holeMesh({ ...lay, t: lay.t * 1.3, r: lay.r * 1.3 }, lay.featureX));
        showFeature = false;
      } else if (lay.bracket) {
        decor.push(gusset(lay, Math.min(0.6, lay.legH * 0.4), lay.w * 1.0));
      } else {
        decor.push(collar(lay, lay.breakX, Math.max(0.45, lay.w * 0.8), Math.max(0.03, lay.t * 0.18), accent()));
      }
      break;
    }
    case "relocate_feature": {
      const target = lay.load === "cantilever" && !lay.bracket ? lay.x1 : nearestEnd(lay, lay.featureX);
      const newX = lerp(lay.featureX, target, 0.6);
      const ghost = new THREE.MeshBasicMaterial({ color: GHOST, transparent: true, opacity: 0.4, depthWrite: false });
      if (lay.feature === "hole") {
        decor.push(holeRim(lay, lay.featureX, ghost), holeRim(lay, newX, accent()));
        featureX = newX;
      } else if (lay.feature === "notch") {
        featureX = newX;
        decor.push(sectionRing(lay, lay.featureX, 0.01, 0.012, ghost), sectionRing(lay, newX, 0.01, 0.02, accent()));
      } else {
        decor.push(sectionRing(lay, lay.breakX, 0.01, 0.012, ghost), sectionRing(lay, newX, 0.01, 0.025, accent()));
      }
      anchor = new THREE.Vector3(newX, lay.top + 0.45, 0);
      break;
    }
    case "densify_infill": {
      partOptions = { opacity: 0.28 };
      decor.push(infill(lay));
      break;
    }
    case "reorient_layers": {
      decor.push(lengthwiseLayers(lay));
      break;
    }
    case "add_rib": {
      if (lay.bracket) {
        decor.push(gusset(lay, Math.min(0.85, lay.legH * 0.55), clamp(lay.w * 0.3, 0.05, 0.14)));
        anchor = new THREE.Vector3(lay.cornerX - 0.35, lay.top + 0.75, 0);
      } else {
        const rib = topRib(lay);
        decor.push(rib.node);
        anchor = rib.anchor;
      }
      break;
    }
    case "surface_treatment": {
      const shell = accent({ transparent: true, opacity: 0.38, metalness: 0.5, roughness: 0.2, depthWrite: false });
      decor.push(collar(lay, clamp(lay.breakX, lay.x0 + 0.7, (lay.bracket ? lay.cornerX : lay.x1) - 0.7), 1.4, 0.012, shell));
      break;
    }
    default: {
      decor.push(sectionRing(lay, lay.breakX, 0.06, 0.03, accent()));
    }
  }

  group.add(intactPart(partLay, partLook, partOptions));
  if (showFeature && lay.feature === "hole") group.add(holeMesh(partLay, featureX));
  if (showFeature && lay.feature === "notch") group.add(notchMesh(partLay, featureX));
  group.add(fixtures(partLay), loadArrows(loads, LOAD_RED), ...decor);
  return {
    group,
    anchor,
    label: (solution.visual && solution.visual.label) || solution.title,
    tone: "solution",
    animate() {},
  };
}

// --- Gallery ----------------------------------------------------------------

function stoneTexture() {
  const size = 512;
  const canvas = document.createElement("canvas");
  canvas.width = size;
  canvas.height = size;
  const context = canvas.getContext("2d");
  context.fillStyle = "#dbe6f1";
  context.fillRect(0, 0, size, size);
  let seed = 7;
  const random = () => ((seed = (seed * 16807) % 2147483647) / 2147483647);
  for (let i = 0; i < 2200; i++) {
    const shade = 190 + Math.floor(random() * 40);
    context.fillStyle = `rgba(${shade - 24}, ${shade - 8}, ${shade}, 0.18)`;
    context.fillRect(random() * size, random() * size, 2 + random() * 5, 2 + random() * 5);
  }
  context.strokeStyle = "rgba(110, 140, 170, 0.35)";
  context.lineWidth = 3;
  for (let i = 0; i <= 2; i++) {
    const p = (i * size) / 2;
    context.beginPath(); context.moveTo(p, 0); context.lineTo(p, size); context.stroke();
    context.beginPath(); context.moveTo(0, p); context.lineTo(size, p); context.stroke();
  }
  const texture = new THREE.CanvasTexture(canvas);
  texture.colorSpace = THREE.SRGBColorSpace;
  texture.wrapS = THREE.RepeatWrapping;
  texture.wrapT = THREE.RepeatWrapping;
  texture.repeat.set(7, 7);
  texture.anisotropy = 4;
  return texture;
}

function pointedArch(halfWidth, spring, radius, bottom) {
  const shape = new THREE.Shape();
  const apexAngle = Math.acos((halfWidth - radius) / radius);
  shape.moveTo(-halfWidth, bottom);
  shape.lineTo(-halfWidth, spring);
  shape.absarc(-halfWidth + radius, spring, radius, Math.PI, apexAngle, true);
  shape.absarc(halfWidth - radius, spring, radius, Math.PI - apexAngle, 0, true);
  shape.lineTo(halfWidth, bottom);
  shape.closePath();
  return shape;
}

function buildGallery(scene) {
  const floor = mesh(
    new THREE.PlaneGeometry(40, 40),
    new THREE.MeshStandardMaterial({ map: stoneTexture(), roughness: 0.9 }),
    { cast: false, receive: true },
  );
  floor.rotation.x = -Math.PI / 2;
  floor.position.y = FLOOR_Y;
  scene.add(floor);

  const wall = mesh(new THREE.PlaneGeometry(40, 14), solid(WALL, { roughness: 0.95, metalness: 0 }), { cast: false, receive: true });
  wall.position.set(0, FLOOR_Y + 7, WALL_Z);
  scene.add(wall);

  const molding = mesh(new THREE.ShapeGeometry(pointedArch(2.35, 3.4, 3.2, FLOOR_Y), 32), solid(0xc9dbec, { roughness: 0.9 }), { cast: false, receive: true });
  molding.position.z = WALL_Z + 0.005;
  const niche = mesh(new THREE.ShapeGeometry(pointedArch(2.15, 3.4, 2.95, FLOOR_Y), 32), solid(NICHE, { roughness: 0.95 }), { cast: false, receive: true });
  niche.position.z = WALL_Z + 0.01;
  scene.add(molding, niche);

  [-3.0, 3.0].forEach((x) => {
    const shaft = mesh(new THREE.BoxGeometry(0.5, 9, 0.35), solid(STONE, { roughness: 0.9 }), { receive: true });
    shaft.position.set(x, FLOOR_Y + 4.5, WALL_Z + 0.18);
    const plinth = mesh(new THREE.BoxGeometry(0.7, 0.5, 0.5), solid(0xd3e1ee, { roughness: 0.9 }), { receive: true });
    plinth.position.set(x, FLOOR_Y + 0.25, WALL_Z + 0.25);
    scene.add(shaft, plinth);
  });

  const stone = solid(STONE, { roughness: 0.88, metalness: 0 });
  const pedestal = mesh(new THREE.BoxGeometry(5.0, 1.32, 2.3), stone, { receive: true });
  pedestal.position.set(0, FLOOR_Y + 0.14 + 0.66, 0);
  const cap = mesh(new THREE.BoxGeometry(5.3, 0.1, 2.6), solid(0xedf3f9, { roughness: 0.8 }), { receive: true });
  cap.position.set(0, -0.05, 0);
  const foot = mesh(new THREE.BoxGeometry(5.3, 0.14, 2.6), solid(0xccdbe9, { roughness: 0.9 }), { receive: true });
  foot.position.set(0, FLOOR_Y + 0.07, 0);
  scene.add(pedestal, cap, foot);

  scene.add(new THREE.HemisphereLight(0xf7fbff, 0x7a8ea4, 1.0));
  const key = new THREE.DirectionalLight(0xfffaf3, 2.4);
  key.position.set(2.5, 7, 4.5);
  key.castShadow = true;
  key.shadow.mapSize.set(2048, 2048);
  key.shadow.camera.left = -5;
  key.shadow.camera.right = 5;
  key.shadow.camera.top = 5;
  key.shadow.camera.bottom = -5;
  key.shadow.bias = -0.0005;
  key.shadow.normalBias = 0.02;
  const fill = new THREE.DirectionalLight(0xdfe7ff, 0.6);
  fill.position.set(-4, 3, 3);
  scene.add(key, fill);
}

function hangPhoto(scene, url) {
  if (!url) return;
  new THREE.TextureLoader().load(url, (texture) => {
    texture.colorSpace = THREE.SRGBColorSpace;
    const aspect = texture.image.width / texture.image.height || 1;
    let width = 1.9;
    let height = width / aspect;
    if (height > 1.55) { height = 1.55; width = height * aspect; }
    const group = new THREE.Group();
    const frame = mesh(new THREE.BoxGeometry(width + 0.3, height + 0.3, 0.08), solid(0x1d2b3a, { roughness: 0.6 }));
    const mat = mesh(new THREE.BoxGeometry(width + 0.14, height + 0.14, 0.085), solid(0xf7fafd, { roughness: 0.95 }), { cast: false });
    const photo = mesh(new THREE.PlaneGeometry(width, height), new THREE.MeshStandardMaterial({ map: texture, roughness: 0.7 }), { cast: false });
    photo.position.z = 0.046;
    group.add(frame, mat, photo);
    group.position.set(0, 2.5, WALL_Z + 0.06);
    scene.add(group);
  }, undefined, () => { /* Undecodable here (e.g. HEIC): the exhibit works without it. */ });
}

// --- Viewer -----------------------------------------------------------------

function webglAvailable() {
  try {
    const canvas = document.createElement("canvas");
    return Boolean(window.WebGL2RenderingContext && canvas.getContext("webgl2"));
  } catch {
    return false;
  }
}

function disposeTree(root) {
  root.traverse((node) => {
    if (node.geometry) node.geometry.dispose();
    const materials = Array.isArray(node.material) ? node.material : node.material ? [node.material] : [];
    materials.forEach((material) => {
      if (material.map) material.map.dispose();
      material.dispose();
    });
  });
}

/**
 * @param {HTMLElement} container  Empty element; the canvas and callout go inside.
 * @param {{specimen: object, material: string, solutions: object[], photoUrl?: string}} options
 */
export function createViewer(container, { specimen, material, solutions, photoUrl }) {
  const lay = computeLayout(specimen);
  const look = MATERIAL_LOOKS[material] || MATERIAL_LOOKS.other;
  const info = { exaggerated: lay.exaggerated, dimensionsSource: specimen.dimensions_source };

  if (!webglAvailable()) {
    const note = document.createElement("div");
    note.className = "v3d-fallback";
    note.textContent = "3D view unavailable: this browser has WebGL turned off. The placard still describes each solution.";
    container.replaceChildren(note);
    return { select() {}, resetView() {}, dispose() {}, info };
  }

  const reducedMotion = window.matchMedia("(prefers-reduced-motion: reduce)").matches;
  const renderer = new THREE.WebGLRenderer({ antialias: true });
  renderer.setPixelRatio(Math.min(window.devicePixelRatio, 2));
  renderer.shadowMap.enabled = true;
  renderer.shadowMap.type = THREE.PCFSoftShadowMap;
  renderer.toneMapping = THREE.ACESFilmicToneMapping;
  renderer.toneMappingExposure = 1.05;
  renderer.setClearColor(WALL);
  const canvas = renderer.domElement;
  canvas.className = "v3d-canvas";
  canvas.setAttribute("role", "img");
  canvas.tabIndex = 0;

  const callout = document.createElement("div");
  callout.className = "v3d-callout";
  callout.setAttribute("aria-hidden", "true");
  const calloutText = document.createElement("span");
  callout.append(document.createElement("i"), calloutText);
  container.replaceChildren(canvas, callout);

  const scene = new THREE.Scene();
  scene.fog = new THREE.Fog(WALL, 14, 30);
  const camera = new THREE.PerspectiveCamera(34, 1, 0.1, 100);
  camera.position.copy(CAMERA_HOME);

  const controls = new OrbitControls(camera, canvas);
  controls.target.copy(TARGET_HOME);
  controls.enableDamping = true;
  controls.enablePan = false;
  controls.minDistance = 3.2;
  controls.maxDistance = 11;
  controls.minPolarAngle = 0.25;
  controls.maxPolarAngle = 1.48;
  controls.minAzimuthAngle = -1.15;
  controls.maxAzimuthAngle = 1.15;
  controls.autoRotate = !reducedMotion;
  controls.autoRotateSpeed = 0.55;
  controls.addEventListener("start", () => { controls.autoRotate = false; });
  controls.update();

  buildGallery(scene);
  hangPhoto(scene, photoUrl);

  let current = null;
  let tweenStart = 0;
  let frame = 0;

  function show(index) {
    if (current) {
      scene.remove(current.group);
      disposeTree(current.group);
    }
    const solution = index === null ? null : solutions[index];
    current = solution
      ? buildSolution(lay, look, solution)
      : buildAsFound(lay, look);
    current.group.traverse((node) => { if (node.isMesh && node.castShadow) node.receiveShadow = true; });
    scene.add(current.group);
    calloutText.textContent = current.label;
    callout.dataset.tone = current.tone;
    canvas.setAttribute("aria-label", solution
      ? `3D model of the part with solution ${index + 1} applied: ${current.label}. Drag to rotate, scroll to zoom.`
      : "3D model of the part as found, broken at the fracture. Drag to rotate, scroll to zoom.");
    tweenStart = performance.now();
    if (reducedMotion) current.animate(1);
  }

  function resize() {
    const width = container.clientWidth || 1;
    const height = container.clientHeight || 1;
    renderer.setSize(width, height, false);
    camera.aspect = width / height;
    // Keep the whole specimen in frame when the stage is narrow (phones).
    camera.fov = camera.aspect < 1.3 ? clamp((34 * 1.3) / camera.aspect, 34, 50) : 34;
    camera.updateProjectionMatrix();
  }
  const observer = new ResizeObserver(resize);
  observer.observe(container);
  resize();

  const projected = new THREE.Vector3();
  function loop(now) {
    frame = requestAnimationFrame(loop);
    if (current && !reducedMotion) {
      const progress = clamp((now - tweenStart) / TWEEN_MS, 0, 1);
      const eased = easeOut(progress);
      current.animate(eased);
      current.group.position.y = (1 - eased) * 0.18;
    }
    controls.update();
    renderer.render(scene, camera);
    if (current) {
      projected.copy(current.anchor).project(camera);
      const visible = projected.z < 1;
      callout.hidden = !visible;
      if (visible) {
        const x = ((projected.x + 1) / 2) * container.clientWidth;
        const y = ((1 - projected.y) / 2) * container.clientHeight;
        callout.style.transform = `translate(${x.toFixed(1)}px, ${y.toFixed(1)}px)`;
        // Keep the label inside the stage; the dot stays on the anchor.
        const labelWidth = calloutText.offsetWidth;
        const margin = 14;
        const left = clamp(x - labelWidth / 2, margin, Math.max(margin, container.clientWidth - labelWidth - margin));
        calloutText.style.left = `${(left - (x - labelWidth / 2)).toFixed(1)}px`;
      }
    }
  }

  show(null);
  frame = requestAnimationFrame(loop);

  return {
    info,
    select(index) {
      show(index === null || index === undefined || !solutions[index] ? null : index);
    },
    resetView() {
      camera.position.copy(CAMERA_HOME);
      controls.target.copy(TARGET_HOME);
      controls.update();
    },
    dispose() {
      cancelAnimationFrame(frame);
      observer.disconnect();
      controls.dispose();
      disposeTree(scene);
      renderer.dispose();
      container.replaceChildren();
    },
  };
}
