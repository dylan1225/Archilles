/**
 * "What could happen" and "the fix" in 3D, for a confirmed weakness on a ceiling or a wall.
 *
 * The room is the building's real scanned mesh. The scan's own panoramas are
 * projected onto it from the positions they were captured at, so the surface
 * you see is the surface that was photographed, flagged region included. Two
 * timelines play on that geometry, both starting from the scan as found:
 *
 *   failure  the stain spreads, the flagged section sags or blisters, lets go
 *            and falls to the floor the scan measured
 *   repair   the damaged section is cut out and a new board fitted, or the
 *            surface is treated in place, then sealed and painted
 *
 *   const viewer = await createForecastViewer(container, { meshUrl, scenario, evidenceUrl });
 *   viewer.setMode('failure' | 'repair');
 *   viewer.setProgress(2.5);        // one unit per stage, 0..viewer.stageCount
 *   viewer.play(onProgress);        // run the current timeline
 *   viewer.setView('room' | 'dollhouse');
 *   viewer.dispose();
 *
 * The scan's coordinates are Z-up metres. Everything lives in one group that
 * is rotated into three.js's Y-up frame, so scenario numbers are used as-is.
 */

import * as THREE from 'three';
import { OrbitControls } from 'three/addons/controls/OrbitControls.js';

const STAGE_SECONDS = [1.4, 3.2, 3.0, 2.4, 2.6];
const REPAIR_SECONDS = [1.2, 2.6, 2.8, 3.2]; // as scanned, then the three repair steps
const DETACH_SIM_SECONDS = 1.7;
const SAG_DEPTH = 0.13;
const GRID = 6; // fragments per side of the falling section
const G = 9.81;
const ORANGE = 0xff7a63; // Archilles weak-point coral (name kept)
const REPAIR_BLUE = 0x3d7fc4; // the change a repair makes, as in the Postmortem exhibit

const clamp01 = (value) => Math.min(Math.max(value, 0), 1);
const smooth = (t) => t * t * (3 - 2 * t);

/** Deterministic pseudo-random numbers, so the collapse looks the same each run. */
function seeded(seed) {
  let state = seed >>> 0;
  return () => {
    state = (state * 1664525 + 1013904223) >>> 0;
    return state / 4294967296;
  };
}

// Colour of one scan position's panorama along the ray to a point.
// Mirrors scan_geometry.face_direction on the server.
const projector = (id) => /* glsl */ `
  uniform sampler2D face${id}0, face${id}1, face${id}2, face${id}3, face${id}4, face${id}5;
  uniform vec3 panoPosition${id};
  uniform vec4 panoInverse${id};
  vec3 panorama${id}(vec3 point) {
    vec3 d = rotateBy(panoInverse${id}, point - panoPosition${id});
    vec3 m = abs(d);
    vec2 uv;
    if (m.z >= m.x && m.z >= m.y) {
      if (d.z > 0.0) { uv = vec2(-d.y, d.x) / d.z; return texture2D(face${id}0, vec2(uv.x, -uv.y) * 0.5 + 0.5).rgb; }
      uv = vec2(d.y, d.x) / d.z; return texture2D(face${id}5, vec2(uv.x, -uv.y) * 0.5 + 0.5).rgb;
    }
    if (m.x >= m.y) {
      if (d.x > 0.0) { uv = vec2(-d.y, -d.z) / d.x; return texture2D(face${id}1, vec2(uv.x, -uv.y) * 0.5 + 0.5).rgb; }
      uv = vec2(-d.y, d.z) / d.x; return texture2D(face${id}3, vec2(uv.x, -uv.y) * 0.5 + 0.5).rgb;
    }
    if (d.y < 0.0) { uv = vec2(d.x, d.z) / d.y; return texture2D(face${id}2, vec2(uv.x, -uv.y) * 0.5 + 0.5).rgb; }
    uv = vec2(d.x, -d.z) / d.y; return texture2D(face${id}4, vec2(uv.x, -uv.y) * 0.5 + 0.5).rgb;
  }
`;

const PROJECTION_GLSL = /* glsl */ `
  vec3 rotateBy(vec4 q, vec3 v) { return v + 2.0 * cross(q.xyz, cross(q.xyz, v) + q.w * v); }
  ${projector('A')}
  ${projector('B')}
  uniform float hasB;
  uniform vec3 boundsMin, boundsMax;

  bool insideRoom(vec3 point) {
    return all(greaterThanEqual(point, boundsMin)) && all(lessThanEqual(point, boundsMax));
  }

  float hash(vec2 p) { return fract(sin(dot(p, vec2(127.1, 311.7))) * 43758.5453); }
  float noise(vec2 p) {
    vec2 i = floor(p), f = fract(p);
    f = f * f * (3.0 - 2.0 * f);
    return mix(mix(hash(i), hash(i + vec2(1, 0)), f.x), mix(hash(i + vec2(0, 1)), hash(i + vec2(1, 1)), f.x), f.y);
  }

  // The flagged surface's own plane: a ceiling or a wall. Everything drawn on
  // it works in these two in-plane coordinates, measured from the region's centre.
  uniform vec3 zoneOrigin, zoneU, zoneV, zoneN;
  uniform float zoneRadius;
  uniform float stain;

  vec2 onPlane(vec3 point) { vec3 r = point - zoneOrigin; return vec2(dot(r, zoneU), dot(r, zoneV)); }

  // Brown water staining that creeps outward from the flagged region.
  vec3 stained(vec3 colour, vec2 xy) {
    float reach = zoneRadius * (0.55 + 1.25 * stain);
    float edge = noise(xy * 9.0) * 0.35 + noise(xy * 23.0) * 0.12;
    float d = length(xy) / reach + edge;
    float wet = stain * (1.0 - smoothstep(0.55, 1.0, d));
    float ring = stain * smoothstep(0.62, 0.86, d) * (1.0 - smoothstep(0.9, 1.04, d));
    colour = mix(colour, colour * vec3(0.74, 0.63, 0.44), wet * 0.75);
    return mix(colour, colour * vec3(0.55, 0.42, 0.26), ring * 0.7);
  }
`;

const HOUSE_VERTEX = /* glsl */ `
  varying vec3 vPoint;
  varying vec3 vNormalModel;
  void main() {
    vPoint = position;
    vNormalModel = normal;
    gl_Position = projectionMatrix * modelViewMatrix * vec4(position, 1.0);
  }
`;

const HOUSE_FRAGMENT = /* glsl */ `
  ${PROJECTION_GLSL}
  uniform vec2 zone[4];
  uniform float hole;
  uniform float mould;
  varying vec3 vPoint;
  varying vec3 vNormalModel;

  float side(vec2 a, vec2 b, vec2 p) { return (b.x - a.x) * (p.y - a.y) - (b.y - a.y) * (p.x - a.x); }
  bool insideZone(vec2 p) {
    float a = side(zone[0], zone[1], p), b = side(zone[1], zone[2], p), c = side(zone[2], zone[3], p), d = side(zone[3], zone[0], p);
    return (a >= 0.0 && b >= 0.0 && c >= 0.0 && d >= 0.0) || (a <= 0.0 && b <= 0.0 && c <= 0.0 && d <= 0.0);
  }

  void main() {
    vec3 n = normalize(vNormalModel);
    float light = 0.62 + 0.38 * max(dot(n, normalize(vec3(0.35, 0.25, 0.9))), 0.0);
    vec3 colour = vec3(0.89, 0.87, 0.82) * light;
    if (insideRoom(vPoint)) {
      // How squarely each scan position sees this surface. The finding's own
      // position (A) wins where it has a fair view; the room's second position
      // fills in what A could not see.
      float seenA = dot(n, normalize(panoPositionA - vPoint));
      float seenB = hasB > 0.5 ? dot(n, normalize(panoPositionB - vPoint)) : -1.0;
      if (seenA > 0.12 || (seenA > 0.0 && seenA >= seenB)) colour = panoramaA(vPoint);
      else if (seenB > 0.0) colour = panoramaB(vPoint);
    }
    // The flagged surface: facing the same way as the region, and in its plane.
    bool flagged = dot(n, zoneN) > 0.6 && abs(dot(vPoint - zoneOrigin, zoneN)) < 0.3;
    if (flagged) {
      vec2 q = onPlane(vPoint);
      colour = stained(colour, q);
      if (mould > 0.0) {
        float d = length(q) / (zoneRadius * 2.4);
        float speck = smoothstep(0.72, 0.9, noise(q * 70.0) * 0.7 + noise(q * 31.0) * 0.5);
        colour = mix(colour, vec3(0.12, 0.13, 0.1), mould * speck * (1.0 - smoothstep(0.5, 1.0, d)) * 0.8);
      }
      if (hole > 0.5 && insideZone(q)) {
        // The cavity behind: dark, with framing members crossing it.
        float joist = smoothstep(0.42, 0.5, abs(fract(q.x / 0.41) - 0.5));
        colour = mix(vec3(0.05, 0.04, 0.035), vec3(0.34, 0.25, 0.16), joist * 0.85);
      }
    }
    gl_FragColor = vec4(colour, 1.0);
    #include <colorspace_fragment>
  }
`;

// The falling section. `rest` is where each vertex sits on the intact ceiling,
// so the photographed surface stays attached to the material as it moves.
const PIECE_VERTEX = /* glsl */ `
  attribute vec3 rest;
  attribute vec2 cell;
  uniform float sag;
  uniform vec3 sagDirection;
  varying vec3 vRest;
  varying vec2 vCell;
  void main() {
    vRest = rest;
    vCell = cell;
    vec3 p = position;
    float dome = pow(sin(3.14159 * cell.x) * sin(3.14159 * cell.y), 0.8);
    p += sagDirection * sag * dome; // into the room: down from a ceiling, out from a wall
    gl_Position = projectionMatrix * modelViewMatrix * vec4(p, 1.0);
  }
`;

const PIECE_FRAGMENT = /* glsl */ `
  ${PROJECTION_GLSL}
  uniform float crack;
  uniform float underside;
  varying vec3 vRest;
  varying vec2 vCell;
  void main() {
    vec3 colour;
    if (gl_FrontFacing == (underside > 0.5)) {
      vec2 q = onPlane(vRest);
      colour = stained(panoramaA(vRest), q);
      // Cracks open along the edge of the section as it sags.
      float rim = min(min(vCell.x, 1.0 - vCell.x), min(vCell.y, 1.0 - vCell.y));
      float line = 1.0 - smoothstep(0.0, 0.035 + 0.02 * noise(q * 40.0), rim);
      colour = mix(colour, vec3(0.08, 0.07, 0.06), line * crack * 0.85);
    } else {
      // Broken back of the finish: pale, chalky, a little uneven.
      colour = vec3(0.78, 0.75, 0.69) * (0.8 + 0.2 * noise(onPlane(vRest) * 60.0));
    }
    gl_FragColor = vec4(colour, 1.0);
    #include <colorspace_fragment>
  }
`;

// The repair, drawn over the flagged region. `repair` runs 0..3, one unit per
// step. With `cutOut` a new board is fitted into the opening; otherwise the
// surface is treated in place (taped, cleaned or checked), sealed and painted.
const PATCH_VERTEX = /* glsl */ `
  uniform float lift;
  uniform vec3 liftDirection;
  varying vec3 vPoint;
  varying vec2 vCell;
  void main() {
    vPoint = position;
    vCell = uv;
    gl_Position = projectionMatrix * modelViewMatrix * vec4(position + liftDirection * lift, 1.0);
  }
`;

const PATCH_FRAGMENT = /* glsl */ `
  ${PROJECTION_GLSL}
  uniform float repair;
  uniform float cutOut;
  uniform float treatment; // 0 tape a crack, 1 scrub clean, 2 check for damp
  uniform float longAxis;  // 0: the region is longer along u, 1: along v
  uniform vec3 paintPoints[4];
  varying vec3 vPoint;
  varying vec2 vCell;
  void main() {
    vec2 q = onPlane(vPoint);
    float rim = min(min(vCell.x, 1.0 - vCell.x), min(vCell.y, 1.0 - vCell.y));
    float grain = noise(q * 55.0);
    vec3 paint = (panoramaA(paintPoints[0]) + panoramaA(paintPoints[1]) + panoramaA(paintPoints[2]) + panoramaA(paintPoints[3])) * 0.25;
    vec3 primer = vec3(0.95, 0.95, 0.93) * (0.97 + 0.03 * grain);
    vec3 compound = vec3(0.9, 0.89, 0.86) * (0.95 + 0.05 * grain);
    float sweep = longAxis > 0.5 ? vCell.y : vCell.x; // work proceeds along the region
    float across = longAxis > 0.5 ? vCell.x : vCell.y;
    vec3 colour = primer;
    float alpha = 0.0;
    if (cutOut > 0.5) {
      // Step 2: a new board. Step 3: taped joints, primer, then paint.
      vec3 board = vec3(0.8, 0.8, 0.76) * (0.94 + 0.06 * grain);
      float screw = 1.0 - smoothstep(0.012, 0.02, length(fract(vCell * 4.0) - 0.5) * 0.25);
      board = mix(board, vec3(0.35), screw * 0.8 * step(rim, 0.14));
      float fitted = smoothstep(0.0, 0.35, repair - 1.0);
      float step3 = clamp(repair - 2.0, 0.0, 1.0);
      float taped = smoothstep(0.0, 0.3, step3) * (1.0 - smoothstep(0.07, 0.11, rim));
      colour = mix(board, compound, taped);
      colour = mix(colour, primer, smoothstep(sweep - 0.08, sweep + 0.08, (step3 - 0.3) * 4.0));
      colour = mix(colour, paint, smoothstep(sweep - 0.08, sweep + 0.08, (step3 - 0.62) * 3.2));
      alpha = fitted;
    } else {
      float feather = smoothstep(0.0, 0.14, rim);
      float step1 = clamp(repair, 0.0, 1.0), step2 = clamp(repair - 1.0, 0.0, 1.0), step3 = clamp(repair - 2.0, 0.0, 1.0);
      float done1 = smoothstep(sweep - 0.1, sweep + 0.1, step1 * 1.3 - 0.1);
      if (treatment < 0.5) {
        // Mesh tape along the crack.
        float band = 1.0 - smoothstep(0.1, 0.14, abs(across - 0.5));
        float weave = 0.82 + 0.18 * step(0.5, fract(q.x * 90.0)) * step(0.5, fract(q.y * 90.0));
        colour = vec3(0.88, 0.87, 0.8) * weave;
        alpha = band * done1 * 0.92;
      } else if (treatment < 1.5) {
        // Scrubbed back to a clean surface.
        colour = mix(paint, vec3(0.86, 0.9, 0.93), 0.25);
        alpha = feather * done1 * 0.8;
      } else {
        // A moisture check sweeping the surface: a reading, not a coating.
        float scan = 1.0 - smoothstep(0.0, 0.06, abs(sweep - step1));
        colour = vec3(0.35, 0.62, 0.86);
        alpha = feather * scan * 0.55 * step(step1, 0.999);
      }
      float done2 = smoothstep(sweep - 0.1, sweep + 0.1, step2 * 1.3 - 0.1);
      vec3 coat = treatment < 0.5 ? compound : primer;
      colour = mix(colour, coat, done2);
      alpha = mix(alpha, feather * 0.96, done2);
      float done3 = smoothstep(sweep - 0.1, sweep + 0.1, step3 * 1.3 - 0.1);
      colour = mix(colour, paint, done3);
      alpha = mix(alpha, feather, done3);
    }
    gl_FragColor = vec4(colour, alpha);
    #include <colorspace_fragment>
  }
`;

function loadTexture(url) {
  return new Promise((resolve, reject) => {
    new THREE.TextureLoader().load(url, (texture) => {
      texture.colorSpace = THREE.SRGBColorSpace;
      texture.wrapS = texture.wrapT = THREE.ClampToEdgeWrapping;
      texture.minFilter = THREE.LinearFilter;
      texture.generateMipmaps = false;
      resolve(texture);
    }, undefined, () => reject(new Error('A scan image for the 3D view could not be loaded.')));
  });
}

/** Point on the flagged quad for fractions (u, v) across it. */
function onQuad(corners, u, v) {
  const mix = (a, b, t) => a.map((value, k) => value + (b[k] - value) * t);
  return mix(mix(corners[0], corners[1], u), mix(corners[3], corners[2], u), v);
}

export async function createForecastViewer(container, { meshUrl, scenario, evidenceUrl }) {
  const projectors = scenario.projectors.slice(0, 2);
  const [meshResponse, ...textures] = await Promise.all([
    fetch(meshUrl),
    ...projectors.flatMap((item) => item.faces.map((viewId) => loadTexture(evidenceUrl(viewId)))),
  ]);
  if (!meshResponse.ok) throw new Error('The 3D mesh for this building is not available.');
  const meshData = await meshResponse.json();

  const zone = scenario.zone;
  const centre = new THREE.Vector3(...zone.center);
  const corners = zone.corners;
  const floorZ = zone.floor_z;
  const isWall = zone.surface === 'wall';
  // The region's plane. Its normal points into the room, toward the scan
  // position that photographed it: down from a ceiling, out from a wall.
  const normal = new THREE.Vector3(...(zone.normal || [0, 0, -1])).normalize();
  if (normal.dot(new THREE.Vector3(...projectors[0].position).sub(centre)) < 0) normal.negate();
  const corner3 = (c) => new THREE.Vector3(c[0], c[1], c[2]);
  const axisU = corner3(corners[1]).sub(corner3(corners[0]));
  axisU.addScaledVector(normal, -axisU.dot(normal)).normalize();
  const axisV = new THREE.Vector3().crossVectors(normal, axisU).normalize();
  const planar = (c) => {
    const r = corner3(c).sub(centre);
    return new THREE.Vector2(r.dot(axisU), r.dot(axisV));
  };
  const radius = Math.max(...corners.map((c) => planar(c).length()));
  const bounds = scenario.bounds
    || { min: [centre.x - 2, centre.y - 2, floorZ - 0.2], max: [centre.x + 2, centre.y + 2, centre.z + (isWall ? 1 : 0.2)] };
  const plan = scenario.repair_plan || {};
  const cutOut = plan.cut_out === true;
  const dry = scenario.dry === true; // a crack: no water is assumed, so none is drawn

  const shared = { hasB: { value: projectors.length > 1 ? 1 : 0 } };
  ['A', 'B'].forEach((id, slot) => {
    // With one scan position, B repeats A and is never sampled.
    const item = projectors[slot] || projectors[0];
    const offset = (projectors[slot] ? slot : 0) * 6;
    const q = item.quaternion;
    for (let face = 0; face < 6; face++) shared[`face${id}${face}`] = { value: textures[offset + face] };
    shared[`panoPosition${id}`] = { value: new THREE.Vector3(...item.position) };
    shared[`panoInverse${id}`] = { value: new THREE.Vector4(-q[0], -q[1], -q[2], q[3]) };
  });
  Object.assign(shared, {
    boundsMin: { value: new THREE.Vector3(...bounds.min) },
    boundsMax: { value: new THREE.Vector3(...bounds.max) },
    zoneOrigin: { value: centre.clone() },
    zoneU: { value: axisU },
    zoneV: { value: axisV },
    zoneN: { value: normal },
    zoneRadius: { value: radius },
    stain: { value: 0 },
  });

  const renderer = new THREE.WebGLRenderer({ antialias: true, preserveDrawingBuffer: true });
  renderer.setPixelRatio(Math.min(window.devicePixelRatio || 1, 2));
  renderer.setClearColor(0x0e1c2a);
  container.append(renderer.domElement);
  renderer.domElement.style.cssText = 'display:block;width:100%;height:100%;touch-action:none';

  const scene = new THREE.Scene();
  const world = new THREE.Group();
  world.rotation.x = -Math.PI / 2; // scan Z-up -> three.js Y-up
  scene.add(world);
  const camera = new THREE.PerspectiveCamera(68, 1, 0.05, 80);
  const controls = new OrbitControls(camera, renderer.domElement);
  controls.enableDamping = true;
  controls.dampingFactor = 0.08;
  controls.minDistance = 0.4;
  controls.maxDistance = 30;

  // --- The building ---------------------------------------------------------
  const houseGeometry = new THREE.BufferGeometry();
  houseGeometry.setAttribute('position', new THREE.Float32BufferAttribute(meshData.positions, 3));
  houseGeometry.setIndex(meshData.indices);
  const flatHouse = houseGeometry.toNonIndexed();
  flatHouse.computeVertexNormals();
  houseGeometry.dispose();
  const houseMaterial = new THREE.ShaderMaterial({
    uniforms: {
      ...shared,
      zone: { value: corners.map(planar) },
      hole: { value: 0 },
      mould: { value: 0 },
    },
    vertexShader: HOUSE_VERTEX,
    fragmentShader: HOUSE_FRAGMENT,
    side: THREE.FrontSide, // room-facing triangles only: from outside you see in
  });
  const house = new THREE.Mesh(flatHouse, houseMaterial);
  world.add(house);

  // --- The flagged ceiling section, as fragments ----------------------------
  const random = seeded(20261114);
  const lattice = [];
  for (let j = 0; j <= GRID; j++) {
    for (let i = 0; i <= GRID; i++) {
      const edge = i === 0 || j === 0 || i === GRID || j === GRID;
      const jitter = edge ? 0 : 0.32 / GRID;
      lattice.push([i / GRID + (random() - 0.5) * 2 * jitter, j / GRID + (random() - 0.5) * 2 * jitter]);
    }
  }
  // Which triangle side faces into the room depends on the box's corner order.
  const edgeU = corner3(corners[1]).sub(corner3(corners[0]));
  const edgeV = corner3(corners[3]).sub(corner3(corners[0]));
  const windingFacesRoom = new THREE.Vector3().crossVectors(edgeU, edgeV).dot(normal) > 0;
  const tangent = new THREE.Vector2(-normal.y, normal.x); // along a wall, level with the floor
  const pieceMaterial = new THREE.ShaderMaterial({
    uniforms: { ...shared, sag: { value: 0 }, sagDirection: { value: normal }, crack: { value: 0 }, underside: { value: windingFacesRoom ? 1 : 0 } },
    vertexShader: PIECE_VERTEX,
    fragmentShader: PIECE_FRAGMENT,
    side: THREE.DoubleSide,
  });
  const drop = centre.z - floorZ;
  const pieces = [];
  const section = new THREE.Group();
  world.add(section);
  for (let j = 0; j < GRID; j++) {
    for (let i = 0; i < GRID; i++) {
      const cells = [lattice[j * (GRID + 1) + i], lattice[j * (GRID + 1) + i + 1], lattice[(j + 1) * (GRID + 1) + i + 1], lattice[(j + 1) * (GRID + 1) + i]];
      const points = cells.map(([u, v]) => onQuad(corners, u, v));
      const middle = points.reduce((sum, p) => sum.map((value, k) => value + p[k] / 4), [0, 0, 0]);
      const local = [];
      const rest = [];
      const cell = [];
      for (const index of [0, 1, 2, 0, 2, 3]) {
        // The section sits 4 mm proud of the scanned surface, so it draws over it.
        local.push(points[index][0] - middle[0], points[index][1] - middle[1], points[index][2] - middle[2]);
        rest.push(...points[index]);
        cell.push(...cells[index]);
      }
      const geometry = new THREE.BufferGeometry();
      geometry.setAttribute('position', new THREE.Float32BufferAttribute(local, 3));
      geometry.setAttribute('rest', new THREE.Float32BufferAttribute(rest, 3));
      geometry.setAttribute('cell', new THREE.Float32BufferAttribute(cell, 2));
      const mesh = new THREE.Mesh(geometry, pieceMaterial);
      mesh.frustumCulled = false;
      section.add(mesh);
      const fromCentre = Math.hypot((i + 0.5) / GRID - 0.5, (j + 0.5) / GRID - 0.5);
      const size = corner3(points[1]).distanceTo(corner3(points[0]));
      const delay = fromCentre * 0.75 + random() * 0.12; // the belly lets go first
      const [driftA, driftB] = [random(), random()];
      pieces.push({
        mesh,
        home: new THREE.Vector3(middle[0], middle[1], middle[2]).addScaledVector(normal, 0.004),
        delay,
        // From a ceiling pieces scatter any way; from a wall they fall clear of it.
        drift: isWall
          ? new THREE.Vector2(normal.x, normal.y).multiplyScalar(0.25 + driftA * 0.5).addScaledVector(tangent, (driftB - 0.5) * 0.4)
          : new THREE.Vector2((driftA - 0.5) * 0.7, (driftB - 0.5) * 0.7),
        axis: new THREE.Vector3(random() - 0.5, random() - 0.5, (random() - 0.5) * 0.4).normalize(),
        spin: 2.5 + random() * 5,
        restYaw: random() * Math.PI * 2,
        restTilt: (random() - 0.5) * 0.5,
        restHeight: 0.012 + random() * size * 0.25,
      });
    }
  }

  // --- Outline of the flagged region ----------------------------------------
  const outlinePoints = [...corners, corners[0]].map((c) => corner3(c).addScaledVector(normal, 0.012));
  const outline = new THREE.Line(
    new THREE.BufferGeometry().setFromPoints(outlinePoints),
    new THREE.LineBasicMaterial({ color: ORANGE, transparent: true, depthTest: false }),
  );
  outline.renderOrder = 5;
  world.add(outline);

  // --- Drips and the wet patch on the floor ----------------------------------
  const DRIPS = 7;
  const dripGeometry = new THREE.BufferGeometry();
  dripGeometry.setAttribute('position', new THREE.Float32BufferAttribute(new Float32Array(DRIPS * 3), 3));
  const drips = new THREE.Points(dripGeometry, new THREE.PointsMaterial({ color: 0xbfd4dc, size: 0.035, transparent: true, opacity: 0.9 }));
  drips.frustumCulled = false;
  world.add(drips);
  // Drips leave a ceiling anywhere in the region, and a wall from the region's lower edge.
  const lowest = [...corners].sort((a, b) => a[2] - b[2]).slice(0, 2);
  const dripSeeds = Array.from({ length: DRIPS }, () => {
    const [a, b, phase] = [random(), random(), random()];
    if (!isWall) return { x: centre.x + (a - 0.5) * radius * 0.9, y: centre.y + (b - 0.5) * radius * 0.9, z: centre.z - 0.02, phase };
    return {
      x: lowest[0][0] + (lowest[1][0] - lowest[0][0]) * a + normal.x * 0.02,
      y: lowest[0][1] + (lowest[1][1] - lowest[0][1]) * a + normal.y * 0.02,
      z: lowest[0][2] + (lowest[1][2] - lowest[0][2]) * a,
      phase,
    };
  });
  const puddle = new THREE.Mesh(
    new THREE.CircleGeometry(1, 40),
    new THREE.MeshBasicMaterial({ color: 0x2c2a24, transparent: true, opacity: 0, depthWrite: false }),
  );
  const puddleReach = isWall ? Math.min(radius, 0.35) : radius;
  puddle.position.set(centre.x + (isWall ? normal.x * 0.2 : 0), centre.y + (isWall ? normal.y * 0.2 : 0), floorZ + 0.012);
  world.add(puddle);

  // --- The repair, drawn over the region ---------------------------------------
  const patchGeometry = new THREE.BufferGeometry();
  patchGeometry.setAttribute('position', new THREE.Float32BufferAttribute(corners.flatMap((c) => corner3(c).addScaledVector(normal, 0.008).toArray()), 3));
  patchGeometry.setAttribute('uv', new THREE.Float32BufferAttribute([0, 0, 1, 0, 1, 1, 0, 1], 2));
  patchGeometry.setIndex([0, 1, 2, 0, 2, 3]);
  const patchMaterial = new THREE.ShaderMaterial({
    uniforms: {
      ...shared,
      repair: { value: 0 },
      cutOut: { value: cutOut ? 1 : 0 },
      treatment: { value: { crack: 0, mould: 1, stain: 2 }[plan.kind] ?? 1 },
      longAxis: { value: edgeU.length() >= edgeV.length() ? 0 : 1 },
      // Paint is matched to the surface itself: the average of four points just inside the region's corners.
      paintPoints: { value: corners.map((c) => corner3(c).lerp(centre, 0.12)) },
      lift: { value: 0 },
      liftDirection: { value: normal },
    },
    vertexShader: PATCH_VERTEX,
    fragmentShader: PATCH_FRAGMENT,
    side: THREE.DoubleSide,
    transparent: true,
    depthWrite: false,
  });
  const patch = new THREE.Mesh(patchGeometry, patchMaterial);
  patch.frustumCulled = false;
  patch.renderOrder = 3;
  patch.visible = false;
  world.add(patch);

  const roomCentre = new THREE.Vector2((bounds.min[0] + bounds.max[0]) / 2, (bounds.min[1] + bounds.max[1]) / 2);

  // --- Timeline ---------------------------------------------------------------
  let progress = 0;
  let clock = 0;
  let mode = 'failure'; // or 'repair'
  const durations = () => (mode === 'repair' ? REPAIR_SECONDS : STAGE_SECONDS);

  function apply() {
    // Each timeline starts from the scan as found. Playing the repair leaves
    // the failure untouched at its first frame, and the other way round.
    const repairing = mode === 'repair';
    const at = repairing ? 0 : progress;
    const repair = repairing ? Math.min(Math.max(progress - 1, 0), 3) : 0;
    const stageOf = (k) => clamp01(at - k);
    const saturation = smooth(stageOf(1));
    const sagging = smooth(stageOf(2));
    const detach = stageOf(3);
    const after = smooth(stageOf(4));
    shared.stain.value = dry ? 0 : saturation;
    pieceMaterial.uniforms.sag.value = SAG_DEPTH * sagging;
    // In a dry scenario the second stage is the crack opening, not a stain spreading.
    pieceMaterial.uniforms.crack.value = dry ? Math.max(saturation, sagging) : sagging;
    houseMaterial.uniforms.hole.value = at > 3 || (repairing && cutOut && repair > 0.04) ? 1 : 0;
    houseMaterial.uniforms.mould.value = dry ? 0 : after;
    outline.material.color.setHex(repairing ? REPAIR_BLUE : ORANGE);
    outline.material.opacity = at > 3 ? 0.4 : 0.78 + 0.22 * Math.sin(clock * 3.2);

    patch.visible = repairing;
    patchMaterial.uniforms.repair.value = repair;
    patchMaterial.uniforms.lift.value = cutOut ? (1 - smooth(clamp01((repair - 1) / 0.8))) * 0.4 : 0;

    const time = detach * DETACH_SIM_SECONDS;
    for (const piece of pieces) {
      const t = Math.max(time - piece.delay, 0);
      const fallTime = Math.sqrt((2 * Math.max(piece.home.z - floorZ - piece.restHeight, 0.01)) / G);
      const { mesh } = piece;
      mesh.scale.setScalar(1);
      if (repairing && cutOut) {
        // Cutting out: each fragment is lifted clear and taken away.
        const taken = smooth(clamp01(repair * 1.8 - piece.delay));
        mesh.position.copy(piece.home).addScaledVector(normal, 0.3 * taken);
        mesh.quaternion.identity();
        mesh.scale.setScalar(Math.max(1 - taken, 0.0001));
        continue;
      }
      if (at <= 3 || t === 0) {
        mesh.position.copy(piece.home);
        mesh.quaternion.identity();
        continue;
      }
      const falling = Math.min(t, fallTime);
      mesh.position.set(
        piece.home.x + piece.drift.x * falling,
        piece.home.y + piece.drift.y * falling,
        piece.home.z - 0.5 * G * falling * falling,
      );
      if (t < fallTime) {
        mesh.quaternion.setFromAxisAngle(piece.axis, piece.spin * t);
      } else {
        mesh.position.z = floorZ + piece.restHeight;
        mesh.quaternion.setFromEuler(new THREE.Euler(piece.restTilt, piece.restTilt * 0.6, piece.restYaw));
      }
    }
    // Sag is drawn in the shader; once pieces separate it is released.
    if (at > 3) pieceMaterial.uniforms.sag.value = SAG_DEPTH * Math.max(1 - detach * 6, 0);

    const dripping = !dry && at > 1.15 && at < 3.05;
    drips.visible = dripping;
    if (dripping) {
      const position = dripGeometry.attributes.position;
      dripSeeds.forEach((seed, index) => {
        const cycle = (clock * 0.55 + seed.phase) % 1;
        const fall = Math.min(0.5 * G * (cycle * 1.4) ** 2, Math.max(seed.z - floorZ, 0));
        position.setXYZ(index, seed.x, seed.y, seed.z - fall);
      });
      position.needsUpdate = true;
    }
    const wet = dry ? 0 : clamp01(at - 1.2) * 0.6;
    puddle.material.opacity = Math.min(wet, 0.38);
    puddle.scale.setScalar(Math.max(puddleReach * (0.35 + 0.9 * clamp01((at - 1.2) / 1.8)), 0.01));
  }

  // --- Camera ------------------------------------------------------------------
  const toThree = (x, y, z) => new THREE.Vector3(x, z, -y);
  let view = 'room';
  let tween = null;

  // Distance from one point toward another before the building gets in the way.
  const sight = new THREE.Raycaster();
  function clearPath(from, to) {
    scene.updateMatrixWorld(true);
    const direction = to.clone().sub(from);
    const length = direction.length();
    sight.set(from, direction.normalize());
    sight.far = length;
    houseMaterial.side = THREE.DoubleSide;
    const hit = sight.intersectObject(house, false)[0];
    houseMaterial.side = THREE.FrontSide;
    return hit ? hit.distance : length;
  }

  function viewPose(name) {
    if (name === 'dollhouse') {
      const span = Math.max(bounds.max[0] - bounds.min[0], bounds.max[1] - bounds.min[1]);
      const out = new THREE.Vector2(roomCentre.x - centre.x, roomCentre.y - centre.y);
      if (out.length() < 0.2) out.set(-1, -1);
      out.setLength(span * 0.95);
      return {
        position: toThree(roomCentre.x + out.x, roomCentre.y + out.y, bounds.max[2] + span * 1.15),
        target: toThree(centre.x, centre.y, floorZ + drop * 0.45),
      };
    }
    // Stand in the corner of the room farthest from the flagged region.
    const inset = 0.55;
    const corner = [
      Math.abs(bounds.min[0] - centre.x) > Math.abs(bounds.max[0] - centre.x) ? bounds.min[0] + inset : bounds.max[0] - inset,
      Math.abs(bounds.min[1] - centre.y) > Math.abs(bounds.max[1] - centre.y) ? bounds.min[1] + inset : bounds.max[1] - inset,
    ];
    const fromCorner = toThree(corner[0], corner[1], floorZ + 1.25);
    const regionPoint = toThree(centre.x + normal.x * 0.03, centre.y + normal.y * 0.03, centre.z + normal.z * 0.03);
    const level = (eye) => Math.hypot(eye.x - regionPoint.x, eye.z - regionPoint.z);
    // How much of the flagged surface a viewpoint takes in: the region's centre
    // and eight points around it. Through a doorway, or from the next room, most
    // of them are hidden.
    const around = [[0, 0], [1, 0], [-1, 0], [0, 1], [0, -1], [0.7, 0.7], [-0.7, 0.7], [0.7, -0.7], [-0.7, -0.7]].map(([u, v]) => {
      const point = centre.clone().addScaledVector(axisU, u * 0.9).addScaledVector(axisV, v * 0.9).addScaledVector(normal, 0.03);
      return toThree(point.x, point.y, point.z);
    });
    const openness = (eye) => around.reduce((sum, point, index) => (
      sum + (clearPath(eye, point) >= eye.distanceTo(point) - 0.05 ? (index === 0 ? 4 : 1) : 0)
    ), 0);
    // Candidates: the far corner of the room, and each scan position in it. The
    // room's extent can reach past a wall (a small room, an open door), which can
    // put the corner outside. A scan position is where someone stood; one directly
    // under the region also offers a step back.
    const candidates = [fromCorner];
    for (const item of projectors) {
      const scan = item.position;
      const eye = toThree(scan[0], scan[1], Math.max(scan[2], floorZ + 1.1));
      candidates.push(eye);
      if (level(eye) >= 0.8) continue;
      const away = new THREE.Vector3(scan[0] - centre.x, 0, -(scan[1] - centre.y));
      if (away.length() < 0.25) away.set(-normal.x, 0, normal.y);
      if (away.length() < 0.25) away.set(1, 0, 0);
      away.normalize();
      const room = clearPath(eye, eye.clone().addScaledVector(away, 6));
      candidates.push(eye.clone().addScaledVector(away, Math.min(Math.max(room - 0.4, 0), 1.2)));
    }
    // Most surface in view wins; then a comfortable distance. Ties go to the corner.
    const score = new Map(candidates.map((eye, index) => [eye, openness(eye) * 2 - Math.abs(level(eye) - 2.2) - index * 0.01]));
    candidates.sort((x, y) => score.get(y) - score.get(x));
    // Close under a ceiling region, look further up so it stays in frame.
    const near = isWall ? 0 : clamp01((2.4 - level(candidates[0])) / 1.4);
    return { position: candidates[0], target: toThree(centre.x, centre.y, isWall ? centre.z : floorZ + drop * (0.62 + 0.33 * near)) };
  }

  function setView(name, immediate = false) {
    view = name === 'dollhouse' ? 'dollhouse' : 'room';
    const pose = viewPose(view);
    if (immediate) {
      camera.position.copy(pose.position);
      controls.target.copy(pose.target);
      tween = null;
    } else {
      tween = { from: camera.position.clone(), fromTarget: controls.target.clone(), ...pose, start: performance.now() };
    }
  }

  // --- Loop ----------------------------------------------------------------------
  let playing = null;
  let frame = 0;
  let disposed = false;
  let last = performance.now();

  function resize() {
    const width = Math.max(container.clientWidth, 1);
    const height = Math.max(container.clientHeight, 1);
    renderer.setSize(width, height, false);
    camera.aspect = width / height;
    camera.updateProjectionMatrix();
  }
  const observer = new ResizeObserver(resize);
  observer.observe(container);

  function tick(now) {
    if (disposed) return;
    const dt = Math.min((now - last) / 1000, 0.1);
    last = now;
    clock += dt;
    if (playing) {
      const seconds = durations();
      const stage = Math.min(Math.floor(progress), seconds.length - 1);
      progress = Math.min(progress + dt / seconds[stage], seconds.length);
      playing.onProgress?.(progress);
      if (progress >= seconds.length) {
        const done = playing.onDone;
        playing = null;
        done?.();
      }
    }
    if (tween) {
      const t = smooth(clamp01((now - tween.start) / 900));
      camera.position.lerpVectors(tween.from, tween.position, t);
      controls.target.lerpVectors(tween.fromTarget, tween.target, t);
      if (t >= 1) tween = null;
    }
    apply();
    controls.update();
    renderer.render(scene, camera);
    frame = requestAnimationFrame(tick);
  }

  resize();
  setView('room', true);
  apply();
  frame = requestAnimationFrame(tick);

  return {
    get stageCount() { return durations().length; },
    get progress() { return progress; },
    get mode() { return mode; },
    /** 'failure' plays what could happen; 'repair' plays the fix. Both start from the scan as found. */
    setMode(name) {
      mode = name === 'repair' ? 'repair' : 'failure';
      playing = null;
      progress = 0;
    },
    get playing() { return Boolean(playing); },
    setProgress(value) {
      playing = null;
      progress = Math.min(Math.max(Number(value) || 0, 0), durations().length);
    },
    play(onProgress, onDone) {
      if (progress >= durations().length - 0.001) progress = 0;
      playing = { onProgress, onDone };
    },
    pause() { playing = null; },
    setView,
    get view() { return view; },
    dispose() {
      disposed = true;
      cancelAnimationFrame(frame);
      observer.disconnect();
      controls.dispose();
      scene.traverse((object) => {
        object.geometry?.dispose();
        if (object.material) (Array.isArray(object.material) ? object.material : [object.material]).forEach((m) => m.dispose());
      });
      textures.forEach((texture) => texture.dispose());
      renderer.dispose();
      renderer.domElement.remove();
    },
  };
}
