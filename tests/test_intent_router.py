"""Deterministic request-router tests; no LLM or search backend is touched."""

from __future__ import annotations

import pytest

from search_agent.intent_router import Intent, IntentRouter, normalize_query


@pytest.fixture()
def router() -> IntentRouter:
    return IntentRouter()


@pytest.mark.parametrize(
    "query",
    ["你好！", "您好", "Hello!", "hi there", "在吗？", "你好呀"],
)
def test_greetings(router: IntentRouter, query: str) -> None:
    assert router.route(query).intent is Intent.GREETING


@pytest.mark.parametrize(
    "query",
    ["你能做什么？", "怎么使用？", "怎么搜索候选人？", "How can you help?", "help"],
)
def test_help_questions(router: IntentRouter, query: str) -> None:
    assert router.route(query).intent is Intent.HELP


@pytest.mark.parametrize(
    "query",
    [
        "找有 3 年经验的后端工程师",
        "Python 工程师",
        "你好，帮我找 Python 工程师",
        "招一位做手机系统开发的工程师",
        "想找几位做客服系统的候选人",
        "招一个会写代码的工程师",
        "帮我搜索候选人",
        "帮我推荐几位候选人",
        "推荐几个人",
        "推荐游戏开发工程师",
        "推荐音乐制作人",
        "推荐电影剪辑师",
    ],
)
def test_recruitment_queries_are_search(router: IntentRouter, query: str) -> None:
    assert router.route(query).intent is Intent.SEARCH


@pytest.mark.parametrize(
    "query",
    [
        "帮我写一首诗",
        "Python 是什么？",
        "今天天气怎么样？",
        "帮我翻译这段简历",
        "帮我写一个查候选人的 SQL",
        "怎么学习 Python",
        "帮我推荐一首歌",
        "推荐一本算法书",
        "推荐游戏",
    ],
)
def test_explicit_non_search_tasks_are_out_of_scope(router: IntentRouter, query: str) -> None:
    assert router.route(query).intent is Intent.OUT_OF_SCOPE


def test_recommending_a_book_is_out_of_scope(router: IntentRouter) -> None:
    assert router.route("推荐一本书").intent is Intent.OUT_OF_SCOPE


def test_recommending_a_recruiting_role_is_search(router: IntentRouter) -> None:
    assert router.route("推荐算法工程师").intent is Intent.SEARCH


@pytest.mark.parametrize("query", ["帮我找一下手机", "帮我找个人", "那北京的呢"])
def test_ambiguous_inputs_default_to_search(router: IntentRouter, query: str) -> None:
    assert router.route(query).intent is Intent.SEARCH


def test_greeting_with_search_action_is_not_a_greeting(router: IntentRouter) -> None:
    assert router.route("你好，推荐几位算法方向的人").intent is Intent.SEARCH


@pytest.mark.parametrize(
    ("query", "expected"),
    [
        ("推荐一本书", Intent.OUT_OF_SCOPE),
        ("推荐一首歌", Intent.OUT_OF_SCOPE),
        ("推荐候选人", Intent.SEARCH),
        ("推荐算法工程师", Intent.SEARCH),
        ("招聘音乐制作人", Intent.SEARCH),
        ("找电影剪辑师", Intent.SEARCH),
        ("你好，帮我找 Python 工程师", Intent.SEARCH),
    ],
)
def test_intent_boundaries(router: IntentRouter, query: str, expected: Intent) -> None:
    assert router.route(query).intent is expected


def test_reply_payload_and_suggestions(router: IntentRouter) -> None:
    decision = router.route("你好")
    payload = decision.to_payload()

    assert payload["type"] == "assistant_reply"
    assert payload["intent"] == "GREETING"
    assert payload["searchStarted"] is False
    assert len(payload["suggestions"]) == 3
    assert all(item["label"] and item["query"] for item in payload["suggestions"])


def test_normalize_query_handles_width_case_and_punctuation() -> None:
    assert normalize_query("  ＨＥＬＬＯ！ ") == "hello"
    assert normalize_query("你好？？") == "你好"
