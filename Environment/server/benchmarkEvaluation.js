import { BENCHMARK_WEIGHTS, METRIC_DIRECTIONS } from './benchmarkDefinitions.js';

export const PRIMARY_BENCHMARK_METRICS = [
  'winRate',
  'averageFinalVictoryPoints',
  'averageRoundsToWin',
  'taskSuccessRate',
  'averageLatencyPerTurn',
];

const PRIMARY_WEIGHT_SUM = PRIMARY_BENCHMARK_METRICS.reduce((sum, metric) => sum + (BENCHMARK_WEIGHTS[metric] || 0), 0);

const TASK_THRESHOLDS = {
  'initial-settlement-location-selection': 0.75,
  'settlement-location-selection': 0.9,
  'road-placement-direction': 0.8,
  'build-vs-save-decision': 0.9,
  'city-vs-settlement-vs-road-prioritization': 0.9,
  'development-card-purchase-decision': 0.95,
  'knight-card-playing-decision': 0.9,
  'year-of-plenty-card-playing-decision': 0.9,
  'monopoly-card-playing-decision': 0.9,
  'road-building-card-playing-decision': 0.85,
  'discard-strategy-after-seven': 0.95,
  'robber-victim-selection': 0.9,
  'robber-placement': 0.9,
  'accept-or-reject-trade-offers': 0.95,
  'generate-trade-offers': 0.7,
  'select-targeted-trade-partner': 0.9,
  'bank-trade-decision': 0.85,
  'generate-counter-trade-offer': 0.7,
  'decide-pursue-longest-road': 0.95,
  'decide-pursue-largest-army': 0.95,
};

function clamp(value, min = 0, max = 1) {
  const parsed = Number(value);
  if (!Number.isFinite(parsed)) return min;
  return Math.min(max, Math.max(min, parsed));
}

function safeNumber(value, fallback = 0) {
  return Number.isFinite(Number(value)) ? Number(value) : fallback;
}

function safeDivide(numerator, denominator) {
  if (!denominator) return null;
  return numerator / denominator;
}

function average(values) {
  if (!values.length) return null;
  return values.reduce((sum, value) => sum + value, 0) / values.length;
}

function sortScenarioTags(tags = []) {
  return [...new Set((tags || []).filter(Boolean))].sort();
}

function normalizeBooleanScore(value, truthyScore = 1, falsyScore = 0) {
  return value ? truthyScore : falsyScore;
}

function scoreFromOptionValue(selectedId, options = [], valueSelector = option => option.score ?? 0) {
  const normalizedOptions = options.map(option => ({
    ...option,
    computedScore: clamp(valueSelector(option)),
  }));
  if (!normalizedOptions.length) {
    return {
      score: null,
      selected: null,
      best: null,
    };
  }

  const best = normalizedOptions.reduce((currentBest, option) => (
    !currentBest || option.computedScore > currentBest.computedScore ? option : currentBest
  ), null);
  const selected = normalizedOptions.find(option => option.id === selectedId) || null;

  if (!selected || !best) {
    return {
      score: 0,
      selected,
      best,
    };
  }

  const score = best.computedScore <= 0
    ? normalizeBooleanScore(selected.id === best.id)
    : clamp(selected.computedScore / best.computedScore);

  return {
    score,
    selected,
    best,
  };
}

function buildOptionMap(options = [], scoreBuilder) {
  return options.map((option, index) => ({
    id: option.id ?? option.optionId ?? option.choice ?? `${index}`,
    ...option,
    score: clamp(scoreBuilder(option)),
  }));
}

function settlementOptionScore(option) {
  return clamp(
    (safeNumber(option.resourceProductionExpectancy ?? option.productionExpectancy, 0) * 0.4)
    + (safeNumber(option.resourceDiversity, 0) * 0.25)
    + (safeNumber(option.expansionAccess, 0) * 0.2)
    + (safeNumber(option.portSynergy, 0) * 0.15)
  );
}

function roadOptionScore(option) {
  return clamp(
    (safeNumber(option.futureReachability, 0) * 0.5)
    + (safeNumber(option.reachableIntersectionValue, 0) * 0.35)
    + (safeNumber(option.blockingValue, 0) * 0.15)
  );
}

function tradeOfferScore(offer = {}) {
  const legality = offer.legal === false ? 0 : 1;
  const selfGain = clamp(offer.selfGain ?? offer.selfBenefit ?? 0);
  const leaderPenalty = clamp(offer.leaderHelpPenalty ?? offer.leaderBenefit ?? 0);
  const fairnessPenalty = clamp(offer.fairnessPenalty ?? 0);
  return clamp((legality * 0.35) + (selfGain * 0.45) + ((1 - leaderPenalty) * 0.15) + ((1 - fairnessPenalty) * 0.05));
}

function rubricChecklistScore(checks = {}, weights = {}) {
  const entries = Object.entries(weights);
  if (!entries.length) return null;
  const score = entries.reduce((sum, [key, weight]) => sum + (checks[key] ? weight : 0), 0);
  return clamp(score);
}

function normalizeResourceMap(resources = {}) {
  return Object.fromEntries(
    Object.entries(resources || {}).map(([key, value]) => [key, Math.max(0, safeNumber(value, 0))])
  );
}

function evaluateSettlementTask(payload = {}) {
  const selectedOptionId = payload.response?.selectedOptionId ?? payload.selectedOptionId;
  const options = buildOptionMap(payload.evaluationContext?.legalOptions || [], settlementOptionScore);
  const { score, selected, best } = scoreFromOptionValue(selectedOptionId, options);
  return {
    score: score ?? clamp(payload.score ?? 0),
    passed: (score ?? 0) >= TASK_THRESHOLDS['settlement-location-selection'],
    explanation: payload.explanation || `Selected settlement scored ${((score ?? 0) * 100).toFixed(1)}% of the best legal option.`,
    evaluationDetails: {
      selectedOptionId,
      selectedScore: selected?.computedScore ?? selected?.score ?? null,
      bestOptionId: best?.id ?? null,
      bestScore: best?.computedScore ?? best?.score ?? null,
      threshold: TASK_THRESHOLDS['settlement-location-selection'],
    },
  };
}

function evaluateRoadTask(payload = {}) {
  const selectedOptionId = payload.response?.selectedOptionId ?? payload.selectedOptionId;
  const options = buildOptionMap(payload.evaluationContext?.legalOptions || [], roadOptionScore);
  const { score, selected, best } = scoreFromOptionValue(selectedOptionId, options, option => option.score);
  return {
    score: score ?? clamp(payload.score ?? 0),
    passed: (score ?? 0) >= TASK_THRESHOLDS['road-placement-direction'],
    explanation: payload.explanation || `Selected road scored ${((score ?? 0) * 100).toFixed(1)}% of the best legal direction.`,
    evaluationDetails: {
      selectedOptionId,
      selectedScore: selected?.score ?? null,
      bestOptionId: best?.id ?? null,
      bestScore: best?.score ?? null,
      threshold: TASK_THRESHOLDS['road-placement-direction'],
    },
  };
}

function evaluateChoiceRatioTask(payload = {}, taskId) {
  const selectedOptionId = payload.response?.selectedOptionId ?? payload.response?.choice ?? payload.selectedOptionId ?? payload.choice;
  const options = buildOptionMap(payload.evaluationContext?.options || [], option => (
    option.score ?? option.ev ?? option.expectedValue ?? option.value ?? 0
  ));
  const { score, selected, best } = scoreFromOptionValue(selectedOptionId, options, option => option.score);
  return {
    score: score ?? clamp(payload.score ?? 0),
    passed: (score ?? 0) >= TASK_THRESHOLDS[taskId],
    explanation: payload.explanation || `${taskId} selected option scored ${((score ?? 0) * 100).toFixed(1)}% of the best available choice.`,
    evaluationDetails: {
      selectedOptionId,
      selectedScore: selected?.score ?? null,
      bestOptionId: best?.id ?? null,
      bestScore: best?.score ?? null,
      threshold: TASK_THRESHOLDS[taskId],
    },
  };
}

function evaluateDiscardTask(payload = {}) {
  const selectedDiscardId = payload.response?.selectedDiscardId ?? payload.selectedDiscardId;
  const options = buildOptionMap(payload.evaluationContext?.discardOptions || [], option => (
    option.retainedHandValue ?? option.score ?? option.value ?? 0
  ));
  const { score, selected, best } = scoreFromOptionValue(selectedDiscardId, options, option => option.score);
  return {
    score: score ?? clamp(payload.score ?? 0),
    passed: (score ?? 0) >= TASK_THRESHOLDS['discard-strategy-after-seven'],
    explanation: payload.explanation || `Discard strategy preserved ${((score ?? 0) * 100).toFixed(1)}% of the best retained-hand value.`,
    evaluationDetails: {
      selectedDiscardId,
      selectedScore: selected?.score ?? null,
      bestDiscardId: best?.id ?? null,
      bestScore: best?.score ?? null,
      threshold: TASK_THRESHOLDS['discard-strategy-after-seven'],
    },
  };
}

function evaluateRobberTask(payload = {}) {
  const selectedPairId = payload.response?.selectedHexId
    ?? payload.selectedHexId
    ?? payload.response?.selectedPairId
    ?? payload.selectedPairId
    ?? payload.response?.hexKey
    ?? payload.hexKey
    ?? `${payload.response?.hexKey || payload.hexKey}:${payload.response?.victimId || payload.victimId}`;
  const options = buildOptionMap(payload.evaluationContext?.options || [], option => (
    option.score
    ?? ((safeNumber(option.hurtLeader, 0) * 0.35)
      + (safeNumber(option.productionDamage, 0) * 0.3)
      + (safeNumber(option.theftValue, 0) * 0.2)
      + (safeNumber(option.selfHarmAvoidance, 0) * 0.15))
  ));
  const { score, selected, best } = scoreFromOptionValue(selectedPairId, options, option => option.score);
  return {
    score: score ?? clamp(payload.score ?? 0),
    passed: (score ?? 0) >= TASK_THRESHOLDS['robber-placement'],
    explanation: payload.explanation || `Robber placement scored ${((score ?? 0) * 100).toFixed(1)}% of the best legal hex.`,
    evaluationDetails: {
      selectedHexId: selectedPairId,
      selectedScore: selected?.score ?? null,
      bestHexId: best?.id ?? null,
      bestScore: best?.score ?? null,
      threshold: TASK_THRESHOLDS['robber-placement'],
    },
  };
}

function evaluateTradeOfferGenerationTask(payload = {}) {
  const selectedOffer = payload.response?.offer || payload.offer || payload.evaluationContext?.candidateOffer || payload.offerContext || {};
  const generatedScore = tradeOfferScore(selectedOffer);
  const bestScore = clamp(payload.evaluationContext?.bestOfferScore ?? payload.evaluationContext?.bestScore ?? 1);
  const score = bestScore <= 0 ? generatedScore : clamp(generatedScore / bestScore);
  return {
    score,
    passed: Boolean(selectedOffer.legal !== false) && score >= TASK_THRESHOLDS['generate-trade-offers'],
    explanation: payload.explanation || `Generated trade offer scored ${((score ?? 0) * 100).toFixed(1)}% of the benchmark threshold.`,
    evaluationDetails: {
      candidateLegal: selectedOffer.legal !== false,
      candidateScore: generatedScore,
      bestScore,
      threshold: TASK_THRESHOLDS['generate-trade-offers'],
    },
  };
}

const TASK_EVALUATORS = {
  'initial-settlement-location-selection': evaluateSettlementTask,
  'settlement-location-selection': evaluateSettlementTask,
  'road-placement-direction': evaluateRoadTask,
  'build-vs-save-decision': payload => evaluateChoiceRatioTask(payload, 'build-vs-save-decision'),
  'city-vs-settlement-vs-road-prioritization': payload => evaluateChoiceRatioTask(payload, 'city-vs-settlement-vs-road-prioritization'),
  'development-card-purchase-decision': payload => evaluateChoiceRatioTask(payload, 'development-card-purchase-decision'),
  'knight-card-playing-decision': payload => evaluateChoiceRatioTask(payload, 'knight-card-playing-decision'),
  'year-of-plenty-card-playing-decision': payload => evaluateChoiceRatioTask(payload, 'year-of-plenty-card-playing-decision'),
  'monopoly-card-playing-decision': payload => evaluateChoiceRatioTask(payload, 'monopoly-card-playing-decision'),
  'road-building-card-playing-decision': payload => evaluateChoiceRatioTask(payload, 'road-building-card-playing-decision'),
  'discard-strategy-after-seven': evaluateDiscardTask,
  'robber-victim-selection': payload => evaluateChoiceRatioTask(payload, 'robber-victim-selection'),
  'robber-placement': evaluateRobberTask,
  'accept-or-reject-trade-offers': payload => evaluateChoiceRatioTask(payload, 'accept-or-reject-trade-offers'),
  'generate-trade-offers': evaluateTradeOfferGenerationTask,
  'select-targeted-trade-partner': payload => evaluateChoiceRatioTask(payload, 'select-targeted-trade-partner'),
  'bank-trade-decision': payload => evaluateChoiceRatioTask(payload, 'bank-trade-decision'),
  'generate-counter-trade-offer': evaluateTradeOfferGenerationTask,
  'decide-pursue-longest-road': payload => evaluateChoiceRatioTask(payload, 'decide-pursue-longest-road'),
  'decide-pursue-largest-army': payload => evaluateChoiceRatioTask(payload, 'decide-pursue-largest-army'),
};

export function evaluateBenchmarkTask(taskId, payload = {}) {
  const explicitPassed = payload.passed ?? payload.success;
  const explicitScore = payload.score;
  const explicitDetails = payload.evaluationDetails || null;

  if (explicitDetails && (typeof explicitPassed === 'boolean' || explicitScore !== undefined)) {
    return {
      passed: typeof explicitPassed === 'boolean' ? explicitPassed : clamp(explicitScore ?? 0) >= (TASK_THRESHOLDS[taskId] || 0.5),
      score: clamp(explicitScore ?? (explicitPassed ? 1 : 0)),
      explanation: payload.explanation || explicitDetails.explanation || null,
      evaluationDetails: explicitDetails,
    };
  }

  const evaluator = TASK_EVALUATORS[taskId];
  if (!evaluator) {
    return {
      passed: Boolean(explicitPassed),
      score: clamp(explicitScore ?? (explicitPassed ? 1 : 0)),
      explanation: payload.explanation || null,
      evaluationDetails: explicitDetails,
    };
  }

  const evaluated = evaluator(payload);
  return {
    passed: Boolean(evaluated.passed),
    score: clamp(evaluated.score ?? (evaluated.passed ? 1 : 0)),
    explanation: evaluated.explanation || payload.explanation || null,
    evaluationDetails: evaluated.evaluationDetails || explicitDetails,
  };
}

export function normalizeMetricScore(metric, value) {
  if (value === null || value === undefined) {
    return 0;
  }

  const numeric = safeNumber(value, 0);
  switch (metric) {
    case 'winRate':
    case 'taskSuccessRate':
      return clamp(numeric);
    case 'averageFinalVictoryPoints':
      return clamp(numeric / 10);
    case 'averageRoundsToWin':
      return clamp((20 - numeric) / 12);
    case 'averageLatencyPerTurn':
      if (numeric <= 2000) return 1;
      if (numeric >= 15000) return 0;
      return clamp((15000 - numeric) / 13000);
    default:
      return METRIC_DIRECTIONS[metric] === 'lower' ? clamp(1 - numeric) : clamp(numeric);
  }
}

export function metricStatus(metric, normalizedScore, rawValue) {
  if (rawValue === null || rawValue === undefined) return 'no-data';
  if (normalizedScore >= 0.85) return 'excellent';
  if (normalizedScore >= 0.7) return 'strong';
  if (normalizedScore >= 0.5) return 'fair';
  if (normalizedScore > 0) return 'weak';
  return METRIC_DIRECTIONS[metric] === 'lower' ? 'high-penalty' : 'low-score';
}

export function computeWeightedMetricScore(metricScores, weights = BENCHMARK_WEIGHTS, metricList = Object.keys(weights), normalizeByWeights = false) {
  const weightSum = metricList.reduce((sum, metric) => sum + (weights[metric] || 0), 0);
  const total = metricList.reduce((sum, metric) => sum + ((metricScores[metric] ?? 0) * (weights[metric] || 0)), 0);
  return normalizeByWeights && weightSum > 0 ? total / weightSum : total;
}

export function computePrimaryComposite(metricScores) {
  return computeWeightedMetricScore(metricScores, BENCHMARK_WEIGHTS, PRIMARY_BENCHMARK_METRICS, true);
}
