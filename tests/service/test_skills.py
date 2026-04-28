"""Skill seed and prompt rendering tests."""

from astracore.service import seeds
from astracore.service.api import chat


def test_render_skill_prompt_injects_current_beijing_time() -> None:
    rendered = chat._render_skill_prompt(
        "时间上下文：\n{{current_time_info}}",
        ai_name="小卡",
        owner_name="灰尘",
    )

    assert "{{current_time_info}}" not in rendered
    assert "【当前时间信息】" in rendered
    assert "北京时间：" in rendered
    assert "当用户提到\"今天\"时" in rendered


def test_builtin_skills_are_ordered_by_frontmatter_order(tmp_path, monkeypatch) -> None:
    (tmp_path / "a.md").write_text(
        "---\nname: A\ndescription: A desc\norder: 30\n---\nA prompt",
        encoding="utf-8",
    )
    (tmp_path / "b.md").write_text(
        "---\nname: B\ndescription: B desc\norder: 10\n---\nB prompt",
        encoding="utf-8",
    )

    monkeypatch.setattr(seeds, "SKILLS_DIR", tmp_path)

    skills = seeds._load_builtin_skills()

    assert [skill["source_key"] for skill in skills] == ["b", "a"]
    assert [skill["order"] for skill in skills] == [10, 30]
