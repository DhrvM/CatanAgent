import assert from 'assert';
import { computeGoalAwareResourceMetrics, PLAN_COSTS } from './resourceHeuristics.js';

function approxBetween(value, min, max) {
  return value >= min && value <= max;
}

const openingResources = {
  brick: 0,
  lumber: 0,
  wool: 1,
  grain: 1,
  ore: 0,
};

const openingPlanMetrics = computeGoalAwareResourceMetrics(openingResources, [
  { type: 'road', cost: PLAN_COSTS.road, basePlanWeight: 0.9 },
  { type: 'settlement', cost: PLAN_COSTS.settlement, basePlanWeight: 0.85 },
  { type: 'city', cost: PLAN_COSTS.city, basePlanWeight: 0.2 },
  { type: 'devCard', cost: PLAN_COSTS.devCard, basePlanWeight: 0.33 },
]);

assert(openingPlanMetrics.need.brick > openingPlanMetrics.need.ore, 'Opening need prioritizes brick over ore');
assert(openingPlanMetrics.need.lumber > openingPlanMetrics.need.ore, 'Opening need prioritizes lumber over ore');
assert(openingPlanMetrics.surplus.ore === 0, 'Absent ore is never considered surplus');
assert(approxBetween(openingPlanMetrics.need.brick, 0, 1), 'Need values stay normalized');
assert(approxBetween(openingPlanMetrics.surplus.wool, 0, 1), 'Surplus values stay normalized');

const sameHandExpansionBoard = computeGoalAwareResourceMetrics({
  brick: 1,
  lumber: 1,
  wool: 0,
  grain: 0,
  ore: 2,
}, [
  { type: 'road', cost: PLAN_COSTS.road, basePlanWeight: 0.9 },
  { type: 'settlement', cost: PLAN_COSTS.settlement, basePlanWeight: 0.8 },
  { type: 'city', cost: PLAN_COSTS.city, basePlanWeight: 0.15 },
  { type: 'devCard', cost: PLAN_COSTS.devCard, basePlanWeight: 0.3 },
]);

const sameHandCityBoard = computeGoalAwareResourceMetrics({
  brick: 1,
  lumber: 1,
  wool: 0,
  grain: 0,
  ore: 2,
}, [
  { type: 'road', cost: PLAN_COSTS.road, basePlanWeight: 0.2 },
  { type: 'settlement', cost: PLAN_COSTS.settlement, basePlanWeight: 0.15 },
  { type: 'city', cost: PLAN_COSTS.city, basePlanWeight: 0.95 },
  { type: 'devCard', cost: PLAN_COSTS.devCard, basePlanWeight: 0.55 },
]);

assert(sameHandCityBoard.need.ore > sameHandExpansionBoard.need.ore, 'Same hand changes ore need when the board shifts toward city plans');
assert(sameHandCityBoard.debug.free.ore < sameHandExpansionBoard.debug.free.ore, 'Same hand reduces raw ore surplus when city plans become attractive');

const bankTradeMetrics = computeGoalAwareResourceMetrics({
  brick: 0,
  lumber: 0,
  wool: 1,
  grain: 1,
  ore: 3,
}, [
  { type: 'road', cost: PLAN_COSTS.road, basePlanWeight: 0.95 },
  { type: 'settlement', cost: PLAN_COSTS.settlement, basePlanWeight: 0.9 },
  { type: 'city', cost: PLAN_COSTS.city, basePlanWeight: 0.1 },
  { type: 'devCard', cost: PLAN_COSTS.devCard, basePlanWeight: 0.2 },
]);

assert(bankTradeMetrics.surplus.ore > 0, 'Low-priority ore can become surplus');
assert(bankTradeMetrics.need.brick > bankTradeMetrics.need.ore, 'Bank-trade behavior favors expansion bottlenecks over ore');

const fullHandMetrics = computeGoalAwareResourceMetrics({
  brick: 4,
  lumber: 4,
  wool: 4,
  grain: 4,
  ore: 4,
}, [
  { type: 'road', cost: PLAN_COSTS.road, basePlanWeight: 0.8 },
  { type: 'settlement', cost: PLAN_COSTS.settlement, basePlanWeight: 0.8 },
  { type: 'city', cost: PLAN_COSTS.city, basePlanWeight: 0.8 },
  { type: 'devCard', cost: PLAN_COSTS.devCard, basePlanWeight: 0.55 },
]);

assert.deepStrictEqual(fullHandMetrics.need, {
  brick: 0,
  lumber: 0,
  wool: 0,
  grain: 0,
  ore: 0,
}, 'Already-affordable plans yield zero need');
assert(approxBetween(fullHandMetrics.surplus.brick, 0, 1), 'Full-hand surplus remains normalized');

const zeroHandMetrics = computeGoalAwareResourceMetrics({}, [
  { type: 'road', cost: PLAN_COSTS.road, basePlanWeight: 0.8 },
  { type: 'settlement', cost: PLAN_COSTS.settlement, basePlanWeight: 0.8 },
]);

assert(approxBetween(zeroHandMetrics.need.brick, 0, 1), 'Zero-hand need stays bounded');
assert.deepStrictEqual(zeroHandMetrics.surplus, {
  brick: 0,
  lumber: 0,
  wool: 0,
  grain: 0,
  ore: 0,
}, 'Zero hand produces zero surplus');

console.log('Resource heuristic tests passed');
