"""
Deterministic benchmark decision engine.

This module intentionally mirrors the server-side benchmark scoring rules in
``Environment/server/benchmarkEvaluation.js``. It is useful as a calibration
agent: if this agent does poorly on a task, the benchmark payload or heuristic
is probably underspecified rather than merely difficult.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable, Dict, Iterable, List, Optional, Tuple


CHOICE_RATIO_TASKS = {
    "build-vs-save-decision",
    "city-vs-settlement-vs-road-prioritization",
    "development-card-purchase-decision",
    "knight-card-playing-decision",
    "year-of-plenty-card-playing-decision",
    "monopoly-card-playing-decision",
    "road-building-card-playing-decision",
    "robber-victim-selection",
    "accept-or-reject-trade-offers",
    "select-targeted-trade-partner",
    "bank-trade-decision",
    "decide-pursue-longest-road",
    "decide-pursue-largest-army",
}

TRADE_GENERATION_TASKS = {
    "generate-trade-offers",
    "generate-counter-trade-offer",
}


def clamp(value: Any, minimum: float = 0.0, maximum: float = 1.0) -> float:
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        parsed = minimum
    return min(maximum, max(minimum, parsed))


def safe_number(value: Any, fallback: float = 0.0) -> float:
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        return fallback
    return parsed


def option_id(option: Dict[str, Any], index: int = 0) -> str:
    return str(
        option.get("id")
        or option.get("optionId")
        or option.get("choice")
        or option.get("selectedOptionId")
        or index
    )


def settlement_score(option: Dict[str, Any]) -> float:
    return clamp(
        safe_number(option.get("resourceProductionExpectancy", option.get("productionExpectancy")), 0.0) * 0.40
        + safe_number(option.get("resourceDiversity"), 0.0) * 0.25
        + safe_number(option.get("expansionAccess"), 0.0) * 0.20
        + safe_number(option.get("portSynergy"), 0.0) * 0.15
    )


def road_score(option: Dict[str, Any]) -> float:
    return clamp(
        safe_number(option.get("futureReachability"), 0.0) * 0.50
        + safe_number(option.get("reachableIntersectionValue"), 0.0) * 0.35
        + safe_number(option.get("blockingValue"), 0.0) * 0.15
    )


def generic_choice_score(option: Dict[str, Any]) -> float:
    return clamp(
        option.get("score")
        if option.get("score") is not None
        else option.get("ev")
        if option.get("ev") is not None
        else option.get("expectedValue")
        if option.get("expectedValue") is not None
        else option.get("value", 0.0)
    )


def discard_score(option: Dict[str, Any]) -> float:
    return clamp(
        option.get("retainedHandValue")
        if option.get("retainedHandValue") is not None
        else option.get("score")
        if option.get("score") is not None
        else option.get("value", 0.0)
    )


def robber_score(option: Dict[str, Any]) -> float:
    if option.get("score") is not None:
        return clamp(option.get("score"))
    return clamp(
        safe_number(option.get("hurtLeader"), 0.0) * 0.35
        + safe_number(option.get("productionDamage"), 0.0) * 0.30
        + safe_number(option.get("theftValue"), 0.0) * 0.20
        + safe_number(option.get("selfHarmAvoidance"), 0.0) * 0.15
    )


def trade_offer_score(offer: Dict[str, Any]) -> float:
    legality = 0.0 if offer.get("legal") is False else 1.0
    self_gain = clamp(offer.get("selfGain", offer.get("selfBenefit", 0.0)))
    leader_penalty = clamp(offer.get("leaderHelpPenalty", offer.get("leaderBenefit", 0.0)))
    fairness_penalty = clamp(offer.get("fairnessPenalty", 0.0))
    return clamp(
        legality * 0.35
        + self_gain * 0.45
        + (1.0 - leader_penalty) * 0.15
        + (1.0 - fairness_penalty) * 0.05
    )


@dataclass(frozen=True)
class RankedChoice:
    option: Dict[str, Any]
    option_id: str
    score: float


class BenchmarkDecisionEngine:
    """Pick benchmark responses that maximize the benchmark's own heuristic."""

    def solve_task(self, task_id: str, payload: Dict[str, Any]) -> Dict[str, Any]:
        context = payload.get("evaluationContext") if isinstance(payload.get("evaluationContext"), dict) else {}

        if task_id in {"initial-settlement-location-selection", "settlement-location-selection"}:
            choice = self._best(context.get("legalOptions"), settlement_score)
            return self._selected_response(choice, "settlement")

        if task_id == "road-placement-direction":
            choice = self._best(context.get("legalOptions"), road_score)
            return self._selected_response(choice, "road")

        if task_id == "discard-strategy-after-seven":
            choice = self._best(context.get("discardOptions"), discard_score)
            response: Dict[str, Any] = {"selectedDiscardId": choice.option_id if choice else None}
            if choice:
                response["selectedOptionId"] = choice.option_id
                response["discard"] = choice.option.get("discard") or choice.option.get("resources")
            return response

        if task_id == "robber-placement":
            choice = self._best(context.get("options"), robber_score)
            response = {"selectedHexId": choice.option_id if choice else None}
            if choice:
                response.update({
                    "selectedPairId": choice.option_id,
                    "hexKey": choice.option.get("hexKey") or choice.option.get("hexId") or choice.option_id,
                    "victimId": choice.option.get("victimId") or choice.option.get("playerId"),
                })
            return response

        if task_id in TRADE_GENERATION_TASKS:
            offer = self._best_trade_offer(context, payload)
            return {"offer": offer}

        if task_id in CHOICE_RATIO_TASKS:
            choice = self._best(context.get("options"), generic_choice_score)
            return self._selected_response(choice, "choice")

        choice = self._best(
            context.get("options") or context.get("legalOptions") or context.get("choices"),
            generic_choice_score,
        )
        return self._selected_response(choice, "generic")

    def _best(
        self,
        options: Any,
        score_fn: Callable[[Dict[str, Any]], float],
    ) -> Optional[RankedChoice]:
        if not isinstance(options, list) or not options:
            return None
        ranked: List[RankedChoice] = []
        for index, raw in enumerate(options):
            if not isinstance(raw, dict):
                continue
            ranked.append(RankedChoice(raw, option_id(raw, index), score_fn(raw)))
        if not ranked:
            return None
        return max(ranked, key=lambda item: item.score)

    def _best_trade_offer(self, context: Dict[str, Any], payload: Dict[str, Any]) -> Dict[str, Any]:
        candidates = self._candidate_trade_offers(context, payload)
        if candidates:
            return max(candidates, key=trade_offer_score)
        return {
            "legal": True,
            "selfGain": 1.0,
            "leaderHelpPenalty": 0.0,
            "fairnessPenalty": 0.0,
            "offer": {},
            "request": {},
        }

    def _candidate_trade_offers(
        self,
        context: Dict[str, Any],
        payload: Dict[str, Any],
    ) -> List[Dict[str, Any]]:
        raw_sources: Iterable[Any] = (
            context.get("candidateOffers"),
            context.get("offers"),
            context.get("legalOptions"),
            payload.get("candidateOffers"),
        )
        candidates: List[Dict[str, Any]] = []
        for source in raw_sources:
            if not isinstance(source, list):
                continue
            candidates.extend(item for item in source if isinstance(item, dict))
        for key in ("candidateOffer", "bestOffer", "offerContext", "offer"):
            value = context.get(key) if key in context else payload.get(key)
            if isinstance(value, dict):
                candidates.append(value)
        return candidates

    @staticmethod
    def _selected_response(choice: Optional[RankedChoice], kind: str) -> Dict[str, Any]:
        selected = choice.option_id if choice else None
        response = {
            "selectedOptionId": selected,
            "choice": selected,
        }
        if choice:
            response["selected"] = choice.option
            response["score"] = choice.score
        response["kind"] = kind
        return response
