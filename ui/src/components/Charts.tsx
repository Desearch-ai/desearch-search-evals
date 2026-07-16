import { useMemo, type ReactNode } from "react";
import {
  ResponsiveContainer, BarChart, Bar, XAxis, YAxis, Cell, LabelList,
  CartesianGrid, Tooltip, Legend,
} from "recharts";
import type { Scoreboard } from "../types";
import type { BenchmarkResults } from "../data";
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

interface TimingRow {
  key: string;
  name: string;
  color: string;
  median: number;
  tail: number; // p90 - median, span between bar end and the p90 tick
  p90: number;
  mean: number;
  n: number;
  label: string;
}

function quantile(sorted: number[], p: number): number {
  if (!sorted.length) return 0;
  const idx = (sorted.length - 1) * p;
  const lo = Math.floor(idx), hi = Math.ceil(idx);
  return sorted[lo] + (sorted[hi] - sorted[lo]) * (idx - lo);
}

function timingRows(results: BenchmarkResults): TimingRow[] {
  const byProvider = new Map<string, number[]>();
  for (const answers of results.answersByQuestion.values()) {
    for (const [provider, a] of Object.entries(answers)) {
      if (!a || a.error || !(a.elapsed > 0)) continue;
      const vals = byProvider.get(provider) ?? [];
      vals.push(a.elapsed);
      byProvider.set(provider, vals);
    }
  }
  const rows: TimingRow[] = [];
  for (const [key, vals] of byProvider) {
    vals.sort((x, y) => x - y);
    const median = quantile(vals, 0.5);
    const p90 = quantile(vals, 0.9);
    const mean = vals.reduce((s, v) => s + v, 0) / vals.length;
    const cfg = PROVIDER_BY_KEY[key as keyof typeof PROVIDER_BY_KEY];
    rows.push({
      key,
      name: cfg?.label ?? key,
      color: cfg?.color ?? "#8a96a7",
      median, p90, mean,
      tail: Math.max(0, p90 - median),
      n: vals.length,
      label: `${median.toFixed(1)}s`,
    });
  }
  return rows.sort((a, b) => a.median - b.median);
}

function TimingTooltip({ active, payload }: { active?: boolean; payload?: any[] }) {
  const row: TimingRow | undefined = payload?.[0]?.payload;
  if (!active || !row) return null;
  return (
    <div style={tooltipStyle} className="px-3 py-2">
      <div className="font-medium mb-1">{row.name}</div>
      <div className="font-mono text-[11px] leading-relaxed">
        median {row.median.toFixed(1)}s · p90 {row.p90.toFixed(1)}s
        <br />mean {row.mean.toFixed(1)}s · {row.n} answers
      </div>
    </div>
  );
}

export function Charts({ data, results }: { data: Scoreboard; results?: BenchmarkResults | null }) {
  const rows = [...data.rows].sort((a, b) => (b.composite ?? 0) - (a.composite ?? 0));
  const timing = useMemo(() => (results ? timingRows(results) : []), [results]);

  // p90 tick: the stacked "tail" segment draws nothing but a marker at its end,
  // so bar length stays proportional to the labeled stat (median).
  const TailTick = (p: any) => {
    const { x, y, width, height, payload } = p;
    return (
      <rect x={x + width - 1} y={y - 3} width={2.5} height={height + 6} rx={1}
            fill={payload?.color ?? AXIS} fillOpacity={0.75} />
    );
  };

  const MedianLabel = (p: any) => {
    const { x, y, width, height, index } = p;
    const row = timing[index];
    if (!row) return null;
    const barEnd = x + width;
    const pxPerSec = row.median > 0 ? width / row.median : 0;
    const tickX = barEnd + Math.max(0, pxPerSec * (row.p90 - row.median));
    // Label hugs the bar (it states the median); jump past the tick only on collision.
    const labelX = tickX - barEnd < 44 ? tickX + 8 : barEnd + 8;
    return (
      <text x={labelX} y={y + height / 2} fill={AXIS} fontSize={12} dominantBaseline="central">
        {row.label}
      </text>
    );
  };

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

      {timing.length > 0 && (
        <div className="lg:col-span-2">
          <ChartCard title="Response Time — bar = median · tick = p90 · fastest first">
            <BarChart data={timing} layout="vertical" margin={{ left: 6, right: 52, top: 4, bottom: 4 }}>
              <CartesianGrid strokeDasharray="3 3" stroke={GRID} horizontal={false} />
              <XAxis type="number" unit="s" tick={{ fill: AXIS, fontSize: 11 }}
                     axisLine={false} tickLine={false} />
              <YAxis type="category" dataKey="name" width={88}
                     tick={{ fill: AXIS, fontSize: 12 }} axisLine={false} tickLine={false} />
              <Tooltip cursor={{ fill: "rgba(255,255,255,0.04)" }} content={<TimingTooltip />} />
              <Bar dataKey="median" stackId="t" barSize={22} radius={[4, 4, 4, 4]} isAnimationActive={false}>
                {timing.map((d) => (
                  <Cell key={d.key} fill={d.color} fillOpacity={d.key === "desearch" ? 1 : 0.82} />
                ))}
                <LabelList content={MedianLabel} />
              </Bar>
              <Bar dataKey="tail" stackId="t" shape={TailTick} isAnimationActive={false} />
            </BarChart>
          </ChartCard>
        </div>
      )}
    </div>
  );
}