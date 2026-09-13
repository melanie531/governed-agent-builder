// DISPLAY HELPERS ONLY. This module carries NO model list and NO launch dates.
//
// The Models catalog is driven entirely by real bedrock:ListFoundationModels
// fields delivered on each catalog Entry (provider, category, lifecycle,
// streaming, inference_types, model_id). There is intentionally no hand-curated
// or guessed model map here anymore: the previous ModelCatalogMeta guessed-list
// (invented model IDs + invented "official launch dates") was rejected because
// the product must show the account's actual Bedrock catalog, not a guess.
//
// RECENCY / DATES: recency is a REAL rolling-window filter over VERIFIED launch
// dates. ListFoundationModels itself exposes no date, so the backend joins each
// modelId (exact match) against a reviewer-verified launch-date map sourced from
// official AWS model cards (see backend/model_launch_dates.json + model_recency.py)
// and emits recency ('recent' | 'pending_verification') + launch_date +
// launch_date_source per row. Lifecycle (ACTIVE|LEGACY) is shown as separate
// lifecycle-info ONLY and is NOT used for recency.

export type ModelCategory = 'text' | 'multimodal' | 'image' | 'embeddings' | 'speech';

export const CATEGORY_LABELS: Record<ModelCategory, string> = {
  text: 'Text', multimodal: 'Multimodal', image: 'Image',
  embeddings: 'Embeddings', speech: 'Speech',
};

export const ALL_CATEGORIES: ModelCategory[] = ['text', 'multimodal', 'image', 'embeddings', 'speech'];

export function categoryLabel(category?: string): string {
  return (category && CATEGORY_LABELS[category as ModelCategory]) || 'Other';
}

// Lifecycle is lifecycle-info only (NOT a recency signal).
export type Lifecycle = 'ACTIVE' | 'LEGACY';

export function lifecycleLabel(lifecycle?: string | null): string {
  if (lifecycle === 'ACTIVE') return 'Active';
  if (lifecycle === 'LEGACY') return 'Legacy';
  return 'Lifecycle unknown';
}
