from __future__ import annotations

import re
from pathlib import Path
from typing import Any

from kernel.evidence import EvidenceStore


URL_PATTERN = re.compile(r"https?://[^\s)>\]\"']+")


def verify_agent_output(task_id: str, agent_output: str, db_path: str | Path) -> dict[str, Any]:
    store = EvidenceStore(db_path)
    evidence = store.get_evidence(task_id)
    citations = URL_PATTERN.findall(agent_output or "")

    verified = 0
    unverified: list[str] = []
    for url in citations:
        if store.verify_citation(task_id, url):
            verified += 1
        else:
            unverified.append(url)

    total = len(citations)
    grounding_score = 1.0 if total == 0 else verified / total

    return {
        "total_citations": total,
        "verified": verified,
        "unverified": unverified,
        "evidence_count": len(evidence),
        "grounding_score": grounding_score,
    }
