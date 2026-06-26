/** LRU cache for synthesized audio blob URLs, keyed by message ID. */

const MAX_ENTRIES = 5;

/**
 * Insertion-ordered Map used as an LRU store.
 * Oldest (least-recently-used) entry is always first in iteration order.
 */
const _cache = new Map<string, string>();

/** Returns the cached blob URL for a message, bumping it to MRU position. */
export function getCachedAudio(messageId: string): string | null {
  const url = _cache.get(messageId);
  if (!url) return null;
  _cache.delete(messageId);
  _cache.set(messageId, url);
  return url;
}

/**
 * Stores a blob URL for a message.
 * Evicts the least-recently-used entry (with URL revocation) when over capacity.
 * Re-inserting an existing key bumps it to MRU without eviction.
 */
export function setCachedAudio(messageId: string, blobUrl: string): void {
  if (_cache.has(messageId)) {
    _cache.delete(messageId);
  } else if (_cache.size >= MAX_ENTRIES) {
    const oldest = _cache.keys().next().value;
    if (oldest !== undefined) {
      URL.revokeObjectURL(_cache.get(oldest)!);
      _cache.delete(oldest);
    }
  }
  _cache.set(messageId, blobUrl);
}
