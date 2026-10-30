/**
 * Categorical colors. Values are CSS variables (defined in globals.css for light + dark),
 * so SVG marks re-theme automatically. Colors follow the entity, never its rank.
 */
const SLOTS = [1, 2, 3, 4, 5, 6, 7, 8].map((i) => `var(--series-${i})`);
export const OTHER_COLOR = "var(--series-other)";

/** Fixed provider → slot mapping (stable across every page). */
const PROVIDER_SLOT: Record<string, number> = {
  google: 0,
  gemini: 0,
  vertex: 0,
  anthropic: 1,
  openai: 2,
  azure_openai: 2,
  mistral: 3,
  cohere: 4,
  bedrock: 5,
  aws: 5,
  groq: 6,
  together: 7,
  deepseek: 7,
};

/** Canonical stacking/legend order for providers (matches validated adjacent-slot order). */
export function providerRank(p: string): number {
  const slot = PROVIDER_SLOT[p.toLowerCase()];
  return slot === undefined ? 100 : slot;
}

export function isOther(key: string): boolean {
  const k = key.toLowerCase();
  return k === "other" || k === "(other)" || k === "others";
}

export function providerColor(p: string): string {
  if (isOther(p)) return OTHER_COLOR;
  const slot = PROVIDER_SLOT[p.toLowerCase()];
  if (slot !== undefined) return SLOTS[slot];
  // deterministic fallback by name hash so unknown providers keep a stable color
  let h = 0;
  for (const c of p) h = (h * 31 + c.charCodeAt(0)) >>> 0;
  return SLOTS[3 + (h % 5)];
}

export function providerLabel(p: string): string {
  const known: Record<string, string> = {
    openai: "OpenAI",
    anthropic: "Anthropic",
    google: "Google",
    mistral: "Mistral",
    cohere: "Cohere",
    bedrock: "Bedrock",
    azure_openai: "Azure OpenAI",
    groq: "Groq",
    deepseek: "DeepSeek",
    together: "Together",
  };
  return known[p.toLowerCase()] ?? p;
}

/**
 * Assign colors for a list of series keys of an arbitrary dimension.
 * Provider dimension uses the fixed map; for others, keys get slots in the order given
 * (callers pass a stable, cost-ranked key list), and "other" is always gray.
 */
export function seriesColors(keys: string[], dimension: string): Record<string, string> {
  const out: Record<string, string> = {};
  let i = 0;
  for (const k of keys) {
    if (isOther(k)) out[k] = OTHER_COLOR;
    else if (dimension === "provider") out[k] = providerColor(k);
    else out[k] = i < SLOTS.length ? SLOTS[i++] : OTHER_COLOR;
  }
  return out;
}

export function seriesLabel(key: string, dimension: string): string {
  if (isOther(key)) return "Other";
  return dimension === "provider" ? providerLabel(key) : key;
}
