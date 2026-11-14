import { createFileRoute } from "@tanstack/react-router";
import { useMemo, useState } from "react";
import { CityScene } from "@/components/city-scene";
import { cities } from "@/data/cities";
import {
  DRIVER_LABELS,
  MATERIAL_LABEL,
  averageMicro,
  riskScore,
  type Building,
  type Material,
  type Microclimate,
} from "@/lib/risk";

export const Route = createFileRoute("/")({ component: Home });

type Mode = "hyperlocal" | "regional";

function Home() {
  const [cityId, setCityId] = useState(cities[0].id);
  const [mode, setMode] = useState<Mode>("hyperlocal");
  const [selectedId, setSelectedId] = useState<string | null>(cities[0].buildings[0].id);
  const [candidate, setCandidate] = useState<Building | null>(null);

  const city = cities.find((c) => c.id === cityId) ?? cities[0];
  const portfolio = candidate && candidate.cityId === cityId ? [...city.buildings, candidate] : city.buildings;
  const regional = useMemo(() => averageMicro(city.buildings), [city]);

  const ranked = useMemo(() => {
    return portfolio
      .map((building) => {
        const micro = mode === "regional" ? regional : building.micro;
        const score = riskScore(building, micro);
        const local = riskScore(building, building.micro);
        const airport = riskScore(building, regional);
        return { building, score, micro, delta: local - airport };
      })
      .sort((a, b) => b.score - a.score);
  }, [portfolio, mode, regional]);

  const selected = ranked.find((r) => r.building.id === selectedId) ?? ranked[0];

  function switchCity(id: string) {
    setCityId(id);
    setCandidate(null);
    const next = cities.find((c) => c.id === id);
    setSelectedId(next?.buildings[0].id ?? null);
  }

  return (
    <main className="min-h-screen bg-bg text-fg">
      <header className="flex flex-wrap items-end justify-between gap-4 border-b border-line px-4 py-4 md:px-6">
        <div>
          <p className="font-mono text-xs tracking-widest text-amber uppercase">Portfolio triage</p>
          <h1 className="text-2xl font-medium tracking-tight">FaultLine</h1>
          <p className="max-w-xl text-sm text-muted">
            Massing from public LiDAR footprints, scored against hyperlocal weather — so inspectors go to the
            buildings that are aging faster than their design life.
          </p>
        </div>
        <div className="flex gap-2">
          {cities.map((c) => (
            <button
              key={c.id}
              onClick={() => switchCity(c.id)}
              className={`min-h-11 rounded-md px-4 text-sm ${
                c.id === cityId ? "bg-amber text-bg" : "bg-surface text-fg"
              }`}
            >
              {c.name}
            </button>
          ))}
        </div>
      </header>

      <div className="grid gap-0 lg:grid-cols-[320px_1fr_340px]">
        <section className="border-b border-line lg:border-r lg:border-b-0">
          <div className="flex items-center justify-between gap-2 px-4 py-3">
            <p className="font-mono text-xs text-muted uppercase">{city.climate}</p>
          </div>
          <div className="flex gap-2 px-4 pb-3">
            <button
              onClick={() => setMode("hyperlocal")}
              className={`min-h-11 flex-1 rounded-md text-sm ${mode === "hyperlocal" ? "bg-surface-2 text-fg" : "text-muted"}`}
            >
              Hyperlocal
            </button>
            <button
              onClick={() => setMode("regional")}
              className={`min-h-11 flex-1 rounded-md text-sm ${mode === "regional" ? "bg-surface-2 text-fg" : "text-muted"}`}
            >
              Airport weather
            </button>
          </div>
          <ol>
            {ranked.map((row, i) => (
              <li key={row.building.id}>
                <button
                  onClick={() => setSelectedId(row.building.id)}
                  className={`flex w-full items-center gap-3 px-4 py-3 text-left ${
                    selected?.building.id === row.building.id ? "bg-surface" : ""
                  }`}
                >
                  <span className="w-5 font-mono text-xs text-muted">{i + 1}</span>
                  <span className="min-w-0 flex-1">
                    <span className="block truncate text-sm">{row.building.name}</span>
                    <span className="block text-xs text-muted">
                      {row.building.year} · {MATERIAL_LABEL[row.building.material]}
                    </span>
                  </span>
                  <Score n={row.score} />
                </button>
              </li>
            ))}
          </ol>
        </section>

        <section className="relative h-[420px] bg-bg lg:h-[calc(100vh-92px)]">
          <CityScene
            buildings={ranked.map((r) => ({ building: r.building, score: r.score }))}
            selectedId={selected?.building.id ?? null}
            onSelect={setSelectedId}
          />
          <p className="pointer-events-none absolute bottom-3 left-3 max-w-xs rounded-md bg-bg/80 px-3 py-2 text-xs text-muted">
            Extruded footprints stand in for a LiDAR city model. Drag to orbit. Color is the triage score, not a
            failure probability.
          </p>
        </section>

        <aside className="border-t border-line p-4 lg:border-t-0 lg:border-l">
          {selected && (
            <>
              <p className="font-mono text-xs text-amber uppercase">{selected.building.neighborhood}</p>
              <h2 className="mt-1 text-xl font-medium">{selected.building.name}</h2>
              <p className="mt-2 text-sm leading-relaxed text-muted">{selected.building.note}</p>
              <dl className="mt-4 grid grid-cols-3 gap-2 text-center">
                <Stat label="Score" value={String(selected.score)} />
                <Stat label="Built" value={String(selected.building.year)} />
                <Stat label="Height" value={`${selected.building.heightM} m`} />
              </dl>
              <p className="mt-4 font-mono text-xs text-muted uppercase">
                {mode === "hyperlocal"
                  ? `Hyperlocal vs airport: ${selected.delta > 0 ? "+" : ""}${selected.delta} pts`
                  : "Using one weather station for the whole city"}
              </p>
              <ul className="mt-3 space-y-2">
                {DRIVER_LABELS.map((d) => (
                  <li key={d.key}>
                    <div className="flex justify-between text-xs">
                      <span>{d.label}</span>
                      <span className="font-mono text-muted">{Math.round(selected.micro[d.key] * 100)}</span>
                    </div>
                    <div className="mt-1 h-1.5 overflow-hidden rounded-full bg-surface-2">
                      <div
                        className="h-full bg-amber"
                        style={{ width: `${Math.round(selected.micro[d.key] * 100)}%` }}
                      />
                    </div>
                  </li>
                ))}
              </ul>
            </>
          )}

          <CandidateForm
            cityId={cityId}
            onAdd={(building) => {
              setCandidate(building);
              setSelectedId(building.id);
              setMode("hyperlocal");
            }}
          />
        </aside>
      </div>
    </main>
  );
}

function Score({ n }: { n: number }) {
  const tone = n >= 68 ? "text-risk" : n >= 45 ? "text-amber" : "text-safe";
  return <span className={`font-mono text-sm ${tone}`}>{n}</span>;
}

function Stat({ label, value }: { label: string; value: string }) {
  return (
    <div className="rounded-md bg-surface px-2 py-2">
      <dt className="text-xs text-muted">{label}</dt>
      <dd className="font-mono text-sm">{value}</dd>
    </div>
  );
}

function CandidateForm({ cityId, onAdd }: { cityId: string; onAdd: (b: Building) => void }) {
  const [name, setName] = useState("Candidate from plan");
  const [year, setYear] = useState(1978);
  const [height, setHeight] = useState(42);
  const [material, setMaterial] = useState<Material>("concrete");
  const [wind, setWind] = useState(0.7);

  function submit(e: React.FormEvent) {
    e.preventDefault();
    const micro: Microclimate = {
      windChannel: wind,
      solarLoad: 0.55,
      heatIsland: 0.6,
      saltExposure: material === "concrete" || material === "steel" ? 0.55 : 0.3,
      vibration: 0.4,
      freezeThaw: cityId === "chi" ? 0.78 : 0.55,
    };
    const w = Math.max(8, height * 0.18);
    onAdd({
      id: "candidate",
      name,
      cityId,
      neighborhood: "Inserted from plan",
      year,
      heightM: height,
      stories: Math.max(1, Math.round(height / 3.2)),
      material,
      footprint: [
        [-w, -w * 0.7],
        [w, -w * 0.7],
        [w, w * 0.7],
        [-w, w * 0.7],
      ],
      position: [0, 0],
      micro,
      note: "Simplified plan ingest: footprint box, year, material, and a street-canyon wind factor. A production version would parse BIM or a scanned sheet into the same fields.",
    });
  }

  return (
    <form onSubmit={submit} className="mt-6 border-t border-line pt-4">
      <h3 className="text-sm font-medium">Score a plan</h3>
      <p className="mt-1 text-xs text-muted">Drop a candidate into the block and re-rank it.</p>
      <label className="mt-3 block text-xs text-muted">
        Name
        <input
          value={name}
          onChange={(e) => setName(e.target.value)}
          className="mt-1 min-h-11 w-full rounded-md border border-line bg-surface px-3 text-sm text-fg"
        />
      </label>
      <div className="mt-2 grid grid-cols-2 gap-2">
        <label className="text-xs text-muted">
          Year
          <input
            type="number"
            value={year}
            onChange={(e) => setYear(Number(e.target.value))}
            className="mt-1 min-h-11 w-full rounded-md border border-line bg-surface px-3 text-sm text-fg"
          />
        </label>
        <label className="text-xs text-muted">
          Height (m)
          <input
            type="number"
            value={height}
            onChange={(e) => setHeight(Number(e.target.value))}
            className="mt-1 min-h-11 w-full rounded-md border border-line bg-surface px-3 text-sm text-fg"
          />
        </label>
      </div>
      <label className="mt-2 block text-xs text-muted">
        Primary material
        <select
          value={material}
          onChange={(e) => setMaterial(e.target.value as Material)}
          className="mt-1 min-h-11 w-full rounded-md border border-line bg-surface px-3 text-sm text-fg"
        >
          {(Object.keys(MATERIAL_LABEL) as Material[]).map((m) => (
            <option key={m} value={m}>
              {MATERIAL_LABEL[m]}
            </option>
          ))}
        </select>
      </label>
      <label className="mt-2 block text-xs text-muted">
        Wind-channel factor {Math.round(wind * 100)}
        <input
          type="range"
          min={0}
          max={100}
          value={Math.round(wind * 100)}
          onChange={(e) => setWind(Number(e.target.value) / 100)}
          className="mt-2 w-full"
        />
      </label>
      <button type="submit" className="mt-3 min-h-11 w-full rounded-md bg-amber text-sm font-medium text-bg">
        Rank this building
      </button>
    </form>
  );
}
