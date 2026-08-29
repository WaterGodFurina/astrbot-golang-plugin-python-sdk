"""技能包（Go 宿主兼容运行时，对齐本体 astrbot.core.skills）。"""

from astrbot.core.skills.skill_manager import (  # noqa: F401
    SkillInfo,
    SkillManager,
    build_skills_prompt,
)

__all__ = ["SkillInfo", "SkillManager", "build_skills_prompt"]