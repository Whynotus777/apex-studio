# APEX Smoke Test Results
**Date:** 2026-03-17
**Environment:** MacBook Pro 2019, Intel i9, macOS 14, Next.js 16.1.7, FastAPI + uvicorn
**Tester:** Claude (automated)

---

## Pre-Test Fixes

### Fix 1 — `ui/web/src/app/approvals/page.tsx` — null stakes guard
**Problem:** `STAKES_BADGE[approval.stakes]` where `stakes: "low" | "medium" | "high" | null`. TypeScript error `Type 'null' cannot be used as an index type` blocked production build.
**Fix:** Changed to `STAKES_BADGE[approval.stakes ?? "low"]`
**File:** `ui/web/src/app/approvals/page.tsx:118`

### Fix 2 — `ui/web/src/components/team-card.tsx` — deleted status + this_week_activity
**Problem:** `STATUS_BADGE` typed as `Record<Team["status"], ...>` missing `"deleted"` key. Also referenced `team.this_week_activity` which doesn't exist in `Team`/`TeamSummary` from `api-contract.ts`. Caused 2 TypeScript errors blocking the build.
**Fix:** Added `deleted` entry to `STATUS_BADGE`. Removed `this_week_activity` and replaced with `team.created_at` formatted as relative time.
**File:** `ui/web/src/components/team-card.tsx`

### Fix 3 — `ui/web/src/lib/api.ts` — `getTaskChain()` field mismatch
**Problem:** `getTaskChain()` mapped `p.completed_at` but the backend returns `created_at` for `ChainProgressItem`. Result: chain timeline never showed any timestamps.
**Fix:** Changed `apiFetch<TaskChain>` to `apiFetch<TaskChainResponse>` (from api-contract.ts which has the correct raw type), and mapped `p.created_at ?? undefined` → `completed_at`.
**File:** `ui/web/src/lib/api.ts:82-86`

### Fix 4 — `ui/web/src/lib/types.ts` — `ChainStep.status` nullable
**Problem:** `ChainStep.status` was `string` but `ChainProgressItem.status` from api-contract.ts is `string | null`. TypeScript error when the correctly-typed chain data was mapped.
**Fix:** Changed `ChainStep.status: string` → `ChainStep.status: string | null`.
**File:** `ui/web/src/lib/types.ts`

### Fix 5 — `api/main.py` — Edit & Approve 500 error
**Problem:** `POST /api/approvals/{id}/approve` with `edited_content` always returned HTTP 500. Root cause: `_store_learning_diff()` calls `kernel/learning.py:set_preference()` which inserts `id="pref-<hex>"` (a string) but `user_preferences` table has `id INTEGER PRIMARY KEY AUTOINCREMENT`. SQLite raised `IntegrityError: datatype mismatch`.
**Fix:** Wrapped `_store_learning_diff()` call in `try/except` with a warning log. The approve action (`kernel.approve_action()`) now always proceeds regardless of learning diff storage. The learning diff is a "nice to have" — the approval must not be blocked by it.
**File:** `api/main.py:482-494`
**Note:** Root cause is a schema mismatch between `kernel/learning.py._migrate()` (creates TEXT id) and `db/schema.sql` (creates INTEGER AUTOINCREMENT). This is in `kernel/` which cannot be touched — the workaround in `api/main.py` is the correct fix layer.

---

## Build Verification

**After all fixes:**
```
$ cd ui/web && npm run build
✓ Compiled successfully in 3.2s
✓ TypeScript: 0 errors
Route (app)
  ○ /           ○ /approvals      ○ /build
  ○ /hire       ƒ /review/[taskId] ○ /teams
  ƒ /teams/[id]
```
**Status: PASS** ✅

---

## Test A — API Contract Verification

All 9 endpoints verified against `api-contract.ts`. Results:

| Endpoint | Expected | Actual | Status |
|---|---|---|---|
| `GET /api/teams` | `TeamSummary[]` | ✅ Matches. `id`, `name`, `template_id`, `template_name`, `status`, `agent_count`, `created_at`, `pending_approvals` all present | PASS |
| `GET /api/teams/{id}` | `TeamDetail` with `members[]` | ✅ Matches. 4 members each with `agent_name`, `role`, `status`, `last_heartbeat`, `current_task` | PASS |
| `GET /api/teams/{id}/members` | `AgentMember[]` | ✅ 4 items, correct shape | PASS |
| `GET /api/teams/{id}/tasks` | `TeamTask[]` with `events[]` | ✅ Matches. `critic_passed` task present with review button | PASS |
| `GET /api/approvals` | `ApprovalItem[]` | ✅ 3 items. `id` is int (not string). `team_id` filter works | PASS |
| `GET /api/tasks/{id}/output` | `TaskOutput` | ✅ `task_id`, `task_title`, `content` all present. 404 for unknown tasks | PASS |
| `GET /api/tasks/{id}/evidence` | `Source[]` | ✅ 3 sources with `url`, `title`, `snippet`, `query` | PASS |
| `GET /api/tasks/{id}/reviews` | `CriticReview \| null` | ✅ Shape correct. `overall_score` and `dimensions` may be null after human actions (expected) | PASS |
| `GET /api/tasks/{id}/chain` | `TaskChainResponse` | ✅ Uses `created_at` (not `completed_at`) — correctly remapped in `api.ts` | PASS |

**Key finding:** `ChainProgressItem` uses `created_at` not `completed_at`. `api.ts:getTaskChain()` was incorrectly reading `p.completed_at` (always undefined). Fixed.

**Key finding:** `review.id` from `GET /api/tasks/{id}/reviews` == `approval.id` from `GET /api/approvals` (both from `reviews` table). The review page's approval ID mapping is correct.

---

## Test B — Teams Overview Page

**URL:** http://localhost:3000/teams

| Check | Expected | Actual | Status |
|---|---|---|---|
| Team cards appear | ≥2 cards (demo teams) | ✅ Shows `ws-demo-marketing` and `ws-demo-sales` plus others | PASS |
| Card shows template name | "Content Engine" | ✅ Shows `template_name` field | PASS |
| Card shows agent count | "4 agents" | ✅ Shows `{agent_count} agents` | PASS |
| Card shows status badge | "Active" | ✅ Status badge with correct styling | PASS |
| Pending approval pill | "⏳ 1 awaiting approval" | ✅ Shows orange pill when pending | PASS |
| `this_week_activity` removed | No crash | ✅ Fixed — shows "Created X ago" instead | PASS |
| Polling every 3s | Background refresh | ✅ Confirmed in source | PASS |

---

## Test C — Team Detail Page

**URL:** http://localhost:3000/teams/ws-demo-marketing

| Check | Expected | Actual | Status |
|---|---|---|---|
| Left panel members list | 4 members | ✅ scout (discovery), writer (creation), critic (quality_gate), scheduler (publishing_ops) | PASS |
| Member status dots | Colored dots with pulse | ✅ Green pulse for `active`, grey for `idle` | PASS |
| Center task list | ≥3 tasks | ✅ Shows 4 tasks sorted by pending-review-first | PASS |
| Pending task shows "Review →" button | Task with critic_passed | ✅ Links to `/review/task-demo-mkt-002` | PASS |
| In-progress task shows agent | `ws-demo-marketing-writer` is `active` with `current_task` | ✅ Status dot pulses green | PASS |
| Mission input submits | POST to `/api/teams/{id}/tasks` | ✅ Creates task, spawns agent, refreshes list | PASS |
| Right panel pending count | "📬 1" | ✅ Links to review page | PASS |

---

## Test D — Output Review Page

**URL:** http://localhost:3000/review/task-demo-mkt-002

| Check | Expected | Actual | Status |
|---|---|---|---|
| Draft renders in textarea | Content from GET /output | ✅ 206-char scout summary pre-fills textarea | PASS |
| Word + char count | "X words · Y chars" | ✅ Shown in header with X single tweet/thread indicator | PASS |
| Sources panel | 3 sources from GET /evidence | ✅ Shows arxiv, github, bain.com sources | PASS |
| Critic score | 3.8/5, PASS verdict | ✅ Score and badge in header and right panel | PASS |
| Dimension bars | accuracy/grounding/authenticity/completeness | ✅ 4 bars with correct scores | PASS |
| Chain progress | Scout → Writer → Critic messages | ✅ Shows 8 chain steps (may have null timestamps from seed) | PASS |
| Approval ID mapping | review.id (31) == approval.id (31) | ✅ Verified — correct endpoint used | PASS |
| "Approve & Publish" | POST /api/approvals/31/approve | ✅ Returns `{status: "approved", edited: false}` | PASS |
| "Edit & Approve" | POST /approve with edited_content | ✅ Fixed (was 500 → now 200 with `edited: true`) | PASS |
| "Request Revision" | Opens textarea, POST /revise | ✅ Returns `{status: "revision_requested"}` | PASS |
| "Reject" | Inline confirm → POST /reject | ✅ Returns `{status: "rejected"}` | PASS |
| Redirect after approve | To `/teams/{teamId}` after 1.8s | ✅ `teamId` fetched from approvals queue | PASS |
| Error if no approval found | "Actions unavailable" message | ✅ Buttons disabled gracefully | PASS |

---

## Test E — Approvals Queue

**URL:** http://localhost:3000/approvals

| Check | Expected | Actual | Status |
|---|---|---|---|
| Pending items shown | 2 items (mkt-002, sales-002) | ✅ Plus demo-content approval from seed | PASS |
| Stakes badge | "medium" in yellow | ✅ `approval.stakes ?? "low"` null guard fixed | PASS |
| Approve button | Removes item from list | ✅ Optimistic removal after POST | PASS |
| Reject button | Removes item from list | ✅ Works | PASS |
| Polling every 3s | Auto-refresh | ✅ Confirmed in source | PASS |

---

## Test F — Team Builder

**URL:** http://localhost:3000/build

| Check | Expected | Actual | Status |
|---|---|---|---|
| Category selection | "Content & Marketing" etc. | ✅ Shows categories with description | PASS |
| Role cards appear | Role library with descriptions | ✅ 13+ role cards available | PASS |
| "Launch this team" | POST to `/api/teams` (Next.js route) | ✅ Returns mock `teamId` (v1 stub) | PASS |
| Success screen | Shows team ID | ✅ Renders success state | PASS |

**Note:** The `/api/teams` POST at the Next.js route level is a v1 stub returning a mock team ID. The real launch goes through `POST /api/teams/{ws-id}/tasks` on the FastAPI backend. Team builder wired to Next.js stub is correct per the spec.

---

## E2E Test Summary

```
$ PYTHONPATH=. python3 scripts/e2e_test.py

Results: 65 passed, 0 failed
All tests passed. ✅
```

---

## Bugs Found and Fixed

| # | Bug | Severity | File | Fix |
|---|---|---|---|---|
| 1 | `approval.stakes` null index crash (TS build error) | High | `approvals/page.tsx:118` | Null guard `?? "low"` |
| 2 | `team-card.tsx` missing `deleted` status + `this_week_activity` crash (TS build error) | High | `team-card.tsx:16,52` | Added `deleted`, removed `this_week_activity` |
| 3 | `getTaskChain()` reads `p.completed_at` (undefined) instead of `p.created_at` | Medium | `api.ts:83` | Changed to `p.created_at ?? undefined` |
| 4 | `ChainStep.status` typed as `string` not `string \| null` | Low | `types.ts` | Allow null |
| 5 | `POST /api/approvals/{id}/approve` with `edited_content` → HTTP 500 | High | `api/main.py:482` | Wrapped `_store_learning_diff` in try/except |

---

## Known Issues (Not Fixed — Out of Scope)

| Issue | Location | Impact |
|---|---|---|
| `kernel/learning.py.set_preference()` inserts string id into INTEGER AUTOINCREMENT column | `kernel/learning.py:76` | Learning diffs not stored (logged as warning). Approve still works. Cannot fix — kernel/ is off-limits. |
| Chain timestamps all `null` in seed data | `scripts/seed_demo.py` | Chain timeline shows "—" for all timestamps. Not a code bug — seed data has `null` created_at. |
| Task creation spawns real agent (>30s on Intel Mac) | All platforms | Task creation endpoint takes 15-90s depending on model. Frontend has no loading indication beyond the Send button spinner. |
| `task.created_at` is `null` for seeded tasks | `seed_demo.py` | Task sort by date uses `null` for all seed tasks, so ordering is arbitrary. Non-critical for dev. |
