const RESOURCE_TYPES = ['brick', 'lumber', 'wool', 'grain', 'ore'];

const PLAN_COSTS = {
  road: { brick: 1, lumber: 1, wool: 0, grain: 0, ore: 0 },
  settlement: { brick: 1, lumber: 1, wool: 1, grain: 1, ore: 0 },
  city: { brick: 0, lumber: 0, wool: 0, grain: 2, ore: 3 },
  devCard: { brick: 0, lumber: 0, wool: 1, grain: 1, ore: 1 },
};

function clamp01(value) {
  const numeric = Number(value);
  if (!Number.isFinite(numeric)) return 0;
  return Math.max(0, Math.min(1, numeric));
}

function toCount(value) {
  return Math.max(0, Number(value) || 0);
}

function createEmptyResourceMap() {
  return Object.fromEntries(RESOURCE_TYPES.map(resource => [resource, 0]));
}

function normalizeByMax(resourceMap = {}) {
  const maxValue = Math.max(0, ...RESOURCE_TYPES.map(resource => toCount(resourceMap[resource])));
  if (maxValue <= 0) {
    return createEmptyResourceMap();
  }

  return Object.fromEntries(
    RESOURCE_TYPES.map(resource => [resource, clamp01(toCount(resourceMap[resource]) / maxValue)])
  );
}

function normalizeResources(resources = {}) {
  return Object.fromEntries(
    RESOURCE_TYPES.map(resource => [resource, toCount(resources[resource])])
  );
}

function normalizePlan(plan = {}) {
  const type = plan.type || 'custom';
  const cost = normalizeResources(plan.cost || PLAN_COSTS[type] || {});
  const basePlanWeight = clamp01(plan.basePlanWeight ?? plan.baseScore ?? plan.score ?? 0);
  return {
    type,
    cost,
    basePlanWeight,
  };
}

function getMissingCost(resources = {}, cost = {}) {
  const missingByResource = createEmptyResourceMap();
  let totalMissing = 0;

  RESOURCE_TYPES.forEach(resource => {
    const missing = Math.max(0, toCount(cost[resource]) - toCount(resources[resource]));
    missingByResource[resource] = missing;
    totalMissing += missing;
  });

  return {
    missingByResource,
    totalMissing,
  };
}

export function computeGoalAwareResourceMetrics(resources = {}, plans = []) {
  const normalizedResources = normalizeResources(resources);
  const needRaw = createEmptyResourceMap();
  const reservedRaw = createEmptyResourceMap();

  const normalizedPlans = plans
    .map(normalizePlan)
    .filter(plan => plan.basePlanWeight > 0);

  normalizedPlans.forEach(plan => {
    const { missingByResource, totalMissing } = getMissingCost(normalizedResources, plan.cost);
    const planWeight = plan.basePlanWeight / (1 + (0.75 * totalMissing));
    const missingDenominator = Math.max(1, totalMissing);

    RESOURCE_TYPES.forEach(resource => {
      const contribution = missingByResource[resource] / missingDenominator;
      needRaw[resource] += planWeight * contribution;
      reservedRaw[resource] += planWeight * plan.cost[resource];
    });
  });

  const reserved = Object.fromEntries(
    RESOURCE_TYPES.map(resource => [
      resource,
      Math.min(normalizedResources[resource], reservedRaw[resource]),
    ])
  );
  const free = Object.fromEntries(
    RESOURCE_TYPES.map(resource => [
      resource,
      Math.max(0, normalizedResources[resource] - reserved[resource]),
    ])
  );

  return {
    need: normalizeByMax(needRaw),
    surplus: normalizeByMax(free),
    debug: {
      plans: normalizedPlans.map(plan => {
        const { missingByResource, totalMissing } = getMissingCost(normalizedResources, plan.cost);
        return {
          type: plan.type,
          basePlanWeight: plan.basePlanWeight,
          totalMissing,
          effectivePlanWeight: plan.basePlanWeight / (1 + (0.75 * totalMissing)),
          missingByResource,
          cost: plan.cost,
        };
      }),
      rawNeed: needRaw,
      reserved,
      free,
    },
  };
}

export { PLAN_COSTS, RESOURCE_TYPES };
