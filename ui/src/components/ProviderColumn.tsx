import { AlertCircle, Check } from "lucide-react";
import { safeUrl } from "../format";
import type { GradedResult, ResultRow } from "../types";

function domainOf(url?: string) {
  return url
    ? new URL(url).hostname.replace(/^www\./, "")
    : "Source URL unavailable";
}

function GoldResult({ result }: { result: GradedResult }) {
  const url = safeUrl(result.url);
  return (
    <div className="result-card">
      <div className="result-summary">
        <span className="rank-number">
          {String(result.rank).padStart(2, "0")}
        </span>
        <span className="result-summary-copy">
          <span className="source-domain">{domainOf(url)}</span>
          {url ? (
            <a
              className="result-title"
              href={url}
              target="_blank"
              rel="noopener noreferrer"
            >
              {result.title || result.url}
            </a>
          ) : (
            <span className="result-title">{result.title}</span>
          )}
        </span>
        {result.gold && (
          <span className="verdict positive">
            <Check size={12} />
            Gold URL
          </span>
        )}
      </div>
    </div>
  );
}

export function ProviderColumn({ result }: { result?: ResultRow }) {
  if (!result || result.search_status !== "ok")
    return (
      <p className="notice warning">
        <AlertCircle size={16} />
        Search failed. This question scores zero.
      </p>
    );
  if (!result.results?.length)
    return <p className="notice">No results. This question scores zero.</p>;
  return (
    <div className="result-list">
      {result.latency_seconds != null && (
        <p className="result-latency">
          Search took {result.latency_seconds.toFixed(2)}s
        </p>
      )}
      {result.results.map((item) => (
        <GoldResult key={item.rank} result={item} />
      ))}
    </div>
  );
}
