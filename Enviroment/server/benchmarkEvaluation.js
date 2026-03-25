import { BENCHMARK_WEIGHTS, METRIC_DIRECTIONS, SECONDARY_SLICE_TAGS } from './benchmarkDefinitions.js';

export const PRIMARY_BENCHMARK_METRICS = [
  'winRate',
  'averageFinalVictoryPoints',
  'averageRoundsToWin',
  'taskSuccessRate',
  'averageLatencyPerTurn',
  'illegalMoveRate',
  'retryRate',
];

export const SECONDARY_BENCHMARK_METRICS = [
  'robustness',
  'consistency',
  'generalization',
];

const PRIMARY_WEIGHT_SUM = PRIMARY_BENCHMARK_METRICS.reduce((sum, metric) => sum + (BENCHMARK_WEIGHTS[metric] || 0), 0);

const TASK_THRESHOLDS = {
  'settlement-location-selection': 0.9,
  'road-placement-direction': 0.9,
  'build-vs-save-decision': 0.9,
  'city-vs-settlement-vs-road-prioritization': 0.9,
  'development-card-purchase-decision': 0.95,
  'development-card-playing-decision': 0.9,
  'discard-strategy-after-seven': 0.95,
  'robber-placement-and-victim-selection': 0.9,
  'accept-or-reject-trade-offers': 0.95,
  'generate-trade-offers': 0.7,
  'select-targeted-trade-partner': 0.9,
  'detect-blocked-expansion-risk': 0.85,
  'infer-opponent-resources': 0.8,
  'discourage-expansion-cutoff': 0.8,
  'discourage-robber-placement': 0.75,
  'warn-against-leader-trade': 0.85,
  'identify-leading-opponent': 1,
  'decide-pursue-longest-road': 0.95,
  'defend-against-longest-road': 0.9,
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

function variance(values) {
  if (values.length < 2) return 0;
  const mean = average(values);
  return average(values.map(value => (value - mean) ** 2)) || 0;
}

function standardDeviation(values) {
  return Math.sqrt(variance(values));
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
  const selectedPairId = payload.response?.selectedPairId
    ?? payload.selectedPairId
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
    passed: (score ?? 0) >= TASK_THRESHOLDS['robber-placement-and-victim-selection'],
    explanation: payload.explanation || `Robber placement scored ${((score ?? 0) * 100).toFixed(1)}% of the best legal target/victim pair.`,
    evaluationDetails: {
      selectedPairId,
      selectedScore: selected?.score ?? null,
      bestPairId: best?.id ?? null,
      bestScore: best?.score ?? null,
      threshold: TASK_THRESHOLDS['robber-placement-and-victim-selection'],
    },
  };
}

function evaluateTradeOfferGenerationTask(payload = {}) {
  const selectedOffer = payload.response?.offer || payload.offer || payload.evaluationContext?.candidateOffer || {};
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

function evaluateBlockedExpansionTask(payload = {}) {
  const response = payload.response || {};
  const expected = payload.evaluationContext || {};
  const riskCorrect = Boolean(response.riskDetected) === Boolean(expected.riskDetected);
  const blockerCorrect = !expected.blockerId || expected.blockerId === response.blockerId;
  const pathCorrect = !expected.pathId || expected.pathId === response.pathId;
  const completeness = clamp(safeNumber(response.explanationCompleteness ?? expected.expectedCompleteness, 0));
  const score = clamp((riskCorrect ? 0.5 : 0) + (blockerCorrect ? 0.25 : 0) + (pathCorrect ? 0.15 : 0) + (completeness * 0.1));
  return {
    score,
    passed: riskCorrect && blockerCorrect && score >= TASK_THRESHOLDS['detect-blocked-expansion-risk'],
    explanation: payload.explanation || `Expansion-risk detection scored ${((score ?? 0) * 100).toFixed(1)}% on correctness and completeness.`,
    evaluationDetails: {
      riskCorrect,
      blockerCorrect,
      pathCorrect,
      completeness,
      threshold: TASK_THRESHOLDS['detect-blocked-expansion-risk'],
    },
  };
}

function evaluateInferResourcesTask(payload = {}) {
  const inferred = normalizeResourceMap(payload.response?.resources || payload.inferredResources || {});
  const actual = normalizeResourceMap(payload.evaluationContext?.actualResources || {});
  const resourceKeys = [...new Set([...Object.keys(inferred), ...Object.keys(actual)])];
  const totalActual = resourceKeys.reduce((sum, key) => sum + actual[key], 0);
  const distance = resourceKeys.reduce((sum, key) => sum + Math.abs((actual[key] || 0) - (inferred[key] || 0)), 0);
  const normalizedDistance = totalActual > 0 ? distance / (2 * totalActual) : (distance > 0 ? 1 : 0);
  const exactMatch = resourceKeys.every(key => (actual[key] || 0) === (inferred[key] || 0));
  const calibratedScore = payload.response?.distributionScore ?? payload.evaluationContext?.distributionScore ?? null;
  const score = clamp(calibratedScore ?? (1 - normalizedDistance));
  return {
    score,
    passed: exactMatch || score >= TASK_THRESHOLDS['infer-opponent-resources'],
    explanation: payload.explanation || `Opponent-resource inference scored ${(score * 100).toFixed(1)}% against hidden-state truth.`,
    evaluationDetails: {
      exactMatch,
      normalizedDistance,
      threshold: TASK_THRESHOLDS['infer-opponent-resources'],
    },
  };
}

function evaluateRubricMessageTask(payload = {}, taskId, weights) {
  const checks = payload.response?.rubricChecks || payload.rubricChecks || {};
  const score = rubricChecklistScore(checks, weights) ?? clamp(payload.score ?? 0);
  return {
    score,
    passed: score >= TASK_THRESHOLDS[taskId] && !(payload.response?.hasCriticalError || payload.hasCriticalError),
    explanation: payload.explanation || `${taskId} satisfied ${(score * 100).toFixed(1)}% of rubric checks.`,
    evaluationDetails: {
      checks,
      hasCriticalError: Boolean(payload.response?.hasCriticalError || payload.hasCriticalError),
      threshold: TASK_THRESHOLDS[taskId],
    },
  };
}

function evaluateIdentifyLeaderTask(payload = {}) {
  const selectedIds = [...new Set(payload.response?.leaderIds || payload.response?.leaderId ? [payload.response?.leaderId, ...(payload.response?.leaderIds || [])] : payload.leaderIds || [])].filter(Boolean).sort();
  const expectedIds = [...new Set(payload.evaluationContext?.leaderIds || payload.evaluationContext?.leaderId ? [payload.evaluationContext?.leaderId, ...(payload.evaluationContext?.leaderIds || [])] : [])].filter(Boolean).sort();
  const intersection = selectedIds.filter(id => expectedIds.includes(id));
  const exact = selectedIds.length === expectedIds.length && intersection.length === expectedIds.length;
  const partial = expectedIds.length ? intersection.length / expectedIds.length : 0;
  const score = exact ? 1 : clamp(partial * 0.5);
  return {
    score,
    passed: exact,
    explanation: payload.explanation || `Leader identification matched ${(score * 100).toFixed(1)}% of the expected leader set.`,
    evaluationDetails: {
      selectedIds,
      expectedIds,
      threshold: TASK_THRESHOLDS['identify-leading-opponent'],
    },
  };
}

const TASK_EVALUATORS = {
  'settlement-location-selection': evaluateSettlementTask,
  'road-placement-direction': evaluateRoadTask,
  'build-vs-save-decision': payload => evaluateChoiceRatioTask(payload, 'build-vs-save-decision'),
  'city-vs-settlement-vs-road-prioritization': payload => evaluateChoiceRatioTask(payload, 'city-vs-settlement-vs-road-prioritization'),
  'development-card-purchase-decision': payload => evaluateChoiceRatioTask(payload, 'development-card-purchase-decision'),
  'development-card-playing-decision': payload => evaluateChoiceRatioTask(payload, 'development-card-playing-decision'),
  'discard-strategy-after-seven': evaluateDiscardTask,
  'robber-placement-and-victim-selection': evaluateRobberTask,
  'accept-or-reject-trade-offers': payload => evaluateChoiceRatioTask(payload, 'accept-or-reject-trade-offers'),
  'generate-trade-offers': evaluateTradeOfferGenerationTask,
  'select-targeted-trade-partner': payload => evaluateChoiceRatioTask(payload, 'select-targeted-trade-partner'),
  'detect-blocked-expansion-risk': evaluateBlockedExpansionTask,
  'infer-opponent-resources': evaluateInferResourcesTask,
  'discourage-expansion-cutoff': payload => evaluateRubricMessageTask(payload, 'discourage-expansion-cutoff', {
    identifiesThreatenedRoute: 0.35,
    givesSelfInterestedRationale: 0.35,
    factuallyAccurate: 0.2,
    strategicallySound: 0.1,
  }),
  'discourage-robber-placement': payload => evaluateRubricMessageTask(payload, 'discourage-robber-placement', {
    truthful: 0.4,
    relevantAlternativeTarget: 0.3,
    strategicallyPersuasive: 0.3,
  }),
  'warn-against-leader-trade': payload => evaluateRubricMessageTask(payload, 'warn-against-leader-trade', {
    identifiesLeader: 0.4,
    explainsTradeBenefit: 0.35,
    factuallyAccurate: 0.25,
  }),
  'identify-leading-opponent': evaluateIdentifyLeaderTask,
  'decide-pursue-longest-road': payload => evaluateChoiceRatioTask(payload, 'decide-pursue-longest-road'),
  'defend-against-longest-road': payload => evaluateChoiceRatioTask(payload, 'defend-against-longest-road'),
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
    case 'robustness':
    case 'consistency':
    case 'generalization':
      return clamp(numeric);
    case 'averageFinalVictoryPoints':
      return clamp(numeric / 10);
    case 'averageRoundsToWin':
      return clamp((20 - numeric) / 12);
    case 'averageLatencyPerTurn':
      if (numeric <= 2000) return 1;
      if (numeric >= 15000) return 0;
      return clamp((15000 - numeric) / 13000);
    case 'illegalMoveRate':
      return clamp(1 - numeric);
    case 'retryRate':
      return clamp(1 - (numeric / 2));
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

export function computeSecondaryMetricsFromSlices(slices = [], runSlices = []) {
  const slicePrimaryScores = slices
    .map(slice => slice.primaryCompositeScore)
    .filter(score => score !== null && score !== undefined);

  const robustnessSliceScores = slices
    .filter(slice => slice.scenarioTags.some(tag => SECONDARY_SLICE_TAGS.robustness.includes(tag)))
    .map(slice => slice.primaryCompositeScore)
    .filter(score => score !== null && score !== undefined);

  const robustness = robustnessSliceScores.length ? average(robustnessSliceScores) : null;

  const groupedByConfig = new Map();
  runSlices.forEach(slice => {
    const current = groupedByConfig.get(slice.configKey) || [];
    current.push(slice.primaryCompositeScore);
    groupedByConfig.set(slice.configKey, current);
  });

  const consistencyBuckets = [...groupedByConfig.values()]
    .filter(scores => scores.length >= 3)
    .map(scores => 1 / (1 + variance(scores)));
  const consistency = consistencyBuckets.length ? average(consistencyBuckets) : null;

  const familyGroups = {
    mapLayoutId: new Map(),
    startingSeat: new Map(),
    opponentPolicySet: new Map(),
  };
  slices.forEach(slice => {
    const families = [
      ['mapLayoutId', slice.mapLayoutId],
      ['startingSeat', slice.startingSeat],
      ['opponentPolicySet', slice.opponentPolicySet],
    ];
    families.forEach(([family, rawValue]) => {
      const value = rawValue ?? null;
      if (value === null) return;
      const scores = familyGroups[family].get(value) || [];
      scores.push(slice.primaryCompositeScore);
      familyGroups[family].set(value, scores);
    });
  });

  const generalizationFamilies = Object.values(familyGroups)
    .map(group => [...group.values()].map(values => average(values.filter(value => value !== null && value !== undefined))).filter(value => value !== null))
    .filter(values => values.length >= 2)
    .map(values => clamp((average(values) ?? 0) - standardDeviation(values)));
  const generalization = generalizationFamilies.length ? average(generalizationFamilies) : null;

  return {
    robustness,
    consistency,
    generalization,
    consistencyVariance: variance(slicePrimaryScores),
  };
}
