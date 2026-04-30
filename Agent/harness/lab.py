#!/usr/bin/env python3
"""
Agent harness (Trading + Development): mock socket + **real OpenAI** (default) or ``--mock``.

Loads ``Agent/.env`` so ``OPENAI_API_KEY`` works like ``python -m Agent.main``.

Usage::

    python -m Agent.harness.lab
    python -m Agent.harness.lab --auto --model gpt-4o-mini
    python -m Agent.harness.lab --mock
"""
from __future__ import annotations

import argparse
import os
import sys
from dataclasses import dataclass
from typing import Any, Callable, Dict, List, Optional

try:
    from dotenv import load_dotenv
except ImportError:
    load_dotenv = None  # type: ignore[misc, assignment]

from Agent.harness.mocks import HarnessOpenAI, StubOpenAIClient
from Agent.harness import scenarios as S

from Agent.shared.base_agent import BaseAgent
from Agent.shared.scratchpad import Scratchpad, StrategyPlan, TradePolicy
from Agent.utils.game_state_processor import GameStateProcessor
from Agent.Tools.registry import build_tool_registry

from Agent.trading_agent.agent import TradingAgent, extract_incoming_offer_for_me
from Agent.development_agent.agent import DevelopmentAgent


# ── Harness LLM (set by configure_harness before any check runs) ─────────────

_harness_llm: Any = None
_harness_mock: bool = True


def _agent_dir() -> str:
    return os.path.dirname(os.path.abspath(os.path.join(__file__, "..")))


def _load_env() -> None:
    if load_dotenv:
        load_dotenv(os.path.join(_agent_dir(), ".env"))


def configure_harness(*, use_mock: bool, model: str) -> None:
    """
    ``use_mock``: ``HarnessOpenAI`` (Trading + Development scripts; no network).
    Else: ``OpenAIClient`` — requires ``OPENAI_API_KEY``.
    """
    global _harness_llm, _harness_mock
    _harness_mock = use_mock
    if use_mock:
        _harness_llm = HarnessOpenAI()
        return
    from Agent.utils.openai_client import OpenAIClient
    _harness_llm = OpenAIClient(model=model)


def _llm() -> Any:
    assert _harness_llm is not None
    return _harness_llm


class StubStrategyPeer(BaseAgent):
    """Minimal Strategy peer for offline tests."""

    def __init__(self, scratchpad: Scratchpad, policy: Optional[TradePolicy] = None) -> None:
        super().__init__("strategy", scratchpad)
        self._policy = policy or TradePolicy(
            willing_to_give=["brick", "lumber"],
            desperately_need=["ore", "grain"],
            should_propose_trades=False,
            min_accept_score=0.45,
        )
        self.last_trade_inform: Any = None

    def get_trade_policy(self) -> TradePolicy:
        return self._policy

    def report_trade_results(self, results: Any = None, **kwargs: Any) -> None:
        self.last_trade_inform = results


class StubRiskPeer(BaseAgent):
    """Minimal Risk peer for Development robber tests."""

    def __init__(self, scratchpad: Scratchpad, targets: Optional[List[Dict[str, Any]]] = None) -> None:
        super().__init__("risk", scratchpad)
        self._targets = list(targets or [])

    def get_robber_targets(self) -> List[Dict[str, Any]]:
        return list(self._targets)


def _make_env(state: Dict[str, Any]) -> tuple[Scratchpad, Any, GameStateProcessor, Any]:
    from Agent.harness.mocks import MockSocketClient

    sp = Scratchpad()
    mock = MockSocketClient(dict(state))
    proc = GameStateProcessor()
    reg = build_tool_registry(mock, proc)
    sp.update_game_state(mock.latest_state() or {}, proc)
    return sp, mock, proc, reg


def _banner(title: str) -> None:
    print(f"\n{'=' * 60}\n  {title}\n{'=' * 60}")


@dataclass
class CheckResult:
    name: str
    passed: bool
    detail: str


def check_trading_incoming_detection() -> CheckResult:
    _banner("1) [Trading] Incoming offer — extraction only (no LLM; rich state)")
    st = S.state_incoming_trade_off_turn_rich()
    has = TradingAgent.has_incoming_offer_for_me(st)
    offer = extract_incoming_offer_for_me(st)
    ok = has and offer is not None and offer.get("from") == 1
    return CheckResult(
        "incoming_offer_detection",
        bool(ok),
        f"has={has}, from={offer.get('from') if offer else None}",
    )


def check_trading_proactive_live() -> CheckResult:
    mode = "mock [thought]/[action]" if _harness_mock else "OpenAI (real reasoning)"
    _banner(f"2) [Trading] proactive_trade — policy ON — {mode}")
    sp, mock, proc, reg = _make_env(S.state_main_my_turn_rich())
    sp.strategy_plan = StrategyPlan(
        priority_resources=["ore", "grain"],
        short_term_goals=["Secure ore for a city", "Avoid feeding Alice (7 VP)"],
        long_term_goal="cities",
        risk_tolerance="moderate",
    )
    strat = StubStrategyPeer(
        sp,
        TradePolicy(
            should_propose_trades=True,
            desperately_need=["ore", "grain"],
            willing_to_give=["brick", "lumber", "wool"],
            max_bank_ratio_acceptable=4,
            min_accept_score=0.5,
        ),
    )
    tr = TradingAgent(sp, _llm(), reg)
    tr.register_peer(strat)
    mock.calls.clear()
    n_log_before = len(sp.action_log)
    try:
        tr.proactive_trade()
    except Exception as e:
        return CheckResult("proactive_live", False, str(e))
    logged = [a.action for a in sp.action_log][n_log_before:]
    events = [c[0] for c in mock.calls]
    if _harness_mock:
        ok = (
            logged.count("get_trade_options") >= 1
            and logged.count("propose_trade") >= 1
            and "proposeTrade" in events
        )
        detail = f"action_log={logged}; socket={events}"
    else:
        # Real LLM: any trading tool activity counts as success
        touched = len(logged) > 0 or len(events) > 0
        ok = touched
        detail = f"action_log={logged}; socket={events} (real model — tools vary)"
    return CheckResult("proactive_live", ok, detail)


def check_trading_reactive_live() -> CheckResult:
    mode = "mock" if _harness_mock else "OpenAI (real reasoning)"
    _banner(f"3) [Trading] respond_to_offer — off-turn — {mode}")
    sp, mock, proc, reg = _make_env(S.state_incoming_trade_off_turn_rich())
    strat = StubStrategyPeer(
        sp,
        TradePolicy(
            should_propose_trades=False,
            min_accept_score=0.45,
            desperately_need=["grain", "ore"],
            willing_to_give=["brick", "lumber", "wool"],
        ),
    )
    tr = TradingAgent(sp, _llm(), reg)
    tr.register_peer(strat)
    mock.calls.clear()
    n_log_before = len(sp.action_log)
    try:
        tr.respond_to_offer()
    except Exception as e:
        return CheckResult("reactive_live", False, str(e))
    events = [c[0] for c in mock.calls]
    logged = [a.action for a in sp.action_log][n_log_before:]
    if _harness_mock:
        ok = "respondToTrade" in events or "counterTrade" in events
        detail = f"action_log={logged}; socket={events}"
    else:
        ok = len(events) > 0 or len(logged) > 0
        detail = f"action_log={logged}; socket={events} (real model — paths vary)"
    return CheckResult("reactive_live", ok, detail)


def check_trading_proactive_policy_off() -> CheckResult:
    _banner("4) [Trading] proactive_trade — policy OFF (Strategy disables trades; no LLM call)")
    sp, mock, proc, reg = _make_env(S.state_main_my_turn_rich())
    strat = StubStrategyPeer(sp, TradePolicy(should_propose_trades=False))
    tr = TradingAgent(sp, StubOpenAIClient(), reg)
    tr.register_peer(strat)
    try:
        tr.proactive_trade()
    except Exception as e:
        return CheckResult("proactive_policy_off", False, str(e))
    return CheckResult("proactive_policy_off", True, "exited early — no API use")


def check_trading_proactive_bank_focus() -> CheckResult:
    mode = "mock [thought]/[action]" if _harness_mock else "OpenAI (real reasoning)"
    _banner(f"5) [Trading] proactive_trade — bank-first / brick surplus — {mode}")
    sp, mock, proc, reg = _make_env(S.state_main_my_turn_bank_focus())
    sp.strategy_plan = StrategyPlan(
        priority_resources=["ore"],
        short_term_goals=[
            "[HARNESS:proactive_bank]",
            "Convert surplus brick through the bank for ore before negotiating with players.",
        ],
        long_term_goal="cities",
        risk_tolerance="moderate",
    )
    strat = StubStrategyPeer(
        sp,
        TradePolicy(
            should_propose_trades=True,
            desperately_need=["ore"],
            willing_to_give=["brick", "lumber", "wool"],
            max_bank_ratio_acceptable=4,
            min_accept_score=0.5,
        ),
    )
    tr = TradingAgent(sp, _llm(), reg)
    tr.register_peer(strat)
    mock.calls.clear()
    n_log_before = len(sp.action_log)
    try:
        tr.proactive_trade()
    except Exception as e:
        return CheckResult("proactive_bank_focus", False, str(e))
    logged = [a.action for a in sp.action_log][n_log_before:]
    events = [c[0] for c in mock.calls]
    if _harness_mock:
        ok = (
            logged.count("get_trade_options") >= 1
            and logged.count("bank_trade") >= 1
            and "bankTrade" in events
        )
        detail = f"action_log={logged}; socket={events}"
    else:
        touched = len(logged) > 0 or len(events) > 0
        ok = touched
        detail = f"action_log={logged}; socket={events} (real model — tools vary)"
    return CheckResult("proactive_bank_focus", ok, detail)


def check_trading_reactive_counter() -> CheckResult:
    mode = "mock [thought]/[action]" if _harness_mock else "OpenAI (real reasoning)"
    _banner(f"6) [Trading] respond_to_offer — greedy ask → counter-offer — {mode}")
    sp, mock, proc, reg = _make_env(S.state_incoming_trade_counter_rich())
    strat = StubStrategyPeer(
        sp,
        TradePolicy(
            should_propose_trades=False,
            min_accept_score=0.55,
            desperately_need=["lumber", "grain"],
            willing_to_give=["brick", "lumber", "wool", "ore"],
        ),
    )
    tr = TradingAgent(sp, _llm(), reg)
    tr.register_peer(strat)
    mock.calls.clear()
    n_log_before = len(sp.action_log)
    try:
        tr.respond_to_offer()
    except Exception as e:
        return CheckResult("reactive_counter", False, str(e))
    events = [c[0] for c in mock.calls]
    logged = [a.action for a in sp.action_log][n_log_before:]
    if _harness_mock:
        ok = "counterTrade" in events and logged.count("counter_trade") >= 1
        detail = f"action_log={logged}; socket={events}"
    else:
        ok = len(events) > 0 or len(logged) > 0
        detail = f"action_log={logged}; socket={events} (real model — paths vary)"
    return CheckResult("reactive_counter", ok, detail)


def check_development_discard_llm() -> CheckResult:
    mode = "mock [thought]/[action]" if _harness_mock else "OpenAI (real reasoning)"
    _banner(f"7) [Development] discard phase — {mode}")
    sp, mock, proc, reg = _make_env(S.state_discard_my_turn())
    sp.strategy_plan = StrategyPlan(
        priority_resources=["ore", "grain"],
        short_term_goals=["Keep ore/grain for city"],
        long_term_goal="cities",
        risk_tolerance="moderate",
    )
    dev = DevelopmentAgent(sp, _llm(), reg)
    mock.calls.clear()
    n_log_before = len(sp.action_log)
    try:
        dev.handle_discard()
    except Exception as e:
        return CheckResult("development_discard", False, str(e))
    logged = [a.action for a in sp.action_log][n_log_before:]
    events = [c[0] for c in mock.calls]
    if _harness_mock:
        ok = logged.count("discard_cards") >= 1 and "discardCards" in events
        detail = f"action_log={logged}; socket={events}"
    else:
        ok = len(logged) > 0 or len(events) > 0
        detail = f"action_log={logged}; socket={events} (real model — tools vary)"
    return CheckResult("development_discard", ok, detail)


def check_development_robber_llm() -> CheckResult:
    mode = "mock [thought]/[action]" if _harness_mock else "OpenAI (real reasoning)"
    _banner(f"8) [Development] robber phase — {mode}")
    sp, mock, proc, reg = _make_env(S.state_robber_my_turn())
    sp.strategy_plan = StrategyPlan(long_term_goal="balanced", risk_tolerance="moderate")
    dev = DevelopmentAgent(sp, _llm(), reg)
    dev.register_peer(StubRiskPeer(sp, [{"hex": "2,-1", "score": 2.0}]))
    mock.calls.clear()
    n_log_before = len(sp.action_log)
    try:
        dev.handle_robber()
    except Exception as e:
        return CheckResult("development_robber", False, str(e))
    logged = [a.action for a in sp.action_log][n_log_before:]
    events = [c[0] for c in mock.calls]
    if _harness_mock:
        ok = logged.count("move_robber") >= 1 and "moveRobber" in events
        detail = f"action_log={logged}; socket={events}"
    else:
        ok = len(logged) > 0 or len(events) > 0
        detail = f"action_log={logged}; socket={events} (real model — tools vary)"
    return CheckResult("development_robber", ok, detail)


def check_development_build_queue() -> CheckResult:
    _banner("9) [Development] build queue — registry tools (no LLM)")
    sp, mock, proc, reg = _make_env(S.state_main_dev_build_queue())
    sp.strategy_plan = StrategyPlan(
        build_queue=[
            {"priority": 1, "action": "place_settlement", "target": "v_0_-1_2"},
        ],
        long_term_goal="expand",
        risk_tolerance="moderate",
    )
    dev = DevelopmentAgent(sp, StubOpenAIClient(), reg)
    mock.calls.clear()
    n_before = len(sp.action_log)
    try:
        dev.execute_build_queue()
    except Exception as e:
        return CheckResult("development_build_queue", False, str(e))
    logged = [a.action for a in sp.action_log][n_before:]
    events = [c[0] for c in mock.calls]
    ok = "place_settlement" in logged and "placeSettlement" in events
    return CheckResult("development_build_queue", ok, f"action_log={logged}; socket={events}")


ALL_CHECKS: List[Callable[[], CheckResult]] = [
    check_trading_incoming_detection,
    check_trading_proactive_live,
    check_trading_reactive_live,
    check_trading_proactive_policy_off,
    check_trading_proactive_bank_focus,
    check_trading_reactive_counter,
    check_development_discard_llm,
    check_development_robber_llm,
    check_development_build_queue,
]

CHECKS_BY_ID: Dict[str, Callable[[], CheckResult]] = {
    str(i + 1): ALL_CHECKS[i] for i in range(len(ALL_CHECKS))
}

SCENARIO_ALIASES: Dict[str, str] = {
    "t1": "1",
    "t2": "2",
    "t3": "3",
    "t4": "4",
    "t5": "5",
    "t6": "6",
    "d1": "7",
    "d2": "8",
    "d3": "9",
}


def run_all_checks() -> List[CheckResult]:
    mode = "MOCK (--mock)" if _harness_mock else "LIVE OpenAI"
    print(
        f"\n### Agent harness ({mode}) — Trading + Development "
        f"([thought]/[action] where LLM runs).\n"
    )
    return [fn() for fn in ALL_CHECKS]


def print_results(results: List[CheckResult]) -> None:
    passed = sum(1 for r in results if r.passed)
    print("\n--- Results ---")
    for r in results:
        status = "PASS" if r.passed else "FAIL"
        print(f"  [{status}] {r.name}: {r.detail}")
    print(f"\nTotal: {passed}/{len(results)} passed\n")


def _resolve_scenario_key(raw: str) -> Optional[str]:
    s = raw.strip().lower()
    if s in SCENARIO_ALIASES:
        return SCENARIO_ALIASES[s]
    if s in CHECKS_BY_ID:
        return s
    return None


def _print_scenario_menu() -> None:
    lines = [
        "",
        "Trading — TradingAgent",
        "  1  (t1)  Incoming offer detection — parser only (no LLM)",
        "  2  (t2)  Proactive trade — policy ON (LLM unless --mock)",
        "  3  (t3)  respond_to_offer — off-turn (LLM unless --mock)",
        "  4  (t4)  Proactive trade — policy OFF (no LLM; early exit)",
        "  5  (t5)  Proactive trade — bank-first / brick surplus",
        "  6  (t6)  respond_to_offer — greedy ask → counter-offer",
        "",
        "Development — DevelopmentAgent",
        "  7  (d1)  Discard phase — LLM + discard_cards",
        "  8  (d2)  Robber phase — LLM + move_robber",
        "  9  (d3)  Build queue — registry tools (no LLM)",
        "",
        "Commands: 1–9 | t1–t6 | d1–d3 | all | help | quit",
        "",
    ]
    print("\n".join(lines))


def chatbot_loop(args: argparse.Namespace) -> None:
    print(
        "\n=== Agent harness (Trading + Development) ===\n"
        f"Mode: {'MOCK (HarnessOpenAI)' if _harness_mock else f'LIVE OpenAI model={args.model}'}\n"
    )
    _print_scenario_menu()
    while True:
        try:
            line = input("lab> ").strip().lower()
        except (EOFError, KeyboardInterrupt):
            print()
            break
        if line in ("q", "quit", "exit"):
            break
        if line in ("h", "help", "?"):
            _print_scenario_menu()
            print("  [all]  Run scenarios 1–9")
            continue
        if line == "all":
            print_results(run_all_checks())
            continue
        sid = _resolve_scenario_key(line)
        if sid and sid in CHECKS_BY_ID:
            print_results([CHECKS_BY_ID[sid]()])
            continue
        if line:
            print("Unknown command. Type 'help'.")


def main() -> None:
    default_model = os.getenv("HARNESS_MODEL", "gpt-4o-mini")
    parser = argparse.ArgumentParser(description="Agent harness: Trading + Development (real OpenAI or --mock)")
    parser.add_argument(
        "--auto",
        action="store_true",
        help="Run all checks non-interactively and exit 1 if any fail",
    )
    parser.add_argument(
        "--mock",
        action="store_true",
        help="Use simulated LLM (no OPENAI_API_KEY; deterministic tool sequence)",
    )
    parser.add_argument(
        "--model",
        default=default_model,
        help=f"OpenAI model for live runs (default: {default_model} or HARNESS_MODEL env)",
    )
    args = parser.parse_args()

    _load_env()

    if not args.mock:
        try:
            configure_harness(use_mock=False, model=args.model)
        except ValueError as e:
            print(f"\n{e}", file=sys.stderr)
            print(
                "\n  Set OPENAI_API_KEY in Agent/.env (or export it), or run with --mock.\n",
                file=sys.stderr,
            )
            sys.exit(1)
    else:
        configure_harness(use_mock=True, model=args.model)

    if args.auto:
        results = run_all_checks()
        print_results(results)
        if not all(r.passed for r in results):
            sys.exit(1)
        sys.exit(0)
    chatbot_loop(args)


if __name__ == "__main__":
    main()
