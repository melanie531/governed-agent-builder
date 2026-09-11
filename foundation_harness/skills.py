"""Instruction-only content; no filesystem/plugin/script loading."""
from .config import digest


def instructions(skills):
    blocks = []
    for skill in skills:
        if digest(skill.instructions) != skill.digest:
            raise ValueError('SKILL_DIGEST_MISMATCH')
        blocks.append(skill.instructions)
    return blocks
