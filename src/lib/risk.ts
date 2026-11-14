export type Material = "concrete" | "steel" | "masonry" | "timber";

export type Microclimate = {
  windChannel: number;
  solarLoad: number;
  heatIsland: number;
  saltExposure: number;
  vibration: number;
  freezeThaw: number;
};

export type Building = {
  id: string;
  name: string;
  cityId: string;
  neighborhood: string;
  year: number;
  heightM: number;
  stories: number;
  material: Material;
  /** Footprint in meters, origin near the block center. */
  footprint: [number, number][];
  position: [number, number];
  micro: Microclimate;
  note: string;
};

export type SourceLink = { label: string; href: string };

export type City = {
  id: string;
  name: string;
  climate: string;
  buildings: Building[];
  extent?: string;
  sources?: SourceLink[];
};

const DESIGN_LIFE: Record<Material, number> = {
  concrete: 75,
  steel: 60,
  masonry: 80,
  timber: 50,
};

export const MATERIAL_LABEL: Record<Material, string> = {
  concrete: "Reinforced concrete",
  steel: "Structural steel",
  masonry: "Load-bearing masonry",
  timber: "Mass timber",
};

export function exposure(m: Microclimate, material: Material) {
  if (material === "concrete") {
    return (
      m.freezeThaw * 0.42 +
      m.saltExposure * 0.22 +
      m.vibration * 0.2 +
      m.heatIsland * 0.16
    );
  }
  if (material === "steel") {
    return (
      m.saltExposure * 0.38 +
      m.windChannel * 0.32 +
      m.heatIsland * 0.16 +
      m.freezeThaw * 0.14
    );
  }
  if (material === "masonry") {
    return (
      m.freezeThaw * 0.46 +
      m.vibration * 0.24 +
      m.windChannel * 0.16 +
      m.solarLoad * 0.14
    );
  }
  return m.solarLoad * 0.3 + m.heatIsland * 0.3 + m.freezeThaw * 0.22 + m.windChannel * 0.18;
}

export function ageFactor(year: number, material: Material, now = 2026) {
  const life = DESIGN_LIFE[material];
  const age = Math.max(0, now - year);
  return Math.min(1.45, age / life);
}

/** 0–100 triage score. Higher = inspect sooner. Not a collapse probability. */
export function riskScore(b: Building, micro: Microclimate, now = 2026) {
  const exp = exposure(micro, b.material);
  const age = ageFactor(b.year, b.material, now);
  const raw = exp * (0.5 + 0.55 * age);
  return Math.round(Math.min(99, Math.max(4, raw * 100)));
}

export function averageMicro(buildings: Building[]): Microclimate {
  const keys: (keyof Microclimate)[] = [
    "windChannel",
    "solarLoad",
    "heatIsland",
    "saltExposure",
    "vibration",
    "freezeThaw",
  ];
  const out = {} as Microclimate;
  for (const k of keys) {
    out[k] = buildings.reduce((s, b) => s + b.micro[k], 0) / buildings.length;
  }
  return out;
}

export const DRIVER_LABELS: { key: keyof Microclimate; label: string; means: string }[] = [
  { key: "windChannel", label: "Wind channel", means: "Street-canyon acceleration off nearby towers" },
  { key: "solarLoad", label: "Solar load", means: "Unshaded facade hours from the LiDAR horizon" },
  { key: "heatIsland", label: "Heat island", means: "Surface temperature vs. nearby canopy" },
  { key: "saltExposure", label: "Salt & exhaust", means: "Road spray and coastal aerosol at grade" },
  { key: "vibration", label: "Ground vibration", means: "Subway, busway, or construction adjacency" },
  { key: "freezeThaw", label: "Freeze–thaw", means: "Cycles that spall concrete and open masonry joints" },
];
