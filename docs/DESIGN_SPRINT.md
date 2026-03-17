# Design Sprint — Make Tinker Feel Inevitable

**This sprint is successful if a stranger looking at the app for the first time says "this looks like a real product" and feels compelled to try it.**

**Sprint Goal:** Apply a cohesive, premium design system across every screen. No new features, no new endpoints, no backend changes. Pure visual and experiential refinement. When this sprint is done, Tinker should feel closer to Linear/Vercel/Notion than to a Bootstrap admin panel.

**The #1 Rule:** Do not optimize for decoration. Optimize for hierarchy, spacing, clarity, and confidence. Every screen should make the main action obvious within 3 seconds. If something is pretty but doesn't make the primary action clearer, remove it.

**Design Direction:** Warm minimalism. Not cold/corporate (like Salesforce), not maximalist (like Figma). Think: confident, calm, spacious, with moments of warmth. The product should feel like a capable, decisive assistant — not a dev tool, not a toy. More Linear than Notion. Tinker is an operating surface, not a blank canvas.

**Reference products:** Linear (typography hierarchy, spacing, confidence), Vercel (clean dashboard, subtle status indicators), Manus (simplicity of entry), Replit (energy of creation).

---

## DESIGN SYSTEM (all agents must read this first)

Every agent must implement these exact tokens. Create or update `ui/web/src/app/globals.css` and `tailwind.config.ts` to include them. Consistency across screens is the #1 priority.

### Typography

**Font:** Use "Inter" for body text (it's already in the Next.js stack). For headlines on the landing page, use a distinctive serif or display font loaded from Google Fonts — suggest "Instrument Serif" or "Playfair Display" for the main headline only, everything else stays Inter. This creates the one moment of personality against the clean system font.

**Scale (use consistently):**
- Display (landing headline only): 48px / font-weight 400 / line-height 1.1 / letter-spacing -0.03em / serif font
- H1 (page titles): 28px / font-weight 600 / line-height 1.2 / letter-spacing -0.02em
- H2 (section headers): 20px / font-weight 600 / line-height 1.3 / letter-spacing -0.01em
- H3 (card titles, agent names): 16px / font-weight 600 / line-height 1.4
- Body: 15px / font-weight 400 / line-height 1.6
- Small (metadata, timestamps): 13px / font-weight 400 / line-height 1.5 / text-gray-500
- Caption (labels, badges): 12px / font-weight 500 / line-height 1.4 / uppercase / letter-spacing 0.05em

### Colors

Light theme only (dark theme is a future sprint). **Use Tailwind's built-in `stone` and `zinc` palettes** — they perfectly match the warm minimalism direction and eliminate custom hex management across 6 parallel agents.

**Backgrounds:**
- Page background: `bg-stone-50` (warm off-white)
- Card background: `bg-white`
- Sidebar background: `bg-stone-100`
- Hover state: `bg-stone-200/50`
- Active/selected: `bg-stone-200`

**Text:**
- Primary text: `text-zinc-900` (not pure black — slightly warm)
- Secondary text: `text-zinc-500`
- Tertiary text: `text-zinc-400`
- Link/accent: `text-blue-600` (used sparingly)

**Status colors (used for badges and indicators only):**
- Success/active: `text-green-600` / `bg-green-50`
- Warning/pending: `text-amber-600` / `bg-amber-50`
- Error/blocked: `text-red-600` / `bg-red-50`
- Info/in-progress: `text-blue-600` / `bg-blue-50`
- Neutral/idle: `text-zinc-400` / `bg-zinc-100`

**Borders:**
- Default border: `border-stone-200`
- Subtle border (cards): `border-stone-150` (use `border-stone-200/70` if 150 not available)
- Focus ring: `ring-blue-600 ring-2 ring-offset-2`

### Spacing (8pt grid)

All spacing should use multiples of 4px, with 8px as the base unit:
- xs: 4px (tight internal padding)
- sm: 8px (between related elements)
- md: 16px (standard padding, gaps between cards)
- lg: 24px (section spacing)
- xl: 32px (major section breaks)
- 2xl: 48px (page-level spacing)
- 3xl: 64px (landing page hero spacing)

**Card styling:**
- Border-radius: `rounded-xl` (12px)
- Border: `border border-stone-200`
- Padding: `p-5` (20px)
- Shadow: none by default, `shadow-sm` on hover
- Transition: `transition-all duration-150`

**Button styling:**
- Primary: `bg-zinc-900 text-white rounded-lg px-5 py-2.5 font-medium text-[15px] hover:bg-zinc-800 transition-colors`
- Secondary: `bg-white border border-stone-200 text-zinc-900 rounded-lg px-5 py-2.5 font-medium text-[15px] hover:bg-stone-50`
- Destructive: `bg-white border border-red-200 text-red-600 rounded-lg px-5 py-2.5 font-medium text-[15px] hover:bg-red-50`
- Ghost: `bg-transparent text-zinc-500 px-3 py-2 rounded-lg hover:bg-stone-200/50 hover:text-zinc-900`

**Badge styling:**
- Rounded-full, px-2.5 py-0.5, text-[12px] font-medium
- Success: `bg-green-50 text-green-700`
- Warning: `bg-amber-50 text-amber-700`
- Error: `bg-red-50 text-red-700`
- Neutral: `bg-zinc-100 text-zinc-500`

**Input styling:**
- `rounded-[10px] border border-stone-200 px-4 py-3 text-[15px]`
- Focus: `focus:border-blue-600 focus:ring-2 focus:ring-blue-600/20`
- Placeholder: `placeholder:text-zinc-400`

### Animation

- Page transitions: fade in 200ms ease
- Card hover: translateY(-1px) + shadow, 150ms ease
- Status pulse: gentle opacity pulse for active states (0.6 → 1.0, 2s infinite)
- Stagger children: 50ms delay between sibling cards on mount
- Loading skeletons: shimmer animation with warm gray gradient

### Icons

Use Lucide React icons consistently. 18px default size, 16px for inline/small contexts. Color should match the text context (primary, secondary, or status color).

---

## Agent 1 — Global Design System + Sidebar + Landing Page

Read the DESIGN SYSTEM section above. Read `/mnt/skills/public/frontend-design/SKILL.md` for design principles.

**Files you own:**
- ui/web/src/app/globals.css (update with design tokens)
- ui/web/tailwind.config.ts (update with custom colors, spacing, fonts)
- ui/web/src/app/layout.tsx (update fonts, base styles)
- ui/web/src/app/page.tsx (redesign landing page)
- ui/web/src/components/sidebar.tsx (redesign)
- ui/web/src/components/shell.tsx (update shell wrapper)
- Do NOT modify: api/, kernel/, templates/, scripts/, or any page other than page.tsx

**Sidebar redesign:**
- Background: `bg-stone-100` with subtle right border `border-stone-200`
- Logo: "Tinker" in H3 weight (16px/600), no lightning bolt icon — just the word in `text-zinc-900`
- Nav items: Ghost button style, 15px text, Lucide icons (Users for My Teams, Bell for Approvals, Plus for Hire a Team). Active state: `bg-stone-200 text-zinc-900`. Hover: `bg-stone-200/50`
- Bottom: subtle "Tinker" in Caption style (12px, uppercase, `text-zinc-400`)
- Width: 240px fixed on desktop
- Mobile: slide-over with backdrop blur

**Landing page — first-time users (no teams):**
- Full screen, no sidebar. Centered vertically and horizontally.
- Headline: "How can we help you?" in Display style (48px, serif font — Instrument Serif or Playfair Display). This is the ONE moment of personality.
- Subtext: "Tell us what you need. We'll assemble an AI team to work on it." in Body style, secondary text color.
- Input: large textarea, 100% width (max 640px), 3 rows, rounded-[10px], with placeholder "Find investors for my startup, write LinkedIn content, research competitors..."
- Submit: Primary button "Get started →" right-aligned below input
- Example chips below: 4-5 examples in Ghost button style, wrapping. Each fills the input when clicked.
- Below chips: nothing. Clean. No cards, no categories, no browse.
- The entire page should feel like taking a deep breath — spacious, warm, confident.

**Landing page — returning users (have teams):**
- Sidebar visible.
- Top section: "Need something else?" in H2, with the same input (smaller, single row) and 3 example chips.
- Below: "Your teams" in H2, then team cards in a responsive grid. Design the cards to match the design system (12px radius, subtle border, no shadow default, shadow on hover).

**Load the serif font:** Add Instrument Serif or Playfair Display via next/font/google in layout.tsx. Only load weight 400 italic (for the headline). Keep Inter as the base font for everything else. **CRITICAL:** Set `display: 'swap'` and provide a safe fallback font stack (`font-family: 'Instrument Serif', Georgia, 'Times New Roman', serif;`) in Tailwind. Do not let font loading block the render or crash the build. If the font fails to load, Georgia is a perfectly acceptable fallback — the design principle (one serif moment) matters more than the specific font.

**CONSISTENCY AUTHORITY:** After the other agents finish their work, you are responsible for one final consistency pass across ALL pages. Check: spacing rhythm, card treatments, button hierarchy, border usage, status badge styling, heading sizes, and overall mood. Do not add features or change functionality — only unify the visual system. Fix any screen that drifts from the design tokens. This pass is your most important deliverable.

---

## Agent 2 — Teams Overview Page

Read the DESIGN SYSTEM section above.

**Files you own:**
- ui/web/src/app/teams/page.tsx (redesign)
- ui/web/src/components/team-card.tsx (redesign)
- Do NOT modify: api/, kernel/, templates/, scripts/, globals.css, sidebar.tsx, or any other page

**Teams overview redesign:**
- Page title: "My Teams" in H1 style. Below: "Updated [time]" in Small style, tertiary text.
- Approval banner (if any pending): warm amber background (#FEF3C7), rounded-xl, with bell icon and "N items awaiting your review" text + "Review now" link. Not alarming, just informative.
- Team cards in a responsive grid: 1 col mobile, 2 col tablet, 3 col desktop (max)
- Each card:
  - Template icon + team name in H3
  - Template type in Small/tertiary below the name
  - Status badge (Active/green, Paused/gray) — top right corner
  - Agent count: "{N} agents" in Small text
  - Compact stats line: "Created [relative time]"
  - Bottom: either "N awaiting approval" (warning badge) or "All clear" (success badge)
  - Full card is clickable, hover lifts slightly with shadow
  - NO "Open →" text — the card itself is the click target
- "Hire a team" button: top right of page, Primary button style with Plus icon
- Empty state (no teams): centered message "You haven't hired any teams yet." with CTA button "Hire your first team →" linking to /onboard or landing page

---

## Agent 3 — Team Detail Page

Read the DESIGN SYSTEM section above.

**Files you own:**
- ui/web/src/app/teams/[id]/page.tsx (redesign)
- ui/web/src/components/team-progress-members.tsx (redesign if it exists)
- ui/web/src/components/team-progress-feed.tsx (redesign if it exists)
- Do NOT modify: api/, kernel/, templates/, scripts/, globals.css, sidebar.tsx, or any other page

**Three-panel layout redesign:**

**CRITICAL LAYOUT RULE:** You are rendering inside the app shell's `<main>` container, which sits next to a 240px sidebar. Your 3-panel layout must use CSS Grid or Flexbox (`grid-cols-1 lg:grid-cols-12`) and handle its own overflow/scrolling internally (e.g., `h-[calc(100vh-4rem)] overflow-y-auto` on scrollable panels). Do not break the global page scroll.

**VISUAL HIERARCHY RULE:** The center column is the star. Left and right columns are quieter support rails. If all three columns feel equally loud, you've failed. The center column should get ~50-60% of visual weight through larger type, more whitespace, and prominent action elements. Left and right should feel calm and informational.

**Left panel (team members):**
- Header: team name in H2, template type in Small/tertiary below
- No workspace ID visible (remove "ws-xxxx")
- Agent list: each agent gets a row with icon (from template), display name in H3, role description in Small/secondary, status dot (color-coded: green=completed, blue=active, gray=waiting)
- Clean dividers between agents (1px #EDEDEB)
- Bottom: "N active · polling" in Caption style

**Center panel (tasks + activity):**
- Status bar (when agents working): blue-tinted banner (#EFF6FF), rounded-xl, with pulse dot + "Your [Agent] is [action]..." in Body weight
- Review-ready card (when output ready): amber-tinted banner (#FEF3C7), rounded-xl, "Your [Critic] reviewed the draft" + "Review now →" Primary button
- Mission input: textarea matching design system input style, with "Give your team a mission..." placeholder. ⌘↵ hint in Caption style. Send button as Primary button.
- Task list header: "All Tasks" in H2 with task count in secondary text
- Each task card: title in H3, description in Small/secondary, status badge, timestamp + agent name in Caption. Cards match design system card style.
- If task has "Review →" button, use Secondary button style.

**Right panel (actions + stats):**
- Pending approvals card with warning styling if count > 0
- Stats card: "This week" header in Caption/uppercase, then clean rows of label + value
- Quick links: Team Settings, Performance, All Approvals, All Teams — all in Ghost button style with Lucide icons

---

## Agent 4 — Onboarding / Team Builder Page

Read the DESIGN SYSTEM section above.

**Files you own:**
- ui/web/src/app/onboard/page.tsx (redesign)
- Do NOT modify: api/, kernel/, templates/, scripts/, globals.css, sidebar.tsx, or any other page

**Onboarding page redesign:**

This page should feel like a conversation, not a form. Light background (#FAFAF9), centered content (max-width 680px), generous vertical spacing.

**Step 1: Recommendation (shown after architect responds):**
- "Your goal:" in Caption/uppercase, then the user's goal text in Body/italic
- Template icon + name in H1 style
- Description in Body/secondary
- "Why this team" explanation in Body, with a subtle left border accent (3px #2563EB)
- Team preview: horizontal card row (flex, gap-md), each role as a compact card:
  - Icon (24px) + role name in H3 + short description in Small/secondary
  - Cards: bg-white, border, rounded-xl, padding 16px
- Pipeline: "How they work together" in Caption/uppercase, then a clean horizontal flow: "Scout → Writer → Critic" with arrow icons between, in Small/secondary

**Step 2: Quick questions:**
- "A couple of quick questions" in H2
- Each question: question text in H3, options as radio-style cards (not raw radio buttons). Each option is a bordered card, selected state has blue left border + `bg-blue-50`. Cards stack vertically with 8px gap.
- Text input questions: standard input style from design system
- Questions should feel conversational, not form-like. Generous spacing between questions (32px).

**Step 3: Launch:**
- "Launch your team →" as Primary button, full width (max 400px), centered
- Below: "You can adjust your team anytime" in Small/tertiary
- On launch: button shows spinner, then redirect

**Animation:**
- Role cards stagger in (50ms delay each)
- Questions fade in sequentially (not all at once)
- Launch button has a subtle scale animation on hover

---

## Agent 5 — Review Page

Read the DESIGN SYSTEM section above.

**Files you own:**
- ui/web/src/app/review/[taskId]/page.tsx (redesign)
- ui/web/src/components/review-recommendation-banner.tsx (redesign if exists)
- ui/web/src/components/review-quality-dimensions.tsx (redesign if exists)
- Do NOT modify: api/, kernel/, templates/, scripts/, globals.css, sidebar.tsx, or any other page

**This is the most important page. It should feel decisive and trustworthy.**

**Header area:**
- "← Back" as Ghost button, top left
- Task title in H1, no task ID visible
- "Ready for your decision" badge (or appropriate status badge) — top right

**Recommendation banner (full width, most prominent element):**
- For PASS: `bg-green-50` background, green left border (4px `border-l-green-600`), success icon. "Your Quality Editor recommends approval" in H3. Feedback quote in Body/secondary below.
- For REVISE: `bg-amber-50` background, amber left border (`border-l-amber-500`), warning icon. "Your Quality Editor flagged issues" in H3. Specific issue text below.
- For BLOCK: `bg-red-50` background, red left border (`border-l-red-600`), error icon. "Your Quality Editor blocked this" in H3. Reason below.
- `rounded-xl p-5 mb-6`

**Two-column layout below the recommendation:**

**Left column (draft — 60% width):**
- "Draft" label in Caption/uppercase
- Word count + char count in Small/tertiary
- The draft text in a clean, readable textarea or content area. Font-size 16px, line-height 1.7. If editable, use a textarea with subtle border that becomes prominent on focus. If read-only (delivery mode), just clean text.
- Copy button in Ghost style, top right of draft area

**Right column (context — 40% width):**
- **Sources card:** "Sources" header (or template-driven label) in H3 with "N verified" badge. Each source: clickable title in Body/link-color, domain + snippet in Small/secondary. Clean dividers between sources.
- **Quality dimensions card:** "Quality" header in H3 with overall score in accent color. Each dimension: label in Small, thin progress bar (8px height, rounded-full), score in Small/right-aligned. Green bar for ≥4, amber for 3-3.9, red for <3.
- **Execution details:** collapsed by default. "Show details" as Ghost button. When expanded, show chain steps in Caption style with status dots.

**Action bar (sticky bottom):**
- White background with top border (#EDEDEB), padding 16px, centered buttons
- Primary action matches the recommendation (Approve for PASS, Revise for REVISE, etc.)
- Buttons spaced with 12px gap
- Primary button is larger/more prominent than secondary buttons

---

## Agent 6 — Hire a Team (Browse) + Approvals Page

Read the DESIGN SYSTEM section above.

**Files you own:**
- ui/web/src/app/hire/page.tsx (redesign)
- ui/web/src/app/approvals/page.tsx (redesign)
- ui/web/src/app/build/page.tsx (redesign if it's the browse entry)
- Do NOT modify: api/, kernel/, templates/, scripts/, globals.css, sidebar.tsx, or any other page except the three listed above

**Hire a Team page:**
- Page title: "Hire a Team" in H1
- Subtext: "Browse our team templates or describe what you need" in Body/secondary
- "Describe what I need →" link/button at top, linking to landing page or /onboard
- Templates grouped by category. Category header: icon + category name in H2
- Template cards: icon + name in H3, description in Body/secondary, "N agents" badge in Neutral style, "View team →" as Secondary button (or make entire card clickable)
- Cards in responsive grid: 1 col mobile, 2 col tablet, 3 col desktop
- "Fully automated — no approval needed" badge for delivery templates (like Daily Briefing) in a subtle info style
- Keep the "Want a custom fit?" card at the top, styled as a subtle highlighted card (`bg-blue-50 border-l-4 border-l-blue-600`)

**Approvals page:**
- Page title: "Approvals" in H1
- Subtitle: "N items need your attention" in Body/secondary (or "All clear — nothing to review" with success icon when empty)
- Each approval item as a card:
  - Task title in H3
  - Team name in Small/secondary
  - Status badge (ready for review, revision requested, etc.)
  - Timestamp in Caption/tertiary
  - Action buttons: "Review →" as Primary button
- Cards sorted by urgency (revisions first, then pending)
- Empty state: centered illustration or icon with "All clear! Your teams are working smoothly." text

---

# WAVE STRUCTURE

**Wave 1: All 6 agents fire simultaneously.** Each owns specific page files. No file overlap.

**Shared concern:** Agent 1 owns globals.css and tailwind.config.ts (the design tokens). Agents 2-6 consume these tokens but do NOT modify globals.css or tailwind.config.ts. Use the Tailwind semantic classes defined in the design system (stone-*, zinc-*, green-*, amber-*, red-*, blue-*). Do NOT hardcode hex colors. If a needed shade isn't in the palette, use the closest standard Tailwind shade.

**The UX contract from the previous sprint (ui/web/src/lib/ux-contract.md) still applies.** All agents should use friendly display names, human-readable status language, and no internal IDs anywhere.

**Wave 2: Agent 1 consistency pass.** After all 6 agents finish, Agent 1 does one final pass across every page. Checks: spacing rhythm, card treatments, button hierarchy, border consistency, status badge styling, heading sizes, overall mood. Fixes anything that drifts from the system. This is the most important step — it turns six individually redesigned pages into one cohesive product.

**After both waves:** Restart make dev, screenshot every page, and for each page ask three questions:
1. Does the main action stand out within 3 seconds?
2. Does the page feel calmer and more premium than before?
3. Would I show this to a stranger without apologizing?

If any answer is "no," that page needs another pass.
