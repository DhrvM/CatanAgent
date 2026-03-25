export const METRIC_DIRECTIONS = {
  winRate: 'higher',
  averageFinalVictoryPoints: 'higher',
  averageRoundsToWin: 'lower',
  taskSuccessRate: 'higher',
  averageLatencyPerTurn: 'lower',
  illegalMoveRate: 'lower',
  retryRate: 'lower',
  robustness: 'higher',
  consistency: 'higher',
  generalization: 'higher',
};

export const BENCHMARK_WEIGHTS = {
  winRate: 0.2,
  averageFinalVictoryPoints: 0.1,
  averageRoundsToWin: 0.1,
  taskSuccessRate: 0.2,
  averageLatencyPerTurn: 0.075,
  illegalMoveRate: 0.075,
  retryRate: 0.05,
  robustness: 0.075,
  consistency: 0.05,
  generalization: 0.075,
};

export const TASK_CATEGORIES = {
  earlyGame: 'Early Game',
  resourcePlanning: 'Resource Optimization & Build Planning',
  opponentInteraction: 'Opponent Interaction',
  strategicCompetition: 'Strategic Competition',
};

export const BENCHMARK_TASKS = [
  {
    id: 'settlement-location-selection',
    name: 'Settlement Location Selection',
    category: 'earlyGame',
    difficulty: 'strategic',
    description: 'Choose the strongest opening settlement location.',
  },
  {
    id: 'road-placement-direction',
    name: 'Road Placement Direction',
    category: 'earlyGame',
    difficulty: 'strategic',
    description: 'Select the best opening road direction for expansion during setup.',
  },
  {
    id: 'build-vs-save-decision',
    name: 'Build vs Save Decision',
    category: 'resourcePlanning',
    difficulty: 'strategic',
    description: 'Decide whether to spend resources now or hold for a stronger turn.',
  },
  {
    id: 'city-vs-settlement-vs-road-prioritization',
    name: 'City vs Settlement vs Road Prioritization',
    category: 'resourcePlanning',
    difficulty: 'strategic',
    description: 'Prioritize the strongest build path from the current hand and board.',
  },
  {
    id: 'development-card-purchase-decision',
    name: 'Development Card Purchase Decision',
    category: 'resourcePlanning',
    difficulty: 'strategic',
    description: 'Decide whether a development card is the best current spend.',
  },
  {
    id: 'development-card-playing-decision',
    name: 'Development Card Playing Decision',
    category: 'resourcePlanning',
    difficulty: 'strategic',
    description: 'Choose whether to play a development card now or later.',
  },
  {
    id: 'discard-strategy-after-seven',
    name: 'Discard Strategy After 7 Roll',
    category: 'resourcePlanning',
    difficulty: 'probabilistic',
    description: 'Discard the least damaging set of cards after a seven.',
  },
  {
    id: 'robber-placement-and-victim-selection',
    name: 'Robber Placement and Victim Selection',
    category: 'opponentInteraction',
    difficulty: 'strategic',
    description: 'Target the best hex and victim when moving the robber.',
  },
  {
    id: 'accept-or-reject-trade-offers',
    name: 'Trade Offer Response',
    category: 'opponentInteraction',
    difficulty: 'strategic',
    description: 'Evaluate whether to accept or reject an incoming player trade offer.',
  },
  {
    id: 'generate-trade-offers',
    name: 'Trade Proposal Quality',
    category: 'opponentInteraction',
    difficulty: 'negotiation',
    description: 'Construct a player-to-player trade offer that advances the agent without aiding leaders.',
  },
  {
    id: 'select-targeted-trade-partner',
    name: 'Trade Partner Targeting',
    category: 'opponentInteraction',
    difficulty: 'strategic',
    description: 'Choose which opponent should receive a targeted trade proposal.',
  },
  {
    id: 'bank-trade-decision',
    name: 'Bank Trade Decision',
    category: 'resourcePlanning',
    difficulty: 'strategic',
    description: 'Choose whether and how to trade with the bank or a port from the current hand and board state.',
  },
  {
    id: 'road-placement-quality',
    name: 'Road Placement Quality',
    category: 'strategicCompetition',
    difficulty: 'strategic',
    description: 'Evaluate a live-game road placement for expansion, blocking, and race value.',
  },
  {
    id: 'counter-trade-offer-quality',
    name: 'Counter Trade Offer Quality',
    category: 'opponentInteraction',
    difficulty: 'negotiation',
    description: 'Construct a counter-offer that improves the agent position without over-helping the proposer.',
  },
  {
    id: 'detect-blocked-expansion-risk',
    name: 'Detect Blocked Expansion Risk',
    category: 'opponentInteraction',
    difficulty: 'multi-step',
    description: 'Recognize when another player is close to cutting off future expansion.',
  },
  {
    id: 'infer-opponent-resources',
    name: 'Infer Opponent Resources From Actions',
    category: 'opponentInteraction',
    difficulty: 'probabilistic',
    description: 'Estimate likely opponent resources from observed play.',
  },
  {
    id: 'discourage-expansion-cutoff',
    name: 'Generate Arguments Against Expansion Cutoff',
    category: 'opponentInteraction',
    difficulty: 'negotiation',
    description: 'Persuade an opponent not to block a critical expansion path.',
  },
  {
    id: 'discourage-robber-placement',
    name: 'Discourage Robber Placement',
    category: 'opponentInteraction',
    difficulty: 'negotiation',
    description: 'Convince opponents not to place the robber on the agent.',
  },
  {
    id: 'warn-against-leader-trade',
    name: 'Warn Against Leader-Benefiting Trade',
    category: 'opponentInteraction',
    difficulty: 'negotiation',
    description: 'Explain why another player should avoid a leader-helping trade.',
  },
  {
    id: 'identify-leading-opponent',
    name: 'Identify Leading Opponent',
    category: 'strategicCompetition',
    difficulty: 'rule-based',
    description: 'Identify the current table leader using visible and inferred signals.',
  },
  {
    id: 'decide-pursue-longest-road',
    name: 'Decide Whether to Pursue Longest Road',
    category: 'strategicCompetition',
    difficulty: 'strategic',
    description: 'Choose whether Longest Road is worth contesting from the current state.',
  },
  {
    id: 'defend-against-longest-road',
    name: 'Defend Against Longest Road',
    category: 'strategicCompetition',
    difficulty: 'multi-step',
    description: 'Prevent or disrupt an opponent pursuing Longest Road.',
  },
  {
    id: 'decide-pursue-largest-army',
    name: 'Decide Whether to Pursue Largest Army',
    category: 'strategicCompetition',
    difficulty: 'strategic',
    description: 'Choose whether Largest Army is the best strategic race.',
  },
];

export const SECONDARY_SLICE_TAGS = {
  robustness: ['resource-scarcity', 'limited-expansion', 'late-game-trade', 'anti-leader'],
  generalization: ['alternate-map', 'seat-variation', 'opponent-policy-variation'],
};

export function getTaskDefinition(taskId) {
  return BENCHMARK_TASKS.find(task => task.id === taskId) || null;
}
