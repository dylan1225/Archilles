import { useEffect, useRef } from "react";
import * as THREE from "three";
import { OrbitControls } from "three/examples/jsm/controls/OrbitControls.js";
import type { Building } from "@/lib/risk";

type Props = {
  buildings: { building: Building; score: number }[];
  selectedId: string | null;
  onSelect: (id: string) => void;
};

function scoreColor(score: number) {
  const c = new THREE.Color();
  if (score < 45) c.set("#3f8f72");
  else if (score < 68) c.set("#e4a04a");
  else c.set("#d4533c");
  return c;
}

export function CityScene({ buildings, selectedId, onSelect }: Props) {
  const wrapRef = useRef<HTMLDivElement>(null);
  const onSelectRef = useRef(onSelect);
  const meshMap = useRef<Map<string, THREE.Mesh>>(new Map());
  onSelectRef.current = onSelect;

  useEffect(() => {
    for (const [id, mesh] of meshMap.current) {
      const mat = mesh.material as THREE.MeshStandardMaterial;
      const base = scoreColor(mesh.userData.score as number);
      mat.emissive.copy(base).multiplyScalar(id === selectedId ? 0.45 : 0.08);
      mesh.position.y = id === selectedId ? 1.5 : 0;
    }
  }, [selectedId]);

  const signature = buildings.map((b) => `${b.building.id}:${b.score}`).join("|");

  useEffect(() => {
    const wrap = wrapRef.current;
    if (!wrap) return;

    const renderer = new THREE.WebGLRenderer({ antialias: true, alpha: true });
    renderer.setPixelRatio(Math.min(window.devicePixelRatio, 2));
    renderer.setSize(wrap.clientWidth, wrap.clientHeight);
    wrap.appendChild(renderer.domElement);

    const scene = new THREE.Scene();
    scene.fog = new THREE.Fog("#121614", 280, 620);

    const camera = new THREE.PerspectiveCamera(
      38,
      wrap.clientWidth / Math.max(wrap.clientHeight, 1),
      0.1,
      900,
    );
    camera.position.set(160, 140, 190);

    const controls = new OrbitControls(camera, renderer.domElement);
    controls.enableDamping = true;
    controls.target.set(0, 20, 0);
    controls.maxPolarAngle = Math.PI / 2.05;
    controls.minDistance = 60;
    controls.maxDistance = 420;

    scene.add(new THREE.AmbientLight("#c9d2c6", 0.65));
    const sun = new THREE.DirectionalLight("#fff4e0", 1.35);
    sun.position.set(80, 160, 40);
    scene.add(sun);
    const fill = new THREE.DirectionalLight("#8aa0b8", 0.35);
    fill.position.set(-100, 40, -80);
    scene.add(fill);

    const ground = new THREE.Mesh(
      new THREE.CircleGeometry(240, 64),
      new THREE.MeshStandardMaterial({ color: "#1a211c", roughness: 1 }),
    );
    ground.rotation.x = -Math.PI / 2;
    scene.add(ground);

    const grid = new THREE.GridHelper(320, 16, "#2a332c", "#222a24");
    grid.position.y = 0.2;
    scene.add(grid);

    const meshes: THREE.Mesh[] = [];
    const map = new Map<string, THREE.Mesh>();
    for (const { building, score } of buildings) {
      const shape = new THREE.Shape();
      const fp = building.footprint;
      shape.moveTo(fp[0][0], fp[0][1]);
      for (let i = 1; i < fp.length; i++) shape.lineTo(fp[i][0], fp[i][1]);
      const geom = new THREE.ExtrudeGeometry(shape, {
        depth: building.heightM * 0.35,
        bevelEnabled: false,
      });
      geom.rotateX(-Math.PI / 2);
      const color = scoreColor(score);
      const mat = new THREE.MeshStandardMaterial({
        color,
        roughness: 0.72,
        metalness: building.material === "steel" ? 0.35 : 0.05,
        emissive: color.clone().multiplyScalar(building.id === selectedId ? 0.45 : 0.08),
      });
      const mesh = new THREE.Mesh(geom, mat);
      mesh.position.set(building.position[0], building.id === selectedId ? 1.5 : 0, building.position[1]);
      mesh.userData.id = building.id;
      mesh.userData.score = score;
      scene.add(mesh);
      meshes.push(mesh);
      map.set(building.id, mesh);

      const edge = new THREE.LineSegments(
        new THREE.EdgesGeometry(geom),
        new THREE.LineBasicMaterial({ color: "#0e1210", transparent: true, opacity: 0.35 }),
      );
      edge.position.copy(mesh.position);
      scene.add(edge);
    }
    meshMap.current = map;

    const raycaster = new THREE.Raycaster();
    const pointer = new THREE.Vector2();

    function pick(ev: PointerEvent) {
      const rect = renderer.domElement.getBoundingClientRect();
      pointer.x = ((ev.clientX - rect.left) / rect.width) * 2 - 1;
      pointer.y = -((ev.clientY - rect.top) / rect.height) * 2 + 1;
      raycaster.setFromCamera(pointer, camera);
      const hits = raycaster.intersectObjects(meshes);
      if (hits[0]) onSelectRef.current(hits[0].object.userData.id as string);
    }
    renderer.domElement.addEventListener("pointerdown", pick);

    let frame = 0;
    const tick = () => {
      frame = requestAnimationFrame(tick);
      controls.update();
      renderer.render(scene, camera);
    };
    tick();

    const ro = new ResizeObserver(() => {
      const w = wrap.clientWidth;
      const h = Math.max(wrap.clientHeight, 1);
      camera.aspect = w / h;
      camera.updateProjectionMatrix();
      renderer.setSize(w, h);
    });
    ro.observe(wrap);

    return () => {
      cancelAnimationFrame(frame);
      ro.disconnect();
      renderer.domElement.removeEventListener("pointerdown", pick);
      controls.dispose();
      renderer.dispose();
      if (renderer.domElement.parentElement === wrap) wrap.removeChild(renderer.domElement);
    };
    // selected highlight is a separate effect so orbit state survives clicks
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [signature]);

  return <div ref={wrapRef} className="h-full w-full" />;
}
