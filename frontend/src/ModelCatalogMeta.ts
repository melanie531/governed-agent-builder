// DISPLAY HELPERS ONLY. This module carries NO model list and NO launch dates.
//
// The Models catalog is driven entirely by real bedrock:ListFoundationModels
// fields delivered on each catalog Entry (provider, category, lifecycle,
// streaming, inference_types, model_id). There is intentionally no hand-curated
// or guessed model map here anymore: the previous ModelCatalogMeta guessed-list
// (invented model IDs + invented "official launch dates") was rejected because
// the product must show the account's actual Bedrock catalog, not a guess.
//
// RECENCY / DATES: ListFoundationModels exposes NO launch or creation date, so
// true "launched in last 6 months" filtering is not possible from the API. We do
// NOT fabricate dates. The honest available signal is modelLifecycle.status
// (ACTIVE | LEGACY), which is surfaced as the recency-adjacent badge/filter.

export type ModelCategory = 'text' | 'multimodal' | 'image' | 'embeddings' | 'speech';

export const CATEGORY_LABELS: Record<ModelCategory, string> = {
  text: 'Text', multimodal: 'Multimodal', image: 'Image',
  embeddings: 'Embeddings', speech: 'Speech',
};

export const ALL_CATEGORIES: ModelCategory[] = ['text', 'multimodal', 'image', 'embeddings', 'speech'];

export function categoryLabel(category?: string): string {
  return (category && CATEGORY_LABELS[category as ModelCategory]) || 'Other';
}

// Lifecycle is the honest recency-adjacent signal (no launch date exists).
export type Lifecycle = 'ACTIVE' | 'LEGACY';

export function lifecycleLabel(lifecycle?: string | null): string {
  if (lifecycle === 'ACTIVE') return 'Active';
  if (lifecycle === 'LEGACY') return 'Legacy';
  return 'Lifecycle unknown';
}
