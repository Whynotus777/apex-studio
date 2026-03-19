"""Shared pipeline execution helpers.

Both api/main.py (HTTP-triggered runs) and kernel/scheduler.py (cron-triggered
runs) import from here so the same logic runs regardless of trigger source.

Public API
----------
run_pipeline_chain(task_id, agent_ids, kernel)
    Spawn agents sequentially, stopping on terminal task states.

get_ordered_pipeline_agents(team_id, template_id, kernel) -> list[str]
    Return workspace-namespaced agent IDs in template pipeline order.

launch_pipeline(team_id, task_id, pipeline_agents, kernel, task_queue, source) -> dict
    Enqueue + conditionally start a pipeline for a team.
    Returns {"status": "running"|"queued", "task_id": ..., "pipeline": ...}.
"""
from __future__ import annotations

import logging
import threading
from typing import TYPE_CHECKING, Any, Callable, Optional

if TYPE_CHECKING:
    from kernel.api import ApexKernel
    from kernel.task_queue import TaskQueue

log = logging.getLogger("apex.pipeline")


# ── Stage-name → agent-role mapping ──────────────────────────────────────────

_STAGE_MAP: dict[str, str] = {
    "discover": "scout",
    "analyze": "analyst",
    "analyse": "analyst",
    "strategize": "strategist",
    "create": "writer",
    "draft": "writer",
    "review": "critic",
    "validate": "critic",
    "publish": "publisher",
    "build": "builder",
    "launch": "apex",
    "grow": "apex",
    "enrich": "analyst",
}


def resolve_start_agent(
    team_id: str,
    workspace: dict[str, Any],
    kernel: "ApexKernel",
    pipeline_stage: str | None = None,
) -> str:
    """Return the workspace-namespaced agent id for the first pipeline stage.

    Raises ValueError for unsupported stages so callers outside HTTP context
    can handle the error without importing HTTPException.
    """
    manifest = kernel.get_template(workspace["template_id"])
    if pipeline_stage:
        first_stage = pipeline_stage.lower().strip()
    else:
        pipeline = manifest.get("pipeline", [])
        if not pipeline:
            raise ValueError("Template has no pipeline stages.")
        first_stage = str(pipeline[0]).lower()

    role = _STAGE_MAP.get(first_stage)
    if not role:
        raise ValueError(f"Unsupported pipeline stage '{first_stage}'.")

    agent_id = f"{team_id}-{role}"
    kernel._ensure_agent_exists(agent_id)
    return agent_id


def get_ordered_pipeline_agents(
    team_id: str,
    workspace: dict[str, Any],
    kernel: "ApexKernel",
) -> list[str]:
    """Return workspace-namespaced agent IDs in template pipeline order."""
    manifest = kernel.get_template(workspace["template_id"])
    return [
        f"{team_id}-{agent['name']}"
        for agent in manifest.get("agents", [])
        if agent.get("name")
    ]


def _score_pending_reviews(task_id: str, kernel: "ApexKernel") -> None:
    """Run the Critic scoring pipeline if the task has unscored reviews.

    Called immediately after a critic agent spawns so that autonomy-policy
    decisions have a real verdict + score to act on.
    """
    try:
        pending = kernel._fetch_all(
            "SELECT id FROM reviews WHERE task_id = ? AND verdict IS NULL",
            (task_id,),
        )
        if not pending:
            log.info("Pipeline chain: no pending reviews for task %s — skipping critic scoring.", task_id)
            return
        log.info(
            "Pipeline chain: scoring %d pending review(s) for task %s via run_critic_pipeline.",
            len(pending),
            task_id,
        )
        kernel.run_critic_pipeline()
    except Exception as exc:
        log.error(
            "Pipeline chain: critic pipeline scoring failed for task %s: %s", task_id, exc
        )


def run_pipeline_chain(
    task_id: str,
    agent_ids: list[str],
    kernel: "ApexKernel",
) -> None:
    """Spawn agents sequentially, stopping on terminal or review task states.

    Blocks the calling thread until the chain finishes or stops early.
    After any critic agent completes, pending reviews are scored immediately
    so that the autonomy policy has a verdict to act on.
    """
    for agent_id in agent_ids:
        try:
            with kernel._connect() as conn:
                row = conn.execute(
                    "SELECT status FROM tasks WHERE id = ?", (task_id,)
                ).fetchone()
            if not row:
                log.warning("Pipeline chain: task %s not found, stopping.", task_id)
                break
            status = row["status"]
            if status in ("cancelled", "failed"):
                log.info("Pipeline chain: task %s is %s, stopping.", task_id, status)
                break
            if status in ("review", "needs_review", "pending_approval"):
                log.info("Pipeline chain: task %s entered review, stopping.", task_id)
                break

            log.info("Pipeline chain: spawning %s for task %s.", agent_id, task_id)
            kernel.spawn_agent(agent_id, task_id)
            log.info("Pipeline chain: %s finished for task %s.", agent_id, task_id)

            # After a critic agent runs, score any pending reviews immediately
            # so the autonomy policy decision has a real verdict + score.
            if "critic" in agent_id.lower():
                _score_pending_reviews(task_id, kernel)

        except Exception as exc:
            log.error(
                "Pipeline chain: %s failed for task %s: %s", agent_id, task_id, exc
            )
            break


def advance_queue(
    workspace_id: str,
    completed_task_id: str,
    kernel: "ApexKernel",
    task_queue: "TaskQueue",
    post_run_hook: "Optional[Callable[[str], None]]" = None,
) -> None:
    """Start the next queued task for a workspace after one completes.

    Called from the pipeline thread's finally block and from the approval
    endpoint so the queue drains automatically regardless of how a task ends.

    Guard: if the next task_id equals the just-completed task_id, stop
    immediately to prevent an infinite loop.
    """
    next_task_id = task_queue.next_runnable_task(workspace_id)
    if not next_task_id:
        log.info("Queue advance: no queued tasks for workspace %s.", workspace_id)
        return

    if next_task_id == completed_task_id:
        log.warning(
            "Queue advance: next task %s matches just-completed task — stopping to prevent loop.",
            next_task_id,
        )
        return

    try:
        workspace = kernel.get_workspace(workspace_id)
        pipeline_agents = get_ordered_pipeline_agents(workspace_id, workspace, kernel)
    except Exception as exc:
        log.error(
            "Queue advance: failed to resolve pipeline for workspace %s: %s",
            workspace_id,
            exc,
        )
        return

    task_queue.mark_active(next_task_id)
    log.info(
        "Queue advance: starting task %s for workspace %s.",
        next_task_id,
        workspace_id,
    )
    thread = threading.Thread(
        target=_run_queued_task,
        args=(next_task_id, pipeline_agents, workspace_id, kernel, task_queue, post_run_hook),
        daemon=True,
        name=f"pipeline-{next_task_id[:8]}",
    )
    thread.start()


def _run_queued_task(
    task_id: str,
    pipeline_agents: list[str],
    workspace_id: str,
    kernel: "ApexKernel",
    task_queue: "TaskQueue",
    post_run_hook: "Optional[Callable[[str], None]]" = None,
) -> None:
    """Run a pipeline and advance the queue when done."""
    try:
        run_pipeline_chain(task_id, pipeline_agents, kernel)
        if post_run_hook:
            try:
                post_run_hook(task_id)
            except Exception as exc:
                log.error("post_run_hook failed for queued task %s: %s", task_id, exc)
    finally:
        task_queue.mark_completed(task_id)
        advance_queue(workspace_id, task_id, kernel, task_queue, post_run_hook=post_run_hook)


def launch_pipeline(
    team_id: str,
    task_id: str,
    pipeline_agents: list[str],
    kernel: "ApexKernel",
    task_queue: "TaskQueue",
    source: str = "api",
    post_run_hook: "Optional[Callable[[str], None]]" = None,
) -> dict[str, Any]:
    """Enqueue *task_id* and start the pipeline unless the team is already busy.

    If the team has an active run the task is left in 'queued' state and will
    be picked up by the auto-continuation logic when the current run completes.

    post_run_hook, if provided, is called with task_id after the chain finishes.
    It is also forwarded to any tasks dequeued by advance_queue, so every task
    in the queue benefits from the same post-chain actions (policy, notifications).

    Returns a dict with keys: status ("running" or "queued"), task_id, pipeline.
    """
    if task_queue.team_has_active_run(team_id):
        task_queue.enqueue_task(team_id, task_id)
        log.info(
            "[%s] Pipeline queued for team %s task %s (active run in progress).",
            source, team_id, task_id,
        )
        return {"status": "queued", "task_id": task_id, "pipeline": pipeline_agents}

    task_queue.enqueue_task(team_id, task_id)
    task_queue.mark_active(task_id)

    def _run_and_complete(tid: str, agents: list[str]) -> None:
        try:
            run_pipeline_chain(tid, agents, kernel)
            if post_run_hook:
                try:
                    post_run_hook(tid)
                except Exception as exc:
                    log.error("post_run_hook failed for task %s: %s", tid, exc)
        finally:
            task_queue.mark_completed(tid)
            advance_queue(team_id, tid, kernel, task_queue, post_run_hook=post_run_hook)

    thread = threading.Thread(
        target=_run_and_complete,
        args=(task_id, pipeline_agents),
        daemon=True,
        name=f"pipeline-{task_id[:8]}",
    )
    thread.start()
    log.info(
        "[%s] Pipeline started for team %s task %s agents=%s.",
        source, team_id, task_id, pipeline_agents,
    )
    return {"status": "running", "task_id": task_id, "pipeline": pipeline_agents}
