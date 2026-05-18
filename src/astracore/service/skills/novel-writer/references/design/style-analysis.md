# Style Analysis

Load this file when the user provides reference novels for style imitation.

## Input Handling

**Pasted excerpts:** Accept directly. Ask user to paste representative passages — ideally from the opening, a climactic scene, and a dialogue-heavy scene.

**File paths:** Read the file. If the file is very long (>10,000 characters), sample from the beginning, middle, and end (approximately 2,000 characters each).

## Analysis Dimensions

For each reference work, analyze:

### Narrative Perspective & Tense
- POV: first person / third limited / third omniscient / second
- Tense: past / present
- Narrative distance: intimate vs. panoramic

### Voice & Tone
- Formal vs. colloquial register
- Emotional temperature: detached, lyrical, urgent, ironic
- Humor style (if present): dry, slapstick, self-aware
- 网文专项: 爽文语气 / 正经热血 / 轻松幽默 / 阴暗压抑

### Prose Style
- Average sentence length: short (≤10 words) / medium / long (30+ words)
- Sentence rhythm: staccato, flowing, varied
- Vocabulary level: simple and direct / literary and layered / technical/specialized
- Imagery density: sparse, moderate, heavy
- Internal monologue frequency and style

### Pacing
- Chapter length (approximate word count)
- Scene vs. summary ratio
- Cliffhanger frequency and style
- 网文专项: 爽点密度（每X字出现一个爽点）

### Dialogue
- Dialogue-to-prose ratio
- Attribution style: said / action beats / minimal
- How characters are differentiated through speech
- Subtext density: explicit vs. implied

### Signature Elements
- Recurring motifs, imagery, or structural patterns
- Genre-specific markers (e.g., 系统面板 in 系统流, unreliable narrator in literary fiction)
- Opening hook style
- Chapter ending style

## Output

After analysis, synthesize findings into `novel-style.md` using this structure:

```markdown
# Style Guide

## Source
Imitated from: [title(s)]

## Narrative Perspective
[POV and tense, with brief description]

## Voice & Tone
[Register, emotional temperature, humor style]

## Prose Style
[Sentence length, rhythm, vocabulary, imagery, internal monologue]

## Pacing
[Chapter length target, scene/summary ratio, cliffhanger style, 爽点密度 if web novel]

## Dialogue Style
[Ratio, attribution, character differentiation, subtext]

## Signature Elements
[Recurring patterns, structural markers, opening/closing style]
```

If analyzing multiple reference works: note where they agree (apply those rules strictly) and where they diverge (present options to the user before proceeding).
