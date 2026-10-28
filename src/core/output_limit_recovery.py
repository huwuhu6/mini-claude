"""Bound extra model requests independently of the agent's tool/turn budget."""

from core.run_state import RunState
from models.config import OutputLimitRecoveryConfig


OUTPUT_LIMIT_REASONS = frozenset({'length', 'max_tokens', 'limit'})


def reserve_retry(state: RunState, policy: OutputLimitRecoveryConfig,
                  parameters: dict, prompt_tokens: int) -> bool:
    # Reserve input plus the full output allowance before issuing the request.
    # Missing usage or a process crash must never replenish this allowance.
    reservation = prompt_tokens + parameters['max_tokens']
    if (not policy.enabled
            or state.output_limit_attempt >= policy.max_retries_per_turn
            or state.output_limit_retries >= policy.max_retries_per_run
            or state.output_limit_reserved_tokens + reservation > policy.max_retry_tokens_per_run):
        return False
    state.output_limit_retries += 1
    state.output_limit_reserved_tokens += reservation
    state.output_limit_attempt += 1
    state.output_limit_last_request = dict(parameters)
    return True


def charge_retry_usage(state: RunState, parameters: dict, prompt_tokens: int, usage: dict) -> None:
    """Never refund unknown usage; charge any reported excess over the estimate."""
    def count(name):
        value = usage.get(name, 0)
        return value if type(value) is int and value >= 0 else 0

    actual = max(count('total_tokens'), count('prompt_tokens') +
                 max(count('completion_tokens'), count('reasoning_tokens')))
    reservation = prompt_tokens + parameters['max_tokens']
    state.output_limit_reserved_tokens += max(0, actual - reservation)
