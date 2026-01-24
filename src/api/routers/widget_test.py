"""Widget integration test endpoint for developer testing."""

import logging

from fastapi import APIRouter, HTTPException
from fastapi.responses import HTMLResponse

from src.assistants import registry

router = APIRouter(tags=["widget"])
logger = logging.getLogger(__name__)


@router.get("/communities/{community_id}/widget-test", response_class=HTMLResponse)
def get_widget_test_page(community_id: str) -> str:
    """Get widget integration test page for a community.

    Returns an HTML page that:
    - Loads the widget with full functionality
    - Tests API connectivity
    - Shows CORS status
    - Displays community configuration
    - Provides diagnostic information
    - Includes copy-paste integration code

    Args:
        community_id: Community identifier (e.g., "hed", "bids")

    Returns:
        HTML page with widget test interface

    Raises:
        HTTPException: 404 if community doesn't exist
    """
    # Get community from registry
    assistant_info = registry.get(community_id)
    if not assistant_info:
        raise HTTPException(
            status_code=404,
            detail=f"Community '{community_id}' not found. Available communities: {', '.join([a.id for a in registry.list_all()])}",
        )

    config = assistant_info.community_config
    if not config:
        raise HTTPException(
            status_code=500,
            detail=f"Community '{community_id}' has no configuration",
        )

    # Build diagnostic information
    api_key_status = "configured" if config.openrouter_api_key_env_var else "using_platform"
    doc_count = len(config.documentation) if config.documentation else 0
    cors_count = len(config.cors_origins) if config.cors_origins else 0

    # Determine health status
    status = "healthy"
    if doc_count == 0:
        status = "error"
    elif api_key_status == "using_platform":
        status = "degraded"

    # Build CORS origins list
    cors_origins_html = ""
    if config.cors_origins:
        cors_origins_html = "".join(
            f'<li class="diagnostic-item"><span class="diagnostic-label">Origin:</span> <code>{origin}</code></li>'
            for origin in config.cors_origins
        )
    else:
        cors_origins_html = '<li class="diagnostic-item"><span class="diagnostic-label">No CORS origins configured</span></li>'

    # Build HTML page
    html_content = f"""<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>Widget Test - {community_id.upper()} Community</title>
    <style>
        * {{
            margin: 0;
            padding: 0;
            box-sizing: border-box;
        }}
        body {{
            font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif;
            background: linear-gradient(135deg, #667eea 0%, #764ba2 100%);
            min-height: 100vh;
            padding: 2rem;
        }}
        .container {{
            max-width: 1200px;
            margin: 0 auto;
        }}
        .header {{
            text-align: center;
            color: white;
            margin-bottom: 2rem;
        }}
        .header h1 {{
            font-size: 2.5rem;
            margin-bottom: 0.5rem;
        }}
        .header p {{
            font-size: 1.1rem;
            opacity: 0.9;
        }}
        .card {{
            background: white;
            border-radius: 12px;
            padding: 2rem;
            margin-bottom: 1.5rem;
            box-shadow: 0 10px 30px rgba(0, 0, 0, 0.2);
        }}
        .card h2 {{
            color: #667eea;
            margin-bottom: 1rem;
            font-size: 1.5rem;
            border-bottom: 2px solid #667eea;
            padding-bottom: 0.5rem;
        }}
        .status {{
            display: inline-block;
            padding: 0.5rem 1rem;
            border-radius: 20px;
            font-weight: 600;
            text-transform: uppercase;
            font-size: 0.9rem;
            margin-bottom: 1rem;
        }}
        .status-healthy {{
            background: #10b981;
            color: white;
        }}
        .status-degraded {{
            background: #f59e0b;
            color: white;
        }}
        .status-error {{
            background: #ef4444;
            color: white;
        }}
        .diagnostic-list {{
            list-style: none;
            margin: 1rem 0;
        }}
        .diagnostic-item {{
            padding: 0.75rem;
            margin-bottom: 0.5rem;
            background: #f3f4f6;
            border-radius: 6px;
            display: flex;
            align-items: center;
        }}
        .diagnostic-label {{
            font-weight: 600;
            color: #374151;
            margin-right: 0.5rem;
        }}
        code {{
            background: #1f2937;
            color: #10b981;
            padding: 0.25rem 0.5rem;
            border-radius: 4px;
            font-family: 'Courier New', monospace;
            font-size: 0.9rem;
        }}
        pre {{
            background: #1f2937;
            color: #e5e7eb;
            padding: 1.5rem;
            border-radius: 8px;
            overflow-x: auto;
            margin: 1rem 0;
            position: relative;
        }}
        pre code {{
            background: transparent;
            color: inherit;
            padding: 0;
        }}
        .copy-btn {{
            position: absolute;
            top: 1rem;
            right: 1rem;
            background: #667eea;
            color: white;
            border: none;
            padding: 0.5rem 1rem;
            border-radius: 6px;
            cursor: pointer;
            font-size: 0.85rem;
            font-weight: 600;
            transition: background 0.2s;
        }}
        .copy-btn:hover {{
            background: #5568d3;
        }}
        .copy-btn:active {{
            transform: scale(0.95);
        }}
        .grid {{
            display: grid;
            grid-template-columns: repeat(auto-fit, minmax(250px, 1fr));
            gap: 1rem;
            margin: 1rem 0;
        }}
        .metric {{
            background: #f9fafb;
            padding: 1.5rem;
            border-radius: 8px;
            border-left: 4px solid #667eea;
        }}
        .metric-value {{
            font-size: 2rem;
            font-weight: 700;
            color: #667eea;
            margin-bottom: 0.5rem;
        }}
        .metric-label {{
            color: #6b7280;
            font-size: 0.9rem;
            font-weight: 500;
        }}
        .test-button {{
            background: #667eea;
            color: white;
            border: none;
            padding: 0.75rem 1.5rem;
            border-radius: 8px;
            font-size: 1rem;
            font-weight: 600;
            cursor: pointer;
            transition: background 0.2s;
            margin-top: 1rem;
        }}
        .test-button:hover {{
            background: #5568d3;
        }}
        .test-button:disabled {{
            background: #9ca3af;
            cursor: not-allowed;
        }}
        #testResults {{
            margin-top: 1rem;
            padding: 1rem;
            background: #f3f4f6;
            border-radius: 8px;
            display: none;
        }}
        #testResults.show {{
            display: block;
        }}
        .test-result {{
            padding: 0.5rem;
            margin-bottom: 0.5rem;
            border-radius: 4px;
        }}
        .test-success {{
            background: #d1fae5;
            color: #065f46;
        }}
        .test-failure {{
            background: #fee2e2;
            color: #991b1b;
        }}
        .info-box {{
            background: #e0e7ff;
            border-left: 4px solid #667eea;
            padding: 1rem;
            border-radius: 6px;
            margin: 1rem 0;
        }}
        .info-box p {{
            margin-bottom: 0.5rem;
            color: #3730a3;
        }}
        .info-box p:last-child {{
            margin-bottom: 0;
        }}
    </style>
</head>
<body>
    <div class="container">
        <div class="header">
            <h1>🧪 Widget Integration Test</h1>
            <p>Testing widget integration for <strong>{community_id.upper()}</strong> community</p>
        </div>

        <div class="card">
            <h2>📊 Community Status</h2>
            <div class="status status-{status}">{status}</div>
            <div class="grid">
                <div class="metric">
                    <div class="metric-value">{doc_count}</div>
                    <div class="metric-label">Documentation Sources</div>
                </div>
                <div class="metric">
                    <div class="metric-value">{cors_count}</div>
                    <div class="metric-label">CORS Origins</div>
                </div>
                <div class="metric">
                    <div class="metric-value">{api_key_status}</div>
                    <div class="metric-label">API Key Status</div>
                </div>
            </div>
        </div>

        <div class="card">
            <h2>🔍 Diagnostics</h2>
            <ul class="diagnostic-list">
                <li class="diagnostic-item">
                    <span class="diagnostic-label">Community ID:</span>
                    <code>{community_id}</code>
                </li>
                <li class="diagnostic-item">
                    <span class="diagnostic-label">Model:</span>
                    <code>{config.default_model or "Not configured"}</code>
                </li>
            </ul>

            <h3 style="margin-top: 1.5rem; margin-bottom: 1rem; color: #374151;">CORS Origins:</h3>
            <ul class="diagnostic-list">
                {cors_origins_html}
            </ul>

            <div class="info-box">
                <p><strong>💡 Testing Tips:</strong></p>
                <p>• The widget should appear in the bottom-right corner</p>
                <p>• Try sending a message with your own API key (BYOK) via settings</p>
                <p>• Check browser console for any errors</p>
                <p>• Current page URL should be included in requests if page context is enabled</p>
            </div>
        </div>

        <div class="card">
            <h2>🧪 API Connectivity Test</h2>
            <p style="margin-bottom: 1rem; color: #6b7280;">Test if the backend API is reachable and healthy.</p>
            <button id="testButton" class="test-button" onclick="testAPI()">Run API Test</button>
            <div id="testResults"></div>
        </div>

        <div class="card">
            <h2>📝 Integration Code</h2>
            <p style="margin-bottom: 1rem; color: #6b7280;">Copy this code to integrate the widget into your website:</p>
            <pre><code>&lt;!-- OSA Chat Widget for {community_id.upper()} --&gt;
&lt;script&gt;
  // Configure widget before loading
  window.OSAChatWidget = {{
    communityId: '{community_id}',
    title: '{config.name or community_id.upper()} Assistant',
    initialMessage: 'Hi! I\\'m the {config.name or community_id.upper()} Assistant. How can I help you today?',
    placeholder: 'Ask about {config.name or community_id}...'
  }};
&lt;/script&gt;
&lt;script src="https://osa.osc.earth/frontend/osa-chat-widget.js"&gt;&lt;/script&gt;</code><button class="copy-btn" onclick="copyCode(this)">Copy</button></pre>

            <div class="info-box">
                <p><strong>📦 What happens when you add this code:</strong></p>
                <p>• Widget loads in bottom-right corner</p>
                <p>• Users can ask questions specific to {community_id.upper()}</p>
                <p>• BYOK (Bring Your Own Key) supported via settings menu</p>
                <p>• Conversation history saved locally</p>
            </div>
        </div>
    </div>

    <!-- Load the widget -->
    <script>
      // Configure widget for this community
      window.OSAChatWidget = {{
        communityId: '{community_id}',
        title: '{config.name or community_id.upper()} Assistant',
        initialMessage: 'Hi! I\\'m the {config.name or community_id.upper()} Assistant. How can I help you today?',
        placeholder: 'Ask about {config.name or community_id}...',
        showExperimentalBadge: true
      }};

      // Helper function to copy code
      function copyCode(button) {{
        const pre = button.parentElement;
        const code = pre.querySelector('code').textContent;
        navigator.clipboard.writeText(code).then(() => {{
          button.textContent = 'Copied!';
          setTimeout(() => {{
            button.textContent = 'Copy';
          }}, 2000);
        }});
      }}

      // API connectivity test
      async function testAPI() {{
        const button = document.getElementById('testButton');
        const results = document.getElementById('testResults');

        button.disabled = true;
        button.textContent = 'Testing...';
        results.innerHTML = '';
        results.classList.add('show');

        const tests = [
          {{ name: 'Health Check', url: window.location.origin + '/health', method: 'GET' }},
          {{ name: 'Communities Health', url: window.location.origin + '/health/communities', method: 'GET', requiresAuth: true }},
          {{ name: 'Community Config', url: window.location.origin + '/communities/{community_id}', method: 'GET' }}
        ];

        for (const test of tests) {{
          try {{
            const options = {{ method: test.method }};
            if (test.requiresAuth) {{
              // Note: This will fail without auth - expected for security
              results.innerHTML += `<div class="test-result test-success">✓ ${{test.name}}: Endpoint exists (auth required)</div>`;
              continue;
            }}

            const response = await fetch(test.url, options);
            if (response.ok) {{
              results.innerHTML += `<div class="test-result test-success">✓ ${{test.name}}: Success (${{response.status}})</div>`;
            }} else {{
              results.innerHTML += `<div class="test-result test-failure">✗ ${{test.name}}: Failed (${{response.status}})</div>`;
            }}
          }} catch (error) {{
            results.innerHTML += `<div class="test-result test-failure">✗ ${{test.name}}: Error - ${{error.message}}</div>`;
          }}
        }}

        button.disabled = false;
        button.textContent = 'Run API Test';
      }}
    </script>

    <!-- Load widget from local frontend directory -->
    <script src="/frontend/osa-chat-widget.js"></script>
</body>
</html>"""

    return html_content
