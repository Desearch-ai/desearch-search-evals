import { providerName } from "../providers";
import type { Profile } from "../types";
import logo from "../assets/desearch-logo.png";
import exa from "../assets/logos/exa.png";
import parallel from "../assets/logos/parallel.png";
import perplexity from "../assets/logos/perplexity.png";
import tavily from "../assets/logos/tavily.png";

const LOGOS: Record<string, string> = {
  Exa: exa,
  Parallel: parallel,
  Perplexity: perplexity,
  Tavily: tavily,
};

export function ProviderIdentity({ profile }: { profile: Profile }) {
  const name = providerName(profile);
  return (
    <span className="provider-identity">
      <span className="provider-icon" aria-hidden="true">
        {name === "Desearch" ? (
          <span className="desearch-mark">
            <img src={logo} alt="" />
          </span>
        ) : LOGOS[name] ? (
          <img className="provider-logo" src={LOGOS[name]} alt="" />
        ) : (
          name.slice(0, 1)
        )}
      </span>
      <span className="provider-copy">
        <span className="provider-title">
          {name}
          {profile.mode && <span className="mode-badge">{profile.mode}</span>}
        </span>
        {name !== "Desearch" && (
          <span className="provider-transport">
            {profile.transport === "openrouter" ? "Via OpenRouter" : "Direct API"}
          </span>
        )}
      </span>
    </span>
  );
}
