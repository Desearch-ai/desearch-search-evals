import type { Metric } from "./types";

export function formatMetric(metric?: Metric): string {
  if (metric == null) return "—";
  return `${(metric * 100).toFixed(1)}%`;
}

export function formatValue(
  metric: Metric | undefined,
  format: "percent" | "number" | "usd" | "seconds" = "percent",
): string {
  if (metric == null) return "—";
  if (format === "number") return metric.toFixed(1);
  if (format === "seconds") return `${metric.toFixed(metric < 10 ? 2 : 1)}s`;
  if (format === "usd") return `$${metric.toFixed(4)}`;
  return formatMetric(metric);
}

export function safeUrl(value?: string | null): string | undefined {
  if (
    !value ||
    Array.from(value).some((character) => character.charCodeAt(0) < 32)
  )
    return;
  try {
    const url = new URL(value);
    if (
      ["https:", "http:"].includes(url.protocol) &&
      !url.username &&
      !url.password
    )
      return url.href;
  } catch {
    return;
  }
}
