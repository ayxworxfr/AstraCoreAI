/** Remove fenced code blocks, inline code, and math before splitting. */
const FENCED_CODE_RE = /```[\s\S]*?```/g;
const INLINE_CODE_RE = /`[^`\n]+`/g;
const MATH_BLOCK_RE = /\$\$[\s\S]*?\$\$/g;
const MATH_INLINE_RE = /\$[^$\n]+\$/g;

const MIN_SENTENCE_LEN = 2;
/** Chrome silently truncates utterances above ~200 chars; keep chunks well below. */
const MAX_CHUNK_LEN = 180;
/** Placeholder for protected (non-boundary) periods during processing. */
const PERIOD_GUARD = '\x00';
/** Sentinel inserted at sentence boundaries before splitting. */
const BOUNDARY_MARK = '\x01';

/** Common abbreviations whose trailing periods are not sentence boundaries. */
const ABBREV_RE =
  /\b(Mr|Mrs|Ms|Dr|Prof|Sr|Jr|Inc|Ltd|Co|Corp|vs|etc|No|Fig|Jan|Feb|Mar|Apr|Jun|Jul|Aug|Sep|Oct|Nov|Dec)\./gi;

/** Replace periods that are NOT sentence boundaries with PERIOD_GUARD. */
function protectNonBoundaryPeriods(text: string): string {
  return (
    text
      // Decimal numbers: 3.14
      .replace(/(\d)\.(?=\d)/g, `$1${PERIOD_GUARD}`)
      // Acronyms: U.S.A., U.S. (requires 2+ uppercase-letter+period sequences)
      .replace(/\b(?:[A-Z]\.){2,}/g, (m) => m.replace(/\./g, PERIOD_GUARD))
      // Common abbreviations
      .replace(ABBREV_RE, (m) => m.slice(0, -1) + PERIOD_GUARD)
      // e.g. / i.e. / et al. — protect the trailing period only
      .replace(/\b(e\.g|i\.e|et\s+al)\./gi, (m) => m.slice(0, -1) + PERIOD_GUARD)
  );
}

/** Further split chunks exceeding MAX_CHUNK_LEN at soft break points (, ; : space). */
function capLength(chunks: string[]): string[] {
  const out: string[] = [];
  for (const chunk of chunks) {
    if (chunk.length <= MAX_CHUNK_LEN) {
      out.push(chunk);
      continue;
    }
    let start = 0;
    while (start < chunk.length) {
      const end = start + MAX_CHUNK_LEN;
      if (end >= chunk.length) {
        const piece = chunk.slice(start).trim();
        if (piece.length >= MIN_SENTENCE_LEN) out.push(piece);
        break;
      }
      // Find the last soft-break position within the window
      const window = chunk.slice(start, end);
      const softBreak = window.search(/[,;:\s][^,;:\s]*$/);
      const cut = softBreak > 0 ? start + softBreak + 1 : end;
      const piece = chunk.slice(start, cut).trim();
      if (piece.length >= MIN_SENTENCE_LEN) out.push(piece);
      start = cut;
    }
  }
  return out;
}

/**
 * Split text into TTS-ready sentences.
 *
 * Removes code blocks and math, then applies sentence boundary detection:
 * - Chinese full-stop punctuation (。！？…) — always split
 * - ASCII ! ? — always split
 * - English period — split only when followed by space + uppercase letter;
 *   periods in abbreviations (Dr. Mr. etc.), decimals (3.14), and acronyms
 *   (U.S.A.) are protected and never treated as boundaries
 * - Newlines — always split
 *
 * Any resulting chunk exceeding MAX_CHUNK_LEN chars is further split at the
 * nearest soft boundary (comma, semicolon, colon, or space) to work around
 * Chrome's speechSynthesis truncation bug at ~200 characters.
 */
export function splitIntoSentences(text: string): string[] {
  const cleaned = text
    .replace(FENCED_CODE_RE, ' ')
    .replace(INLINE_CODE_RE, ' ')
    .replace(MATH_BLOCK_RE, ' ')
    .replace(MATH_INLINE_RE, ' ');

  const guarded = protectNonBoundaryPeriods(cleaned);

  // Insert BOUNDARY_MARK at every sentence boundary
  const marked = guarded
    .replace(/[。！？…]+/g, (m) => m + BOUNDARY_MARK)
    .replace(/[!?]+/g, (m) => m + BOUNDARY_MARK)
    .replace(/\.+(?=[ \t]+[A-Z])/g, (m) => m + BOUNDARY_MARK)
    .replace(/\n/g, BOUNDARY_MARK);

  const sentences = marked
    .split(BOUNDARY_MARK)
    .map((s) => s.replace(/\x00/g, '.').trim())
    .filter((s) => s.length >= MIN_SENTENCE_LEN);

  return capLength(sentences);
}
