import { useEffect, useMemo, useState } from 'react';
import './ObservationDeck.css';

function formatPercent(value) {
  if (value === null || value === undefined) return 'N/A';
  return `${(value * 100).toFixed(1)}%`;
}

function formatMetric(metric, value) {
  if (value === null || value === undefined) return 'N/A';
  if (metric.includes('Rate') || metric === 'winRate' || metric === 'taskSuccessRate') {
    return formatPercent(value);
  }
  if (metric.includes('Latency')) {
    return `${Math.round(value)} ms`;
  }
  return Number(value).toFixed(2);
}

function metricLabel(metric) {
  return metric
    .replace(/([A-Z])/g, ' $1')
    .replace(/^./, char => char.toUpperCase());
}

function statusLabel(status) {
  switch (status) {
    case 'excellent':
      return 'Excellent';
    case 'strong':
      return 'Strong';
    case 'fair':
      return 'Fair';
    case 'weak':
      return 'Needs work';
    case 'high-penalty':
      return 'High penalty';
    case 'low-score':
      return 'Low score';
    default:
      return 'No data';
  }
}

function buildQuery(filters) {
  const params = new URLSearchParams();
  Object.entries(filters).forEach(([key, value]) => {
    if (value) {
      params.set(key, value);
    }
  });
  return params.toString();
}

function SummaryCard({ label, value, hint }) {
  return (
    <div className="deck-summary-card">
      <span className="deck-summary-label">{label}</span>
      <strong>{value}</strong>
      {hint ? <span className="deck-summary-hint">{hint}</span> : null}
    </div>
  );
}

function formatEventStatus(entry) {
  if (entry.eventType === 'task') {
    const score = entry.details?.score;
    const scoreText = score === null || score === undefined ? '' : ` (${(score * 100).toFixed(1)}%)`;
    return `Task success: ${entry.status || 'evaluated'}${scoreText}`;
  }
  if (entry.eventType === 'action') {
    if (entry.benchmarkTaskId) {
      const score = entry.details?.benchmarkScore;
      const scoreText = score === null || score === undefined ? '' : ` (${(score * 100).toFixed(1)}%)`;
      return `Task success: ${entry.details?.benchmarkPassed ? 'passed' : 'failed'}${scoreText}`;
    }
    return `Action status: ${entry.status || 'recorded'}`;
  }
  return entry.status || 'recorded';
}

function formatMoveTimeSeconds(value) {
  if (value === null || value === undefined) return 'No move timing';
  return `Move time ${(value / 1000).toFixed(1)}s`;
}

function formatScoreValue(value) {
  if (value === null || value === undefined || Number.isNaN(Number(value))) return null;
  return Number(value).toFixed(3);
}

function formatShortTermScoreDetails(entry) {
  const details = entry.eventType === 'task'
    ? entry.details?.evaluationDetails
    : entry.details?.benchmarkEvaluationDetails;
  const category = entry.eventType === 'task'
    ? entry.details?.taskCategory
    : entry.details?.benchmarkTaskCategory;

  if (category !== 'shortTerm' || !details) return null;

  const selected = formatScoreValue(details.selectedScore);
  const best = formatScoreValue(details.bestScore);
  const threshold = formatScoreValue(details.threshold);
  const parts = [];

  if (selected !== null) parts.push(`selected ${selected}`);
  if (best !== null) parts.push(`best ${best}`);
  if (threshold !== null) parts.push(`threshold ${threshold}`);

  if (!parts.length) return null;
  return `Short-term score details: ${parts.join(' | ')}`;
}

function formatLogContext(entry) {
  if (entry.eventType === 'task') {
    return `Turn ${entry.turnNumber ?? 'N/A'} - ${entry.task || 'Benchmark task'}`;
  }
  if (entry.eventType === 'action') {
    return `Turn ${entry.turnNumber ?? 'N/A'} - ${entry.benchmarkTaskName || entry.actionName || 'Action'}`;
  }
  return `Turn ${entry.turnNumber ?? 'N/A'} - ${entry.task || entry.actionName || entry.eventType}`;
}

function ObservationDeck({ serverUrl, onBack }) {
  const [overview, setOverview] = useState(null);
  const [leaderboard, setLeaderboard] = useState(null);
  const [detail, setDetail] = useState(null);
  const [runDetail, setRunDetail] = useState(null);
  const [slices, setSlices] = useState([]);
  const [liveLog, setLiveLog] = useState([]);
  const [loading, setLoading] = useState(true);
  const [detailLoading, setDetailLoading] = useState(false);
  const [error, setError] = useState(null);
  const [filters, setFilters] = useState({
    benchmarkId: '',
    taskCategory: '',
    mapLayoutId: '',
    opponentPolicySet: '',
    runId: '',
    search: '',
  });
  const [entityType, setEntityType] = useState('agent');

  const filterQuery = useMemo(() => buildQuery(filters), [filters]);

  useEffect(() => {
    let cancelled = false;

    async function loadDeck() {
      try {
        setLoading(true);
        const [overviewRes, leaderboardRes, slicesRes] = await Promise.all([
          fetch(`${serverUrl}/api/benchmark/overview`),
          fetch(`${serverUrl}/api/benchmark/leaderboard?entityType=${entityType}${filterQuery ? `&${filterQuery}` : ''}`),
          fetch(`${serverUrl}/api/benchmark/slices${filterQuery ? `?${filterQuery}` : ''}`),
        ]);
        if (!overviewRes.ok || !leaderboardRes.ok || !slicesRes.ok) {
          throw new Error('Failed to load benchmark observation data');
        }

        const [overviewData, leaderboardData, slicesData] = await Promise.all([
          overviewRes.json(),
          leaderboardRes.json(),
          slicesRes.json(),
        ]);

        if (!cancelled) {
          setOverview(overviewData);
          setLeaderboard(leaderboardData);
          setSlices(slicesData.slices || []);
          setError(null);
        }
      } catch (fetchError) {
        if (!cancelled) {
          setError(fetchError.message);
        }
      } finally {
        if (!cancelled) {
          setLoading(false);
        }
      }
    }

    loadDeck();
    return () => {
      cancelled = true;
    };
  }, [serverUrl, entityType, filterQuery]);

  useEffect(() => {
    let cancelled = false;

    async function loadLiveLog() {
      try {
        const response = await fetch(`${serverUrl}/api/benchmark/live-log?limit=30${filterQuery ? `&${filterQuery}` : ''}`);
        if (!response.ok) {
          throw new Error('Failed to load live benchmark log');
        }
        const payload = await response.json();
        if (!cancelled) {
          setLiveLog(payload.entries || []);
        }
      } catch (fetchError) {
        if (!cancelled) {
          setError(current => current || fetchError.message);
        }
      }
    }

    loadLiveLog();
    const intervalId = setInterval(loadLiveLog, 3000);
    return () => {
      cancelled = true;
      clearInterval(intervalId);
    };
  }, [serverUrl, filterQuery]);

  const handleFilterChange = event => {
    const { name, value } = event.target;
    setFilters(current => ({
      ...current,
      [name]: value,
    }));
  };

  const loadDetail = async entry => {
    setDetailLoading(true);
    setRunDetail(null);
    try {
      const query = filterQuery ? `?${filterQuery}` : '';
      const response = await fetch(`${serverUrl}/api/benchmark/${entry.entityType === 'player' ? 'players' : 'agents'}/${encodeURIComponent(entry.entityKey)}${query}`);
      if (!response.ok) {
        throw new Error('Failed to load detail view');
      }
      setDetail(await response.json());
    } catch (fetchError) {
      setError(fetchError.message);
    } finally {
      setDetailLoading(false);
    }
  };

  const loadRunDetail = async runId => {
    setDetail(null);
    setDetailLoading(true);
    try {
      const response = await fetch(`${serverUrl}/api/benchmark/runs/${encodeURIComponent(runId)}`);
      if (!response.ok) {
        throw new Error('Failed to load run detail');
      }
      setRunDetail(await response.json());
    } catch (fetchError) {
      setError(fetchError.message);
    } finally {
      setDetailLoading(false);
    }
  };

  const filtersMeta = overview?.filters || {
    benchmarkIds: [],
    mapLayoutIds: [],
    opponentPolicySets: [],
    runIds: [],
    taskCategories: [],
  };

  return (
    <div className="observation-deck">
      <div className="observation-deck__hero">
        <div>
          <p className="observation-deck__eyebrow">Benchmark Deck</p>
          <h1>Benchmark analytics for every Catan player and agent</h1>
          <p className="observation-deck__subhead">
            Compare benchmark runs, drill into task families, inspect turn telemetry, and rank agents with absolute benchmark scoring.
          </p>
        </div>
        <div className="observation-deck__hero-actions">
          <button className="deck-secondary-button" onClick={onBack}>Back To Lobby</button>
        </div>
      </div>

      {error ? <div className="deck-error">{error}</div> : null}

      {loading ? (
        <div className="deck-loading">Loading benchmark deck...</div>
      ) : (
        <>
          <div className="deck-summary-grid">
            <SummaryCard label="Tracked Runs" value={overview?.totals?.runs ?? 0} hint="Benchmark manifests persisted to JSON" />
            <SummaryCard label="Tracked Games" value={overview?.totals?.games ?? 0} hint="Full-game benchmark results" />
            <SummaryCard label="Reasoning Tasks" value={overview?.totals?.tasks ?? 0} hint="Task-level evaluations captured" />
            <SummaryCard label="Telemetry Records" value={overview?.totals?.telemetryRecords ?? 0} hint="Per-turn action attempts and latencies" />
          </div>

          <div className="deck-toolbar">
            <div className="deck-toolbar__row">
              <label>
                Entity
                <select value={entityType} onChange={event => setEntityType(event.target.value)}>
                  <option value="agent">Agents</option>
                  <option value="player">Players</option>
                </select>
              </label>
              <label>
                Benchmark
                <select name="benchmarkId" value={filters.benchmarkId} onChange={handleFilterChange}>
                  <option value="">All</option>
                  {filtersMeta.benchmarkIds.map(value => <option key={value} value={value}>{value}</option>)}
                </select>
              </label>
              <label>
                Task Family
                <select name="taskCategory" value={filters.taskCategory} onChange={handleFilterChange}>
                  <option value="">All</option>
                  {filtersMeta.taskCategories.map(category => (
                    <option key={category.value} value={category.value}>{category.label}</option>
                  ))}
                </select>
              </label>
            </div>
            <div className="deck-toolbar__row">
              <label>
                Map
                <select name="mapLayoutId" value={filters.mapLayoutId} onChange={handleFilterChange}>
                  <option value="">All</option>
                  {filtersMeta.mapLayoutIds.map(value => <option key={value} value={value}>{value}</option>)}
                </select>
              </label>
              <label>
                Opponent Policy
                <select name="opponentPolicySet" value={filters.opponentPolicySet} onChange={handleFilterChange}>
                  <option value="">All</option>
                  {filtersMeta.opponentPolicySets.map(value => <option key={value} value={value}>{value}</option>)}
                </select>
              </label>
              <label>
                Run
                <select name="runId" value={filters.runId} onChange={handleFilterChange}>
                  <option value="">All</option>
                  {filtersMeta.runIds.map(run => <option key={run.runId} value={run.runId}>{run.label}</option>)}
                </select>
              </label>
              <label className="deck-toolbar__search">
                Search
                <input
                  name="search"
                  value={filters.search}
                  onChange={handleFilterChange}
                  placeholder="Agent, player, or task"
                />
              </label>
            </div>
          </div>

          <div className="deck-layout">
            <section className="deck-panel">
              <div className="deck-panel__header">
                <h2>Live Benchmark Log</h2>
                <span>{liveLog.length} recent events</span>
              </div>
              <div className="deck-list">
                {liveLog.map(entry => (
                  <div key={entry.id} className="deck-list__item">
                    <strong>{`${entry.gameCode || 'No game'} - ${entry.playerName || 'Unknown player'}`}</strong>
                    <span>{formatLogContext(entry)}</span>
                    <span>{`${entry.eventType}: ${formatEventStatus(entry)}`}</span>
                    <span>{formatMoveTimeSeconds(entry.moveTimeMs)}</span>
                    {formatShortTermScoreDetails(entry) ? (
                      <span>{formatShortTermScoreDetails(entry)}</span>
                    ) : null}
                    <span>
                      {entry.eventType === 'task'
                        ? (entry.details?.explanation || 'No evaluator notes')
                        : (entry.details?.benchmarkExplanation || 'No evaluator notes')}
                    </span>
                  </div>
                ))}
              </div>
            </section>

            <section className="deck-panel deck-panel--leaderboard">
              <div className="deck-panel__header">
                <h2>{entityType === 'agent' ? 'Agent Leaderboard' : 'Player Leaderboard'}</h2>
                <span>{leaderboard?.entries?.length ?? 0} ranked entries</span>
              </div>
              <div className="deck-table">
                <div className="deck-table__header">
                  <span>Rank</span>
                  <span>Name</span>
                  <span>Benchmark Score</span>
                  <span>Win Rate</span>
                  <span>Task Success</span>
                </div>
                {(leaderboard?.entries || []).map(entry => (
                  <button key={entry.entityKey} className="deck-table__row" onClick={() => loadDetail(entry)}>
                    <span>#{entry.rank}</span>
                    <span>{entry.displayName}</span>
                    <span>{entry.overallScore?.toFixed(3) ?? 'N/A'}</span>
                    <span>{formatPercent(entry.rawMetrics.winRate)}</span>
                    <span>{formatPercent(entry.rawMetrics.taskSuccessRate)}</span>
                  </button>
                ))}
              </div>
            </section>

            <section className="deck-panel deck-panel--detail">
              <div className="deck-panel__header">
                <h2>Detail View</h2>
                <span>{detailLoading ? 'Loading...' : 'Select an entry or run'}</span>
              </div>
              {detail ? (
                <div className="deck-detail">
                  <div className="deck-detail__summary">
                    <h3>{detail.displayName}</h3>
                    <p>{detail.entityType === 'agent' ? detail.entityKey : detail.playerKey}</p>
                    <div className="deck-detail__scorecards">
                      <SummaryCard label="Overall Score" value={detail.overallScore?.toFixed(3) ?? 'N/A'} />
                      <SummaryCard label="Gameplay Score" value={detail.categoryScores.gameplay?.toFixed(3) ?? 'N/A'} />
                      <SummaryCard label="Reasoning Score" value={detail.categoryScores.reasoning?.toFixed(3) ?? 'N/A'} />
                      <SummaryCard label="Operations Score" value={detail.categoryScores.operations?.toFixed(3) ?? 'N/A'} />
                    </div>
                  </div>

                  <div className="deck-metrics-grid">
                    {Object.entries(detail.rawMetrics).map(([metric, value]) => (
                      <div key={metric} className="deck-metric-card">
                        <span>{metricLabel(metric)}</span>
                        <strong>{formatMetric(metric, value)}</strong>
                        {detail.normalizedMetrics?.[metric] !== undefined ? (
                          <small>{`Absolute score ${(detail.normalizedMetrics[metric] * 100).toFixed(1)}%`}</small>
                        ) : null}
                        <small>{statusLabel(detail.metricStatuses?.[metric])}</small>
                      </div>
                    ))}
                  </div>

                  <div className="deck-subsection">
                    <h4>Task Matrix</h4>
                    <div className="deck-list">
                      {detail.taskBreakdown.map(task => (
                        <div key={task.taskId} className="deck-list__item">
                          <strong>{task.taskName}</strong>
                          <span>{`${task.taskCategory} | ${task.difficulty}`}</span>
                          <span>{task.attempts} attempts</span>
                          <span>{task.attempts > 0 ? `${formatPercent(task.successRate)} success` : 'No evaluations yet'}</span>
                          <span>{task.averageScore?.toFixed(2) ?? 'N/A'} avg rubric score</span>
                          <span>{task.scoring || task.description || 'No rubric description'}</span>
                          <span>{task.commonFailureReasons?.[0]?.reason || 'No common failure reason'}</span>
                        </div>
                      ))}
                    </div>
                  </div>

                  <div className="deck-subsection">
                    <h4>Slice Comparisons</h4>
                    <div className="deck-list">
                      {detail.slices.slice(0, 10).map(slice => (
                        <div key={slice.sliceKey} className="deck-list__item">
                          <strong>{slice.taskId || slice.gameType || 'Gameplay Slice'}</strong>
                          <span>{slice.mapLayoutId}</span>
                          <span>{slice.sliceScore?.toFixed(3) ?? 'N/A'} primary score</span>
                        </div>
                      ))}
                    </div>
                  </div>
                </div>
              ) : runDetail ? (
                <div className="deck-detail">
                  <div className="deck-detail__summary">
                    <h3>{runDetail.run.runLabel || runDetail.run.runId}</h3>
                    <p>{runDetail.run.runId}</p>
                  </div>
                  <div className="deck-list">
                    <div className="deck-list__item">
                      <strong>Games</strong>
                      <span>{runDetail.games.length}</span>
                    </div>
                    <div className="deck-list__item">
                      <strong>Tasks</strong>
                      <span>{runDetail.tasks.length}</span>
                    </div>
                    <div className="deck-list__item">
                      <strong>Telemetry</strong>
                      <span>{runDetail.telemetry.length}</span>
                    </div>
                  </div>
                </div>
              ) : (
                <div className="deck-empty-state">
                  Select a leaderboard row or recent run to inspect metrics, tasks, and slice-by-slice comparisons.
                </div>
              )}
            </section>
          </div>

          <div className="deck-layout">
            <section className="deck-panel">
              <div className="deck-panel__header">
                <h2>Benchmark Explorer</h2>
                <span>{overview?.tasks?.length ?? 0} benchmark task definitions</span>
              </div>
              <div className="deck-list">
                {(overview?.tasks || []).map(task => (
                  <div key={task.id} className="deck-list__item">
                    <strong>{task.name}</strong>
                    <span>{task.id}</span>
                    <span>{`${task.category} | ${task.difficulty}`}</span>
                    <span>{task.scoring}</span>
                  </div>
                ))}
              </div>
            </section>

            <section className="deck-panel">
              <div className="deck-panel__header">
                <h2>Recent Runs</h2>
                <span>Click for run detail</span>
              </div>
              <div className="deck-list">
                {(overview?.recentRuns || []).map(run => (
                  <button key={run.runId} className="deck-list__item deck-list__item--button" onClick={() => loadRunDetail(run.runId)}>
                    <strong>{run.runLabel || run.runId}</strong>
                    <span>{run.benchmarkId}</span>
                    <span>{run.players?.length || 0} participants</span>
                  </button>
                ))}
              </div>
            </section>

            <section className="deck-panel">
              <div className="deck-panel__header">
                <h2>Slice Drilldown</h2>
                <span>Absolute comparisons by map, seat, task, and opponent</span>
              </div>
              <div className="deck-list">
                {slices.slice(0, 12).map(slice => (
                  <div key={slice.sliceKey} className="deck-list__item">
                    <strong>{slice.taskId || slice.gameType || 'Gameplay'}</strong>
                    <span>{slice.mapLayoutId}</span>
                    <span>{slice.entries.length} entries</span>
                  </div>
                ))}
              </div>
            </section>
          </div>
        </>
      )}
    </div>
  );
}

export default ObservationDeck;
