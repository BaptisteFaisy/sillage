"""The export of a fiche as a skill (annexe F, EF-EXE-01, EF-EXE-02, R18).

The journey of chapter 8.7: a referent exports a fiche, the folder goes to the
registry as a draft, and it becomes discoverable once it is approved. The
tests here check the folder, the draft, and the two rules around them: only a
referent exports, and a fiche that goes aside takes its skill with it.
"""

from __future__ import annotations

from sillage.kernel.types import Level, PrincipalRole
from sillage.tools import export_skill, set_aside
from sillage.tools.errors import is_refusal
from sillage.tools.skill import skill_folder
from tools_fakes import CLIENT_A, add_fiche, build_stack


def test_a_proven_fiche_is_exported_as_a_draft_in_the_registry():
    """R18, EF-EXE-01, EF-EXE-02: the skill is registered, and it is a draft."""
    stack = build_stack()
    fiche = add_fiche(stack, level=Level.PROVEN)
    referent = stack.principal(PrincipalRole.REFERENT, CLIENT_A)

    answer = export_skill(stack, fiche_id=fiche.fiche_id, principal=referent)

    assert not is_refusal(answer)
    assert answer["state"] == "draft"
    assert answer["files"] == ["SKILL.md"]
    record = stack.registry.records[answer["recordId"]]
    assert record.state == "draft"
    assert record.folder["SKILL.md"].startswith("---")


def test_the_folder_is_the_one_of_annexe_f():
    """The file Kiro would load: the front matter, the steps, the traps, the return."""
    stack = build_stack()
    fiche = add_fiche(stack, level=Level.PROVEN)

    folder = skill_folder(fiche)

    text = folder["SKILL.md"]
    assert "name: connexions-a-la-base-epuisees" in text
    assert "description:" in text
    assert "## Quand l'utiliser" in text
    assert "## Étapes" in text
    assert "1. Comparer la taille du groupe de connexions" in text
    assert "## À ne pas faire" in text
    assert "Redémarrer le service" in text
    assert "report_outcome" in text
    assert fiche.fiche_id in text


def test_only_the_referent_exports_a_skill():
    """Table 10.5: the export is the referent's."""
    stack = build_stack()
    fiche = add_fiche(stack, level=Level.PROVEN)
    engineer = stack.principal(PrincipalRole.ENGINEER, CLIENT_A)

    answer = export_skill(stack, fiche_id=fiche.fiche_id, principal=engineer)

    assert is_refusal(answer)
    assert "M.4" in answer["message"]


def test_a_draft_fiche_is_not_exported():
    """EF-EXE-01: a skill carries validated knowledge, never a draft."""
    stack = build_stack()
    fiche = add_fiche(stack, level=Level.PROPOSED)
    referent = stack.principal(PrincipalRole.REFERENT, CLIENT_A)

    answer = export_skill(stack, fiche_id=fiche.fiche_id, principal=referent)

    assert is_refusal(answer)
    assert answer["error"] == "validation_error"


def test_a_fiche_set_aside_deprecates_its_skill():
    """EF-EXE-04: the skill of a fiche that failed leaves the registry."""
    stack = build_stack()
    fiche = add_fiche(stack, level=Level.PROVEN)
    referent = stack.principal(PrincipalRole.REFERENT, CLIENT_A)
    exported = export_skill(stack, fiche_id=fiche.fiche_id, principal=referent)

    set_aside(stack, fiche_id=fiche.fiche_id, reason="deux échecs de suite", principal=referent)

    assert stack.registry.records[exported["recordId"]].state == "deprecated"
    assert stack.catalog.get_fiche(fiche.fiche_id).level is Level.SET_ASIDE
