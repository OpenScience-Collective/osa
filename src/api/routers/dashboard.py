"""Community dashboard page.

Single-page dashboard served via FastAPI HTMLResponse that showcases
community activity with public data and optional admin-only sections.
Uses Chart.js via CDN for charts.
"""

import logging

from fastapi import APIRouter
from fastapi.responses import HTMLResponse

logger = logging.getLogger(__name__)

router = APIRouter(tags=["Dashboard"])

DASHBOARD_HTML = """<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>Open Science Assistant - Community Dashboard</title>
    <script src="https://cdn.jsdelivr.net/npm/chart.js@4.4.7/dist/chart.umd.min.js"></script>
    <style>
        * {
            margin: 0;
            padding: 0;
            box-sizing: border-box;
        }
        body {
            font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif;
            background: linear-gradient(135deg, #667eea 0%, #764ba2 100%);
            min-height: 100vh;
            padding: 2rem;
            color: #374151;
        }
        .container {
            max-width: 1200px;
            margin: 0 auto;
        }
        .header {
            text-align: center;
            color: white;
            margin-bottom: 2rem;
        }
        .header h1 {
            font-size: 2.2rem;
            margin-bottom: 0.5rem;
        }
        .header p {
            font-size: 1.1rem;
            opacity: 0.9;
        }
        .card {
            background: white;
            border-radius: 12px;
            padding: 2rem;
            margin-bottom: 1.5rem;
            box-shadow: 0 10px 30px rgba(0, 0, 0, 0.2);
        }
        .card h2 {
            color: #667eea;
            margin-bottom: 1rem;
            font-size: 1.4rem;
            border-bottom: 2px solid #667eea;
            padding-bottom: 0.5rem;
        }
        .overview-grid {
            display: grid;
            grid-template-columns: repeat(auto-fit, minmax(200px, 1fr));
            gap: 1rem;
            margin-bottom: 1rem;
        }
        .metric {
            background: #f9fafb;
            padding: 1.5rem;
            border-radius: 8px;
            border-left: 4px solid #667eea;
            text-align: center;
        }
        .metric-value {
            font-size: 2.2rem;
            font-weight: 700;
            color: #667eea;
            margin-bottom: 0.25rem;
        }
        .metric-label {
            color: #6b7280;
            font-size: 0.85rem;
            font-weight: 500;
            text-transform: uppercase;
            letter-spacing: 0.05em;
        }

        /* Tabs */
        .tab-bar {
            display: flex;
            gap: 0.5rem;
            margin-bottom: 1.5rem;
            flex-wrap: wrap;
        }
        .tab-btn {
            padding: 0.6rem 1.2rem;
            border: 2px solid #667eea;
            background: white;
            color: #667eea;
            border-radius: 8px;
            cursor: pointer;
            font-weight: 600;
            font-size: 0.9rem;
            transition: all 0.2s;
        }
        .tab-btn:hover {
            background: #f0f0ff;
        }
        .tab-btn.active {
            background: #667eea;
            color: white;
        }

        /* Period toggle */
        .period-toggle {
            display: flex;
            gap: 0.5rem;
            margin-bottom: 1rem;
        }
        .period-btn {
            padding: 0.4rem 1rem;
            border: 1px solid #d1d5db;
            background: white;
            color: #374151;
            border-radius: 6px;
            cursor: pointer;
            font-size: 0.85rem;
            transition: all 0.2s;
        }
        .period-btn:hover {
            border-color: #667eea;
        }
        .period-btn.active {
            background: #667eea;
            color: white;
            border-color: #667eea;
        }

        /* Status badges */
        .status-badge {
            display: inline-block;
            padding: 0.3rem 0.8rem;
            border-radius: 20px;
            font-weight: 600;
            font-size: 0.8rem;
            text-transform: uppercase;
        }
        .status-healthy { background: #d1fae5; color: #065f46; }
        .status-degraded { background: #fef3c7; color: #92400e; }
        .status-error { background: #fee2e2; color: #991b1b; }
        .status-unknown { background: #f3f4f6; color: #6b7280; }

        /* Charts */
        .chart-container {
            position: relative;
            height: 300px;
            margin: 1rem 0;
        }
        .chart-row {
            display: grid;
            grid-template-columns: 1fr 1fr;
            gap: 1.5rem;
            margin: 1rem 0;
        }
        @media (max-width: 768px) {
            .chart-row { grid-template-columns: 1fr; }
        }

        /* Admin section */
        .admin-section {
            display: none;
            margin-top: 1.5rem;
            padding-top: 1.5rem;
            border-top: 2px dashed #e5e7eb;
        }
        .admin-section.visible { display: block; }
        .admin-section h3 {
            color: #667eea;
            margin-bottom: 1rem;
            font-size: 1.1rem;
        }
        .admin-input-row {
            display: flex;
            gap: 0.5rem;
            align-items: center;
            margin-top: 1rem;
        }
        .admin-input {
            padding: 0.6rem 1rem;
            border: 2px solid #d1d5db;
            border-radius: 8px;
            font-size: 0.9rem;
            flex: 1;
            max-width: 400px;
        }
        .admin-input:focus {
            outline: none;
            border-color: #667eea;
        }
        .admin-btn {
            padding: 0.6rem 1.2rem;
            background: #667eea;
            color: white;
            border: none;
            border-radius: 8px;
            cursor: pointer;
            font-weight: 600;
            font-size: 0.9rem;
            transition: background 0.2s;
        }
        .admin-btn:hover { background: #5568d3; }
        .admin-status {
            font-size: 0.85rem;
            color: #6b7280;
            margin-left: 0.5rem;
        }
        .admin-status.success { color: #059669; }
        .admin-status.error { color: #dc2626; }

        /* Sync info */
        .sync-info {
            display: grid;
            grid-template-columns: repeat(auto-fit, minmax(250px, 1fr));
            gap: 1rem;
            margin: 1rem 0;
        }
        .sync-item {
            padding: 1rem;
            background: #f9fafb;
            border-radius: 8px;
            border-left: 3px solid #667eea;
        }
        .sync-item-label {
            font-weight: 600;
            color: #374151;
            margin-bottom: 0.25rem;
        }
        .sync-item-value {
            color: #6b7280;
            font-size: 0.9rem;
        }

        /* Loading */
        .loading {
            text-align: center;
            color: #6b7280;
            padding: 2rem;
        }
        .loading::after {
            content: '';
            display: inline-block;
            width: 1.2rem;
            height: 1.2rem;
            border: 2px solid #667eea;
            border-top-color: transparent;
            border-radius: 50%;
            animation: spin 0.8s linear infinite;
            margin-left: 0.5rem;
            vertical-align: middle;
        }
        @keyframes spin { to { transform: rotate(360deg); } }

        .error-msg {
            color: #dc2626;
            background: #fee2e2;
            padding: 1rem;
            border-radius: 8px;
            margin: 1rem 0;
        }

        /* Community detail */
        .community-header {
            display: flex;
            align-items: center;
            gap: 1rem;
            margin-bottom: 1rem;
        }
        .community-header h3 {
            font-size: 1.2rem;
            color: #374151;
        }
    </style>
</head>
<body>
    <div class="container">
        <div class="header">
            <h1>Open Science Assistant</h1>
            <p>Community Dashboard</p>
        </div>

        <!-- Overview Cards -->
        <div class="card">
            <h2>Overview</h2>
            <div id="overviewContent" class="loading">Loading metrics...</div>
        </div>

        <!-- Community Section -->
        <div class="card">
            <h2>Communities</h2>
            <div id="tabBar" class="tab-bar"></div>
            <div id="communityContent"></div>
        </div>

        <!-- Admin Key Input -->
        <div class="card">
            <h2>Admin Access</h2>
            <p style="margin-bottom: 0.5rem; color: #6b7280;">
                Enter admin API key to view token usage, costs, and model details.
            </p>
            <div class="admin-input-row">
                <input type="password" id="adminKeyInput" class="admin-input"
                       placeholder="Admin API key..." autocomplete="off">
                <button id="adminKeyBtn" class="admin-btn" onclick="unlockAdmin()">Unlock</button>
                <span id="adminStatus" class="admin-status"></span>
            </div>
        </div>
    </div>

    <script>
    // ---------------------------------------------------------------------------
    // State
    // ---------------------------------------------------------------------------
    const API_BASE = window.location.origin;
    let adminKey = null;         // kept in memory only
    let overviewData = null;
    let activeCommunity = null;
    let activePeriod = 'daily';
    let usageChartInstance = null;
    let toolsChartInstance = null;
    let adminTokenChartInstance = null;
    let adminCostChartInstance = null;

    // Chart.js color palette
    const COLORS = [
        '#667eea', '#764ba2', '#10b981', '#f59e0b', '#ef4444',
        '#8b5cf6', '#06b6d4', '#ec4899', '#84cc16', '#f97316'
    ];

    // ---------------------------------------------------------------------------
    // Initialization
    // ---------------------------------------------------------------------------
    document.addEventListener('DOMContentLoaded', () => {
        loadOverview();
    });

    // ---------------------------------------------------------------------------
    // Public Data
    // ---------------------------------------------------------------------------
    async function loadOverview() {
        const el = document.getElementById('overviewContent');
        try {
            const resp = await fetch(`${API_BASE}/metrics/public/overview`);
            if (!resp.ok) throw new Error(`HTTP ${resp.status}`);
            overviewData = await resp.json();
            renderOverview(overviewData);
        } catch (err) {
            el.className = '';
            el.innerHTML = `<div class="error-msg">Failed to load metrics: ${err.message}</div>`;
        }
    }

    function renderOverview(data) {
        const el = document.getElementById('overviewContent');
        const successRate = data.total_requests > 0
            ? ((1 - data.error_rate) * 100).toFixed(1)
            : '0.0';

        el.className = '';
        el.innerHTML = `
            <div class="overview-grid">
                <div class="metric">
                    <div class="metric-value">${data.total_requests.toLocaleString()}</div>
                    <div class="metric-label">Questions Answered</div>
                </div>
                <div class="metric">
                    <div class="metric-value">${data.communities_active}</div>
                    <div class="metric-label">Active Communities</div>
                </div>
                <div class="metric">
                    <div class="metric-value">${successRate}%</div>
                    <div class="metric-label">Success Rate</div>
                </div>
            </div>
        `;

        // Build community tabs
        renderTabs(data.communities);
    }

    function renderTabs(communities) {
        const tabBar = document.getElementById('tabBar');
        tabBar.innerHTML = '';

        if (!communities || communities.length === 0) {
            tabBar.innerHTML = '<span style="color:#6b7280;">No community data yet.</span>';
            return;
        }

        communities.forEach((c, idx) => {
            const btn = document.createElement('button');
            btn.className = 'tab-btn' + (idx === 0 ? ' active' : '');
            btn.textContent = c.community_id.toUpperCase();
            btn.dataset.community = c.community_id;
            btn.onclick = () => selectCommunity(c.community_id);
            tabBar.appendChild(btn);
        });

        // Auto-select first community
        selectCommunity(communities[0].community_id);
    }

    async function selectCommunity(communityId) {
        activeCommunity = communityId;

        // Update tab styling
        document.querySelectorAll('.tab-btn').forEach(btn => {
            btn.classList.toggle('active', btn.dataset.community === communityId);
        });

        const el = document.getElementById('communityContent');
        el.innerHTML = '<div class="loading">Loading community data...</div>';

        try {
            // Fetch community summary, usage, and sync data in parallel
            const [summaryResp, usageResp, syncResp, healthResp] = await Promise.all([
                fetch(`${API_BASE}/metrics/public/${communityId}`),
                fetch(`${API_BASE}/metrics/public/${communityId}/usage?period=${activePeriod}`),
                fetch(`${API_BASE}/sync/status`).catch(() => null),
                fetch(`${API_BASE}/sync/health`).catch(() => null),
            ]);

            if (!summaryResp.ok) throw new Error(`HTTP ${summaryResp.status}`);
            if (!usageResp.ok) throw new Error(`HTTP ${usageResp.status}`);

            const summary = await summaryResp.json();
            const usage = await usageResp.json();
            const sync = syncResp && syncResp.ok ? await syncResp.json() : null;
            const health = healthResp && healthResp.ok ? await healthResp.json() : null;

            renderCommunityDetail(summary, usage, sync, health, communityId);

            // If admin key is set, fetch and render admin data
            if (adminKey) {
                loadAdminData(communityId);
            }
        } catch (err) {
            el.innerHTML = `<div class="error-msg">Failed to load data for ${communityId}: ${err.message}</div>`;
        }
    }

    function renderCommunityDetail(summary, usage, sync, health, communityId) {
        const el = document.getElementById('communityContent');
        const successRate = summary.total_requests > 0
            ? ((1 - summary.error_rate) * 100).toFixed(1)
            : '0.0';

        // Determine health status
        let healthStatus = 'unknown';
        let healthLabel = 'Unknown';
        if (health && health.status) {
            healthStatus = health.status;
            healthLabel = health.status.charAt(0).toUpperCase() + health.status.slice(1);
        }

        el.innerHTML = `
            <div class="community-header">
                <h3>${communityId.toUpperCase()} Community</h3>
                <span class="status-badge status-${healthStatus}">${healthLabel}</span>
            </div>

            <div class="overview-grid">
                <div class="metric">
                    <div class="metric-value">${summary.total_requests.toLocaleString()}</div>
                    <div class="metric-label">Requests</div>
                </div>
                <div class="metric">
                    <div class="metric-value">${successRate}%</div>
                    <div class="metric-label">Success Rate</div>
                </div>
            </div>

            ${renderSyncInfo(sync, communityId)}

            <div class="period-toggle">
                <button class="period-btn ${activePeriod === 'daily' ? 'active' : ''}" onclick="changePeriod('daily')">Daily</button>
                <button class="period-btn ${activePeriod === 'weekly' ? 'active' : ''}" onclick="changePeriod('weekly')">Weekly</button>
                <button class="period-btn ${activePeriod === 'monthly' ? 'active' : ''}" onclick="changePeriod('monthly')">Monthly</button>
            </div>

            <div class="chart-row">
                <div>
                    <h3 style="color:#374151;margin-bottom:0.5rem;">Requests Over Time</h3>
                    <div class="chart-container">
                        <canvas id="usageChart"></canvas>
                    </div>
                </div>
                <div>
                    <h3 style="color:#374151;margin-bottom:0.5rem;">Top Tools</h3>
                    <div class="chart-container">
                        <canvas id="toolsChart"></canvas>
                    </div>
                </div>
            </div>

            <div class="admin-section" id="adminSection">
                <h3>Admin: Token & Cost Details</h3>
                <div class="chart-row">
                    <div>
                        <h3 style="color:#374151;margin-bottom:0.5rem;font-size:1rem;">Token Usage by Model</h3>
                        <div class="chart-container">
                            <canvas id="adminTokenChart"></canvas>
                        </div>
                    </div>
                    <div>
                        <h3 style="color:#374151;margin-bottom:0.5rem;font-size:1rem;">Key Source Breakdown</h3>
                        <div class="chart-container">
                            <canvas id="adminCostChart"></canvas>
                        </div>
                    </div>
                </div>
            </div>
        `;

        renderUsageChart(usage);
        renderToolsChart(summary.top_tools);
    }

    function renderSyncInfo(sync, communityId) {
        if (!sync) return '';

        // Find GitHub sync info from the sync status
        let lastGithub = 'N/A';
        let lastPapers = 'N/A';

        if (sync.github && sync.github.repos) {
            const times = Object.values(sync.github.repos)
                .map(r => r.last_sync)
                .filter(Boolean);
            if (times.length > 0) {
                const latest = times.sort().reverse()[0];
                lastGithub = formatRelativeTime(latest);
            }
        }

        if (sync.papers && sync.papers.sources) {
            const times = Object.values(sync.papers.sources)
                .map(s => s.last_sync)
                .filter(Boolean);
            if (times.length > 0) {
                const latest = times.sort().reverse()[0];
                lastPapers = formatRelativeTime(latest);
            }
        }

        return `
            <div class="sync-info">
                <div class="sync-item">
                    <div class="sync-item-label">GitHub Sync</div>
                    <div class="sync-item-value">${lastGithub}</div>
                </div>
                <div class="sync-item">
                    <div class="sync-item-label">Papers Sync</div>
                    <div class="sync-item-value">${lastPapers}</div>
                </div>
            </div>
        `;
    }

    function formatRelativeTime(isoStr) {
        try {
            const dt = new Date(isoStr);
            const now = new Date();
            const diffMs = now - dt;
            const diffHrs = Math.floor(diffMs / 3600000);
            if (diffHrs < 1) return 'Less than 1 hour ago';
            if (diffHrs < 24) return `${diffHrs} hour${diffHrs === 1 ? '' : 's'} ago`;
            const diffDays = Math.floor(diffHrs / 24);
            return `${diffDays} day${diffDays === 1 ? '' : 's'} ago`;
        } catch {
            return isoStr || 'N/A';
        }
    }

    // ---------------------------------------------------------------------------
    // Charts
    // ---------------------------------------------------------------------------
    function renderUsageChart(usage) {
        if (usageChartInstance) usageChartInstance.destroy();
        const canvas = document.getElementById('usageChart');
        if (!canvas || !usage.buckets || usage.buckets.length === 0) return;

        const labels = usage.buckets.map(b => b.bucket);
        const requests = usage.buckets.map(b => b.requests);
        const errors = usage.buckets.map(b => b.errors);

        usageChartInstance = new Chart(canvas, {
            type: 'bar',
            data: {
                labels,
                datasets: [
                    {
                        label: 'Requests',
                        data: requests,
                        backgroundColor: 'rgba(102, 126, 234, 0.7)',
                        borderColor: '#667eea',
                        borderWidth: 1,
                    },
                    {
                        label: 'Errors',
                        data: errors,
                        backgroundColor: 'rgba(239, 68, 68, 0.7)',
                        borderColor: '#ef4444',
                        borderWidth: 1,
                    }
                ]
            },
            options: {
                responsive: true,
                maintainAspectRatio: false,
                plugins: { legend: { position: 'top' } },
                scales: {
                    y: { beginAtZero: true, ticks: { stepSize: 1 } }
                }
            }
        });
    }

    function renderToolsChart(topTools) {
        if (toolsChartInstance) toolsChartInstance.destroy();
        const canvas = document.getElementById('toolsChart');
        if (!canvas || !topTools || topTools.length === 0) {
            if (canvas) {
                const ctx = canvas.getContext('2d');
                ctx.font = '14px sans-serif';
                ctx.fillStyle = '#6b7280';
                ctx.textAlign = 'center';
                ctx.fillText('No tool usage data yet', canvas.width / 2, canvas.height / 2);
            }
            return;
        }

        toolsChartInstance = new Chart(canvas, {
            type: 'bar',
            data: {
                labels: topTools.map(t => t.tool),
                datasets: [{
                    label: 'Calls',
                    data: topTools.map(t => t.count),
                    backgroundColor: COLORS.slice(0, topTools.length),
                    borderWidth: 0,
                }]
            },
            options: {
                responsive: true,
                maintainAspectRatio: false,
                indexAxis: 'y',
                plugins: { legend: { display: false } },
                scales: {
                    x: { beginAtZero: true, ticks: { stepSize: 1 } }
                }
            }
        });
    }

    function changePeriod(period) {
        activePeriod = period;
        if (activeCommunity) selectCommunity(activeCommunity);
    }

    // ---------------------------------------------------------------------------
    // Admin
    // ---------------------------------------------------------------------------
    async function unlockAdmin() {
        const input = document.getElementById('adminKeyInput');
        const status = document.getElementById('adminStatus');
        const key = input.value.trim();
        if (!key) return;

        status.textContent = 'Verifying...';
        status.className = 'admin-status';

        try {
            // Verify key by calling admin-only endpoint
            const resp = await fetch(`${API_BASE}/metrics/overview`, {
                headers: { 'X-API-Key': key }
            });
            if (resp.ok) {
                adminKey = key;
                input.value = '';
                status.textContent = 'Unlocked';
                status.className = 'admin-status success';
                // Refresh current community to show admin data
                if (activeCommunity) loadAdminData(activeCommunity);
            } else if (resp.status === 403 || resp.status === 401) {
                status.textContent = 'Invalid key';
                status.className = 'admin-status error';
            } else {
                status.textContent = `Error (${resp.status})`;
                status.className = 'admin-status error';
            }
        } catch (err) {
            status.textContent = 'Connection error';
            status.className = 'admin-status error';
        }
    }

    async function loadAdminData(communityId) {
        if (!adminKey) return;

        const section = document.getElementById('adminSection');
        if (!section) return;
        section.classList.add('visible');

        try {
            const resp = await fetch(
                `${API_BASE}/metrics/tokens?community_id=${communityId}`,
                { headers: { 'X-API-Key': adminKey } }
            );
            if (!resp.ok) {
                if (resp.status === 401 || resp.status === 403) {
                    adminKey = null;
                    section.classList.remove('visible');
                    const status = document.getElementById('adminStatus');
                    status.textContent = 'Key expired or invalid';
                    status.className = 'admin-status error';
                }
                return;
            }
            const data = await resp.json();
            renderAdminCharts(data);
        } catch {
            // Silently degrade if admin data fails
        }
    }

    function renderAdminCharts(data) {
        // Token usage by model (doughnut)
        if (adminTokenChartInstance) adminTokenChartInstance.destroy();
        const tokenCanvas = document.getElementById('adminTokenChart');
        if (tokenCanvas && data.by_model && data.by_model.length > 0) {
            adminTokenChartInstance = new Chart(tokenCanvas, {
                type: 'doughnut',
                data: {
                    labels: data.by_model.map(m => m.model || 'Unknown'),
                    datasets: [{
                        data: data.by_model.map(m => m.total_tokens),
                        backgroundColor: COLORS.slice(0, data.by_model.length),
                    }]
                },
                options: {
                    responsive: true,
                    maintainAspectRatio: false,
                    plugins: {
                        legend: { position: 'bottom' },
                        tooltip: {
                            callbacks: {
                                label: function(ctx) {
                                    const model = data.by_model[ctx.dataIndex];
                                    return `${model.model}: ${model.total_tokens.toLocaleString()} tokens ($${model.estimated_cost.toFixed(4)})`;
                                }
                            }
                        }
                    }
                }
            });
        }

        // Key source breakdown (doughnut)
        if (adminCostChartInstance) adminCostChartInstance.destroy();
        const costCanvas = document.getElementById('adminCostChart');
        if (costCanvas && data.by_key_source && data.by_key_source.length > 0) {
            adminCostChartInstance = new Chart(costCanvas, {
                type: 'doughnut',
                data: {
                    labels: data.by_key_source.map(k => k.key_source || 'Unknown'),
                    datasets: [{
                        data: data.by_key_source.map(k => k.requests),
                        backgroundColor: ['#667eea', '#10b981', '#f59e0b', '#ef4444'],
                    }]
                },
                options: {
                    responsive: true,
                    maintainAspectRatio: false,
                    plugins: {
                        legend: { position: 'bottom' },
                        tooltip: {
                            callbacks: {
                                label: function(ctx) {
                                    const src = data.by_key_source[ctx.dataIndex];
                                    return `${src.key_source}: ${src.requests} requests ($${src.estimated_cost.toFixed(4)})`;
                                }
                            }
                        }
                    }
                }
            });
        }
    }
    </script>
</body>
</html>"""


@router.get("/dashboard", response_class=HTMLResponse)
async def dashboard() -> str:
    """Serve the community dashboard page.

    Returns an HTML page with:
    - Overview cards (total questions, active communities, success rate)
    - Community tabs with per-community charts
    - Time period toggle (daily/weekly/monthly)
    - Admin key input to unlock sensitive sections
    """
    return DASHBOARD_HTML
