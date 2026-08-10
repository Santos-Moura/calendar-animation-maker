# Invisible summary ordering

Google Calendar does not expose a horizontal subcolumn field. The project therefore keeps two
event properties deliberately separate:

```text
summary -> deterministic horizontal ordering key
colorId -> visible Calendar color
```

New full-grid plans use `zero-width` ordering by default. The generator uses lexicographically
ordered combinations of only these codepoints:

```text
U+200B ZERO WIDTH SPACE
U+200C ZERO WIDTH NON-JOINER
U+200D ZERO WIDTH JOINER
U+2060 WORD JOINER
U+2063 INVISIBLE SEPARATOR
```

The first 18 two-codepoint combinations are the exact sequence validated by the project. The
generator is deterministic, unique, and supports other configured slot counts. It retains two
codepoints through 25 slots and increases the combination length only when necessary, without
introducing another Unicode character.

## Production behavior

```text
new full-grid plan -> zero-width
--subcolumn-ordering zero-width -> zero-width
--subcolumn-ordering numeric -> visible 00..NN fallback
persisted numeric plan -> its recorded strategy and summaries
persisted summary-prefix plan -> legacy numeric behavior
sparse plan without an explicit strategy -> blank summary
```

An old plan is uploaded from its persisted event drafts; changing the default does not regenerate
or reinterpret it. Private metadata records `subcolumn_index`, `subcolumn_order_strategy`, the raw
`subcolumn_order_key`, and an auditable `subcolumn_order_key_codepoints` value. Debugging and
cleanup do not need to infer a slot from the summary.

Empty or equal summaries are not a substitute. They eliminate distinct ordering keys and therefore
remove the deterministic ordering evidence.

## Empirical evidence

The strategy was validated against the project's real Google Calendar laboratory:

- 18 generated summaries and 18 unique Python strings;
- strict lexicographic ordering;
- 18/18 exact Google Calendar API round-trip matches;
- no trimming, collapsing, removal, or Unicode normalization;
- exact values preserved in DOM `textContent` and `innerText`;
- left-to-right order stable after refresh;
- order and geometry stable after next-week/back navigation;
- manual real-frame geometry equivalent to numeric summaries;
- near-invisible visual result.

This is empirically validated behavior for this project. It is not a documented Google Calendar
API or rendering contract, so `numeric` remains available as the visible baseline and fallback.

The time label rendered by Google Calendar remains outside this feature. No CSS injection, DOM
modification, OCR, inpainting, or screenshot post-processing is performed.
