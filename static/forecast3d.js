/**
 * "What could happen" 3D view for a confirmed ceiling weakness.
 *
 * The room is the building's real scanned mesh. The scan's own panoramas are
 * projected onto it from the positions they were captured at, so the ceiling
 * you see is the ceiling that was photographed, flagged region included. The
 * failure scenario then plays on that geometry: the stain spreads, the flagged
 * section sags, lets go and falls to the floor the scan measured.
 *
 *   const viewer = await createForecastViewer(container, { meshUrl, scenario, evidenceUrl });
 *   viewer.setProgress(2.5);        // 0..5, one unit per stage
 *   viewer.play(onProgress);        // run the whole timeline
 *   viewer.setView('room' | 'dollhouse');
 *   viewer.dispose();
 *
 * The scan's coordinates are Z-up metres. Everything lives in one group that
 * is rotated into three.js's Y-up frame, so scenario numbers are used as-is.
 */

import * as THREE from 'three';
import { OrbitControls } from 'three/addons/controls/OrbitControls.js';

const STAGE_SECONDS = [1.4, 3.2, 3.0, 2.4, 2.6];
const DETACH_SIM_SECONDS = 1.7;
const SAG_DEPTH = 0.13;
const GRID = 6; // fragments per side of the falling section
const G = 9.81;
const ORANGE = 0xe77500;

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

  uniform vec2 zoneCentre;
  uniform float zoneRadius;
  uniform float stain;

  // Brown water staining that creeps outward from the flagged region.
  vec3 stained(vec3 colour, vec2 xy) {
    float reach = zoneRadius * (0.55 + 1.25 * stain);
    float edge = noise(xy * 9.0) * 0.35 + noise(xy * 23.0) * 0.12;
    float d = distance(xy, zoneCentre) / reach + edge;
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
  uniform float ceilingZ;
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
    bool ceiling = n.z < -0.6 && abs(vPoint.z - ceilingZ) < 0.3;
    if (ceiling) {
      colour = stained(colour, vPoint.xy);
      if (mould > 0.0) {
        float d = distance(vPoint.xy, zoneCentre) / (zoneRadius * 2.4);
        float speck = smoothstep(0.72, 0.9, noise(vPoint.xy * 70.0) * 0.7 + noise(vPoint.xy * 31.0) * 0.5);
        colour = mix(colour, vec3(0.12, 0.13, 0.1), mould * speck * (1.0 - smoothstep(0.5, 1.0, d)) * 0.8);
      }
      if (hole > 0.5 && insideZone(vPoint.xy)) {
        // The cavity above: dark, with framing members crossing it.
        float joist = smoothstep(0.42, 0.5, abs(fract(vPoint.x / 0.41) - 0.5));
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
  varying vec3 vRest;
  varying vec2 vCell;
  void main() {
    vRest = rest;
    vCell = cell;
    vec3 p = position;
    float dome = pow(sin(3.14159 * cell.x) * sin(3.14159 * cell.y), 0.8);
    p.z -= sag * dome;
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
      colour = stained(panoramaA(vRest), vRest.xy);
      // Cracks open along the edge of the section as it sags.
      float rim = min(min(vCell.x, 1.0 - vCell.x), min(vCell.y, 1.0 - vCell.y));
      float line = 1.0 - smoothstep(0.0, 0.035 + 0.02 * noise(vRest.xy * 40.0), rim);
      colour = mix(colour, vec3(0.08, 0.07, 0.06), line * crack * 0.85);
    } else {
      // Broken back of the finish: pale, chalky, a little uneven.
      colour = vec3(0.78, 0.75, 0.69) * (0.8 + 0.2 * noise(vRest.xy * 60.0));
    }
    gl_FragColor = vec4(colour, 1.0);
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
  const radius = Math.max(...corners.map((c) => Math.hypot(c[0] - centre.x, c[1] - centre.y)));
  const bounds = scenario.bounds
    || { min: [centre.x - 2, centre.y - 2, floorZ - 0.2], max: [centre.x + 2, centre.y + 2, centre.z + 0.2] };

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
    zoneCentre: { value: new THREE.Vector2(centre.x, centre.y) },
    zoneRadius: { value: radius },
    stain: { value: 0 },
  });

  const renderer = new THREE.WebGLRenderer({ antialias: true, preserveDrawingBuffer: true });
  renderer.setPixelRatio(Math.min(window.devicePixelRatio || 1, 2));
  renderer.setClearColor(0x1d211b);
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
      zone: { value: corners.map((c) => new THREE.Vector2(c[0], c[1])) },
      ceilingZ: { value: centre.z },
      hole: { value: 0 },
      mould: { value: 0 },
    },
    vertexShader: HOUSE_VERTEX,
    fragmentShader: HOUSE_FRAGMENT,
    side: THREE.FrontSide, // room-facing triangles only: from outside you see in
  });
  world.add(new THREE.Mesh(flatHouse, houseMaterial));

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
  // Which triangle side faces down into the room depends on the box's corner order.
  const edgeU = corners[1].map((value, k) => value - corners[0][k]);
  const edgeV = corners[3].map((value, k) => value - corners[0][k]);
  const windingFacesDown = edgeU[0] * edgeV[1] - edgeU[1] * edgeV[0] < 0;
  const pieceMaterial = new THREE.ShaderMaterial({
    uniforms: { ...shared, sag: { value: 0 }, crack: { value: 0 }, underside: { value: windingFacesDown ? 1 : 0 } },
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
        // 4 mm below the scanned ceiling, so the section draws over it.
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
      const size = Math.hypot(points[1][0] - points[0][0], points[1][1] - points[0][1]);
      pieces.push({
        mesh,
        home: new THREE.Vector3(middle[0], middle[1], middle[2] - 0.004),
        delay: fromCentre * 0.75 + random() * 0.12, // the belly lets go first
        drift: new THREE.Vector2((random() - 0.5) * 0.7, (random() - 0.5) * 0.7),
        axis: new THREE.Vector3(random() - 0.5, random() - 0.5, (random() - 0.5) * 0.4).normalize(),
        spin: 2.5 + random() * 5,
        restYaw: random() * Math.PI * 2,
        restTilt: (random() - 0.5) * 0.5,
        restHeight: 0.012 + random() * size * 0.25,
      });
    }
  }

  // --- Outline of the flagged region ----------------------------------------
  const outlinePoints = [...corners, corners[0]].map((c) => new THREE.Vector3(c[0], c[1], c[2] - 0.012));
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
  const dripSeeds = Array.from({ length: DRIPS }, () => [(random() - 0.5) * radius * 0.9, (random() - 0.5) * radius * 0.9, random()]);
  const puddle = new THREE.Mesh(
    new THREE.CircleGeometry(1, 40),
    new THREE.MeshBasicMaterial({ color: 0x2c2a24, transparent: true, opacity: 0, depthWrite: false }),
  );
  puddle.position.set(centre.x, centre.y, floorZ + 0.012);
  world.add(puddle);

  const roomCentre = new THREE.Vector2((bounds.min[0] + bounds.max[0]) / 2, (bounds.min[1] + bounds.max[1]) / 2);

  // --- Timeline ---------------------------------------------------------------
  let progress = 0;
  let clock = 0;

  function apply() {
    const stageOf = (k) => clamp01(progress - k);
    const saturation = smooth(stageOf(1));
    const sagging = smooth(stageOf(2));
    const detach = stageOf(3);
    const after = smooth(stageOf(4));
    shared.stain.value = saturation;
    pieceMaterial.uniforms.sag.value = SAG_DEPTH * sagging;
    pieceMaterial.uniforms.crack.value = sagging;
    houseMaterial.uniforms.hole.value = progress > 3 ? 1 : 0;
    houseMaterial.uniforms.mould.value = after;
    outline.material.opacity = progress > 3 ? 0.4 : 0.78 + 0.22 * Math.sin(clock * 3.2);

    const time = detach * DETACH_SIM_SECONDS;
    for (const piece of pieces) {
      const t = Math.max(time - piece.delay, 0);
      const fallTime = Math.sqrt((2 * (drop - piece.restHeight)) / G);
      const { mesh } = piece;
      if (progress <= 3 || t === 0) {
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
    if (progress > 3) pieceMaterial.uniforms.sag.value = SAG_DEPTH * Math.max(1 - detach * 6, 0);

    const dripping = progress > 1.15 && progress < 3.05;
    drips.visible = dripping;
    if (dripping) {
      const position = dripGeometry.attributes.position;
      dripSeeds.forEach(([dx, dy, phase], index) => {
        const cycle = (clock * 0.55 + phase) % 1;
        const fall = Math.min(0.5 * G * (cycle * 1.4) ** 2, drop);
        position.setXYZ(index, centre.x + dx, centre.y + dy, centre.z - 0.02 - fall);
      });
      position.needsUpdate = true;
    }
    const wet = clamp01(progress - 1.2) * 0.6;
    puddle.material.opacity = Math.min(wet, 0.38);
    puddle.scale.setScalar(Math.max(radius * (0.35 + 0.9 * clamp01((progress - 1.2) / 1.8)), 0.01));
  }

  // --- Camera ------------------------------------------------------------------
  const toThree = (x, y, z) => new THREE.Vector3(x, z, -y);
  let view = 'room';
  let tween = null;

  function viewPose(name) {
    if (name === 'dollhouse') {
      const span = Math.max(bounds.max[0] - bounds.min[0], bounds.max[1] - bounds.min[1]);
      const out = new THREE.Vector2(roomCentre.x - centre.x, roomCentre.y - centre.y);
      if (out.length() < 0.2) out.set(-1, -1);
      out.setLength(span * 0.95);
      return {
        position: toThree(roomCentre.x + out.x, roomCentre.y + out.y, centre.z + span * 1.15),
        target: toThree(centre.x, centre.y, floorZ + drop * 0.45),
      };
    }
    // Stand in the corner of the room farthest from the flagged region.
    const inset = 0.55;
    const corner = [
      Math.abs(bounds.min[0] - centre.x) > Math.abs(bounds.max[0] - centre.x) ? bounds.min[0] + inset : bounds.max[0] - inset,
      Math.abs(bounds.min[1] - centre.y) > Math.abs(bounds.max[1] - centre.y) ? bounds.min[1] + inset : bounds.max[1] - inset,
    ];
    return {
      position: toThree(corner[0], corner[1], floorZ + 1.25),
      target: toThree(centre.x, centre.y, floorZ + drop * 0.62),
    };
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
      const stage = Math.min(Math.floor(progress), STAGE_SECONDS.length - 1);
      progress = Math.min(progress + dt / STAGE_SECONDS[stage], STAGE_SECONDS.length);
      playing.onProgress?.(progress);
      if (progress >= STAGE_SECONDS.length) {
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
    stageCount: STAGE_SECONDS.length,
    get progress() { return progress; },
    get playing() { return Boolean(playing); },
    setProgress(value) {
      playing = null;
      progress = Math.min(Math.max(Number(value) || 0, 0), STAGE_SECONDS.length);
    },
    play(onProgress, onDone) {
      if (progress >= STAGE_SECONDS.length - 0.001) progress = 0;
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
