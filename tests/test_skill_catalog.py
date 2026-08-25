import json
from pathlib import Path


def test_skill_catalog_has_one_agent_and_five_skills() -> None:
    root = Path(__file__).parents[1]
    catalog = json.loads((root / "wanwu" / "skills" / "catalog.json").read_text(encoding="utf-8"))

    assert catalog["agent_id"] == "shicha_qianji_industrial_agent"
    skills = catalog["skills"]
    assert len(skills) == 5
    assert len({item["id"] for item in skills}) == 5
    assert catalog["interaction_policy"]["confirmation_required_for_side_effects"] is True

    side_effect_tools = {
        tool
        for skill in skills
        for tool in skill["action_tools"]
    }
    assert "configure_industrial_data_source" in side_effect_tools
    assert "run_unattended_industrial_cycle" in side_effect_tools
    assert "run_industrial_reinspection_cycle" in side_effect_tools
