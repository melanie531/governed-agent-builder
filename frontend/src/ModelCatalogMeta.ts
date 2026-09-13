// DISPLAY METADATA ONLY. This module never grants access, changes readiness, or
// makes a model callable. It maps a verified Bedrock native model ID to human
// presentation metadata (provider, friendly name, category, official launch
// date) so the Models catalog can behave like the AWS Bedrock model catalog:
// grouped by provider, filterable by category, name-first with the raw ID kept
// in a technical section. Access / readiness / grant / execution state is read
// exclusively from the API Entry (usable/requestable/execution_ready/binding)
// and is untouched here.
//
// Recency: a model is "within the discovery window" when its official launch
// date is on/after WINDOW_START. As of 2026-09-13 the product owner window is
// the last 6 months, i.e. on/after 2026-03-13. Launch dates below were each
// verified against the model's official AWS Bedrock model card
// (docs.aws.amazon.com/bedrock/latest/userguide/model-card-*). The date is the
// documented "Model launch date" — never a region-availability date and never a
// date parsed out of the model ID string.

export type ModelCategory = 'text' | 'multimodal' | 'image' | 'embeddings' | 'speech';

export type CuratedModel = {
  provider: string;      // Human provider name for grouping (Anthropic, Amazon, ...)
  name: string;          // Friendly model name shown as the card/row title
  category: ModelCategory;
  launchDate: string;    // ISO date of the official AWS Bedrock launch (GA/launch)
  sourceUrl: string;     // Authoritative model-card URL the date was verified from
  preview?: boolean;     // True when the model card marks it Preview / gated access
};

// The discovery-window boundary. Kept as a constant so tests and UI agree.
export const WINDOW_START = '2026-03-13';

// Keyed on the exact native Bedrock model ID. Native IDs (not inference-profile
// prefixes like us./global.) are the stable key; callers should strip region
// prefixes before lookup (see nativeKey below).
export const CURATED_MODELS: Record<string, CuratedModel> = {
  'anthropic.claude-fable-5-1-20260901-v1:0': {
    provider: 'Anthropic', name: 'Claude Fable 5.1', category: 'multimodal',
    launchDate: '2026-09-01',
    sourceUrl: 'https://docs.aws.amazon.com/bedrock/latest/userguide/model-card-anthropic-claude-fable-5-1.html',
  },
  'anthropic.claude-mythos-5-1-20260901-v1:0': {
    provider: 'Anthropic', name: 'Claude Mythos 5.1', category: 'text',
    launchDate: '2026-09-01', preview: true,
    sourceUrl: 'https://docs.aws.amazon.com/bedrock/latest/userguide/model-card-anthropic-claude-mythos-5-1.html',
  },
  'anthropic.claude-opus-5-20260724-v1:0': {
    provider: 'Anthropic', name: 'Claude Opus 5', category: 'multimodal',
    launchDate: '2026-07-24',
    sourceUrl: 'https://docs.aws.amazon.com/bedrock/latest/userguide/model-card-anthropic-claude-opus-5.html',
  },
  'anthropic.claude-sonnet-5-20260630-v1:0': {
    provider: 'Anthropic', name: 'Claude Sonnet 5', category: 'multimodal',
    launchDate: '2026-06-30',
    sourceUrl: 'https://docs.aws.amazon.com/bedrock/latest/userguide/model-card-anthropic-claude-sonnet-5.html',
  },
  'anthropic.claude-opus-4-8-20260528-v1:0': {
    provider: 'Anthropic', name: 'Claude Opus 4.8', category: 'multimodal',
    launchDate: '2026-05-28',
    sourceUrl: 'https://docs.aws.amazon.com/bedrock/latest/userguide/model-card-anthropic-claude-opus-4-8.html',
  },
  'anthropic.claude-opus-4-7-20260416-v1:0': {
    provider: 'Anthropic', name: 'Claude Opus 4.7', category: 'multimodal',
    launchDate: '2026-04-16',
    sourceUrl: 'https://docs.aws.amazon.com/bedrock/latest/userguide/model-card-anthropic-claude-opus-4-7.html',
  },
  'google.gemma-4-31b-v1:0': {
    provider: 'Google', name: 'Gemma 4 31B', category: 'multimodal',
    launchDate: '2026-03-31',
    sourceUrl: 'https://docs.aws.amazon.com/bedrock/latest/userguide/model-card-google-gemma-4-31b.html',
  },
  // Verified but intentionally OUT of the discovery window (kept documented so
  // reviewers can see the boundary was applied to real dates, not guessed).
  // Claude Haiku 4.5 launched 2025-10-16 — it is the one already-wired route and
  // therefore stays visible when the API returns it, but it is NOT part of the
  // recency-filtered curated discovery list.
  'anthropic.claude-haiku-4-5-20251001-v1:0': {
    provider: 'Anthropic', name: 'Claude Haiku 4.5', category: 'multimodal',
    launchDate: '2025-10-16',
    sourceUrl: 'https://docs.aws.amazon.com/bedrock/latest/userguide/model-card-anthropic-claude-haiku-4-5.html',
  },
};

// Strip an inference-profile region prefix and any gateway/target qualifier so a
// route ID resolves to its native model key. Mirrors AICatalog.routeFamily.
export function nativeKey(id: string | undefined): string {
  const qualified = id || '';
  const raw = qualified.includes('/') ? qualified.slice(qualified.lastIndexOf('/') + 1) : qualified;
  return raw.replace(/^(?:global|us|eu|apac)\./, '');
}

export function curatedFor(nativeModelId?: string, modelId?: string): CuratedModel | null {
  return CURATED_MODELS[nativeModelId || ''] || CURATED_MODELS[nativeKey(modelId)] || null;
}

export function withinWindow(launchDate: string, windowStart: string = WINDOW_START): boolean {
  return launchDate >= windowStart;
}

export const CATEGORY_LABELS: Record<ModelCategory, string> = {
  text: 'Text', multimodal: 'Multimodal', image: 'Image',
  embeddings: 'Embeddings', speech: 'Speech',
};
