"""Turn axe-core results into findings. Pure functions."""

import json
from datetime import datetime
from typing import Any

from agentready.core import AuditFinding, AxeImpact, Severity

IMPACT_SEVERITY = {
    AxeImpact.CRITICAL: Severity.CRITICAL,
    AxeImpact.SERIOUS: Severity.HIGH,
    AxeImpact.MODERATE: Severity.MEDIUM,
    AxeImpact.MINOR: Severity.LOW,
}
MAX_EXAMPLES = 3
MAX_HTML_CHARS = 300


def axe_options(tags: list[str]) -> dict[str, Any]:
    return {"runOnly": {"type": "tag", "values": tags}, "resultTypes": ["violations"]}


def severity_for(impact: str | None) -> Severity:
    try:
        return IMPACT_SEVERITY[AxeImpact(impact)]
    except ValueError:
        return Severity.MEDIUM


def findings_from_axe(
    results: dict[str, Any], *, site_id: str, url: str, audited_at: datetime
) -> list[AuditFinding]:
    """One finding per violated rule on the page, with a few example elements as evidence."""
    findings = []
    for violation in results.get("violations", []):
        nodes = violation.get("nodes", [])
        evidence = {
            "impact": violation.get("impact"),
            "help_url": violation.get("helpUrl"),
            "wcag": [t for t in violation.get("tags", []) if t.startswith("wcag")],
            "nodes": len(nodes),
            "examples": [
                {
                    "target": " ".join(str(t) for t in node.get("target", [])),
                    "html": (node.get("html") or "")[:MAX_HTML_CHARS],
                }
                for node in nodes[:MAX_EXAMPLES]
            ],
        }
        findings.append(
            AuditFinding(
                site_id=site_id,
                url=url,
                check_id=violation["id"],
                severity=severity_for(violation.get("impact")),
                message=violation.get("help") or violation["id"],
                evidence=json.dumps(evidence, ensure_ascii=False),
                audited_at=audited_at,
            )
        )
    return findings
