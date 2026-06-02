import type { ReactNode } from "react";
import {
  ResponsiveContainer, BarChart, Bar, XAxis, YAxis, Cell, LabelList,
  CartesianGrid, Tooltip, Legend,
} from "recharts";
import type { Scoreboard } from "../types";
import { PROVIDER_BY_KEY, PROVIDERS } from "../providers";

const AXIS = "#8a96a7";
const GRID = "rgba(255,255,255,0.07)";
const H = 250;

const pct = (v: number | null | undefined): number =>
  v == null ? 0 : Math.round(v * 1000) / 10;

const tooltipStyle = {
  background: "rgba(15,20,25,0.96)",
  border: "1px solid rgba(255,255,255,0.14)",
  borderRadius: 10,
  fontSize: 12,
  color: "#f4f7fb",
};

const METRICS = [
  ["source_relevance", "Source Relevance"],
  ["answer_quality", "Answer Quality"],
  ["groundedness", "Groundedness"],
] as const;

function ChartCard({ title, children }: { title: string; children: ReactNode }) {
  return (
    <div className="glass-strong rounded-2xl p-4 hover-lift">
      <div className="text-xs font-medium text-text-muted uppercase tracking-wider mb-3">{title}</div>
      <ResponsiveContainer width="100%" height={H}>{children as any}</ResponsiveContainer>
    </div>
  );
}

export function Charts({ data }: { data: Scoreboard }) {
  const rows = [...data.rows].sort((a, b) => (b.composite ?? 0) - (a.composite ?? 0));

  const barData = rows.map((r) => {
    const cfg = PROVIDER_BY_KEY[r.provider];
    const v = pct(r.composite);
    return { key: r.provider, name: cfg?.label ?? r.provider, value: v, label: `${v}%`, color: cfg?.color ?? "#8a96a7" };
  });

  const metricData = METRICS.map(([k, label]) => {
    const o: Record<string, number | string> = { metric: label };
    for (const r of data.rows) o[r.provider] = pct(r[k] as number | null);
    return o;
  });

  return (
    <div className="grid grid-cols-1 lg:grid-cols-2 gap-4 mb-6">
      <ChartCard title="Composite Score">
        <BarChart data={barData} layout="vertical" margin={{ left: 6, right: 44, top: 4, bottom: 4 }}>
          <XAxis type="number" domain={[0, 100]} hide />
          <YAxis type="category" dataKey="name" width={88}
                 tick={{ fill: AXIS, fontSize: 12 }} axisLine={false} tickLine={false} />
          <Tooltip cursor={{ fill: "rgba(255,255,255,0.04)" }} contentStyle={tooltipStyle}
                   formatter={(v: any) => [`${v}%`, "composite"]} />
          <Bar dataKey="value" name="composite" radius={[4, 4, 4, 4]} barSize={22} isAnimationActive={false}>
            {barData.map((d) => (
              <Cell key={d.key} fill={d.color} fillOpacity={d.key === "desearch" ? 1 : 0.82} />
            ))}
            <LabelList dataKey="label" position="right" fill={AXIS} fontSize={12} />
          </Bar>
        </BarChart>
      </ChartCard>

      <ChartCard title="Per-Metric Breakdown">
        <BarChart data={metricData} margin={{ left: -14, right: 8, top: 4, bottom: 4 }} barCategoryGap="22%">
          <CartesianGrid strokeDasharray="3 3" stroke={GRID} vertical={false} />
          <XAxis dataKey="metric" tick={{ fill: AXIS, fontSize: 11 }} axisLine={false} tickLine={false} />
          <YAxis domain={[0, 100]} tick={{ fill: AXIS, fontSize: 11 }} axisLine={false} tickLine={false} width={40} />
          <Tooltip cursor={{ fill: "rgba(255,255,255,0.04)" }} contentStyle={tooltipStyle}
                   formatter={(v: any) => `${v}%`} />
          <Legend wrapperStyle={{ fontSize: 11 }} iconType="circle" iconSize={8} />
          {PROVIDERS.map((p) => (
            <Bar key={p.key} dataKey={p.key} name={p.label} fill={p.color}
                 radius={[3, 3, 0, 0]} isAnimationActive={false} />
          ))}
        </BarChart>
      </ChartCard>
    </div>
  );
}