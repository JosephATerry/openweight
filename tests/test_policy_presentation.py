import pytest

from openweight_platform.api.app import _policy_response
from openweight_platform.api.policy_presentation import (
    PolicyAnswerPresentation,
    present_policy_answer,
)
from openweight_platform.api.runtime import GroundedPolicyAnswer


@pytest.mark.parametrize("chunk_size", [1, 2, 7, 80])
def test_source_wrappers_never_escape_stream_across_chunk_boundaries(chunk_size):
    raw = (
        "Use **named users** [EPG-ACCESS-001#1].\n\n"
        "[SOURCE [EPG-ACCESS-001#1]]\n[SOURCE [EPG-INCIDENT-008#1]]"
    )
    presenter = PolicyAnswerPresentation()
    chunks = [presenter.feed(raw[i:i + chunk_size]) for i in range(0, len(raw), chunk_size)]
    chunks.append(presenter.finish())
    visible = "".join(chunks)
    assert "SOURCE" not in visible
    assert visible.strip() == "Use **named users** [EPG-ACCESS-001#1]."
    assert present_policy_answer(raw) == visible.strip()


def test_incomplete_source_footer_is_suppressed():
    assert present_policy_answer("Use approval. [SOURCE [EPG-ACCESS") == "Use approval."
    assert present_policy_answer("A [normal bracket] remains.") == "A [normal bracket] remains."


def test_typed_response_cleans_answer_without_changing_validated_citations():
    raw = "Use **approval**.\n\n[SOURCE [EPG-ACCESS-001#1]]"
    result = GroundedPolicyAnswer(
        status="answered", answer=raw, citations=["[EPG-ACCESS-001#1]"],
        citation_valid=True, evidence=[],
    )
    response = _policy_response(result, request_id="preserved-correlation")
    assert response.answer == "Use **approval**."
    assert result.answer == raw
    assert response.citations == result.citations
    assert response.citation_valid
    assert response.request_id == "preserved-correlation"
