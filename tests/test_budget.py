from search_agent.core.budget import BudgetState, should_finalize
from search_agent.models.artifacts import Budget


def _bs(**kw):
    return BudgetState(budget=Budget(**kw))


def test_target_reached():
    bs = _bs(target_qualified=10)
    fin, reason = should_finalize(qualified_count=10, has_pending_paths=True, bs=bs)
    assert fin and reason == "target_reached"


def test_no_progress_when_no_paths_and_no_new_qualified():
    bs = _bs(target_qualified=100, max_iterations=10, max_llm_calls=100, time_budget_sec=60)
    bs.new_qualified_last_round = 0
    fin, reason = should_finalize(qualified_count=5, has_pending_paths=False, bs=bs)
    assert fin and reason == "no_progress"


def test_continue_when_progress():
    bs = _bs(target_qualified=100, max_iterations=10, max_llm_calls=100, time_budget_sec=60)
    bs.new_qualified_last_round = 3
    fin, reason = should_finalize(qualified_count=5, has_pending_paths=True, bs=bs)
    assert not fin and reason is None


def test_iteration_exceeded():
    bs = _bs(max_iterations=2)
    bs.iteration = 3
    fin, reason = should_finalize(qualified_count=0, has_pending_paths=True, bs=bs)
    assert fin and reason == "max_iterations"


def test_iteration_exceeded_at_boundary():
    # iteration == max_iterations must stop (off-by-one fix: strict > -> >=)
    bs = _bs(max_iterations=2)
    bs.iteration = 2
    fin, reason = should_finalize(qualified_count=0, has_pending_paths=True, bs=bs)
    assert fin and reason == "max_iterations"


def test_zero_max_iterations_stops_immediately():
    # single_pass route sets max_iterations=0 -> stop at iteration 0
    bs = _bs(max_iterations=0)
    bs.iteration = 0
    fin, reason = should_finalize(qualified_count=5, has_pending_paths=True, bs=bs)
    assert fin and reason == "max_iterations"


def test_record_llm_and_exceeded():
    bs = _bs(max_llm_calls=2)
    bs.record_llm()
    assert not bs.llm_exceeded()
    bs.record_llm()
    assert bs.llm_exceeded()
