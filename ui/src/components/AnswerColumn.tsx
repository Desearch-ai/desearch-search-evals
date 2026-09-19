import { Check, Minus } from "lucide-react";
import type { Profile, ResultRow } from "../types";
import { profileName } from "../providers";

export function AnswerColumn({
  profiles,
  rows,
}: {
  profiles: Profile[];
  rows: Map<string, ResultRow>;
}) {
  return (
    <div className="answer-list">
      {profiles.map((profile) => {
        const row = rows.get(profile.id);
        const correct = (row?.score ?? 0) >= 1;
        if (profile.placeholder)
          return (
            <div key={profile.id} className="answer-row">
              <span className="answer-provider">{profileName(profile)}</span>
              <span className="answer-meta">—</span>
            </div>
          );
        return (
          <div key={profile.id} className="answer-row">
            <span className="answer-provider">{profileName(profile)}</span>
            <span className={"verdict " + (correct ? "positive" : "")}>
              {correct ? <Check size={12} /> : <Minus size={12} />}
              {correct ? "Correct" : "Wrong"}
            </span>
            <span className="answer-value">
              {row?.answer || "No answer submitted"}
            </span>
            <span className="answer-meta">
              {row?.searches ?? 0} searches · {row?.turns ?? 0} turns
              {row?.seconds != null && ` · ${row.seconds.toFixed(1)}s total`}
              {row?.search_seconds != null &&
                ` · ${row.search_seconds.toFixed(1)}s searching`}
            </span>
          </div>
        );
      })}
    </div>
  );
}
