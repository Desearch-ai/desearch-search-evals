import { Crown, Trophy, Medal } from "lucide-react";
import type { Scoreboard } from "../types";
import { PROVIDER_BY_KEY } from "../providers";

const COLS: Array<{ key: keyof Scoreboard["rows"][number]; label: string; weight: number; tooltip: string }> = [
  {
    key: "source_relevance",
    label: "Source Relevance",
    weight: 0.45,
    tooltip:
      "For every URL the provider cited, the judge fetches the page and decides whether it is relevant " +
      "to the question (YES / MAYBE / NO). Mean across all cited URLs.",
  },
  {
    key: "answer_quality",
    label: "Answer Quality",
    weight: 0.25,
    tooltip:
      "The judge reads the question and answer and picks one of RESPONSIVE / APPROPRIATE_DECLINE / EVASIVE / " +
      "WRONG_DECLINE / HALLUCINATED. Catches evasion, wrong refusals, and confident fabrication.",
  },
  {
    key: "groundedness",
    label: "Groundedness",
    weight: 0.30,
    tooltip:
      "For every claim in the answer, the judge fetches the cited page and verifies the page's actual " +
      "content supports it. Catches hallucinated citations, the proof real-time search happened.",
  },
];

function fmt(v: number | null | undefined): string {
  if (v === null || v === undefined) return "n/a";
  return `${(v * 100).toFixed(1)}%`;
}

function cellColor(v: number | null | undefined): string {
  if (v === null || v === undefined) return "text-text-dim";
  if (v >= 0.8) return "text-success";
  if (v >= 0.5) return "text-warn";
  return "text-danger";
}

function rankIcon(rank: number) {
  if (rank === 0) return <Crown size={14} className="text-amber-400 inline-block -mt-0.5" />;
  if (rank === 1) return <Trophy size={14} className="text-zinc-300 inline-block -mt-0.5" />;
  if (rank === 2) return <Medal size={14} className="text-orange-400 inline-block -mt-0.5" />;
  return <span className="text-text-dim text-xs">{rank + 1}</span>;
}

export function Scorecard({ data }: { data: Scoreboard }) {
  const rows = data.rows;

  return (
    <div className="glass-strong rounded-2xl overflow-hidden mb-4 hover-lift">
      <div className="px-5 py-4 border-b border-border flex items-center justify-between flex-wrap gap-3">
        <div>
          <div className="text-sm font-semibold text-text tracking-tight">Composite Leaderboard</div>
          <div className="text-xs text-text-muted mt-0.5">
            Ranked by a weighted blend of three judge-graded evaluators.
          </div>
        </div>
        <div className="text-xs text-text-muted tabular flex gap-4">
          {COLS.map((c) => (
            <span key={c.key} title={c.tooltip} className="cursor-help">
              <span className="text-text-dim underline decoration-dotted decoration-text-dim/40 underline-offset-2">{c.label}</span>{" "}
              <span className="text-text-muted">{Math.round(c.weight * 100)}%</span>
            </span>
          ))}
        </div>
      </div>

      <table className="w-full text-sm">
        <thead className="bg-surface-2/40 text-text-muted text-xs uppercase tracking-wider">
          <tr>
            <th className="text-left px-4 py-2.5 w-10">#</th>
            <th className="text-left px-4 py-2.5">Provider</th>
            {COLS.map((c) => (
              <th key={c.key} title={c.tooltip} className="text-right px-4 py-2.5 font-normal cursor-help">
                <span className="underline decoration-dotted decoration-text-dim/40 underline-offset-2">{c.label}</span>
              </th>
            ))}
            <th className="text-right px-4 py-2.5 font-semibold text-text cursor-help"
                title="Weighted mean: 0.45·source_relevance + 0.25·answer_quality + 0.30·groundedness. Missing evaluators are skipped and weights renormalized.">
              <span className="underline decoration-dotted decoration-text-dim/40 underline-offset-2">Composite</span>
            </th>
          </tr>
        </thead>
        <tbody>
          {rows.map((row, i) => {
            const cfg = PROVIDER_BY_KEY[row.provider];
            const isDesearch = row.provider === "desearch";
            return (
              <tr key={row.provider}
                  className={`border-t border-border ${isDesearch ? "row-brand" : "hover:bg-surface-2/30"}`}>
                <td className="px-4 py-3 tabular">{rankIcon(i)}</td>
                <td className="px-4 py-3">
                  <div className="flex items-center gap-2.5">
                    <span className={`inline-block w-2 h-2 rounded-full ${cfg?.dot ?? "bg-zinc-500"}`}></span>
                    <span className={isDesearch ? "font-semibold" : "font-medium"}>{cfg?.label ?? row.provider}</span>
                  </div>
                </td>
                {COLS.map((c) => {
                  const v = row[c.key] as number | null;
                  return (
                    <td key={c.key} className={`text-right tabular px-4 py-3 ${cellColor(v)}`}>{fmt(v)}</td>
                  );
                })}
                <td className="text-right tabular px-4 py-3">
                  <span className={`font-semibold ${cellColor(row.composite)}`}>{fmt(row.composite)}</span>
                </td>
              </tr>
            );
          })}
        </tbody>
      </table>
    </div>
  );
}
