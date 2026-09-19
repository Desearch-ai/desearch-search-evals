import type { Profile } from "./types";

const names: Record<string, string> = {
  desearch: "Desearch",
  exa: "Exa",
  parallel: "Parallel",
  perplexity: "Perplexity",
  tavily: "Tavily",
};

export function providerName(profile: Profile): string {
  const identifier = profile.id.split("-")[0];
  const engine =
    profile.engine ?? (names[identifier] ? identifier : profile.transport);
  return names[engine] ?? engine;
}

export function profileName(profile: Profile): string {
  return providerName(profile) + (profile.mode ? " · " + profile.mode : "");
}
