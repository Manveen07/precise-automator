from types import SimpleNamespace

from app.services import spintax_service
from app.services.spintax_service import (
    SPINTAX_SYSTEM_PROMPT,
    apply_spintax_to_plan,
    body_has_spintax,
    count_bodies_needing_spintax,
)


def test_body_has_spintax_detects_pipe_blocks():
    assert body_has_spintax("{Hi|Hey} {{first_name}}, ...") is True
    assert body_has_spintax("{a|b|c} and {x|y}") is True


def test_body_has_spintax_does_not_match_merge_tags_or_signature():
    assert body_has_spintax("Hi {{first_name}}, here is %Signature%") is False
    assert body_has_spintax("plain body, no spintax") is False
    # Single brace without a pipe is not spintax
    assert body_has_spintax("{not spintax}") is False


def test_spintax_prompt_forbids_merge_tags_inside_spintax_blocks():
    assert "Do NOT place template variables inside a spintax block" in SPINTAX_SYSTEM_PROMPT
    assert "BAD: {a few ideas for {{company_name}}|a quick example}" in SPINTAX_SYSTEM_PROMPT


def test_spintax_prompt_requires_meaning_preservation():
    assert "Do NOT change the selling message, offer, audience, intent, CTA" in SPINTAX_SYSTEM_PROMPT
    assert "Every option in a spintax block must be a true synonym in context" in SPINTAX_SYSTEM_PROMPT


def test_count_bodies_needing_spintax_counts_per_variant():
    plan = {
        "sequence": [
            {"variants": [{"body": "{Hi|Hey} there"}, {"body": "plain"}]},
            {"variants": [{"body": "plain follow-up"}]},
        ]
    }
    need, total = count_bodies_needing_spintax(plan)
    assert (need, total) == (2, 3)


def test_strip_combination_footer_removes_trailing_unique_combinations_line():
    raw = "Spun body {Hi|Hey} there.\n\nUnique combinations: 2 x 3 = 6"
    assert spintax_service._strip_combination_footer(raw) == "Spun body {Hi|Hey} there."


def test_strip_combination_footer_handles_no_footer():
    raw = "Spun body {Hi|Hey} there."
    assert spintax_service._strip_combination_footer(raw) == "Spun body {Hi|Hey} there."


class FakeAnthropicClient:
    """Minimal stand-in: each call records the prompt body and returns a deterministic spun result."""
    def __init__(self):
        self.calls = []
        self.messages = SimpleNamespace(create=self._create)

    def _create(self, *, model, max_tokens, system, messages):
        body = messages[0]["content"]
        self.calls.append(body)
        spun = "{Hi|Hey} " + body
        text = f"{spun}\n\nUnique combinations: 2 = 2"
        return SimpleNamespace(content=[SimpleNamespace(text=text)])


def test_apply_spintax_skips_bodies_that_already_have_spintax():
    client = FakeAnthropicClient()
    plan = {"sequence": [{"variants": [{"body": "{Hi|Hey} already spun"}]}]}
    new_plan, stats = apply_spintax_to_plan(plan, client)

    assert client.calls == []
    assert stats == {"generated": 0, "skipped_already_spun": 1, "unique_calls": 0}
    assert new_plan["sequence"][0]["variants"][0]["body"] == "{Hi|Hey} already spun"


def test_apply_spintax_dedupes_identical_bodies_across_variants():
    """Step-1 cross-product produces duplicate bodies. We must only call Claude once per
    unique body — otherwise a 2-subject × 3-body draft costs 6 API calls instead of 3."""
    client = FakeAnthropicClient()
    plan = {
        "sequence": [
            {
                "variants": [
                    {"subject": "S1", "body": "Body A"},
                    {"subject": "S2", "body": "Body A"},
                    {"subject": "S1", "body": "Body B"},
                ]
            }
        ]
    }
    new_plan, stats = apply_spintax_to_plan(plan, client)

    assert client.calls == ["Body A", "Body B"]
    assert stats["unique_calls"] == 2
    assert stats["generated"] == 3
    assert new_plan["sequence"][0]["variants"][0]["body"] == "{Hi|Hey} Body A"
    assert new_plan["sequence"][0]["variants"][1]["body"] == "{Hi|Hey} Body A"
    assert new_plan["sequence"][0]["variants"][2]["body"] == "{Hi|Hey} Body B"


def test_apply_spintax_strips_unique_combinations_footer_from_returned_body():
    client = FakeAnthropicClient()
    plan = {"sequence": [{"variants": [{"body": "Plain body"}]}]}
    new_plan, _ = apply_spintax_to_plan(plan, client)

    spun = new_plan["sequence"][0]["variants"][0]["body"]
    assert "Unique combinations" not in spun
    assert spun == "{Hi|Hey} Plain body"


def test_generate_spintax_normalizes_signature_token_to_lowercase():
    client = FakeAnthropicClient()
    plan = {"sequence": [{"variants": [{"body": "Plain body\n%SIGNATURE%"}]}]}
    new_plan, _ = apply_spintax_to_plan(plan, client)

    assert "%signature%" in new_plan["sequence"][0]["variants"][0]["body"]
    assert "%SIGNATURE%" not in new_plan["sequence"][0]["variants"][0]["body"]
    assert "%Signature%" not in new_plan["sequence"][0]["variants"][0]["body"]


def test_apply_spintax_does_not_mutate_input_plan():
    client = FakeAnthropicClient()
    original = {"sequence": [{"variants": [{"body": "Plain body"}]}]}
    apply_spintax_to_plan(original, client)
    assert original["sequence"][0]["variants"][0]["body"] == "Plain body"


def test_apply_spintax_skips_linkedin_channel():
    client = FakeAnthropicClient()
    plan = {
        "sequence": [
            {"channel": "email", "variants": [{"body": "Email body"}]},
            {"channel": "linkedin", "variants": [{"body": "LinkedIn body"}]},
        ]
    }
    new_plan, stats = apply_spintax_to_plan(plan, client)

    # Only the email body should be processed (1 call)
    assert client.calls == ["Email body"]
    assert stats["generated"] == 1
    assert new_plan["sequence"][0]["variants"][0]["body"] == "{Hi|Hey} Email body"
    # LinkedIn body must remain completely unchanged
    assert new_plan["sequence"][1]["variants"][0]["body"] == "LinkedIn body"


def test_count_bodies_skips_linkedin_channel():
    plan = {
        "sequence": [
            {"channel": "email", "variants": [{"body": "plain email"}]},
            {"channel": "linkedin", "variants": [{"body": "plain linkedin"}]},
        ]
    }
    need, total = count_bodies_needing_spintax(plan)
    # Only email step variants should be counted
    assert (need, total) == (1, 1)

