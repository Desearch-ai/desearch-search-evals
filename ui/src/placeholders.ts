import type { Profile, ProfileScore, Scoreboard } from "./types";

// Estimates for metrics a run did not record: these BrowseComp profiles ran before
// time per task was measured (searches x latency plus about 2.5s per model turn).
const UNTIMED: { route: Profile; metrics: ProfileScore["metrics"] }[] = [
  {
    route: { id: "exa-standard", transport: "exa", mode: "auto" },
    metrics: { seconds_per_task: 53.3 },
  },
  {
    route: { id: "parallel-standard", transport: "parallel", mode: "basic" },
    metrics: { seconds_per_task: 78.0 },
  },
];

export const PLACEHOLDERS: Record<
  string,
  { route: Profile; metrics: ProfileScore["metrics"] }[]
> = {
  browsecomp: UNTIMED,
};

export function withPlaceholders(scoreboard: Scoreboard): Scoreboard {
  const entries = PLACEHOLDERS[scoreboard.run_id] ?? [];
  if (!entries.length) return scoreboard;

  const routes = [...scoreboard.routes];
  const profiles = { ...scoreboard.profiles };
  for (const { route, metrics } of entries) {
    const current = profiles[route.id];
    if (!current) {
      routes.push({ ...route, placeholder: true });
      profiles[route.id] = { metrics };
      continue;
    }
    const missing = Object.entries(metrics).filter(
      ([key]) => current.metrics[key] == null || current.metrics[key] === 0,
    );
    if (missing.length)
      profiles[route.id] = {
        ...current,
        metrics: { ...current.metrics, ...Object.fromEntries(missing) },
      };
  }
  return { ...scoreboard, routes, profiles };
}
