import { useState } from "react";
import { ChevronDown, Quote } from "lucide-react";
import { profileName } from "../providers";
import type { Profile, Question, ResultRow } from "../types";
import { AnswerColumn } from "./AnswerColumn";
import { ProviderColumn } from "./ProviderColumn";

export function QuestionCard({
  question,
  profiles,
  rows,
  agent,
}: {
  question: Question;
  profiles: Profile[];
  rows: Map<string, ResultRow>;
  agent?: boolean;
}) {
  const [open, setOpen] = useState(false);
  const [selected, setSelected] = useState(profiles[0]?.id ?? "");

  return (
    <details
      open={open}
      onToggle={(event) => setOpen(event.currentTarget.open)}
      className="question-card"
    >
      <summary className="question-summary">
        <span className="question-title">{question.question}</span>
        <ChevronDown size={17} className="disclosure-icon" />
      </summary>
      {open && (
        <div className="question-body">
          <div className="reference-answer">
            <div className="eyebrow">
              <Quote size={13} />
              Verified answer
            </div>
            <p className="answer-value">{question.answer}</p>
          </div>
          {agent ? (
            <AnswerColumn profiles={profiles} rows={rows} />
          ) : (
            <>
              <div
                className="provider-tabs"
                role="group"
                aria-label="Provider profiles"
              >
                {profiles.map((profile) => (
                  <button
                    key={profile.id}
                    type="button"
                    aria-pressed={selected === profile.id}
                    onClick={() => setSelected(profile.id)}
                  >
                    <span
                      className={
                        "result-dot " +
                        (rows.get(profile.id)?.metrics.gold_hit_at_10
                          ? "hit"
                          : "")
                      }
                      aria-hidden="true"
                    />
                    {profileName(profile)}
                  </button>
                ))}
              </div>
              <ProviderColumn key={selected} result={rows.get(selected)} />
            </>
          )}
        </div>
      )}
    </details>
  );
}
