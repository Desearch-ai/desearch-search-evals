import { formatValue } from "../format";
import type { Scoreboard } from "../types";
import { ProviderIdentity } from "./ProviderIdentity";

export interface Column {
  key: string;
  label: string;
  format?: "percent" | "number" | "usd" | "seconds";
}

export interface ColumnGroup {
  title: string;
  columns: Column[];
}

export function ResultsTable({
  data,
  groups,
}: {
  data: Scoreboard;
  groups: ColumnGroup[];
}) {
  const columns = groups.flatMap((group) => group.columns);
  const keys = columns.map((column) => column.key);
  const lead = keys[0];
  const best = Object.fromEntries(
    keys.map((key) => [
      key,
      Math.max(
        ...data.routes.map((r) => data.profiles[r.id].metrics[key] ?? 0),
      ),
    ]),
  );
  const routes = [...data.routes].sort(
    (a, b) =>
      (data.profiles[b.id].metrics[lead] ?? 0) -
      (data.profiles[a.id].metrics[lead] ?? 0),
  );

  return (
    <div
      className="table-scroll"
      role="region"
      aria-label="Provider scores"
      tabIndex={0}
    >
      <table className="score-table results-table">
        <thead>
          <tr>
            <th scope="col">Provider</th>
            {groups.flatMap((group) =>
              group.columns.map((column) => (
                <th scope="col" key={column.key}>
                  {column.label}
                </th>
              )),
            )}
          </tr>
        </thead>
        <tbody>
          {routes.map((profile) => {
            const metrics = data.profiles[profile.id].metrics;
            return (
              <tr key={profile.id}>
                <th scope="row">
                  <ProviderIdentity profile={profile} />
                </th>
                {columns.map(({ key, format }) => {
                  const value = metrics[key];
                  const top = value != null && value === best[key];
                  return (
                    <td key={key} className={top ? "best" : undefined}>
                      {key === lead ? (
                        <div className="score-cell">
                          <span className="mini-track" aria-hidden="true">
                            <span style={{ width: (value ?? 0) * 100 + "%" }} />
                          </span>
                          <strong>{formatValue(value, format)}</strong>
                        </div>
                      ) : (
                        formatValue(value, format)
                      )}
                    </td>
                  );
                })}
              </tr>
            );
          })}
        </tbody>
      </table>
    </div>
  );
}
