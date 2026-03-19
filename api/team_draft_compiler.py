from __future__ import annotations

import json
import shutil
from pathlib import Path
from typing import Any

from kernel.api import ApexKernel
from kernel.autonomy_policy import save_workspace_autonomy
from kernel.team_drafts import TeamDraftStore


class TeamDraftCompiler:
    def __init__(self, kernel: ApexKernel) -> None:
        self.kernel = kernel
        self.store = TeamDraftStore(self.kernel.db_path)

    def build_draft_from_template(
        self,
        user_id: str,
        source_goal: str,
        template_id: str,
        recommended_name: str | None = None,
        autonomy: str = "hands_on",
        update_cadence: str = "after_each_step",
        channels: list[str] | None = None,
    ) -> dict[str, Any]:
        manifest = self.kernel.get_template(template_id)
        draft = self.kernel.create_team_draft(
            source_goal=source_goal,
            recommended_template_id=template_id,
            name=recommended_name or manifest.get("name") or template_id,
            user_id=user_id,
            autonomy=autonomy,
            update_cadence=update_cadence,
            channels=channels or [],
            metadata={
                "template_name": manifest.get("name") or template_id,
                "template_category": manifest.get("category"),
                "template_version": manifest.get("version"),
                "pipeline": manifest.get("pipeline", []),
            },
        )

        default_tool_grants = self._default_tool_grants(manifest)
        for position, agent_cfg in enumerate(manifest.get("agents", []), start=1):
            role_key = str(agent_cfg.get("name") or "").strip()
            if not role_key:
                continue
            self.kernel.add_team_draft_agent(
                draft_id=draft["id"],
                role_key=role_key,
                display_name=self._draft_display_name(agent_cfg),
                role_description=agent_cfg.get("description"),
                tools=list(default_tool_grants.get(role_key, [])),
                skills=list(agent_cfg.get("capabilities") or []),
                pipeline_position=position,
                source="template",
                enabled=True,
                metadata={
                    "heartbeat": agent_cfg.get("heartbeat"),
                    "heartbeat_description": agent_cfg.get("heartbeat_description"),
                    "model": agent_cfg.get("model"),
                    "can_message": agent_cfg.get("can_message", []),
                    "output_format": agent_cfg.get("output_format"),
                    "template_agent_name": role_key,
                },
            )

        return self._full_draft(draft["id"])

    def launch_draft(self, draft_id: str) -> dict[str, Any]:
        draft = self.store.get_draft(draft_id)
        if draft is None:
            raise ValueError(f"Draft '{draft_id}' not found.")
        if draft.get("status") == "launched":
            raise ValueError(f"Draft '{draft_id}' has already been launched.")

        template_id = str(draft.get("recommended_template_id") or "").strip()
        if not template_id:
            raise ValueError(f"Draft '{draft_id}' is missing recommended_template_id.")

        enabled_agents = [
            agent for agent in self.store.get_draft_agents(draft_id)
            if bool(agent.get("enabled"))
        ]
        if not enabled_agents:
            raise ValueError(f"Draft '{draft_id}' must have at least one enabled agent.")

        enabled_agents.sort(key=lambda item: (int(item.get("pipeline_position") or 0), item.get("created_at") or ""))
        manifest = self.kernel.get_template(template_id)
        template_agents = {
            str(agent.get("name") or "").strip(): agent
            for agent in manifest.get("agents", [])
            if str(agent.get("name") or "").strip()
        }

        missing_roles = [
            str(agent.get("role_key") or "").strip()
            for agent in enabled_agents
            if str(agent.get("role_key") or "").strip() not in template_agents
        ]
        if missing_roles:
            raise ValueError(
                f"Draft '{draft_id}' references template roles that do not exist: {', '.join(missing_roles)}"
            )

        launch_result = self.kernel.launch_template(
            template_id,
            overrides={"workspace_name": draft.get("name") or manifest.get("name") or template_id},
        )
        workspace_id = str(launch_result["workspace_id"])

        self._remove_disabled_agents(
            workspace_id=workspace_id,
            enabled_role_keys={
                str(agent.get("role_key") or "").strip()
                for agent in enabled_agents
            },
        )

        for draft_agent in enabled_agents:
            role_key = str(draft_agent.get("role_key") or "").strip()
            live_agent_id = f"{workspace_id}-{role_key}"
            custom_config_path = self._materialize_agent_instance(
                workspace_id=workspace_id,
                draft_agent=draft_agent,
                template_agent=template_agents[role_key],
            )
            self._update_live_agent(
                live_agent_id=live_agent_id,
                draft_id=draft_id,
                draft_agent=draft_agent,
                custom_config_path=custom_config_path,
            )

        save_workspace_autonomy(self.kernel.db_path, workspace_id, str(draft.get("autonomy") or "hands_on"))
        metadata = dict(draft.get("metadata") or {})
        metadata.update(
            {
                "workspace_id": workspace_id,
                "launched_template_id": template_id,
                "launched_agent_count": len(enabled_agents),
            }
        )
        self.store.update_draft(draft_id, {"metadata": metadata})
        self.store.set_status(draft_id, "launched")

        return {
            "workspace_id": workspace_id,
            "team_name": draft.get("name") or manifest.get("name") or workspace_id,
            "agent_count": len(enabled_agents),
        }

    def get_draft(self, draft_id: str) -> dict[str, Any] | None:
        draft = self.store.get_draft(draft_id)
        if draft is None:
            return None
        draft["agents"] = self.store.get_draft_agents(draft_id)
        return draft

    def _full_draft(self, draft_id: str) -> dict[str, Any]:
        draft = self.get_draft(draft_id)
        if draft is None:
            raise ValueError(f"Draft '{draft_id}' not found.")
        return draft

    def _default_tool_grants(self, manifest: dict[str, Any]) -> dict[str, list[str]]:
        grants = {
            str(role): list(tools)
            for role, tools in (manifest.get("default_tool_grants") or {}).items()
            if isinstance(tools, list)
        }
        if grants:
            return grants

        all_integrations = set(manifest.get("integrations", [])) | set(manifest.get("optional_integrations", []))
        if "web_search" in all_integrations:
            return {"scout": ["web_search"], "analyst": ["web_search"]}
        return {}

    def _draft_display_name(self, agent_cfg: dict[str, Any]) -> str:
        description = str(agent_cfg.get("description") or "").strip()
        for marker in (" — ", " – ", " - "):
            if marker in description:
                return description.split(marker, 1)[0].strip()
        role_key = str(agent_cfg.get("name") or "").strip()
        return role_key.replace("_", " ").title() if role_key else "Agent"

    def _materialize_agent_instance(
        self,
        workspace_id: str,
        draft_agent: dict[str, Any],
        template_agent: dict[str, Any],
    ) -> Path:
        role_key = str(draft_agent.get("role_key") or "").strip()
        template_agent_dir = self.kernel.apex_home / "templates" / str(
            self.store.get_draft(draft_agent["draft_id"]).get("recommended_template_id")  # type: ignore[union-attr]
        ) / "agents" / role_key
        target_dir = self.kernel.apex_home / "runtime" / "workspaces" / workspace_id / "agents" / role_key
        target_dir.parent.mkdir(parents=True, exist_ok=True)
        shutil.copytree(template_agent_dir, target_dir, dirs_exist_ok=True)

        agent_json_path = target_dir / "agent.json"
        agent_json = {
            "name": role_key,
            "role": template_agent.get("role", "custom"),
            "description": draft_agent.get("role_description") or template_agent.get("description", ""),
            "model": template_agent.get("model", {"primary": "qwen3.5-apex", "fallback": "claude-sonnet"}),
            "heartbeat": template_agent.get("heartbeat"),
            "heartbeat_description": template_agent.get("heartbeat_description"),
            "capabilities": list(draft_agent.get("skills") or template_agent.get("capabilities") or []),
            "can_message": template_agent.get("can_message", []),
            "api_config": template_agent.get("api_config", {"think": False, "num_ctx": 4096, "temperature": 0.3}),
        }
        if template_agent.get("output_format") is not None:
            agent_json["output_format"] = template_agent.get("output_format")
        if template_agent.get("stakes_routing") is not None:
            agent_json["stakes_routing"] = template_agent.get("stakes_routing")
        agent_json_path.write_text(json.dumps(agent_json, indent=2) + "\n")
        return agent_json_path

    def _remove_disabled_agents(self, workspace_id: str, enabled_role_keys: set[str]) -> None:
        with self.kernel._connect() as conn:
            rows = conn.execute(
                """
                SELECT agent_name
                FROM agent_status
                WHERE workspace_id = ?
                """,
                (workspace_id,),
            ).fetchall()
            for row in rows:
                agent_name = str(row["agent_name"])
                role_key = agent_name.removeprefix(f"{workspace_id}-")
                if role_key in enabled_role_keys:
                    continue
                conn.execute("DELETE FROM tool_grants WHERE agent_id = ?", (agent_name,))
                conn.execute("DELETE FROM permissions WHERE agent_id = ?", (agent_name,))
                conn.execute("DELETE FROM budgets WHERE agent_id = ?", (agent_name,))
                conn.execute("DELETE FROM agent_status WHERE agent_name = ?", (agent_name,))
            conn.commit()

    def _update_live_agent(
        self,
        live_agent_id: str,
        draft_id: str,
        draft_agent: dict[str, Any],
        custom_config_path: Path,
    ) -> None:
        with self.kernel._connect() as conn:
            row = conn.execute(
                "SELECT meta FROM agent_status WHERE agent_name = ?",
                (live_agent_id,),
            ).fetchone()
            if row is None:
                raise ValueError(f"Live agent '{live_agent_id}' was not created.")

            meta = self.kernel._load_json(row["meta"] if row else None)
            meta.update(
                {
                    "config_path": str(custom_config_path),
                    "draft_id": draft_id,
                    "draft_agent_id": draft_agent["id"],
                    "display_name": draft_agent.get("display_name"),
                    "role_description": draft_agent.get("role_description"),
                    "pipeline_position": draft_agent.get("pipeline_position"),
                    "skills": draft_agent.get("skills", []),
                    "tools": draft_agent.get("tools", []),
                }
            )
            conn.execute(
                "UPDATE agent_status SET meta = ? WHERE agent_name = ?",
                (json.dumps(meta), live_agent_id),
            )
            conn.execute("DELETE FROM tool_grants WHERE agent_id = ?", (live_agent_id,))
            for tool_id in draft_agent.get("tools", []):
                conn.execute(
                    """
                    INSERT OR IGNORE INTO tool_grants (agent_id, tool_id, permission_level)
                    VALUES (?, ?, 'read_only')
                    """,
                    (live_agent_id, tool_id),
                )
            conn.commit()
