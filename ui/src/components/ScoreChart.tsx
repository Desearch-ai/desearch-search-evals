import { useState } from "react";
import { formatValue } from "../format";
import { profileName, providerName } from "../providers";
import type { Scoreboard } from "../types";
import type { ColumnGroup } from "./ResultsTable";

const HIGHLIGHTED = new Set(["Desearch"]);

export function ScoreChart({
  data,
  groups,
}: {
  data: Scoreboard;
  groups: ColumnGroup[];
}) {
  const options = groups.flatMap((group) =>
    group.columns.map((column) => ({
      key: column.key,
      label: column.label,
      format: column.format ?? "percent",
    })),
  );
  const [picked, setPicked] = useState(options[0]?.key);
  const option = options.find((o) => o.key === picked) ?? options[0];
  if (!option) return null;
  const { key, format } = option;

  const value = (id: string) => data.profiles[id].metrics[key];
  // Time and cost read best when lowest; scores and counts when highest.
  const lowerIsBetter = format === "seconds" || format === "usd";
  const measured = data.routes.filter((route) => value(route.id) != null);
  const routes = [...measured].sort((a, b) =>
    lowerIsBetter
      ? (value(a.id) ?? 0) - (value(b.id) ?? 0)
      : (value(b.id) ?? 0) - (value(a.id) ?? 0),
  );
  const largest = Math.max(...routes.map((route) => value(route.id) ?? 0), 0);
  const width = (metric: number) =>
    format === "percent" ? metric : largest ? metric / largest : 0;

  return (
    <figure className="score-chart">
      {options.length > 1 && (
        <div className="chart-options" role="group" aria-label="Chart metric">
          {options.map((choice) => (
            <button
              key={choice.key}
              type="button"
              aria-pressed={choice.key === key}
              onClick={() => setPicked(choice.key)}
            >
              {choice.label}
            </button>
          ))}
        </div>
      )}
      <ol className="chart-bars">
        {routes.map((profile) => {
          const metric = value(profile.id) ?? 0;
          return (
            <li
              key={profile.id}
              className={
                HIGHLIGHTED.has(providerName(profile))
                  ? "is-desearch"
                  : undefined
              }
            >
              <span className="chart-label">{profileName(profile)}</span>
              <span className="chart-track" aria-hidden="true">
                <span style={{ width: `${width(metric) * 100}%` }} />
              </span>
              <strong>{formatValue(metric, format)}</strong>
            </li>
          );
        })}
      </ol>
    </figure>
  );
}
