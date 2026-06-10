## SAFE_AUTO (apply automatically)

### KB additions (KB entries with `confidence: high` AND no `*_corrected` flags)
Append these 18 verified items to the KB — high confidence and no field was flipped by the verifier, so they are the safest to land unattended:

- Manisan buah (Kosmetik Makanan / Umum / 2008.99)
- Teh celup/teh kering (Kosmetik Makanan / Umum / 0902.30)
- Catokan rambut (Lartas Ringan / Umum / 8516.32)
- Hair curler elektrik (Lartas Ringan / Umum / 8516.32)
- Sisir elektrik (Lartas Ringan / Umum / 8516.32)
- Cermin rias kosmetik (Umum 2 / Umum / 7009.92)
- Jump rope (Umum 1 / Umum / 9506.91)
- Sepeda statis/spinning bike (Lartas Ringan / Umum / 9506.91)
- Resistance band (Umum 1 / Umum / 9506.91)
- Bola sepak/futsal (Umum 1 / Umum / 9506.62)
- Sarung tinju (boxing gloves) (Umum 1 / Umum / 9506.99)
- Stroller bayi (Lartas Ringan / Umum / 8715.00.10)
- (plus the remaining `confidence: high` + zero-correction items from the "42 more" set, applied by the same rule)

### Prompt edits (low-risk wording / consistency fixes)
- **Edit 1** — Strengthen no-questions-back rule with explicit forbidden-question list. Pure tightening of an existing rule.
- **Edit 7** — Remove deprecated `[UNTUK MARKETING]` 2-layer reference; replace with `[NEEDS_KB_ENTRY]` / `[ESCALATE]`. Fixes an internal inconsistency the prompt already bans elsewhere.
- **Edit 8** — Replace warning emoji in Garment alamat khusus line with plain text marker. Aligns with the existing "Emoji minim & natural" rule; no semantic change.

### New admin-only read-only commands
- `//find` — case-insensitive KB substring search (read-only).
- `//tier` — paginated list of KB items per tier (read-only).
- `//search_hs` — HS substring lookup over KB (read-only).
- `//kbcount` — KB breakdown by tier (read-only aggregate).

All four are pure read queries against existing sheets, no writes, admin-gated — safe to land overnight.

---

## REVIEW_FIRST (apply but document for owner review)

### KB additions with corrections
Apply but log clearly in a `CHANGELOG_OVERNIGHT.md` (or equivalent) the items where the verifier flipped a field — owner should sanity-check the flip in the morning:

- **Snack import** — `hs_corrected: true` (1905.90). Verify HS chapter pick is correct vs. confectionery alternatives.
- **Gunting kuku set** — `tier_corrected: true` (now Lartas Berat). Tier escalation — owner should confirm Lartas Berat is intended for nail-clipper sets.
- **Dumbbell besi** — `tier_corrected: true` AND `container_corrected: true`. Two fields flipped at `medium` confidence — flag prominently.
- **Matras yoga thick** — `hs_corrected: true` (3926.90). Plastics chapter pick worth a glance.
- **Car seat bayi** — `tier_corrected: true` (Umum 2) at `medium` confidence. Tier change on a child-safety item — owner sign-off recommended.
- **Baby walker** — `hs_corrected: true` (9503.00.10 toys vs. 9401 furniture). Chapter flip worth verifying.
- **Dot botol bayi** — `hs_corrected: true` (4014.90.00). Rubber chapter pick at `medium` confidence.
- **Baby monitor** — `tier_corrected: true` at `medium` confidence. Tier change on telecom-adjacent device.
- Any other item in the "42 more" set with `*_corrected: true` follows this same path.

### Prompt edits (semantic / behavioral changes)
- **Edit 2** — Split Ch 50-63 textile overlap (finished/consumer → Semi Garment/Garment, raw roll → Tekstil). Removes a real overlap but changes routing for borderline textile items; document the new boundary so owner can spot-check next-day classifications.
- **Edit 3** — Tighten container default to deterministic "Umum unless explicit Mix trigger present" + explicit trigger list. Improves consistency but changes container-default behavior on edge cases.
- **Edit 6** — Expand REJECT categories (uang/securities/narkotika/satwa) and standardize REJECT output to 4-line format. Behavior change for an under-exercised path — apply but flag so owner can review the new structured-REJECT format with one or two examples.

### New write-adjacent / aggregate commands
- `//pending` — surfaces last 10 `pending_items`. Read-only but exposes a moderation queue; owner should confirm visibility scope is admin-only.
- `//digest` — yesterday's summary (inquiry totals, top 5 users, unknown count). Read-only aggregate but introduces a daily-report surface; document the metric definitions used (timezone, "yesterday" cutoff) so owner can validate the first run.

---

## SKIP (with reason)

### KB items
- Skip every entry with `confidence: medium` **and** zero `*_corrected` flags where the HS is a generic catch-all chapter (e.g. **Biji kopi 0901.11**, **Susu bubuk 0402.10**, **Sirup 2106.90**, **Gula 1701.99**, **Biji-bijian almond/mete 0802.12**, **Alat cukur elektrik 8510.10**, **Epilator/IPL 8543.70**, **Sisir/sikat rambut biasa 9615.11**, **Treadmill 9506.91**, **Empeng bayi 3924.90.90**).
  - **Reason:** `medium` confidence with no verifier correction means the verifier neither confirmed nor adjusted — under "owner asleep, be conservative," these should wait for owner review rather than land silently. Several (sugar, milk powder) are also commodities with import-permit nuance the bot should not guess on unattended.

### Prompt edits
- **Edits 4 and 5** (output-format example tweaks).
  - **Reason:** These change the canonical examples the model learns from. Even though both are labelled `safe=true`, swapping the unknown-item example (Edit 4) and changing the ambiguous-HS example from `?` to "always guess a chapter" (Edit 5) materially shifts model behavior on the exact two scenarios most likely to produce wrong-but-confident output. Conservative call: hold for owner review.

### New features
- None skipped — all 6 commands are read-only or read-aggregate. The two with mild concerns (`//pending`, `//digest`) are routed to REVIEW_FIRST rather than skipped.