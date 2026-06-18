"""Skill seed and prompt rendering tests."""

from astracore.modules.skills import seeds
from astracore.modules.skills.prompt_utils import render_skill_prompt


def test_render_skill_prompt_injects_current_beijing_time() -> None:
    rendered = render_skill_prompt(
        "时间上下文：\n{{current_time_info}}",
        ai_name="小卡",
        owner_name="灰尘",
    )

    assert "{{current_time_info}}" not in rendered
    # XML 自闭合时间标签：<datetime now="..." today="..." weekday="..." tz="Asia/Shanghai"/>
    assert "<datetime " in rendered
    assert 'tz="Asia/Shanghai"' in rendered
    assert 'today="' in rendered


def test_builtin_skills_are_ordered_by_frontmatter_order(tmp_path, monkeypatch) -> None:
    skill_a = tmp_path / "a"
    skill_b = tmp_path / "b"
    skill_a.mkdir()
    skill_b.mkdir()
    (skill_a / "SKILL.md").write_text(
        "---\nname: A\ndescription: A desc\norder: 30\n---\nA prompt",
        encoding="utf-8",
    )
    (skill_b / "SKILL.md").write_text(
        "---\nname: B\ndescription: B desc\norder: 10\n---\nB prompt",
        encoding="utf-8",
    )

    monkeypatch.setattr(seeds, "SKILLS_DIR", tmp_path)

    skills = seeds._load_builtin_skills()

    assert [skill["source_key"] for skill in skills] == ["b", "a"]
    assert [skill["order"] for skill in skills] == [10, 30]
