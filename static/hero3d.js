// Archilles hero: monumental engineering parts standing in still water, layered
// in depth (bracket and gear up front, a bolt mid-field, an I-beam behind, a
// truss bridge on the misty horizon) so camera drift and the cursor give
// parallax. The bracket's sharp inner corner glows coral: the weak point.
// Illustrative only; real numbers come from mechanics.py in a diagnosis.
// If WebGL or this module fails, the hero keeps its CSS gradient and works.

import * as THREE from "three";
import { Reflector } from "three/addons/objects/Reflector.js";

const PALETTE = {
  part: 0xe8f1fb,     // the focal bracket stays pale so its coral weak point pops
  steel: 0x86a8cc,
  far: 0x7d9ec4,
  fog: 0xc6daee,
  skyTop: "#2f76c4",
  skyLow: "#cfe3f6",
  water: 0x7fa6cf,
  sun: 0xfff6ec,
  flag: new THREE.Color(0xff7a63),
  contour: new THREE.Color(0x4f86bf),
};
const MAX_DPR = 1.5;
const CAMERA = { y: 1.55, z: 9.2, lookY: 2.1, lookZ: -5 };
const DOLLY = 3.2;          // how far the camera moves in as the hero scrolls away
const PARALLAX = { x: 0.7, y: 0.35 };
const BRACKET = { leg: 1.0, long: 3.3, tall: 3.0, depth: 1.5 };

const stage = document.getElementById("hero-stage");
const callout = document.getElementById("hero-callout");
const reducedMotion = window.matchMedia("(prefers-reduced-motion: reduce)").matches;

function webglAvailable() {
  try {
    return Boolean(window.WebGL2RenderingContext && document.createElement("canvas").getContext("webgl2"));
  } catch {
    return false;
  }
}

function matte(color, roughness = 0.8, metalness = 0.05) {
  return new THREE.MeshStandardMaterial({ color, roughness, metalness });
}

function solid(geometry, material) {
  const mesh = new THREE.Mesh(geometry, material);
  mesh.castShadow = true;
  mesh.receiveShadow = true;
  return mesh;
}

function extrude(shape, depth, bevel = 0.04) {
  return new THREE.ExtrudeGeometry(shape, {
    depth, bevelEnabled: bevel > 0, bevelSize: bevel, bevelThickness: bevel, bevelSegments: 2, curveSegments: 24,
  });
}

// --- The weak point: an L-bracket with a sharp inner corner -----------------

/** Standard material plus a heat field around one point, computed per pixel. */
function heatMaterial(hot) {
  const uniforms = { uTime: { value: 0 }, uHot: { value: hot }, uFlag: { value: PALETTE.flag }, uContour: { value: PALETTE.contour } };
  const material = matte(PALETTE.part, 0.55, 0.08);
  material.onBeforeCompile = (shader) => {
    Object.assign(shader.uniforms, uniforms);
    shader.vertexShader = shader.vertexShader
      .replace("#include <common>", "#include <common>\nvarying vec3 vLocal;")
      .replace("#include <begin_vertex>", "#include <begin_vertex>\nvLocal = position;");
    shader.fragmentShader = shader.fragmentShader
      .replace("#include <common>", "#include <common>\nvarying vec3 vLocal;\nuniform float uTime;\nuniform vec2 uHot;\nuniform vec3 uFlag;\nuniform vec3 uContour;")
      .replace("#include <color_fragment>", `#include <color_fragment>
        float d = length(vLocal.xy - uHot);
        float heat = exp(-pow(d / 0.6, 2.0)) * (0.88 + 0.12 * sin(uTime * 1.6));
        float ring = 1.0 - smoothstep(0.0, 0.035, abs(fract(d * 4.2 - uTime * 0.12) - 0.5));
        ring *= smoothstep(2.6, 0.5, d) * (1.0 - heat);
        diffuseColor.rgb = mix(diffuseColor.rgb, uContour, ring * 0.28);
        diffuseColor.rgb = mix(diffuseColor.rgb, uFlag, clamp(heat, 0.0, 1.0) * 0.95);`);
  };
  return { material, uniforms };
}

function bracket() {
  const { leg, long, tall, depth } = BRACKET;
  const shape = new THREE.Shape();
  shape.moveTo(0, 0);
  shape.lineTo(long, 0);
  shape.lineTo(long, leg);
  shape.lineTo(leg, leg);   // the inner corner, left sharp on purpose
  shape.lineTo(leg, tall);
  shape.lineTo(0, tall);
  shape.closePath();
  const geometry = extrude(shape, depth);
  geometry.translate(-long / 2, 0, -depth / 2);
  const hot = new THREE.Vector2(leg - long / 2, leg);
  const { material, uniforms } = heatMaterial(hot);
  const group = new THREE.Group();
  group.add(solid(geometry, material));
  group.add(new THREE.LineSegments(new THREE.EdgesGeometry(geometry, 28),
    new THREE.LineBasicMaterial({ color: 0x0e1c2a, transparent: true, opacity: 0.32 })));
  return { group, uniforms, weakPoint: new THREE.Vector3(hot.x, hot.y, depth / 2 + 0.05) };
}

// --- Supporting cast ----------------------------------------------------------

function gear(teeth = 16, outer = 2.2, inner = 1.86) {
  const shape = new THREE.Shape();
  const step = (Math.PI * 2) / teeth;
  for (let i = 0; i < teeth; i++) {
    const a = i * step;
    const points = [[inner, a], [outer, a + step * 0.18], [outer, a + step * 0.48], [inner, a + step * 0.66]];
    points.forEach(([r, angle], j) => {
      const x = Math.cos(angle) * r;
      const y = Math.sin(angle) * r;
      if (i === 0 && j === 0) shape.moveTo(x, y); else shape.lineTo(x, y);
    });
  }
  shape.closePath();
  const bore = new THREE.Path();
  bore.absarc(0, 0, 0.55, 0, Math.PI * 2, true);
  shape.holes.push(bore);
  for (let i = 0; i < 5; i++) {
    const a = (i / 5) * Math.PI * 2;
    const hole = new THREE.Path();
    hole.absarc(Math.cos(a) * 1.2, Math.sin(a) * 1.2, 0.3, 0, Math.PI * 2, true);
    shape.holes.push(hole);
  }
  const geometry = extrude(shape, 0.55, 0.03);
  geometry.translate(0, 0, -0.275);
  return solid(geometry, matte(PALETTE.steel, 0.45, 0.25));
}

function bolt() {
  const group = new THREE.Group();
  const steel = matte(PALETTE.steel, 0.4, 0.3);
  group.add(solid(new THREE.CylinderGeometry(0.45, 0.45, 5, 40), steel));
  const ring = new THREE.TorusGeometry(0.46, 0.05, 8, 40);
  for (let i = 0; i < 16; i++) {
    const thread = solid(ring, steel);
    thread.rotation.x = Math.PI / 2;
    thread.position.y = -2.3 + i * 0.19;
    group.add(thread);
  }
  const head = solid(new THREE.CylinderGeometry(0.95, 0.95, 0.6, 6), steel);
  head.position.y = 2.8;
  group.add(head);
  return group;
}

function iBeam(length = 12) {
  const w = 1.3, h = 1.6, web = 0.22, flange = 0.24;
  const shape = new THREE.Shape();
  [[-w / 2, 0], [w / 2, 0], [w / 2, flange], [web / 2, flange], [web / 2, h - flange], [w / 2, h - flange],
    [w / 2, h], [-w / 2, h], [-w / 2, h - flange], [-web / 2, h - flange], [-web / 2, flange], [-w / 2, flange]]
    .forEach(([x, y], i) => (i ? shape.lineTo(x, y) : shape.moveTo(x, y)));
  shape.closePath();
  const geometry = extrude(shape, length, 0.02);
  geometry.translate(0, 0, -length / 2);
  return solid(geometry, matte(PALETTE.far, 0.7, 0.15));
}

/** A Warren truss bridge as thin members, for the far horizon. */
function trussBridge(span = 70, bay = 4, height = 3.2) {
  const group = new THREE.Group();
  const material = matte(PALETTE.far, 0.9, 0);
  const member = (x1, y1, x2, y2, thickness = 0.16) => {
    const length = Math.hypot(x2 - x1, y2 - y1);
    const piece = new THREE.Mesh(new THREE.BoxGeometry(length, thickness, thickness), material);
    piece.position.set((x1 + x2) / 2, (y1 + y2) / 2, 0);
    piece.rotation.z = Math.atan2(y2 - y1, x2 - x1);
    group.add(piece);
  };
  const start = -span / 2;
  member(start, 0, -start, 0, 0.32);
  member(start + bay / 2, height, -start - bay / 2, height, 0.24);
  for (let x = start; x < -start; x += bay) {
    member(x, 0, x + bay / 2, height);
    member(x + bay / 2, height, x + bay, 0);
  }
  for (let x = start + bay * 2; x < -start; x += bay * 6) {
    const pier = new THREE.Mesh(new THREE.BoxGeometry(0.9, 6, 0.9), material);
    pier.position.set(x, -3, 0);
    group.add(pier);
  }
  return group;
}

function skyTexture() {
  const canvas = document.createElement("canvas");
  canvas.width = 16;
  canvas.height = 512;
  const context = canvas.getContext("2d");
  const gradient = context.createLinearGradient(0, 0, 0, 512);
  gradient.addColorStop(0, PALETTE.skyTop);
  gradient.addColorStop(0.72, PALETTE.skyLow);
  gradient.addColorStop(1, "#f6faff");
  context.fillStyle = gradient;
  context.fillRect(0, 0, 16, 512);
  const texture = new THREE.CanvasTexture(canvas);
  texture.colorSpace = THREE.SRGBColorSpace;
  return texture;
}

const WATER_SHADER = {
  name: "ArchillesWater",
  uniforms: { color: { value: null }, tDiffuse: { value: null }, textureMatrix: { value: null }, uTime: { value: 0 } },
  vertexShader: /* glsl */`
    uniform mat4 textureMatrix;
    varying vec4 vUv;
    varying vec2 vXZ;
    #include <common>
    #include <logdepthbuf_pars_vertex>
    void main() {
      vUv = textureMatrix * vec4(position, 1.0);
      vXZ = (modelMatrix * vec4(position, 1.0)).xz;
      gl_Position = projectionMatrix * modelViewMatrix * vec4(position, 1.0);
      #include <logdepthbuf_vertex>
    }`,
  fragmentShader: /* glsl */`
    uniform vec3 color;
    uniform sampler2D tDiffuse;
    uniform float uTime;
    varying vec4 vUv;
    varying vec2 vXZ;
    #include <logdepthbuf_pars_fragment>
    void main() {
      #include <logdepthbuf_fragment>
      vec2 p = vXZ;
      float w = sin(p.x * 2.1 + uTime * 0.7) * 0.5 + sin(p.y * 3.4 - uTime * 0.55 + p.x * 0.8) * 0.5
              + sin((p.x + p.y) * 6.5 + uTime * 1.2) * 0.22;
      vec4 uv = vUv;
      uv.xy += vec2(w * 0.004, w * 0.008) * uv.w;
      vec3 reflected = texture2DProj(tDiffuse, uv).rgb;
      gl_FragColor = vec4(mix(reflected, color, 0.36) * (0.94 + w * 0.03), 1.0);
      #include <tonemapping_fragment>
      #include <colorspace_fragment>
    }`,
};

function buildScene(scene) {
  const sky = new THREE.Mesh(new THREE.PlaneGeometry(220, 70), new THREE.MeshBasicMaterial({ map: skyTexture(), fog: false }));
  sky.position.set(0, 18, -60);
  scene.add(sky);

  // Horizon: a truss bridge in the mist.
  const bridge = trussBridge();
  bridge.position.set(0, 3.2, -30);
  scene.add(bridge);

  // Back: an I-beam lying across the water.
  const beam = iBeam();
  beam.rotation.y = Math.PI / 2 - 0.35;
  beam.position.set(6, -0.85, -15); // kept low and off-centre so the headline has calm water behind it
  scene.add(beam);

  // Mid-field: a bolt standing upright.
  const standing = bolt();
  standing.position.set(-4.4, 2.2, -10.5); // rises behind the gear, clear of the centred type
  standing.rotation.set(0.05, 0.4, -0.06);
  scene.add(standing);

  // Front left: a gear on its edge, half in the water, turning slowly.
  const cog = gear();
  cog.position.set(-5.4, 1.1, -1.8);
  cog.rotation.y = 0.75;
  scene.add(cog);

  // Front right: the bracket and its weak point.
  const { group: part, uniforms, weakPoint } = bracket();
  part.position.set(4.3, 0.02, -1.2);
  part.rotation.y = -0.55;
  scene.add(part);

  const water = new Reflector(new THREE.PlaneGeometry(160, 120), {
    shader: WATER_SHADER, color: PALETTE.water, textureWidth: 1024, textureHeight: 1024, clipBias: 0.003,
  });
  water.rotation.x = -Math.PI / 2;
  water.position.y = 0.02;
  scene.add(water);

  part.updateMatrixWorld();
  return { cog, uniforms, water, weakPoint: part.localToWorld(weakPoint.clone()) };
}

function lights(scene) {
  scene.add(new THREE.HemisphereLight(0xdcebff, 0x6f8fb4, 0.6));
  const sun = new THREE.DirectionalLight(PALETTE.sun, 3.4);
  sun.position.set(7, 10, 4);
  sun.target.position.set(0, 0, -4);
  sun.castShadow = true;
  sun.shadow.mapSize.set(2048, 2048);
  Object.assign(sun.shadow.camera, { left: -16, right: 16, top: 16, bottom: -8, near: 1, far: 45 });
  sun.shadow.bias = -0.0004;
  sun.shadow.radius = 6;
  scene.add(sun, sun.target);
}

function start(container) {
  const renderer = new THREE.WebGLRenderer({ antialias: true, powerPreference: "high-performance" });
  renderer.setPixelRatio(Math.min(window.devicePixelRatio || 1, MAX_DPR));
  renderer.outputColorSpace = THREE.SRGBColorSpace;
  renderer.toneMapping = THREE.ACESFilmicToneMapping;
  renderer.toneMappingExposure = 1.04;
  renderer.shadowMap.enabled = true;
  renderer.shadowMap.type = THREE.PCFSoftShadowMap;
  renderer.setClearColor(PALETTE.fog);
  container.append(renderer.domElement);

  const scene = new THREE.Scene();
  scene.fog = new THREE.Fog(PALETTE.fog, 18, 62);
  const camera = new THREE.PerspectiveCamera(45, 1, 0.1, 120);
  lights(scene);
  const { cog, uniforms, water, weakPoint } = buildScene(scene);

  const pointer = { x: 0, y: 0, tx: 0, ty: 0 };
  const lookAt = new THREE.Vector3();
  const projected = new THREE.Vector3();
  let visible = true;
  let frame = 0;
  let framing = 0;

  function resize() {
    const width = container.clientWidth || window.innerWidth;
    const height = container.clientHeight || window.innerHeight;
    renderer.setSize(width, height, false);
    camera.aspect = width / height;
    camera.fov = camera.aspect < 1 ? 64 : 45;
    camera.updateProjectionMatrix();
    framing = camera.aspect < 1 ? 1.6 : 0; // phones: pan right so the bracket stays in frame
  }

  function placeCallout() {
    if (!callout) return;
    projected.copy(weakPoint).project(camera);
    const x = (projected.x * 0.5 + 0.5) * container.clientWidth;
    const y = (-projected.y * 0.5 + 0.5) * container.clientHeight;
    callout.style.transform = `translate3d(${x.toFixed(1)}px, ${y.toFixed(1)}px, 0)`;
    callout.dataset.fit = x > container.clientWidth - 300 ? "compact" : "full";
  }

  function render(time) {
    const t = reducedMotion ? 0 : time / 1000;
    const scroll = Math.min(1, window.scrollY / Math.max(1, window.innerHeight));
    pointer.x += (pointer.tx - pointer.x) * 0.05;
    pointer.y += (pointer.ty - pointer.y) * 0.05;
    camera.position.set(
      framing + Math.sin(t * 0.11) * 0.35 + pointer.x * PARALLAX.x,
      CAMERA.y + Math.sin(t * 0.13) * 0.06 - pointer.y * PARALLAX.y,
      CAMERA.z - scroll * DOLLY,
    );
    lookAt.set(framing + pointer.x * 0.25, CAMERA.lookY, CAMERA.lookZ);
    camera.lookAt(lookAt);
    water.material.uniforms.uTime.value = t;
    uniforms.uTime.value = t;
    cog.rotation.z = t * 0.05;
    renderer.render(scene, camera);
    placeCallout();
  }

  function loop(time) {
    frame = 0;
    if (!visible || document.hidden) return;
    render(time);
    if (!reducedMotion) frame = requestAnimationFrame(loop);
  }

  function wake() {
    if (!frame && visible && !document.hidden) frame = requestAnimationFrame(loop);
  }

  resize();
  render(0);
  container.dataset.ready = "true";
  new ResizeObserver(() => { resize(); render(performance.now()); }).observe(container);
  new IntersectionObserver(([entry]) => { visible = entry.isIntersecting; wake(); }).observe(container);
  document.addEventListener("visibilitychange", wake);
  window.addEventListener("pointermove", (event) => {
    pointer.tx = (event.clientX / window.innerWidth) * 2 - 1;
    pointer.ty = (event.clientY / window.innerHeight) * 2 - 1;
    if (reducedMotion) render(performance.now());
  }, { passive: true });
  if (reducedMotion) window.addEventListener("scroll", () => render(performance.now()), { passive: true });
  wake();
}

function showCallout() {
  if (callout) setTimeout(() => { callout.dataset.on = "true"; }, reducedMotion ? 0 : 1100);
}

if (stage && webglAvailable()) {
  try {
    start(stage);
    if (document.documentElement.dataset.entered === "true") showCallout();
    else window.addEventListener("archilles:enter", showCallout, { once: true });
  } catch (error) {
    stage.replaceChildren(); // CSS gradient remains; the page is fully usable without 3D.
  }
}
