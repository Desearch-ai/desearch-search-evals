import type { Provider } from "./types";

// Display config for providers. Desearch is the brand color; others get neutral
// tints so the brand row stands out. Order = top-to-bottom in the scorecard.
export interface ProviderConfig {
  key: Provider;
  label: string;
  short: string;
  // Hex accent, shared by the scorecard dot and the charts.
  color: string;
  // Tailwind classes for the per-provider accent dot in the scorecard.
  dot: string;
  // Tailwind classes for the column header in QuestionCard.
  columnAccent: string;
}

export const PROVIDERS: ProviderConfig[] = [
  {
    key: "desearch",
    label: "Desearch",
    short: "Des",
    color: "#3b82f6",
    dot: "bg-brand shadow-[0_0_8px_var(--color-brand-glow)]",
    columnAccent: "border-brand/40 bg-brand/5",
  },
  {
    key: "gpt5mini",
    label: "GPT-5-mini",
    short: "GPT",
    color: "#34d399",
    dot: "bg-emerald-400/80",
    columnAccent: "border-emerald-400/20 bg-emerald-400/5",
  },
  {
    key: "perplexity",
    label: "Perplexity",
    short: "Ppx",
    color: "#a78bfa",
    dot: "bg-violet-400/80",
    columnAccent: "border-violet-400/20 bg-violet-400/5",
  },
  {
    key: "tavily",
    label: "Tavily",
    short: "Tav",
    color: "#22d3ee",
    dot: "bg-cyan-400/80",
    columnAccent: "border-cyan-400/20 bg-cyan-400/5",
  },
  {
    key: "exa",
    label: "Exa",
    short: "Exa",
    color: "#fb7185",
    dot: "bg-rose-400/80",
    columnAccent: "border-rose-400/20 bg-rose-400/5",
  },
];

export const PROVIDER_BY_KEY: Record<Provider, ProviderConfig> = Object.fromEntries(
  PROVIDERS.map((p) => [p.key, p]),
) as Record<Provider, ProviderConfig>;
