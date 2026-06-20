# Messaging File Format (the standard the parser expects)

This is the **one** format every campaign messaging doc must follow before you
download it as `.txt` and upload to Precise Automator. Write the Google Doc in
exactly this shape. Do not improvise — if the format drifts, steps get dropped
or merged silently.

> Rule of thumb: **be specific, not "around this".** The parser reads structure
> literally. Same structure in → same campaign out on Smartlead.

---

## Copy-paste template

```
<Campaign Title>

Step 1 — Email (Day 0)

Subject Line Options:
1. <subject A>
2. <subject B>
3. <subject C>

Message Body:
Hi {{first_name}},

<your email 1 copy, spaced exactly how you want it to look in the inbox>

%signature%

Step 2 — Email (Day 3)

{{first_name}},

<your email 2 copy>

%signature%

Step 3 — LinkedIn (Connection Request) (Day 3)

<LinkedIn copy — NOT sent by Smartlead, see "Channels" below>

Step 4 — Email (Day 7)

{{first_name}},

<your email 3 copy>

%signature%
```

---

## The rules

### 1. Step headers — `Step N — <Channel> (Day X)`
- Format: `Step <number> — <Channel> (Day <number>)`.
- Separator after the number can be `—`, `:`, or `-`. All work.
- `<Channel>` must be one of: `Email`, `LinkedIn`, `LI`, `DM`, `Connection Request`.
- `(Day X)` is optional but **strongly recommended** — it sets the send delay.

Valid examples (all parse):
```
Step 1 — Email (Day 0)
Step 2 : Email#2 (Day 3)
Step 3 - LinkedIn DM #1 (Day 3)
```

### 2. Channels — only Email is sent
- **Email** steps → synced to Smartlead.
- **LinkedIn / LI / DM / Connection Request** steps → **skipped**. The app reports
  each skipped step in the warnings ("Skipped Step N (LinkedIn)…"). Nothing fails,
  but those messages do **not** go out via Smartlead (LinkedIn isn't wired up yet).
- Email steps are renumbered 1..N for Smartlead, so interleaved LinkedIn steps
  don't break the email sequence numbering.

### 3. Subjects — only under Step 1
- Put `Subject Line Options:` (or `Subject Lines`) **after the Step 1 header**.
- List subjects as numbered lines: `1. …`, `2. …`. Each becomes an A/B/C/D variant.
- Subjects are taken from Step 1 only. Follow-up emails reply in-thread (no subject).

### 4. Body — under `Message Body:`
- `Message Body:` heading is optional but keeps things clear. The parser strips the
  label and the subject block, so neither leaks into the email.
- **Spacing is preserved exactly** as written. The blank lines you leave here are
  the blank lines the lead sees. Lay it out the way you want it in the inbox.

### 5. Day → delay
- `(Day X)` on each email header drives the gap between sends.
- Delay = difference between consecutive **email** days. e.g. Email at Day 0 then
  Day 3 → 3-day delay. Day 0 → Day 5 → Day 10 → delays 0, 5, 5.
- Step 1's day is ignored for timing (email 1 sends immediately). You can still
  override any follow-up delay in the app's plan preview before syncing.
- If you omit `(Day X)`, the app falls back to default delays.

### 6. Merge tags & signature
- Personalization: `{{first_name}}`, `{{company}}` — exactly this `{{ }}` form.
- Signature: `%signature%` (lower-case preferred; the app normalizes `%Signature%`).

### 7. One body per step — no "Alt version"
- Write a **single** body per email step.
- Do **not** paste two drafts under one step (e.g. a paragraph plus an
  `Alt version: …` line). The parser ships everything under the step as body, so
  **both** would be sent — duplicate copy in one email. Pick one before exporting.
- True A/B body testing is a separate flow (subject variants are the supported case).

---

## What the app cleans up automatically

You don't need to hand-fix these — the parser handles them:

- **Google Docs comment markers** (`[a]`, `[b]`…) and the comment dump at the
  bottom (`_Assigned to_`, `CC: @…`) are stripped.
- **Sentences glued by export** (`care.That` → `care. That`) are repaired.
- **Inconsistent blank-line runs / BOM / non-breaking spaces** are normalized.

So focus on getting the **structure** (steps, channels, subjects, days) right —
the cosmetic export noise is handled for you.

---

## Export & upload

1. In Google Docs: **File → Download → Plain Text (.txt)**.
2. Open the `.txt` once to sanity-check the structure is intact.
3. Upload in Precise Automator → **New Campaign** (or **Edit Existing** to add
   spintax to a campaign link).
4. Pick the **workspace** (this selects the right Smartlead account/API key).
5. Generate spintax → review plan preview (delays, copy) → **Sync to Smartlead**.
6. The campaign lands in Smartlead as a **draft**. Select the sending server and
   launch manually there.

---

## Worked example (Mythic – Health Systems)

```
Health Systems Campaign: 5-Step Sequence

Step 1 — Email (Day 0)

Subject Line Options:
1. Cone Health Perspective
2. Complex Care Perception
3. Brand Preference Gap
4. Regional Care Choice

Message Body:
Hi {{first_name}},

A few years ago, Cone Health faced a problem.

The clinical care was there. The community trust was there. But the
market perception did not fully match the level of care being delivered.

Worth sending over a short overview of what we learned?

%signature%

Step 2 — LinkedIn (Connection Request) (Day 0)

{{first_name}}, I led the strategy work behind Cone Health's brand
transformation. Would love to connect.

Step 3 — Email (Day 3)

{{first_name}},

One pattern we saw with Cone Health is that the media mix is often built
for the already-decided patient.

Open to seeing what that looks like for {{company}}?

%signature%
```

Parses to: **2 email steps** (Day 0, Day 3 → 0 / 3-day delays), 4 subject
variants on email 1, and **1 skipped LinkedIn step** reported in the warnings.
