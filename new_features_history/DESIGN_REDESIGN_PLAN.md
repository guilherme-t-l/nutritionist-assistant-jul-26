# NutriAgent — Visual Redesign Plan

**Status:** Proposal (no implementation yet)  
**Scope:** Presentation layer only — `src/app/templates/onboarding.html`  
**Constraint:** Zero changes to routes, APIs, state, auth, plan logic, or user flows

---

## 1. Current State (Audit Summary)

### Architecture

The entire frontend lives in a **single file**:

| Layer | Location |
|-------|----------|
| HTML structure | `src/app/templates/onboarding.html` |
| CSS | Inline `<style>` (~615 lines) |
| JS | Inline `<script>` (~805 lines) |
| Static assets | None |
| CSS framework | None |

Only `GET /` serves UI. All other routes are JSON APIs. Four client-side views are toggled with `.hidden`.

### Screens / Views

| View | ID | Role |
|------|-----|------|
| Welcome | `#welcome-view` | Guest CTA + login form |
| Onboarding | `#onboarding-view` | Preferences form → generate or import plan |
| Import panel | `#import-panel` | Paste/file import (nested under onboarding) |
| App (split) | `#split-view` | Chat (left) + meal plan (right) |

### Current Design Traits (to replace)

- System font stack (`-apple-system`, Segoe UI)
- Warm cream background + bright forest-green accent (reads as “generic health app”)
- Card-heavy UI with soft drop shadows
- Pill-shaped ghost buttons and day tabs
- Dense form labels without editorial hierarchy
- Chat that feels like a standard messaging widget
- Meal plan that reads like a calorie spreadsheet (dashed borders, compact rows)
- Brand naming inconsistency: “Nutritionist AI” vs “NutriAgent”

### What Must Not Change

- All `id`s used by JS (`guest-btn`, `login-form`, `chat-form`, `save-plan-btn`, etc.)
- All JS behavior, `fetch` calls, `state` shape, `sessionStorage` keys
- Class names referenced by JS-generated HTML in `renderPlan` / `renderMeal` / `renderChat` — **or** update those strings in lockstep with CSS only (no logic change)
- Button actions, confirm dialogs, loading label text swaps (“Thinking…”, “Saving…”, etc.)
- View switching functions and when each view appears
- API contracts and backend code

---

## 2. Design Direction

### Positioning

**Premium personal nutrition coach** — calm, editorial, aspirational — closer to a high-end wellness brand experience than to MyFitnessPal, Cronometer, or a SaaS admin panel.

**Inspiration (not imitation):** Alo, Lululemon — restraint, confidence, typography, whitespace, natural materials, fashion-editorial clarity.

### Visual Thesis

> One quiet composition. Strong type. Warm stone. Botanical ink. Almost no chrome.

### Mood Keywords

Premium · Minimal · Calm · Editorial · Aspirational · Effortless · Sophisticated

### Explicitly Avoid

| Avoid | Why |
|-------|-----|
| Generic SaaS blue / purple gradients | Feels like tooling, not wellness |
| Bright saturated greens | Clinical “health app” cue |
| Heavy card stacks + multi-layer shadows | Dated dashboard look |
| Pill clusters, badge clutter, icon rows | Visual noise |
| Spreadsheet density for macros | Undermines premium feel |
| Dark mode as default | Not the brand direction |
| Copying Alo/Lululemon layouts or photography | Inspiration only |

---

## 3. New Design System

### 3.1 Brand Voice (UI Copy — cosmetic only)

Unify naming to **NutriAgent** everywhere (title, welcome H1, headers).  
Optional microcopy polish (same meaning, calmer tone) — e.g. subtitle refinement — **without** changing CTA actions or flow.

### 3.2 Color Tokens

Replace the current `:root` palette with a cohesive **warm stone + botanical** system.

```css
:root {
  /* Surfaces */
  --canvas: #ebe6de;          /* warm limestone page ground */
  --canvas-deep: #e2dcd2;     /* subtle depth / atmospheric wash */
  --surface: #faf8f4;         /* primary panels */
  --surface-raised: #fffcf7;  /* chat / elevated areas */
  --surface-mute: #f3efe8;    /* inset wells, empty states */

  /* Ink */
  --ink: #1c1b19;             /* near-black charcoal */
  --ink-soft: #3a3834;        /* secondary headings */
  --muted: #6e6a62;           /* supporting text */
  --muted-soft: #8a857c;      /* tertiary / macros */

  /* Accent — deep botanical olive (not bright “health green”) */
  --accent: #3a4a38;
  --accent-hover: #2f3d2e;
  --accent-soft: #e4e8e1;
  --accent-ink: #2a3529;

  /* Borders & lines */
  --line: rgba(28, 27, 25, 0.08);
  --line-strong: rgba(28, 27, 25, 0.14);

  /* Chat */
  --user-bubble: #3a4a38;
  --user-ink: #faf8f4;
  --assistant-bubble: #f0ebe3;
  --assistant-ink: #1c1b19;

  /* Status */
  --danger: #8f3a32;          /* muted clay red — calm, not alarmist */
  --ok: #3a4a38;

  /* Elevation — barely there */
  --shadow: 0 1px 0 rgba(28, 27, 25, 0.04), 0 12px 40px rgba(28, 27, 25, 0.06);
  --shadow-soft: 0 8px 28px rgba(28, 27, 25, 0.05);
}
```

**Principle:** Prefer hairline separators and tonal shifts over borders and shadows. Accent used sparingly for primary actions and key emphasis.

### 3.3 Typography

Load two fonts via Google Fonts (no npm dependency):

| Role | Family | Character |
|------|--------|-----------|
| Display | **Fraunces** (soft optical sizing) | Editorial, warm, confident titles |
| UI / Body | **Figtree** | Clean, modern, highly legible |

```css
--font-display: "Fraunces", Georgia, serif;
--font-body: "Figtree", system-ui, sans-serif;
```

**Type scale**

| Token | Size / weight | Use |
|-------|---------------|-----|
| `--text-hero` | clamp(2.5rem, 5vw, 3.5rem) / 500 | Welcome brand title |
| `--text-page` | 2rem / 500 | Onboarding page title |
| `--text-section` | 1.25rem / 500 | Card/section titles, meal names |
| `--text-label` | 0.7rem / 600 · uppercase · letter-spacing 0.08em | Form labels, meta labels |
| `--text-body` | 1rem / 400 · line-height 1.55 | Body, chat |
| `--text-support` | 0.875rem / 400 | Hints, macros, secondary |
| `--text-micro` | 0.75rem / 500 | File names, errors, deltas |

**Numeric:** Keep `font-variant-numeric: tabular-nums` on calories/macros.

### 3.4 Spacing & Layout Tokens

```css
--space-1: 0.25rem;
--space-2: 0.5rem;
--space-3: 0.75rem;
--space-4: 1rem;
--space-5: 1.5rem;
--space-6: 2rem;
--space-7: 3rem;
--space-8: 4.5rem;

--radius-sm: 4px;     /* inputs */
--radius-md: 8px;     /* buttons, wells */
--radius-lg: 16px;    /* major surfaces */
--radius-xl: 24px;    /* welcome shell (rare) */

--max-welcome: 28rem;
--max-form: 40rem;
--max-plan: 42rem;
```

**Whitespace rule:** Prefer larger vertical rhythm between sections; tighten only inside dense meal rows.

### 3.5 Motion

Keep existing functional animations; refine timing to feel calmer:

| Motion | Spec |
|--------|------|
| View content enter | `opacity` + `translateY(8px)` · 280ms · ease-out |
| Bubble in | Existing, slightly slower (220ms) |
| Typing dots | Keep; softer opacity curve |
| Button hover | Background / border · 180ms · ease |
| Save button appear | Keep `plan-save-in`; softer travel |

No decorative parallax, no glow pulses, no confetti.

### 3.6 Component Patterns (Visual Only)

#### Buttons

| Variant | Treatment |
|---------|-----------|
| **Primary** | Solid botanical fill, `--radius-md`, medium weight, no pill unless save FAB-style retained |
| **Secondary** | Transparent / surface fill, 1px `--line-strong`, accent ink text |
| **Ghost** | Text-only or ultra-light underline/border; used in header — **not** crowded pill chips |
| **Send** | Square-ish `--radius-md`, same accent as primary |
| **Save plan** | Keep behavior; restyle as refined pill or rectangular CTA aligned to plan column |

Disabled: lower opacity + `cursor` unchanged from today.

#### Forms

- Labels: uppercase micro-labels (`--text-label`) above fields
- Inputs: soft surface fill, hairline border, taller padding (`0.75rem 0.9rem`)
- Focus: 1px accent border + soft soft ring (`--accent-soft`) — not thick default outline
- Checkbox grids: quiet selectable rows; checked state uses soft accent wash, not loud green
- Macro row: three equal columns with clearer visual grouping

#### Cards / Surfaces

- Reduce “card everywhere.” Prefer:
  - **Welcome / Onboarding:** one primary surface or sectioned form with hairline dividers
  - **Plan:** single editorial surface (not nested card chrome)
  - Shadows only on the main elevated surface when needed

#### Chat

- Less “iMessage,” more “private coach notes”
- User bubbles: botanical, slightly smaller radius, generous padding
- Assistant: warm mute surface, no harsh bubble notch required
- Input: full-width quiet bar, flush with panel bottom, refined focus ring
- Header: brand wordmark (display font, smaller) + text actions with generous gap — avoid pill clutter

#### Meal Plan

Shift from spreadsheet → **editorial day menu**:

- Meal name as typographic section head
- Food rows with more air; calories as quiet trailing meta
- Macros as secondary line in `--muted-soft`
- Day total: strong top rule + display-weight numbers (not thick black bar)
- Target delta: soft success/warn color (muted clay / olive), not neon
- Day tabs: underlined or segmented text control — not candy pills
- Empty state: calm centered line + optional short supportive sentence (same meaning)

#### Feedback

- Errors: remain inline; quieter clay red, slightly smaller type
- Loading: keep button label swaps + typing dots; optionally dim plan surface during wait (CSS-only if easy; no new states required)
- Native `confirm()` stays (no modal system)

---

## 4. Global Atmosphere

### Background

- Body uses `--canvas` with a **subtle vertical wash** (CSS gradient: `--canvas` → `--canvas-deep`) — atmospheric, not decorative illustration
- No stock photography required for v1 (keeps zero asset pipeline); optional later

### Brand Mark

- Replace green “dot + glow” with a refined wordmark: **NutriAgent** in Fraunces
- Optional thin horizontal rule under brand in app header
- Welcome: brand is the hero signal (largest type on screen)

---

## 5. Screen-by-Screen Redesign Plan

### 5.1 Welcome (`#welcome-view`)

**Today:** Narrow column, small H1, card with guest + login, utility feel.

**Proposed:**

| Element | Redesign |
|---------|----------|
| Composition | Full-viewport centered column; generous top padding; one calm composition |
| Brand | “NutriAgent” as hero (`--text-hero`), display font |
| Support | One short line under brand (refined subtitle) |
| Actions | Guest as primary vertical CTA; “or” hairline divider; login fields as quiet secondary block |
| Surface | Soft raised panel **or** borderless stacked form on atmospheric canvas — not a heavy floating SaaS card |
| Login fields | Full width, refined inputs; primary “Log In” full width |

**Preserve:** `#guest-btn`, `#login-form`, `#username`, `#password`, `#login-btn`, `#login-error`, same submit behavior.

---

### 5.2 Onboarding / Preferences (`#onboarding-view`)

**Today:** Multi-card form, dense labels, utility buttons.

**Proposed:**

| Element | Redesign |
|---------|----------|
| Header | Page title in Fraunces; subtitle in muted body |
| Structure | Single continuous form surface with **section breaks** (Goals · Body · Preferences · Constraints) via hairline + section titles — may keep one `.card` wrapper for simplicity or restyle `.card` to match |
| Fields | Uppercase micro-labels; taller inputs; more vertical spacing between groups |
| Checkbox grids | Softer alignment; more padding; less “settings panel” |
| Actions | Primary + secondary side-by-side with clear hierarchy; Cancel remains ghost |
| Width | Slightly wider breathing room (`--max-form`), more bottom padding |

**Preserve:** Entire `#profile-form` field names, checkbox `value`s, `#submit-btn`, `#upload-plan-btn`, `#cancel-form-btn`, `#form-actions` / `.prefs-only` behavior, `#onboarding-error`.

---

### 5.3 Import Panel (`#import-panel`)

**Today:** Dashed drop zone under form; functional but utilitarian.

**Proposed:**

| Element | Redesign |
|---------|----------|
| Header | Section title in display/section type |
| Helper | Calmer supporting paragraph |
| Drop zone | Soft mute surface, subtle dashed border in `--line-strong`, larger padding |
| Actions | Same three buttons; clearer primary (“Use this plan”) vs secondary (“Edit…”) vs ghost Back |

**Preserve:** All import IDs, file accept types, adapt vs use flows, error node.

---

### 5.4 App Split View (`#split-view`)

**Today:** 45/55 white chat | padded plan; pill header actions; plan card with shadow.

**Proposed layout (visual):**

```
┌─────────────────────────┬──────────────────────────────┐
│  NutriAgent     actions │                              │
│─────────────────────────│   [Save]                     │
│                         │                              │
│   coach conversation    │   editorial meal plan        │
│                         │                              │
│─────────────────────────│                              │
│   compose bar           │                              │
└─────────────────────────┴──────────────────────────────┘
```

| Zone | Redesign |
|------|----------|
| **Column ratio** | Consider ~40/60 or 38/62 so the **plan is the hero**; chat remains usable |
| **Chat panel** | `--surface-raised`; quieter header; text-link style actions with spacing |
| **Plan panel** | Canvas-tinted scroll area; plan as editorial document centered at `--max-plan` |
| **Header actions** | Same buttons/IDs; restyle from pills → refined ghost text buttons; wrap gracefully |
| **Save** | Keep dirty/disabled/loading behavior; restyle to match primary system |

**Preserve:** Grid structure (chat left / plan right desktop; plan-above-chat mobile), all header button IDs and show/hide logic, chat form, plan save, `renderPlan` data structure.

---

### 5.5 Chat Thread & Input

| Element | Redesign |
|---------|----------|
| Thread | More padding; larger gap between turns |
| Bubbles | Soft radii (`--radius-lg`); less “chat app notch”; refined type size |
| Typing | Keep 3-dot indicator; match assistant surface |
| Input row | Quieter container; send button matches primary accent |
| Errors | Inline under input (existing) |

**Preserve:** `renderChat` bubble classes (`.bubble.user` / `.assistant` / `.typing`), autosize, send disabled rules, `#chat-error`.

---

### 5.6 Meal Plan (`#plan-card` + JS renderers)

**This is the product’s visual centerpiece.**

| Element | Redesign |
|---------|----------|
| Empty | Centered calm message; more vertical space |
| Day tabs | Minimal text/segment control |
| Meal blocks | Generous padding; hairline between meals; meal name in section type |
| Food rows | More vertical rhythm; name primary, qty muted, macros micro |
| Meal total | Soft separator; less “accounting” |
| Day total | Strong typographic summary row |
| Target / delta | Quiet status coloring |
| Notes | Non-italic or lightly italic; treated as coach footnote |

**Preserve:** HTML structure/classes produced by `renderPlan` / `renderMeal` (update CSS + optionally only presentational markup strings if needed for hierarchy — no data/logic changes). Day tab `data-day` click behavior unchanged.

---

### 5.7 Mobile (≤768px)

**Today:** Plan stacked above chat; height caps.

**Proposed:**

| Element | Redesign |
|---------|----------|
| Stack order | Keep plan → chat (plan remains primary) |
| Heights | Revisit `max-height` ratios for calmer reading (e.g. plan ~55vh) without breaking scroll |
| Welcome / onboarding | Increase horizontal padding comfort; hero type scales down via `clamp` |
| Header actions | Allow wrap with consistent gaps; consider slightly smaller type, not icons-only (would change discoverability) |
| Touch | Larger tap targets on primary buttons (≥44px height) |

---

## 6. Implementation Approach

### Phase A — Design tokens & foundations

1. Add Google Fonts link in `<head>`
2. Rewrite `:root` tokens + base `body` styles
3. Establish type utilities / element defaults (`h1`, `label`, buttons)

### Phase B — Shared components

1. Restyle `.btn-primary`, `.btn-secondary`, `.btn-ghost`, `.send-btn`, `.plan-save-btn`
2. Restyle inputs, checkbox grid, form actions
3. Restyle `.card` / surfaces / errors

### Phase C — Screens top to bottom

1. Welcome
2. Onboarding + import
3. Split layout + header
4. Chat
5. Meal plan (CSS + presentational tweaks in `renderPlan` / `renderMeal` HTML strings only)

### Phase D — Motion, polish, responsive pass

1. Refine transitions
2. Mobile breakpoint pass
3. Visual QA against “premium wellness” checklist

### File strategy

**Default for this redesign:** keep everything in `onboarding.html` (matches current architecture; no build step).  
Optional later (out of scope unless requested): extract `static/css/app.css`.

### JS touch rules

| Allowed | Not allowed |
|---------|-------------|
| Class names in template HTML | Changing fetch URLs, payloads |
| Class names / presentational markup inside `renderPlan` / `renderMeal` / `renderChat` | Changing `state`, dirty logic, auth flow |
| CSS-only visual state | New features, modals, toasts |
| Copy tweaks that don’t alter actions | Renaming element `id`s |

---

## 7. Quality Checklist (Definition of Done)

### Visual

- [ ] Feels like a new premium wellness product, not a recolor
- [ ] Brand (`NutriAgent`) is hero-level on welcome
- [ ] Typography creates clear hierarchy on every screen
- [ ] Meal plan reads as an editorial menu, not a spreadsheet
- [ ] Chat feels like a coach, not a generic chatbot skin
- [ ] Generous whitespace; minimal borders/shadows
- [ ] Cohesive tokens used consistently (few hardcoded one-offs)

### Functional (regression)

- [ ] Guest → onboarding → plan → chat refine
- [ ] Login / logout / session resume
- [ ] Update preferences / new plan / new chat
- [ ] Import use vs adapt + back
- [ ] Save plan dirty/clean/disabled/error
- [ ] Unsaved `confirm` + `beforeunload`
- [ ] Mobile stack + scrolling still usable
- [ ] All loading/error strings still appear

---

## 8. Open Decisions (for approval before coding)

1. **Fonts:** Fraunces + Figtree (proposed) — approve or prefer alternatives?
2. **Accent:** Deep botanical olive (proposed) vs softer sage or near-black mono accent?
3. **Welcome surface:** Borderless on canvas vs single soft elevated panel?
4. **Split ratio:** Keep 45/55 or shift plan-heavy (e.g. 38/62)?
5. **Day tabs / header actions:** Drop pill shape for underline/text style?
6. **Copy:** Unify brand to NutriAgent + light subtitle polish — yes/no?

---

## 9. Out of Scope

- Backend, agents, prompts, DB, auth cookies
- New screens, modals, toast system, dark mode
- Image assets / photography
- Extracting a component framework or SPA rebuild
- Changing API contracts or plan JSON shape

---

*Once this plan is approved (or adjusted against §8), implementation proceeds in Phases A→D inside `onboarding.html` only.*
