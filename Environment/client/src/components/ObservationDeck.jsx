import { useEffect, useMemo, useState } from 'react';
import './ObservationDeck.css';

function formatPercent(value) {
  if (value === null || value === undefined || Number.isNaN(Number(value))) return 'N/A';
  return `${(Number(value) * 100).toFixed(1)}%`;
}

function formatNumber(value, digits = 2) {
  if (value === null || value === undefined || Number.isNaN(Number(value))) return 'N/A';
  return Number(value).toFixed(digits);
}

function formatMetric(metric, value) {
  if (metric.includes('Rate') || metric === 'taskSuccessRate') return formatPercent(value);
  if (metric.includes('Latency')) return value === null || value === undefined ? 'N/A' : `${Math.round(value)} ms`;
  return formatNumber(value, 2);
}

function metricLabel(metric) {
  return metric
    .replace(/([A-Z])/g, ' $1')
    .replace(/^./, char => char.toUpperCase());
}

function buildQuery(filters) {
  const params = new URLSearchParams();
  Object.entries(filters).forEach(([key, value]) => {
    if (value) params.set(key, value);
  });
  return params.toString();
}

function scoreClass(value) {
  if (value === null || value === undefined) return 'muted';
  if (value >= 0.85) return 'good';
  if (value >= 0.65) return 'ok';
  return 'bad';
}

function eventLabel(entry) {
  if (entry.eventType === 'game') return entry.success ? 'Win' : 'Loss';
  if (entry.eventType === 'task') return entry.success ? 'Passed' : 'Failed';
  if (entry.benchmarkTaskId) return entry.details?.benchmarkPassed ? 'Passed' : 'Failed';
  return entry.status || 'Recorded';
}

function eventClass(entry) {
  if (entry.eventType === 'game') return entry.success ? 'good' : 'muted';
  if (entry.eventType === 'task' || entry.benchmarkTaskId) return entry.success || entry.details?.benchmarkPassed ? 'good' : 'bad';
  if (entry.status === 'rejected' || entry.illegal) return 'bad';
  return 'ok';
}

function eventTitle(entry) {
  const name = entry.benchmarkTaskName || entry.task || entry.actionName || entry.eventType || 'Event';
  return `Turn ${entry.turnNumber ?? 'N/A'} - ${name}`;
}

function eventSubtitle(entry) {
  const game = entry.gameCode || 'No game';
  const player = entry.playerName || entry.agentId || 'Unknown';
  return `${game} / ${player}`;
}

function eventExplanation(entry) {
  if (entry.eventType === 'task') return entry.details?.explanation || 'No evaluator details recorded.';
  if (entry.benchmarkTaskId) return entry.details?.benchmarkExplanation || 'No evaluator details recorded.';
  if (entry.eventType === 'game') {
    const points = entry.details?.finalVictoryPoints;
    const rounds = entry.details?.roundsPlayed;
    return `Final VP: ${points ?? 'N/A'}${rounds ? ` / Rounds: ${rounds}` : ''}`;
  }
  return 'Action telemetry only; no benchmark task was linked to this event.';
}

function eventScore(entry) {
  if (entry.eventType === 'task') return entry.details?.score;
  return entry.details?.benchmarkScore;
}

function taskScoreLine(entry) {
  const details = entry.eventType === 'task'
    ? entry.details?.evaluationDetails
    : entry.details?.benchmarkEvaluationDetails;
  if (!details) return null;
  const parts = [];
  if (details.selectedScore !== undefined && details.selectedScore !== null) parts.push(`selected ${formatNumber(details.selectedScore, 3)}`);
  if (details.bestScore !== undefined && details.bestScore !== null) parts.push(`best ${formatNumber(details.bestScore, 3)}`);
  if (details.threshold !== undefined && details.threshold !== null) parts.push(`threshold ${formatNumber(details.threshold, 3)}`);
  if (details.candidateScore !== undefined && details.candidateScore !== null) parts.push(`candidate ${formatNumber(details.candidateScore, 3)}`);
  return parts.length ? parts.join(' / ') : null;
}

function compactDate(value) {
  if (!value) return 'No timestamp';
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return value;
  return date.toLocaleString([], {
    month: 'short',
    day: 'numeric',
    hour: 'numeric',
    minute: '2-digit',
  });
}

function LeaderboardRow({ entry, selected, onSelect }) {
  const taskRate = entry.rawMetrics?.taskSuccessRate;
  const winRate = entry.rawMetrics?.winRate;
  const vp = entry.rawMetrics?.averageFinalVictoryPoints;
  return (
    <button className={`benchmark-row ${selected ? 'benchmark-row--selected' : ''}`} onClick={() => onSelect(entry)}>
      <span className="rank-cell">#{entry.rank}</span>
      <span className="name-cell">
        <strong>{entry.displayName}</strong>
        <small>{entry.agentVersion || entry.entityKey}</small>
      </span>
      <span className={`score-pill ${scoreClass(entry.overallScore)}`}>{formatNumber(entry.overallScore, 3)}</span>
      <span>{formatPercent(winRate)}</span>
      <span>{formatPercent(taskRate)}</span>
      <span>{formatNumber(vp, 1)}</span>
      <span>{entry.rawMetrics?.averageLatencyPerTurn ? `${Math.round(entry.rawMetrics.averageLatencyPerTurn)} ms` : 'N/A'}</span>
    </button>
  );
}

function MetricBar({ label, value, display }) {
  const width = Math.max(0, Math.min(100, Number(value || 0) * 100));
  return (
    <div className="metric-bar">
      <div className="metric-bar__label">
        <span>{label}</span>
        <strong>{display || formatPercent(value)}</strong>
      </div>
      <div className="metric-bar__track">
        <div className={`metric-bar__fill ${scoreClass(value)}`} style={{ width: `${width}%` }} />
      </div>
    </div>
  );
}

function BreakdownPanel({ detail, loading }) {
  if (loading) {
    return <div className="empty-panel">Loading breakdown...</div>;
  }
  if (!detail) {
    return <div className="empty-panel">Select a leaderboard row to inspect the score breakdown.</div>;
  }

  const weakestTasks = [...(detail.taskBreakdown || [])]
    .filter(task => task.attempts > 0)
    .sort((left, right) => (left.successRate ?? -1) - (right.successRate ?? -1))
    .slice(0, 5);
  const recentTasks = [...(detail.taskResults || [])].slice(0, 8);
  const recentGames = [...(detail.games || [])].slice(0, 5);

  return (
    <div className="breakdown-panel">
      <div className="breakdown-heading">
        <div>
          <span className="section-kicker">{detail.entityType}</span>
          <h2>{detail.displayName}</h2>
        </div>
        <span className={`score-pill ${scoreClass(detail.overallScore)}`}>{formatNumber(detail.overallScore, 3)}</span>
      </div>

      <div className="breakdown-stats">
        <MetricBar label="Gameplay" value={detail.categoryScores?.gameplay} />
        <MetricBar label="Reasoning" value={detail.categoryScores?.reasoning} />
        <MetricBar label="Operations" value={detail.categoryScores?.operations} />
      </div>

      <div className="metric-grid">
        {Object.entries(detail.rawMetrics || {}).map(([metric, value]) => (
          <div key={metric} className="metric-tile">
            <span>{metricLabel(metric)}</span>
            <strong>{formatMetric(metric, value)}</strong>
            <small>{formatPercent(detail.normalizedMetrics?.[metric])} normalized</small>
          </div>
        ))}
      </div>

      <section className="breakdown-section">
        <div className="section-title">
          <h3>Needs Attention</h3>
          <span>{weakestTasks.length} task groups</span>
        </div>
        <div className="task-list">
          {weakestTasks.length ? weakestTasks.map(task => (
            <div key={task.taskId} className="task-row">
              <div>
                <strong>{task.taskName}</strong>
                <small>{task.taskId}</small>
              </div>
              <span>{task.attempts} tries</span>
              <span className={`score-pill ${scoreClass(task.successRate)}`}>{formatPercent(task.successRate)}</span>
            </div>
          )) : <div className="empty-inline">No completed task attempts yet.</div>}
        </div>
      </section>

      <section className="breakdown-section">
        <div className="section-title">
          <h3>Recent Task Results</h3>
          <span>{recentTasks.length} latest</span>
        </div>
        <div className="task-list">
          {recentTasks.length ? recentTasks.map(task => (
            <div key={task.id} className="task-row">
              <div>
                <strong>{task.taskName || task.taskId}</strong>
                <small>{compactDate(task.createdAt)}</small>
              </div>
              <span>{formatNumber(task.score, 3)}</span>
              <span className={`score-pill ${task.success ? 'good' : 'bad'}`}>{task.success ? 'pass' : 'fail'}</span>
            </div>
          )) : <div className="empty-inline">No task history for this filter.</div>}
        </div>
      </section>

      <section className="breakdown-section">
        <div className="section-title">
          <h3>Games</h3>
          <span>{recentGames.length} recent</span>
        </div>
        <div className="game-strip">
          {recentGames.length ? recentGames.map(game => (
            <div key={game.gameId} className="game-chip">
              <strong>{game.won ? 'Win' : 'Loss'}</strong>
              <span>{game.finalVictoryPoints ?? 'N/A'} VP</span>
              <small>{game.gameId}</small>
            </div>
          )) : <div className="empty-inline">No completed games yet.</div>}
        </div>
      </section>
    </div>
  );
}

function LogRow({ entry }) {
  const score = eventScore(entry);
  const scoreText = score === null || score === undefined ? null : formatPercent(score);
  const line = taskScoreLine(entry);

  return (
    <details className="log-row">
      <summary>
        <span className={`event-dot ${eventClass(entry)}`} />
        <span className="log-main">
          <strong>{eventTitle(entry)}</strong>
          <small>{eventSubtitle(entry)}</small>
        </span>
        <span className={`score-pill ${eventClass(entry)}`}>{eventLabel(entry)}</span>
        <span>{scoreText || '-'}</span>
        <span>{entry.moveTimeMs ? `${(entry.moveTimeMs / 1000).toFixed(1)}s` : '-'}</span>
      </summary>
      <div className="log-detail">
        <p>{eventExplanation(entry)}</p>
        {line ? <p>{line}</p> : null}
        <dl>
          <div><dt>Time</dt><dd>{compactDate(entry.timestamp)}</dd></div>
          <div><dt>Run</dt><dd>{entry.runId || 'N/A'}</dd></div>
          <div><dt>Benchmark</dt><dd>{entry.benchmarkId || 'N/A'}</dd></div>
          <div><dt>Action</dt><dd>{entry.actionName || 'N/A'}</dd></div>
        </dl>
      </div>
    </details>
  );
}

function ObservationDeck({ serverUrl, onBack }) {
  const [overview, setOverview] = useState(null);
  const [leaderboard, setLeaderboard] = useState(null);
  const [detail, setDetail] = useState(null);
  const [liveLog, setLiveLog] = useState([]);
  const [loading, setLoading] = useState(true);
  const [detailLoading, setDetailLoading] = useState(false);
  const [error, setError] = useState(null);
  const [entityType, setEntityType] = useState('agent');
  const [logFilter, setLogFilter] = useState('all');
  const [filters, setFilters] = useState({
    benchmarkId: '',
    taskCategory: '',
    runId: '',
    search: '',
  });

  const filterQuery = useMemo(() => buildQuery(filters), [filters]);
  const selectedKey = detail?.entityKey || null;

  useEffect(() => {
    let cancelled = false;

    async function loadDeck() {
      try {
        setLoading(true);
        const [overviewRes, leaderboardRes] = await Promise.all([
          fetch(`${serverUrl}/api/benchmark/overview`),
          fetch(`${serverUrl}/api/benchmark/leaderboard?entityType=${entityType}${filterQuery ? `&${filterQuery}` : ''}`),
        ]);
        if (!overviewRes.ok || !leaderboardRes.ok) throw new Error('Failed to load benchmark deck');
        const [overviewData, leaderboardData] = await Promise.all([overviewRes.json(), leaderboardRes.json()]);
        if (!cancelled) {
          setOverview(overviewData);
          setLeaderboard(leaderboardData);
          setError(null);
        }
      } catch (fetchError) {
        if (!cancelled) setError(fetchError.message);
      } finally {
        if (!cancelled) setLoading(false);
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
        const response = await fetch(`${serverUrl}/api/benchmark/live-log?limit=80${filterQuery ? `&${filterQuery}` : ''}`);
        if (!response.ok) throw new Error('Failed to load benchmark log');
        const payload = await response.json();
        if (!cancelled) setLiveLog(payload.entries || []);
      } catch (fetchError) {
        if (!cancelled) setError(current => current || fetchError.message);
      }
    }

    loadLiveLog();
    const intervalId = setInterval(loadLiveLog, 3000);
    return () => {
      cancelled = true;
      clearInterval(intervalId);
    };
  }, [serverUrl, filterQuery]);

  const filtersMeta = overview?.filters || {
    benchmarkIds: [],
    runIds: [],
    taskCategories: [],
  };

  const handleFilterChange = event => {
    const { name, value } = event.target;
    setFilters(current => ({ ...current, [name]: value }));
  };

  const loadDetail = async entry => {
    setDetailLoading(true);
    try {
      const query = filterQuery ? `?${filterQuery}` : '';
      const endpoint = entry.entityType === 'player' ? 'players' : 'agents';
      const response = await fetch(`${serverUrl}/api/benchmark/${endpoint}/${encodeURIComponent(entry.entityKey)}${query}`);
      if (!response.ok) throw new Error('Failed to load leaderboard breakdown');
      setDetail(await response.json());
    } catch (fetchError) {
      setError(fetchError.message);
    } finally {
      setDetailLoading(false);
    }
  };

  const filteredLog = useMemo(() => liveLog.filter(entry => {
    if (logFilter === 'all') return true;
    if (logFilter === 'failures') return entry.eventType === 'task' ? !entry.success : entry.benchmarkTaskId && !entry.details?.benchmarkPassed;
    if (logFilter === 'tasks') return entry.eventType === 'task' || Boolean(entry.benchmarkTaskId);
    return true;
  }), [liveLog, logFilter]);

  const leaderboardEntries = leaderboard?.entries || [];

  return (
    <div className="observation-deck">
      <header className="deck-topbar">
        <div>
          <span className="section-kicker">Benchmark Deck</span>
          <h1>Leaderboard</h1>
        </div>
        <button className="deck-button" onClick={onBack}>Back To Lobby</button>
      </header>

      {error ? <div className="deck-error">{error}</div> : null}

      <section className="deck-controls" aria-label="Benchmark filters">
        <div className="segmented-control">
          <button className={entityType === 'agent' ? 'active' : ''} onClick={() => setEntityType('agent')}>Agents</button>
          <button className={entityType === 'player' ? 'active' : ''} onClick={() => setEntityType('player')}>Players</button>
        </div>
        <label>
          Benchmark
          <select name="benchmarkId" value={filters.benchmarkId} onChange={handleFilterChange}>
            <option value="">All</option>
            {(filtersMeta.benchmarkIds || []).map(value => <option key={value} value={value}>{value}</option>)}
          </select>
        </label>
        <label>
          Task Family
          <select name="taskCategory" value={filters.taskCategory} onChange={handleFilterChange}>
            <option value="">All</option>
            {(filtersMeta.taskCategories || []).map(category => (
              <option key={category.value} value={category.value}>{category.label}</option>
            ))}
          </select>
        </label>
        <label>
          Run
          <select name="runId" value={filters.runId} onChange={handleFilterChange}>
            <option value="">All</option>
            {(filtersMeta.runIds || []).map(run => <option key={run.runId} value={run.runId}>{run.label}</option>)}
          </select>
        </label>
        <label>
          Search
          <input name="search" value={filters.search} onChange={handleFilterChange} placeholder="Name, task, run" />
        </label>
      </section>

      {loading ? (
        <div className="empty-panel">Loading leaderboard...</div>
      ) : (
        <main className="deck-main">
          <section className="leaderboard-panel">
            <div className="panel-heading">
              <div>
                <span className="section-kicker">{leaderboardEntries.length} ranked</span>
                <h2>{entityType === 'agent' ? 'Agent Standings' : 'Player Standings'}</h2>
              </div>
              <div className="totals-line">
                <span>{overview?.totals?.games ?? 0} games</span>
                <span>{overview?.totals?.tasks ?? 0} tasks</span>
                <span>{overview?.totals?.telemetryRecords ?? 0} logs</span>
              </div>
            </div>
            <div className="benchmark-table">
              <div className="benchmark-table__head">
                <span>Rank</span>
                <span>Name</span>
                <span>Score</span>
                <span>Win</span>
                <span>Tasks</span>
                <span>VP</span>
                <span>Latency</span>
              </div>
              {leaderboardEntries.length ? leaderboardEntries.map(entry => (
                <LeaderboardRow
                  key={entry.entityKey}
                  entry={entry}
                  selected={selectedKey === entry.entityKey}
                  onSelect={loadDetail}
                />
              )) : <div className="empty-inline">No leaderboard entries match these filters.</div>}
            </div>
          </section>

          <aside className="breakdown-shell">
            <BreakdownPanel detail={detail} loading={detailLoading} />
          </aside>
        </main>
      )}

      <section className="log-panel">
        <div className="panel-heading">
          <div>
            <span className="section-kicker">{filteredLog.length} visible</span>
            <h2>Benchmark Log</h2>
          </div>
          <div className="segmented-control segmented-control--compact">
            <button className={logFilter === 'all' ? 'active' : ''} onClick={() => setLogFilter('all')}>All</button>
            <button className={logFilter === 'failures' ? 'active' : ''} onClick={() => setLogFilter('failures')}>Failures</button>
            <button className={logFilter === 'tasks' ? 'active' : ''} onClick={() => setLogFilter('tasks')}>Tasks</button>
          </div>
        </div>
        <div className="log-table">
          <div className="log-head">
            <span>Event</span>
            <span>Status</span>
            <span>Score</span>
            <span>Time</span>
          </div>
          {filteredLog.length ? filteredLog.map(entry => (
            <LogRow key={entry.id} entry={entry} />
          )) : <div className="empty-inline">No log entries match this view.</div>}
        </div>
      </section>
    </div>
  );
}

export default ObservationDeck;
