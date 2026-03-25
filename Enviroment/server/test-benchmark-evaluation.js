import assert from 'assert';
import {
  computePrimaryComposite,
  computeWeightedMetricScore,
  evaluateBenchmarkTask,
  normalizeMetricScore,
} from './benchmarkEvaluation.js';
import { BENCHMARK_WEIGHTS } from './benchmarkDefinitions.js';

const choiceOptions = [
  { id: 'best', score: 1 },
  { id: 'near', score: 0.91 },
  { id: 'bad', score: 0.3 },
];

const messageChecks = (overrides = {}) => ({
  identifiesThreatenedRoute: true,
  givesSelfInterestedRationale: true,
  factuallyAccurate: true,
  strategicallySound: true,
  truthful: true,
  relevantAlternativeTarget: true,
  strategicallyPersuasive: true,
  identifiesLeader: true,
  explainsTradeBenefit: true,
  ...overrides,
});

const cases = [
  {
    taskId: 'settlement-location-selection',
    pass: {
      response: { selectedOptionId: 'best' },
      evaluationContext: {
        legalOptions: [
          { id: 'best', resourceProductionExpectancy: 1, resourceDiversity: 1, expansionAccess: 0.9, portSynergy: 0.8 },
          { id: 'near', resourceProductionExpectancy: 0.9, resourceDiversity: 0.9, expansionAccess: 0.8, portSynergy: 0.7 },
          { id: 'bad', resourceProductionExpectancy: 0.4, resourceDiversity: 0.3, expansionAccess: 0.2, portSynergy: 0.1 },
        ],
      },
    },
    near: {
      response: { selectedOptionId: 'near' },
      evaluationContext: {
        legalOptions: [
          { id: 'best', resourceProductionExpectancy: 1, resourceDiversity: 1, expansionAccess: 1, portSynergy: 1 },
          { id: 'near', resourceProductionExpectancy: 0.92, resourceDiversity: 0.92, expansionAccess: 0.88, portSynergy: 0.84 },
        ],
      },
    },
    fail: {
      response: { selectedOptionId: 'bad' },
      evaluationContext: {
        legalOptions: [
          { id: 'best', resourceProductionExpectancy: 1, resourceDiversity: 1, expansionAccess: 1, portSynergy: 1 },
          { id: 'bad', resourceProductionExpectancy: 0.1, resourceDiversity: 0.1, expansionAccess: 0.1, portSynergy: 0.1 },
        ],
      },
    },
  },
  {
    taskId: 'road-placement-direction',
    pass: { response: { selectedOptionId: 'best' }, evaluationContext: { legalOptions: [{ id: 'best', futureReachability: 1, reachableIntersectionValue: 1, blockingValue: 0.8 }, { id: 'bad', futureReachability: 0.2, reachableIntersectionValue: 0.1, blockingValue: 0.1 }] } },
    near: { response: { selectedOptionId: 'near' }, evaluationContext: { legalOptions: [{ id: 'best', futureReachability: 1, reachableIntersectionValue: 1, blockingValue: 1 }, { id: 'near', futureReachability: 0.9, reachableIntersectionValue: 0.9, blockingValue: 0.9 }] } },
    fail: { response: { selectedOptionId: 'bad' }, evaluationContext: { legalOptions: [{ id: 'best', futureReachability: 1, reachableIntersectionValue: 1, blockingValue: 0.8 }, { id: 'bad', futureReachability: 0.1, reachableIntersectionValue: 0.1, blockingValue: 0.1 }] } },
  },
  {
    taskId: 'road-placement-quality',
    pass: { response: { selectedOptionId: 'best' }, evaluationContext: { legalOptions: [{ id: 'best', futureReachability: 1, reachableIntersectionValue: 1, blockingValue: 0.8 }, { id: 'bad', futureReachability: 0.2, reachableIntersectionValue: 0.1, blockingValue: 0.1 }] } },
    near: { response: { selectedOptionId: 'near' }, evaluationContext: { legalOptions: [{ id: 'best', futureReachability: 1, reachableIntersectionValue: 1, blockingValue: 1 }, { id: 'near', futureReachability: 0.9, reachableIntersectionValue: 0.9, blockingValue: 0.9 }] } },
    fail: { response: { selectedOptionId: 'bad' }, evaluationContext: { legalOptions: [{ id: 'best', futureReachability: 1, reachableIntersectionValue: 1, blockingValue: 0.8 }, { id: 'bad', futureReachability: 0.1, reachableIntersectionValue: 0.1, blockingValue: 0.1 }] } },
  },
  ...[
    'build-vs-save-decision',
    'city-vs-settlement-vs-road-prioritization',
    'development-card-purchase-decision',
    'development-card-playing-decision',
    'accept-or-reject-trade-offers',
    'select-targeted-trade-partner',
    'bank-trade-decision',
    'decide-pursue-longest-road',
    'defend-against-longest-road',
    'decide-pursue-largest-army',
  ].map(taskId => ({
    taskId,
    pass: { response: { selectedOptionId: 'best' }, evaluationContext: { options: choiceOptions } },
    near: { response: { selectedOptionId: 'near' }, evaluationContext: { options: choiceOptions } },
    fail: { response: { selectedOptionId: 'bad' }, evaluationContext: { options: choiceOptions } },
  })),
  {
    taskId: 'discard-strategy-after-seven',
    pass: { response: { selectedDiscardId: 'best' }, evaluationContext: { discardOptions: [{ id: 'best', retainedHandValue: 1 }, { id: 'bad', retainedHandValue: 0.4 }] } },
    near: { response: { selectedDiscardId: 'near' }, evaluationContext: { discardOptions: [{ id: 'best', retainedHandValue: 1 }, { id: 'near', retainedHandValue: 0.96 }] } },
    fail: { response: { selectedDiscardId: 'bad' }, evaluationContext: { discardOptions: [{ id: 'best', retainedHandValue: 1 }, { id: 'bad', retainedHandValue: 0.2 }] } },
  },
  {
    taskId: 'robber-placement-and-victim-selection',
    pass: { response: { selectedPairId: 'best' }, evaluationContext: { options: [{ id: 'best', hurtLeader: 1, productionDamage: 1, theftValue: 0.8, selfHarmAvoidance: 1 }, { id: 'bad', hurtLeader: 0.1, productionDamage: 0.2, theftValue: 0.1, selfHarmAvoidance: 0.2 }] } },
    near: { response: { selectedPairId: 'near' }, evaluationContext: { options: [{ id: 'best', score: 1 }, { id: 'near', score: 0.92 }] } },
    fail: { response: { selectedPairId: 'bad' }, evaluationContext: { options: [{ id: 'best', score: 1 }, { id: 'bad', score: 0.25 }] } },
  },
  {
    taskId: 'generate-trade-offers',
    pass: { response: { offer: { legal: true, selfGain: 0.9, leaderHelpPenalty: 0.1, fairnessPenalty: 0.1 } }, evaluationContext: { bestOfferScore: 0.95 } },
    near: { response: { offer: { legal: true, selfGain: 0.8, leaderHelpPenalty: 0.15, fairnessPenalty: 0.1 } }, evaluationContext: { bestOfferScore: 0.92 } },
    fail: { response: { offer: { legal: false, selfGain: 0.4, leaderHelpPenalty: 0.8, fairnessPenalty: 0.4 } }, evaluationContext: { bestOfferScore: 0.9 } },
  },
  {
    taskId: 'counter-trade-offer-quality',
    pass: { offerContext: { legal: true, selfGain: 0.9, leaderHelpPenalty: 0.1, fairnessPenalty: 0.1 }, evaluationContext: { bestOfferScore: 0.95 } },
    near: { offerContext: { legal: true, selfGain: 0.8, leaderHelpPenalty: 0.15, fairnessPenalty: 0.1 }, evaluationContext: { bestOfferScore: 0.92 } },
    fail: { offerContext: { legal: false, selfGain: 0.4, leaderHelpPenalty: 0.8, fairnessPenalty: 0.4 }, evaluationContext: { bestOfferScore: 0.9 } },
  },
  {
    taskId: 'detect-blocked-expansion-risk',
    pass: { response: { riskDetected: true, blockerId: 'p2', pathId: 'path-a', explanationCompleteness: 1 }, evaluationContext: { riskDetected: true, blockerId: 'p2', pathId: 'path-a' } },
    near: { response: { riskDetected: true, blockerId: 'p2', pathId: 'path-a', explanationCompleteness: 0.5 }, evaluationContext: { riskDetected: true, blockerId: 'p2', pathId: 'path-a' } },
    fail: { response: { riskDetected: false, blockerId: 'p3', pathId: 'path-b', explanationCompleteness: 0.2 }, evaluationContext: { riskDetected: true, blockerId: 'p2', pathId: 'path-a' } },
  },
  {
    taskId: 'infer-opponent-resources',
    pass: { response: { resources: { ore: 2, wheat: 1 } }, evaluationContext: { actualResources: { ore: 2, wheat: 1 } } },
    near: { response: { resources: { ore: 2, wheat: 0 } }, evaluationContext: { actualResources: { ore: 2, wheat: 1 } } },
    fail: { response: { resources: { ore: 0, wheat: 3 } }, evaluationContext: { actualResources: { ore: 2, wheat: 1 } } },
  },
  {
    taskId: 'discourage-expansion-cutoff',
    pass: { response: { rubricChecks: messageChecks() } },
    near: { response: { rubricChecks: messageChecks({ strategicallySound: false }) } },
    fail: { response: { rubricChecks: messageChecks({ identifiesThreatenedRoute: false, givesSelfInterestedRationale: false }), hasCriticalError: true } },
  },
  {
    taskId: 'discourage-robber-placement',
    pass: { response: { rubricChecks: messageChecks() } },
    near: { response: { rubricChecks: messageChecks({ strategicallyPersuasive: false }) } },
    fail: { response: { rubricChecks: messageChecks({ truthful: false, relevantAlternativeTarget: false }), hasCriticalError: true } },
  },
  {
    taskId: 'warn-against-leader-trade',
    pass: { response: { rubricChecks: messageChecks() } },
    near: { response: { rubricChecks: messageChecks({ factuallyAccurate: false }) } },
    fail: { response: { rubricChecks: messageChecks({ identifiesLeader: false, explainsTradeBenefit: false }), hasCriticalError: true } },
  },
  {
    taskId: 'identify-leading-opponent',
    pass: { response: { leaderId: 'p1' }, evaluationContext: { leaderId: 'p1' } },
    near: { response: { leaderIds: ['p1'] }, evaluationContext: { leaderIds: ['p1', 'p2'] } },
    fail: { response: { leaderId: 'p3' }, evaluationContext: { leaderId: 'p1' } },
  },
];

cases.forEach(({ taskId, pass, near, fail }) => {
  const passResult = evaluateBenchmarkTask(taskId, pass);
  const nearResult = evaluateBenchmarkTask(taskId, near);
  const failResult = evaluateBenchmarkTask(taskId, fail);

  assert.strictEqual(passResult.passed, true, `${taskId} pass case should pass`);
  assert(passResult.score >= failResult.score, `${taskId} pass should score at least as high as fail`);
  assert(nearResult.score >= failResult.score, `${taskId} near-threshold should outperform fail`);
  assert.strictEqual(failResult.passed, false, `${taskId} fail case should fail`);
});

assert.strictEqual(normalizeMetricScore('winRate', 0.75), 0.75, 'Win rate normalization is identity');
assert.strictEqual(normalizeMetricScore('averageFinalVictoryPoints', 8), 0.8, 'VP normalization uses /10');
assert.strictEqual(normalizeMetricScore('averageRoundsToWin', 8), 1, 'Rounds-to-win normalization caps at 1');
assert(normalizeMetricScore('averageLatencyPerTurn', 5000) < 1, 'Latency normalization penalizes slow turns');
assert.strictEqual(normalizeMetricScore('illegalMoveRate', 0.2), 0.8, 'Illegal move normalization inverts error rate');
assert.strictEqual(normalizeMetricScore('retryRate', 2), 0, 'Retry normalization bottoms out at 2 retries/turn');

const metricScores = {
  winRate: 0.8,
  averageFinalVictoryPoints: 0.7,
  averageRoundsToWin: 0.9,
  taskSuccessRate: 0.6,
  averageLatencyPerTurn: 0.75,
  illegalMoveRate: 0.9,
  retryRate: 0.8,
  robustness: 0.7,
  consistency: 0.6,
  generalization: 0.65,
};
const overallScore = computeWeightedMetricScore(metricScores, BENCHMARK_WEIGHTS);
const primaryComposite = computePrimaryComposite(metricScores);

assert(overallScore > 0 && overallScore <= 1, 'Overall score stays within [0,1]');
assert(primaryComposite > 0 && primaryComposite <= 1, 'Primary composite stays within [0,1]');

console.log('Benchmark evaluation tests passed');
