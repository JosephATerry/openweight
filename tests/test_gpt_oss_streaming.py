from openweight_platform.backends.gpt_oss import _FinalAnswerStreamFilter


def test_stream_filter_releases_only_final_answer_across_split_markers() -> None:
    stream_filter = _FinalAnswerStreamFilter()
    released = []

    for piece in (
        "<|channel|>analysis<|message|>private chain of thought",
        "<|chan",
        "nel|>final<|message|>Privileged access ",
        "requires approval [EPG-ACCESS-001#1].<|ret",
        "urn|>ignored",
    ):
        text = stream_filter.feed(piece)
        if text:
            released.append(text)
    tail = stream_filter.finish()
    if tail:
        released.append(tail)

    answer = "".join(released)
    assert answer == (
        "Privileged access requires approval [EPG-ACCESS-001#1]."
    )
    assert "private chain of thought" not in answer
    assert "<|channel|>" not in answer
    assert "<|return|>" not in answer


def test_stream_filter_fails_closed_when_no_final_channel_exists() -> None:
    stream_filter = _FinalAnswerStreamFilter()

    assert stream_filter.feed("analysis-only private content") == ""
    assert stream_filter.finish() == ""


def test_stream_filter_drops_truncated_control_marker() -> None:
    stream_filter = _FinalAnswerStreamFilter()

    assert stream_filter.feed(
        "<|channel|>final<|message|>Employee answer.<|ret"
    ) == "Employee answer."
    assert stream_filter.finish() == ""
