"""The Cedar text of the policies: annex D, action names, no client in clear.

The rendered text is what ``infra/cdk`` hands to AgentCore Policy, so it is
checked the way a validator would read it: the shape of every policy, the five
policies of annex D, the action names built by ``Settings.action_name``, and the
absence of any client identifier written in the policies themselves.
"""

from __future__ import annotations

import re

import pytest

from sillage.kernel.config import DEFAULT, Settings
from sillage.policy.cedar import (
    GATEWAY_PLACEHOLDER,
    actions_of,
    cedar_actions_of,
    forbidden_actions,
    gateway_arn,
    policy_document,
    render_policies,
    rendered_rules,
)
from sillage.policy.rules import ANNEX_D, CLIENT_TOOLS, RULES, TOOLS

#: The exact shape of a policy, as the brief of the project states it.
POLICY_SHAPE = re.compile(
    r'^(?P<effect>permit|forbid) \(principal, action == AgentCore::Action::"(?P<action>[^"]+)", '
    r'resource == AgentCore::Gateway::"(?P<resource>[^"]+)"\)\n'
    r"(?P<clause>when|unless) \{\n(?P<condition>(?: {2}\S.*\n)+)\};$",
    re.MULTILINE,
)

#: Every comparison of the client of a call, in the whole text.
CLIENT_COMPARISON = re.compile(r"context\.input\.clientId\s*(?:==|!=)\s*(?P<right>[^\n]+)")


@pytest.fixture
def text() -> str:
    return render_policies()


def policies_of(text: str) -> list[re.Match[str]]:
    """Every policy block of the rendered text."""
    return list(POLICY_SHAPE.finditer(text))


class TestAnnexD:
    """The five policies of annex D are all there, with their effect."""

    @pytest.mark.parametrize(
        ("rule_id", "tool", "effect"),
        [
            ("D.1", "recall", "permit"),
            ("D.2", "recall_all", "forbid"),
            ("D.3", "validate_fiche", "permit"),
            ("D.4", "publish_common", "forbid"),
            ("D.5", "record_episode", "permit"),
        ],
    )
    def test_the_rule_is_rendered(self, text, rule_id, tool, effect):
        block = next(part for part in text.split("\n\n") if part.startswith(f"// {rule_id} "))
        assert block.startswith(f"// {rule_id} —")
        assert f'{effect} (principal, action == AgentCore::Action::"Savoir___{tool}"' in block

    def test_the_five_ids_are_the_ones_of_the_annex(self):
        assert ANNEX_D == ("D.1", "D.2", "D.3", "D.4", "D.5")
        assert {rule.id for rule in rendered_rules()} >= set(ANNEX_D)

    def test_d1_compares_the_call_with_the_token(self, text):
        block = next(part for part in text.split("\n\n") if part.startswith("// D.1 "))
        assert 'principal.getTag("role") == "agent"' in block
        assert 'principal.hasTag("clientId")' in block
        assert 'context.input.clientId == principal.getTag("clientId")' in block

    def test_d4_uses_the_unless_clause_of_the_annex(self, text):
        block = next(part for part in text.split("\n\n") if part.startswith("// D.4 "))
        assert "\nunless {" in block
        assert 'principal.getTag("role") == "referent"' in block

    def test_the_mode_is_compared_with_the_value_of_the_kernel(self, text):
        """Annex D writes "autonome"; the token carries the value of the kernel."""
        block = next(part for part in text.split("\n\n") if part.startswith("// D.2 "))
        assert 'principal.getTag("mode") == "autonomous"' in block
        assert 'principal.getTag("mode") == "autonome"' not in block


class TestActionNames:
    """Chapter 15.1: the actions are ``Savoir___<outil>``."""

    def test_every_tool_of_the_gateway_has_an_action(self, text):
        for tool in TOOLS:
            assert f'AgentCore::Action::"Savoir___{tool}"' in text, tool

    def test_the_names_come_from_the_settings(self):
        settings = Settings(tool_target_name="Savoir")
        assert settings.action_name("recall") == "Savoir___recall"
        assert f'"{settings.action_name("recall")}"' in render_policies(settings)

    def test_another_target_name_renames_every_action(self):
        text = render_policies(Settings(tool_target_name="Memoire"))
        assert 'AgentCore::Action::"Memoire___recall"' in text
        assert "Savoir___" not in text

    def test_no_action_outside_the_gateway_is_rendered(self, text):
        actions = {match.group("action") for match in policies_of(text)}
        assert actions == {DEFAULT.action_name(tool) for tool in TOOLS}
        assert "Savoir___*" not in actions


class TestShape:
    """The text a policy validator would read."""

    def test_every_policy_has_the_shape_of_the_annex(self, text):
        blocks = [block for block in text.split("\n\n") if not block.startswith("// Sillage")]
        rendered = [block for block in blocks if "(principal," in block]
        assert rendered, "aucune politique rendue"
        assert len(policies_of(text)) == len(rendered)

    def test_every_policy_ends_with_a_semicolon(self, text):
        for match in policies_of(text):
            assert match.group(0).endswith("};")

    def test_the_effects_are_the_ones_of_the_table(self, text):
        effects = {match.group("action"): match.group("effect") for match in policies_of(text)}
        assert effects[DEFAULT.action_name("validate_fiche")] in {"permit", "forbid"}
        assert "forbid" in {match.group("effect") for match in policies_of(text)}
        assert "permit" in {match.group("effect") for match in policies_of(text)}

    def test_the_resource_is_the_gateway(self, text):
        assert {match.group("resource") for match in policies_of(text)} == {GATEWAY_PLACEHOLDER}

    def test_the_arn_is_built_from_the_settings(self):
        settings = Settings(account_id="111122223333", region="eu-central-1", gateway_name="sillage-gateway")
        arn = gateway_arn(settings)
        assert arn == "arn:aws:bedrock-agentcore:eu-central-1:111122223333:gateway/sillage-gateway"
        assert arn in render_policies(settings)
        assert gateway_arn() == GATEWAY_PLACEHOLDER

    def test_the_rendering_is_stable(self):
        assert render_policies() == render_policies()

    def test_a_rule_without_a_deployed_twin_is_not_rendered(self, text):
        for rule in RULES:
            if not rule.cedar:
                assert f"// {rule.id} —" not in text

    def test_every_deployed_rule_is_rendered_once_per_action(self, text):
        for rule in rendered_rules():
            expected = len(cedar_actions_of(rule))
            assert text.count(f"// {rule.id} —") == expected, rule.id

    def test_the_cross_client_rule_covers_every_client_tool(self, text):
        rule = next(candidate for candidate in RULES if candidate.id == "S.1")
        assert cedar_actions_of(rule) == CLIENT_TOOLS
        for tool in CLIENT_TOOLS:
            wanted = f'Savoir___{tool}"'
            block = [part for part in text.split("\n\n") if part.startswith("// S.1 ") and wanted in part]
            assert block, tool
            assert 'context.input.clientId != principal.getTag("clientId")' in block[0]

    def test_actions_of_prefixes_the_target_name(self):
        rule = next(candidate for candidate in RULES if candidate.id == "D.3")
        assert actions_of(rule) == ("Savoir___validate_fiche",)
        assert actions_of(rule, Settings(tool_target_name="X")) == ("X___validate_fiche",)


class TestNoClientInClear:
    """A policy compares the call with the token: it never names a client."""

    def test_no_demo_client_appears_in_the_text(self, text):
        for client in DEFAULT.demo_clients:
            assert client not in text
        assert "client-a" not in text
        assert "client-b" not in text

    def test_the_client_is_only_compared_with_the_token(self, text):
        comparisons = CLIENT_COMPARISON.findall(text)
        assert comparisons, "le cloisonnement par client doit apparaître dans les politiques"
        for right in comparisons:
            assert right.strip() == 'principal.getTag("clientId")'

    def test_no_literal_client_value_is_compared(self, text):
        assert not re.search(r'clientId\s*(?:==|!=)\s*"[a-z0-9-]+"', text)

    def test_no_account_and_no_secret_is_written(self, text):
        assert DEFAULT.account_id == ""
        assert not re.search(r"\b\d{12}\b", text)
        assert "AKIA" not in text


class TestPolicyDocument:
    """What ``infra/cdk`` feeds to AgentCore Policy."""

    def test_the_document_carries_the_text_and_the_rules(self):
        document = policy_document()
        assert document["cedar"] == render_policies()
        assert document["actions"] == [DEFAULT.action_name(tool) for tool in TOOLS]
        assert document["mode"] == DEFAULT.policy_mode
        assert document["gateway"] == GATEWAY_PLACEHOLDER
        assert {policy["id"] for policy in document["policies"]} == {rule.id for rule in rendered_rules()}

    def test_every_policy_of_the_document_is_explicit(self):
        for policy in policy_document()["policies"]:
            assert policy["effect"] in {"permit", "forbid"}
            assert policy["clause"] in {"when", "unless"}
            assert policy["why"]
            assert policy["cedar"]
            assert all(action.startswith("Savoir___") for action in policy["actions"])

    def test_the_document_follows_the_settings(self):
        document = policy_document(Settings(account_id="111122223333", policy_mode="LOG_ONLY"))
        assert document["mode"] == "LOG_ONLY"
        assert "111122223333" in document["gateway"]

    def test_the_forbidden_actions_are_listed(self):
        actions = forbidden_actions()
        assert DEFAULT.action_name("recall") in actions
        assert DEFAULT.action_name("validate_fiche") in actions
        assert DEFAULT.action_name("report_outcome") not in actions
