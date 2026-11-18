"""Select hypothetical examples within the assessment's guarded product cap."""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .profiles import Assessment


_PRODUCT_ORDER = {f"R{level}": level for level in range(1, 6)}
_SCENARIOS = (
    (
        "liquidity_buffer",
        "R1",
        "短期资金缓冲模拟",
        "设定一笔虚拟的短期支出，比较两种资金缓冲下可随时调用的余额与资金缺口。",
        ("FC", "OC"),
    ),
    (
        "drawdown_response",
        "R2",
        "回撤应对模拟",
        "对同一笔虚拟本金施加轻度和较大回撤，分别记录承受的损失和预先设定的暂停条件。",
        ("RA", "BD"),
    ),
)


def simulation_for(assessment: Assessment | None, mode: str) -> dict[str, object]:
    if assessment is None:
        return {"state": "assessment_required", "reason": "five_dimension_assessment_required", "scenarios": []}
    if assessment.prohibitions:
        return {"state": "blocked", "reason": "upstream_prohibition", "scenarios": []}
    if mode != "advisory":
        reason = "education_mode" if mode == "education" else "clarification_required"
        return {"state": "not_applicable", "reason": reason, "scenarios": []}

    product_cap = _PRODUCT_ORDER.get(assessment.product_cap)
    if product_cap is None:
        return {"state": "blocked", "reason": "invalid_product_cap", "scenarios": []}

    scenarios = []
    for scenario_id, risk, title, description, dimensions in _SCENARIOS:
        if _PRODUCT_ORDER[risk] > product_cap:
            continue
        references = [
            {"type": "dimension", "code": code, "value": assessment.dimensions[code]}
            for code in dimensions
        ]
        references.append({"type": "product_cap", "value": assessment.product_cap})
        scenarios.append({
            "id": scenario_id,
            "title": title,
            "description": description,
            "product_risk": risk,
            "references": references,
        })

    return {"state": "available", "scenarios": scenarios}
