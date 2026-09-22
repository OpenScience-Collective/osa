/**
 * OSA Chat Widget
 * A floating chat assistant for Open Science tools (HED, BIDS, etc.)
 * Connects to OSA Cloudflare Worker for secure access.
 * Version: 2026-01-26-v2
 */

(function() {
  'use strict';

  // Auto-detect environment based on hostname
  // Production: demo.osc.earth routes to production API
  // Development: develop-demo.osc.earth and other *-demo.osc.earth subdomains
  //              route to dev API for testing without affecting production data
  // Single-level subdomains (develop-demo vs develop.demo) avoid SSL cert issues
  //
  // This is the ONE place hostname -> environment -> API endpoint is resolved.
  // frontend/index.html reuses it via OSAChatWidget.getConfig().apiEndpoint
  // instead of re-deriving it; do not duplicate this logic elsewhere (#437).
  const hostname = window.location.hostname;
  const isProduction = hostname === 'demo.osc.earth' || hostname === 'osa-demo.pages.dev';
  const isDev = !isProduction && (
                hostname.endsWith('-demo.osc.earth') ||
                hostname.endsWith('.osa-demo.pages.dev') ||
                hostname.includes('localhost') ||
                hostname.includes('127.0.0.1'));

  // Configuration (can be customized via OSAChatWidget.setConfig)
  const CONFIG = {
    // Community identifier - determines which assistant to use
    // Endpoints will be: /${communityId}/ask, /${communityId}/chat
    communityId: 'hed',
    // Route to dev worker for all non-production deployments (preview branches, localhost)
    // or production worker for demo.osc.earth (production only).
    // The worker sits in front of the FastAPI backend (api.osc.earth) as the
    // security proxy (Turnstile, rate limiting, CORS, backend API key), so
    // this always points at the worker's own product-owned host, never directly
    // at the backend. widget.osc.earth/osa (prod) and
    // develop-widget.osc.earth/osa (dev) are a stable, product-owned host
    // for widgets in general (see #437); "widget" is a generic host shared
    // by future widgets, each under its own path, so this one lives at
    // "/osa" rather than consuming a subdomain of its own. The previous
    // account-scoped Cloudflare subdomain is retired.
    apiEndpoint: isDev
      ? 'https://develop-widget.osc.earth/osa'
      : 'https://widget.osc.earth/osa',
    storageKey: 'osa-chat-history-hed',
    // Turnstile: disabled for now (not set up yet)
    turnstileSiteKey: null,
    // Customizable branding (generic defaults; community-specific values loaded from API)
    title: 'Open Science Assistant',
    initialMessage: 'Hi! I\'m the Open Science Assistant. How can I help you today?',
    placeholder: 'Ask a question...',
    suggestedQuestions: [],
    // Per-page instructions for the assistant (set by widget embedder)
    // These are sent to the backend as part of page_context
    widgetInstructions: null,
    showExperimentalBadge: true,
    repoUrl: 'https://osc.earth/osa/',
    repoName: 'Open Science Assistant',
    // Page context awareness - sends current page URL/title to help the assistant
    // provide more contextually relevant answers
    allowPageContext: true,  // Show the checkbox option
    pageContextDefaultEnabled: true,  // Default state of checkbox
    pageContextStorageKey: 'osa-page-context-enabled',
    pageContextLabel: 'Share page URL to help answer questions',
    // AI disclaimer shown above the footer
    disclaimerEnabled: true,
    disclaimerText: 'This is an AI assistant and may make mistakes.',
    disclaimerColor: '#9a3412',
    disclaimerBackground: '#fff7ed',
    // Fullscreen mode (for pop-out windows)
    fullscreen: false,
    // Streaming responses - enable progressive text display for better UX
    streamingEnabled: true,
    // Where this script was loaded from, for a copy that runs inline (the
    // pop-out) and so cannot tell. The browser runtime is found next to it.
    widgetScriptUrl: null
  };

  // Log environment for debugging
  if (isDev) {
    console.log('[OSA] Using DEV backend:', CONFIG.apiEndpoint);
  }

  // Fallback model options for the settings dropdown, used only until
  // fetchCommunityConfig's offered_models response arrives (or if a
  // community config ever omits that field). The live list is the source
  // of truth; see offeredModels below.
  const DEFAULT_MODELS = [
    { value: 'claude-haiku-4-5', label: 'Claude Haiku 4.5' },
    { value: 'claude-sonnet-5', label: 'Claude Sonnet 5' }
  ];

  // Models to show in the settings dropdown: the live offered_models list
  // from the community config endpoint, falling back to DEFAULT_MODELS
  // until that response arrives.
  function getModelMenuOptions() {
    return (offeredModels && offeredModels.length) ? offeredModels : DEFAULT_MODELS;
  }

  // Helper to get human-readable label for a model
  function getModelLabel(modelId) {
    const model = getModelMenuOptions().find(m => m.value === modelId);
    return model ? model.label : modelId;
  }

  // A valid model id is either a bare first-party id (e.g. "claude-haiku-4-5")
  // or an OpenRouter-style "provider/model" id (e.g. "openai/gpt-5"), which
  // the custom-model field still accepts for BYOK callers.
  function isValidModelId(model) {
    if (typeof model !== 'string' || !model) return false;
    return /^[a-zA-Z0-9._-]+$/.test(model) || /^[a-zA-Z0-9_-]+\/[a-zA-Z0-9._-]+$/.test(model);
  }

  // BYOK key formats, matching the server-side redaction patterns in
  // src/core/logging.py so widget-side validation stays in sync with what
  // the backend actually accepts.
  const ANTHROPIC_KEY_PATTERN = /^sk-ant-[a-zA-Z0-9_-]{80,}$/i;
  const OPENROUTER_KEY_PATTERN = /^sk-or-v1-[0-9a-f]{64}$/i;

  // Infer which provider a BYOK key belongs to from its prefix, so
  // keyProvider never has to be stored as a separate user choice.
  function inferKeyProvider(apiKey) {
    if (ANTHROPIC_KEY_PATTERN.test(apiKey)) return 'anthropic';
    if (OPENROUTER_KEY_PATTERN.test(apiKey)) return 'openrouter';
    return null;
  }

  function isValidApiKey(apiKey) {
    return ANTHROPIC_KEY_PATTERN.test(apiKey) || OPENROUTER_KEY_PATTERN.test(apiKey);
  }

  // Track which CONFIG keys were explicitly set by the embedder via setConfig,
  // so fetchCommunityConfig does not overwrite them with API defaults.
  const _userSetKeys = new Set();

  // State
  let isOpen = false;
  let isLoading = false;
  let isThinking = false; // True once a 'thinking' SSE event arrives before any content chunk; swaps the loading label to "Thinking..."
  let messages = [];
  let turnstileToken = null;
  let turnstileWidgetId = null;
  let backendOnline = null; // null = checking, true = online, false = offline
  let backendVersion = null; // Backend version from health check
  let backendCommitSha = null; // Backend git commit SHA from health check
  let pageContextEnabled = true; // Runtime state for page context toggle
  let chatPopup = null; // Reference to pop-out window (prevents duplicates)
  let userSettings = { apiKey: null, model: null, keyProvider: null }; // User settings (BYOK and model selection)
  let communityDefaultModel = null; // Community's default model from API
  let offeredModels = null; // Live offered_models list from the community config API; null until loaded
  let sessionId = null; // Server-side session ID for multi-turn conversations
  let communityConfigReady = null; // Promise for the community config fetch, once started
  // Browser code execution (#431). Set only for a community that configures it,
  // and only once the runtime bundle has loaded and passed its integrity check.
  let browserToolsReady = null; // Promise resolving to the controller, or null
  let browserTools = null; // OSARuntime.ClientToolController
  let browserRuntime = null; // The OSARuntime.PyodideRuntime it drives
  // What the tool panel shows while a tool_request is answered:
  // {phase: 'asking', prompt, decide} while the person is asked,
  // {phase: 'running', prompt, progress} while code runs, else null.
  let toolActivity = null;
  const CHAT_HISTORY_VERSION = 2;
  let responseSequence = 0;

  function createResponseId() {
    responseSequence += 1;
    return `response-${Date.now()}-${responseSequence}`;
  }

  // Notice queued by an init-time failure (corrupted/inaccessible settings,
  // an invalid saved key or model, or corrupted history) that happened
  // before the widget's DOM existed or before the user had opened it, so
  // there was nowhere to show it yet. Flushed via showError the first time
  // the widget is actually opened (see flushPendingNotice).
  let pendingNotice = null;

  // Queue a notice for display the next time the widget opens, instead of
  // trying (and failing) to show it immediately at load time.
  function queuePendingNotice(message) {
    pendingNotice = pendingNotice ? `${pendingNotice} ${message}` : message;
  }

  // Show any notice queued by an init-time failure. Called once, the first
  // time the widget is opened, so a user whose saved settings or history
  // failed to load is not left silently switched to defaults.
  function flushPendingNotice(container) {
    if (pendingNotice) {
      showError(container, pendingNotice);
      pendingNotice = null;
    }
  }

  // Store script URL at load time for reliable pop-out
  const WIDGET_SCRIPT_URL = document.currentScript?.src || null;

  // The browser Python runtime ships as a separate file, loaded only for a
  // community that configures client tools, and verified against this hash.
  // The versioned embed pins THIS file by SRI, so the hash here extends that
  // pin to the runtime: a runtime that does not match is refused by the
  // browser before any of it runs. Written by scripts/build-runtime-bundle.js;
  // CI rebuilds and fails if the committed bundle or this line is stale.
  // BEGIN GENERATED: runtime bundle integrity
  const RUNTIME_BUNDLE_INTEGRITY = 'sha384-eE/G8VeW4pt2dpSMkMEsYgW9s9OBhCANczz6BzCJnjcHkO7ghjlztuzizC8hvhUW';
  // END GENERATED: runtime bundle integrity

  // Icons (SVG)
  const ICONS = {
    chat: '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M21 15a2 2 0 0 1-2 2H7l-4 4V5a2 2 0 0 1 2-2h14a2 2 0 0 1 2 2z"></path></svg>',
    close: '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><line x1="18" y1="6" x2="6" y2="18"></line><line x1="6" y1="6" x2="18" y2="18"></line></svg>',
    send: '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><line x1="22" y1="2" x2="11" y2="13"></line><polygon points="22 2 15 22 11 13 2 9 22 2"></polygon></svg>',
    reset: '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M3 12a9 9 0 1 0 9-9 9.75 9.75 0 0 0-6.74 2.74L3 8"/><path d="M3 3v5h5"/></svg>',
    brain: '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M12 5a3 3 0 1 0-5.997.125 4 4 0 0 0-2.526 5.77 4 4 0 0 0 .556 6.588A4 4 0 1 0 12 18Z"/><path d="M12 5a3 3 0 1 1 5.997.125 4 4 0 0 1 2.526 5.77 4 4 0 0 1-.556 6.588A4 4 0 1 1 12 18Z"/><path d="M15 13a4.5 4.5 0 0 1-3-4 4.5 4.5 0 0 1-3 4"/></svg>',
    copy: '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><rect x="9" y="9" width="13" height="13" rx="2" ry="2"></rect><path d="M5 15H4a2 2 0 0 1-2-2V4a2 2 0 0 1 2-2h9a2 2 0 0 1 2 2v1"></path></svg>',
    check: '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><polyline points="20 6 9 17 4 12"></polyline></svg>',
    popout: '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M18 13v6a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2V8a2 2 0 0 1 2-2h6"/><polyline points="15 3 21 3 21 9"/><line x1="10" y1="14" x2="21" y2="3"/></svg>',
    settings: '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" fill="currentColor"><circle cx="12" cy="5" r="2"></circle><circle cx="12" cy="12" r="2"></circle><circle cx="12" cy="19" r="2"></circle></svg>',
    thumbUp: '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M7 10v12"/><path d="M15 5.88 14 10h5.83a2 2 0 0 1 1.92 2.56l-2.33 8A2 2 0 0 1 17.5 22H4a2 2 0 0 1-2-2v-8a2 2 0 0 1 2-2h2.76a2 2 0 0 0 1.79-1.11L12 2a3.13 3.13 0 0 1 3 3.88Z"/></svg>',
    thumbDown: '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M17 14V2"/><path d="M9 18.12 10 14H4.17a2 2 0 0 1-1.92-2.56l2.33-8A2 2 0 0 1 6.5 2H20a2 2 0 0 1 2 2v8a2 2 0 0 1-2 2h-2.76a2 2 0 0 0-1.79 1.11L12 22a3.13 3.13 0 0 1-3-3.88Z"/></svg>'
  };

  // CSS Styles
  const STYLES = `
    .osa-chat-widget {
      --osa-primary: #2563eb;
      --osa-primary-dark: #1d4ed8;
      --osa-bg: #ffffff;
      --osa-text: #1f2937;
      --osa-text-light: #6b7280;
      --osa-border: #e5e7eb;
      --osa-user-bg: #2563eb;
      --osa-user-text: #ffffff;
      --osa-assistant-bg: #f3f4f6;
      --osa-shadow: 0 10px 25px rgba(0, 0, 0, 0.15);
      font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif;
      font-size: 14px;
      line-height: 1.5;
    }

    .osa-chat-button {
      position: fixed;
      bottom: 20px;
      right: 20px;
      width: 56px;
      height: 56px;
      border-radius: 50%;
      background: var(--osa-primary);
      color: white;
      border: none;
      cursor: pointer;
      box-shadow: var(--osa-shadow);
      display: flex;
      align-items: center;
      justify-content: center;
      transition: transform 0.2s, background 0.2s;
      z-index: 10000;
    }

    .osa-chat-button:hover {
      background: var(--osa-primary-dark);
      transform: scale(1.05);
    }

    .osa-chat-button svg {
      width: 24px;
      height: 24px;
    }

    /* Tooltip that appears next to chat button on initial page load
       Auto-hides after 8 seconds or when chat is opened */
    .osa-chat-tooltip {
      position: fixed;
      bottom: 28px;
      right: 86px;
      background: var(--osa-bg);
      color: var(--osa-text);
      padding: 10px 14px;
      border-radius: 8px;
      box-shadow: var(--osa-shadow);
      font-size: 13px;
      font-weight: 500;
      white-space: nowrap;
      z-index: 9999;
      opacity: 0;
      transform: translateX(10px);
      transition: opacity 0.3s ease, transform 0.3s ease;
      pointer-events: none;
    }

    .osa-chat-tooltip.visible {
      opacity: 1;
      transform: translateX(0);
    }

    .osa-chat-tooltip::after {
      content: '';
      position: absolute;
      right: -6px;
      top: 50%;
      transform: translateY(-50%);
      border: 6px solid transparent;
      border-left-color: var(--osa-bg);
      border-right: none;
    }

    /* Hide tooltip when chat is open */
    .osa-chat-widget.chat-open .osa-chat-tooltip {
      display: none;
    }

    .osa-chat-window {
      position: fixed;
      bottom: 90px;
      right: 20px;
      width: 440px;
      max-width: calc(100vw - 40px);
      height: 680px;
      max-height: calc(100vh - 120px);
      min-width: 300px;
      min-height: 350px;
      background: var(--osa-bg);
      border-radius: 16px;
      box-shadow: var(--osa-shadow);
      display: none;
      flex-direction: column;
      overflow: hidden;
      z-index: 10000;
    }

    .osa-chat-window.open {
      display: flex;
    }

    .osa-chat-header {
      padding: 12px 16px;
      background: var(--osa-primary);
      color: white;
      display: flex;
      align-items: center;
      gap: 12px;
    }

    .osa-chat-avatar {
      width: 36px;
      height: 36px;
      background: rgba(255,255,255,0.2);
      border-radius: 50%;
      display: flex;
      align-items: center;
      justify-content: center;
      flex-shrink: 0;
    }

    .osa-chat-avatar svg {
      width: 20px;
      height: 20px;
    }

    .osa-chat-avatar img {
      width: 32px;
      height: 32px;
      object-fit: contain;
      border-radius: 50%;
    }

    .osa-chat-title-area {
      flex: 1;
      min-width: 0;
    }

    .osa-chat-title {
      display: flex;
      align-items: center;
      gap: 8px;
      font-size: 15px;
      font-weight: 600;
      margin: 0;
    }

    .osa-experimental-badge {
      font-size: 9px;
      font-weight: 600;
      background: rgba(255,255,255,0.25);
      padding: 2px 6px;
      border-radius: 4px;
      text-transform: uppercase;
      letter-spacing: 0.5px;
    }

    .osa-chat-status {
      display: flex;
      align-items: center;
      gap: 6px;
      font-size: 11px;
      opacity: 0.9;
      margin-top: 2px;
    }

    .osa-status-dot {
      width: 8px;
      height: 8px;
      border-radius: 50%;
      background: #22c55e;
    }

    .osa-status-dot.offline {
      background: #ef4444;
    }

    .osa-status-dot.checking {
      background: #f59e0b;
      animation: osa-pulse 1.5s infinite;
    }

    @keyframes osa-pulse {
      0%, 100% { opacity: 1; }
      50% { opacity: 0.4; }
    }

    .osa-header-actions {
      display: flex;
      gap: 4px;
    }

    .osa-header-btn {
      background: transparent;
      border: none;
      color: white;
      cursor: pointer;
      padding: 6px;
      border-radius: 6px;
      display: flex;
      align-items: center;
      justify-content: center;
      opacity: 0.8;
      transition: opacity 0.2s, background 0.2s;
    }

    .osa-header-btn:hover {
      opacity: 1;
      background: rgba(255,255,255,0.15);
    }

    .osa-header-btn:disabled {
      opacity: 0.4;
      cursor: not-allowed;
    }

    .osa-header-btn svg {
      width: 18px;
      height: 18px;
    }

    .osa-chat-messages {
      flex: 1;
      overflow-y: auto;
      padding: 16px;
      display: flex;
      flex-direction: column;
      gap: 16px;
    }

    .osa-message {
      display: flex;
      flex-direction: column;
      gap: 4px;
    }

    .osa-message-label {
      font-size: 11px;
      font-weight: 600;
      color: var(--osa-text-light);
      text-transform: uppercase;
      letter-spacing: 0.3px;
    }

    .osa-message-content {
      padding: 10px 14px;
      border-radius: 12px;
      word-wrap: break-word;
    }

    .osa-message.user .osa-message-content {
      background: var(--osa-user-bg);
      color: var(--osa-user-text);
      align-self: flex-end;
      border-bottom-right-radius: 4px;
    }

    .osa-message.user {
      align-items: flex-end;
    }

    .osa-message.assistant .osa-message-content {
      background: var(--osa-assistant-bg);
      color: var(--osa-text);
      border-bottom-left-radius: 4px;
    }

    /* Markdown styling */
    .osa-message-content p {
      margin: 0 0 8px 0;
    }

    .osa-message-content p:last-child {
      margin-bottom: 0;
    }

    .osa-message-content h1, .osa-message-content h2, .osa-message-content h3,
    .osa-message-content h4, .osa-message-content h5, .osa-message-content h6 {
      margin: 16px 0 8px 0;
      font-weight: 600;
      line-height: 1.3;
    }

    .osa-message-content h1:first-child, .osa-message-content h2:first-child,
    .osa-message-content h3:first-child {
      margin-top: 0;
    }

    .osa-message-content h1 { font-size: 1.3em; }
    .osa-message-content h2 { font-size: 1.2em; }
    .osa-message-content h3 { font-size: 1.1em; }
    .osa-message-content h4, .osa-message-content h5, .osa-message-content h6 { font-size: 1em; }

    .osa-message-content code {
      background: rgba(0,0,0,0.08);
      padding: 2px 6px;
      border-radius: 4px;
      font-size: 13px;
      font-family: 'SF Mono', Monaco, 'Courier New', monospace;
    }

    .osa-message-content pre {
      background: #1f2937;
      color: #f9fafb;
      padding: 12px;
      border-radius: 8px;
      overflow-x: auto;
      margin: 8px 0;
      position: relative;
    }

    .osa-message-content pre code {
      background: transparent;
      padding: 0;
      color: inherit;
    }

    .osa-message-content ul, .osa-message-content ol {
      margin: 8px 0;
      padding-left: 20px;
    }

    .osa-message-content li {
      margin: 4px 0;
    }

    .osa-message-content a {
      color: var(--osa-primary);
      text-decoration: none;
    }

    .osa-message-content a:hover {
      text-decoration: underline;
    }

    .osa-message-content hr {
      border: none;
      border-top: 1px solid var(--osa-border);
      margin: 12px 0;
    }

    .osa-message-content strong {
      font-weight: 600;
    }

    /* Table styling */
    .osa-table-wrapper {
      overflow-x: auto;
      margin: 8px 0;
    }

    .osa-table {
      border-collapse: collapse;
      width: 100%;
      font-size: 13px;
    }

    .osa-table th, .osa-table td {
      border: 1px solid var(--osa-border);
      padding: 8px 10px;
      text-align: left;
    }

    .osa-table th {
      background: rgba(0,0,0,0.04);
      font-weight: 600;
    }

    .osa-table tr:nth-child(even) {
      background: rgba(0,0,0,0.02);
    }

    /* Inline citation markers */
    .osa-citation {
      font-size: 0.75em;
      line-height: 0;
      margin-left: 1px;
    }

    .osa-citation a {
      color: var(--osa-primary);
      text-decoration: none;
    }

    .osa-citation a:hover {
      text-decoration: underline;
    }

    /* Compact numbered source list under a cited answer */
    .osa-message-sources {
      margin: 8px 0 0;
      padding-left: 0;
      font-size: 12px;
      color: var(--osa-text-light);
      list-style: none;
    }

    .osa-message-sources li {
      margin: 2px 0;
    }

    .osa-source-marker {
      font-variant-numeric: tabular-nums;
      margin-right: 2px;
    }

    .osa-message-sources a {
      color: var(--osa-text-light);
      text-decoration: underline;
    }

    .osa-message-sources a:hover {
      color: var(--osa-primary);
    }

    /* Copy button styles */
    .osa-copy-btn {
      position: absolute;
      top: 6px;
      right: 6px;
      background: rgba(255,255,255,0.1);
      border: none;
      border-radius: 4px;
      padding: 4px 6px;
      cursor: pointer;
      color: #9ca3af;
      display: flex;
      align-items: center;
      gap: 4px;
      font-size: 11px;
      transition: background 0.2s, color 0.2s;
    }

    .osa-copy-btn:hover {
      background: rgba(255,255,255,0.2);
      color: #f9fafb;
    }

    .osa-copy-btn svg {
      width: 14px;
      height: 14px;
    }

    .osa-copy-btn.copied {
      color: #22c55e;
    }

    .osa-message-copy-btn {
      background: transparent;
      border: none;
      border-radius: 4px;
      padding: 4px;
      cursor: pointer;
      color: var(--osa-text-light);
      display: flex;
      align-items: center;
      transition: color 0.2s, background 0.2s;
      margin-left: auto;
    }

    .osa-message-copy-btn:hover {
      color: var(--osa-primary);
      background: rgba(0,0,0,0.05);
    }

    .osa-message-copy-btn svg {
      width: 14px;
      height: 14px;
    }

    .osa-message-copy-btn.copied {
      color: #22c55e;
    }

    .osa-message-header {
      display: flex;
      align-items: center;
      gap: 8px;
    }

    .osa-message-feedback {
      display: flex;
      align-items: center;
      flex-wrap: wrap;
      gap: 4px;
      margin-top: 6px;
    }

    .osa-feedback-btn {
      display: inline-flex;
      align-items: center;
      justify-content: center;
      width: 26px;
      height: 26px;
      padding: 0;
      border: none;
      border-radius: 6px;
      background: transparent;
      color: var(--osa-text-light);
      cursor: pointer;
      transition: background 0.15s ease, color 0.15s ease;
    }

    .osa-feedback-btn svg {
      width: 15px;
      height: 15px;
    }

    .osa-feedback-btn:hover {
      background: var(--osa-assistant-bg);
      color: var(--osa-text);
    }

    .osa-feedback-up.selected {
      color: #16a34a;
    }

    .osa-feedback-down.selected {
      color: #dc2626;
    }

    .osa-message-feedback.recorded .osa-feedback-btn {
      cursor: default;
    }

    .osa-message-feedback.recorded .osa-feedback-btn:not(.selected) {
      opacity: 0.3;
    }

    .osa-message-feedback.recorded .osa-feedback-btn:hover {
      background: transparent;
    }

    .osa-feedback-thanks {
      font-size: 12px;
      color: var(--osa-text-light);
      margin-left: 4px;
    }

    .osa-feedback-comment {
      flex-basis: 100%;
      display: flex;
      flex-direction: column;
      gap: 6px;
      margin-top: 4px;
    }

    .osa-feedback-comment-input {
      width: 100%;
      box-sizing: border-box;
      resize: vertical;
      min-height: 44px;
      padding: 6px 8px;
      font: inherit;
      font-size: 13px;
      color: var(--osa-text);
      background: var(--osa-bg);
      border: 1px solid var(--osa-border);
      border-radius: 6px;
    }

    .osa-feedback-comment-input:focus {
      outline: none;
      border-color: var(--osa-primary);
    }

    .osa-feedback-comment-actions {
      display: flex;
      gap: 8px;
      justify-content: flex-end;
    }

    .osa-feedback-comment-actions button {
      font: inherit;
      font-size: 12px;
      padding: 4px 10px;
      border-radius: 6px;
      cursor: pointer;
      border: 1px solid var(--osa-border);
    }

    .osa-feedback-skip {
      background: transparent;
      color: var(--osa-text-light);
    }

    .osa-feedback-send {
      background: var(--osa-primary);
      color: #fff;
      border-color: var(--osa-primary);
    }

    .osa-feedback-send:hover {
      background: var(--osa-primary-dark);
    }

    .osa-suggestions {
      padding: 12px 16px;
      border-top: 1px solid var(--osa-border);
    }

    .osa-suggestions-label {
      display: block;
      font-size: 11px;
      font-weight: 600;
      color: var(--osa-text-light);
      text-transform: uppercase;
      letter-spacing: 0.3px;
      margin-bottom: 8px;
    }

    .osa-suggestions-list {
      display: flex;
      flex-direction: column;
      gap: 6px;
    }

    .osa-suggestion {
      background: var(--osa-assistant-bg);
      border: 1px solid var(--osa-border);
      border-radius: 8px;
      padding: 8px 12px;
      font-size: 13px;
      cursor: pointer;
      transition: background 0.2s, border-color 0.2s;
      color: var(--osa-text);
      text-align: left;
    }

    .osa-suggestion:hover {
      background: #e5e7eb;
      border-color: #d1d5db;
    }

    .osa-chat-input {
      padding: 12px 16px;
      border-top: 1px solid var(--osa-border);
      display: flex;
      gap: 8px;
      align-items: center;
    }

    .osa-chat-input input {
      flex: 1;
      padding: 10px 14px;
      border: 1px solid var(--osa-border);
      border-radius: 20px;
      outline: none;
      font-size: 14px;
      transition: border-color 0.2s;
    }

    .osa-chat-input input:focus {
      border-color: var(--osa-primary);
    }

    .osa-chat-input input:disabled {
      background: #f9fafb;
    }

    .osa-send-btn {
      width: 40px;
      height: 40px;
      border-radius: 50%;
      background: var(--osa-primary);
      color: white;
      border: none;
      cursor: pointer;
      display: flex;
      align-items: center;
      justify-content: center;
      transition: background 0.2s;
      flex-shrink: 0;
    }

    .osa-send-btn:hover:not(:disabled) {
      background: var(--osa-primary-dark);
    }

    .osa-send-btn:disabled {
      background: #9ca3af;
      cursor: not-allowed;
    }

    .osa-send-btn svg {
      width: 18px;
      height: 18px;
    }

    .osa-loading {
      display: flex;
      flex-direction: column;
      gap: 4px;
    }

    .osa-loading-label {
      font-size: 11px;
      font-weight: 600;
      color: var(--osa-text-light);
      text-transform: uppercase;
      letter-spacing: 0.3px;
    }

    .osa-loading-dots {
      display: flex;
      gap: 4px;
      padding: 10px 14px;
      background: var(--osa-assistant-bg);
      border-radius: 12px;
      border-bottom-left-radius: 4px;
      width: fit-content;
    }

    .osa-loading-dot {
      width: 8px;
      height: 8px;
      background: var(--osa-text-light);
      border-radius: 50%;
      animation: osa-bounce 1.4s infinite ease-in-out both;
    }

    .osa-loading-dot:nth-child(1) { animation-delay: -0.32s; }
    .osa-loading-dot:nth-child(2) { animation-delay: -0.16s; }

    @keyframes osa-bounce {
      0%, 80%, 100% { transform: scale(0); }
      40% { transform: scale(1); }
    }


    .osa-turnstile-container {
      padding: 12px 16px;
      border-top: 1px solid var(--osa-border);
      display: flex;
      justify-content: center;
    }

    .osa-error {
      color: #dc2626;
      font-size: 12px;
      padding: 8px 16px;
      background: #fef2f2;
      border-top: 1px solid #fecaca;
    }

    .osa-warning {
      color: #92400e;
      font-size: 12px;
      padding: 8px 16px;
      background: #fffbeb;
      border-top: 1px solid #fde68a;
    }

    .osa-resize-handle {
      position: absolute;
      top: 0;
      left: 0;
      width: 20px;
      height: 20px;
      cursor: nwse-resize;
      z-index: 10;
    }

    .osa-resize-handle::before {
      content: '';
      position: absolute;
      top: 6px;
      left: 6px;
      width: 8px;
      height: 8px;
      border-left: 2px solid rgba(0,0,0,0.2);
      border-top: 2px solid rgba(0,0,0,0.2);
    }

    .osa-ai-disclaimer {
      padding: 5px 16px;
      font-size: 10px;
      display: flex;
      align-items: center;
      justify-content: space-between;
      gap: 10px;
      border-top: 1px solid var(--osa-border);
      color: var(--osa-disclaimer-color, #9a3412);
      background: var(--osa-disclaimer-bg, #fff7ed);
    }

    .osa-ai-disclaimer .osa-disclaimer-text {
      text-align: left;
    }

    .osa-ai-disclaimer .osa-feedback-link {
      background: none;
      border: none;
      padding: 0;
      font: inherit;
      color: inherit;
      cursor: pointer;
      text-decoration: underline;
      white-space: nowrap;
      flex-shrink: 0;
    }

    .osa-ai-disclaimer .osa-feedback-link:hover {
      opacity: 0.75;
    }

    .osa-combined-footer {
      padding: 6px 16px;
      font-size: 11px;
      color: var(--osa-text-light);
      display: flex;
      align-items: center;
      justify-content: space-between;
      border-top: 1px solid var(--osa-border);
      gap: 8px;
    }

    .osa-combined-footer .osa-page-context-toggle {
      display: flex;
      align-items: center;
      gap: 5px;
      flex-shrink: 0;
    }

    .osa-combined-footer .osa-page-context-toggle::after {
      content: '|';
      color: var(--osa-text-light);
      opacity: 0.5;
      margin-left: 3px;
    }

    .osa-combined-footer input[type="checkbox"] {
      width: 11px;
      height: 11px;
      margin: 0;
      cursor: pointer;
      accent-color: var(--osa-primary);
    }

    .osa-combined-footer label {
      cursor: pointer;
      user-select: none;
    }

    .osa-combined-footer .osa-footer-powered {
      text-align: right;
      white-space: nowrap;
    }

    .osa-combined-footer .osa-footer-powered a {
      color: var(--osa-text-light);
      text-decoration: none;
      font-weight: 600;
    }

    .osa-combined-footer .osa-footer-powered a:hover {
      color: var(--osa-primary);
      text-decoration: underline;
    }

    .osa-feedback-textarea {
      resize: vertical;
      min-height: 80px;
      font-family: inherit;
    }

    .osa-feedback-modal-thanks {
      padding: 16px 20px;
      color: #16a34a;
      font-weight: 600;
    }

    /* Settings modal - contained within chat window to avoid z-index conflicts
       and ensure modal is properly scoped to the widget's stacking context */
    .osa-settings-overlay {
      position: absolute;
      top: 0;
      left: 0;
      right: 0;
      bottom: 0;
      background: rgba(0, 0, 0, 0.4);
      display: none;
      align-items: center;
      justify-content: center;
      z-index: 100;
      border-radius: 16px;
    }

    .osa-settings-overlay.open {
      display: flex;
    }

    .osa-settings-modal {
      background: var(--osa-bg);
      border-radius: 12px;
      width: 90%;
      max-width: 340px;
      max-height: 85%;
      overflow-y: auto;
      box-shadow: 0 4px 20px rgba(0, 0, 0, 0.15);
      margin: 10px;
    }

    .osa-settings-header {
      padding: 16px 20px;
      border-bottom: 1px solid var(--osa-border);
      display: flex;
      align-items: center;
      justify-content: space-between;
    }

    .osa-settings-title {
      font-size: 16px;
      font-weight: 600;
      color: var(--osa-text);
      margin: 0;
    }

    .osa-settings-close-btn {
      background: transparent;
      border: none;
      color: var(--osa-text-light);
      cursor: pointer;
      padding: 4px;
      border-radius: 4px;
      display: flex;
      align-items: center;
      justify-content: center;
      transition: background 0.2s, color 0.2s;
    }

    .osa-settings-close-btn:hover {
      background: var(--osa-border);
      color: var(--osa-text);
    }

    .osa-settings-close-btn svg {
      width: 20px;
      height: 20px;
    }

    .osa-settings-body {
      padding: 20px;
    }

    .osa-settings-field {
      margin-bottom: 20px;
    }

    .osa-settings-field:last-child {
      margin-bottom: 0;
    }

    .osa-settings-label {
      display: block;
      font-size: 13px;
      font-weight: 600;
      color: var(--osa-text);
      margin-bottom: 6px;
    }

    .osa-settings-hint {
      display: block;
      font-size: 11px;
      color: var(--osa-text-light);
      margin-top: 4px;
    }

    .osa-settings-input {
      width: 100%;
      padding: 10px 12px;
      border: 1px solid var(--osa-border);
      border-radius: 8px;
      font-size: 13px;
      font-family: 'SF Mono', Monaco, 'Courier New', monospace;
      outline: none;
      transition: border-color 0.2s;
      box-sizing: border-box;
    }

    .osa-settings-input:focus {
      border-color: var(--osa-primary);
    }

    .osa-settings-select {
      width: 100%;
      padding: 10px 12px;
      border: 1px solid var(--osa-border);
      border-radius: 8px;
      font-size: 13px;
      outline: none;
      cursor: pointer;
      box-sizing: border-box;
      transition: border-color 0.2s;
      background: var(--osa-bg);
    }

    .osa-settings-select:focus {
      border-color: var(--osa-primary);
    }

    .osa-settings-footer {
      padding: 16px 20px;
      border-top: 1px solid var(--osa-border);
      display: flex;
      gap: 10px;
      justify-content: flex-end;
    }

    .osa-settings-btn {
      padding: 10px 20px;
      border-radius: 8px;
      font-size: 13px;
      font-weight: 600;
      cursor: pointer;
      transition: background 0.2s, color 0.2s;
      border: none;
    }

    .osa-settings-btn-cancel {
      background: transparent;
      color: var(--osa-text);
      border: 1px solid var(--osa-border);
    }

    .osa-settings-btn-cancel:hover {
      background: var(--osa-border);
    }

    .osa-settings-btn-save {
      background: var(--osa-primary);
      color: white;
    }

    .osa-settings-btn-save:hover {
      background: var(--osa-primary-dark);
    }

    /* Fullscreen mode (for pop-out windows) */
    .osa-chat-widget.fullscreen .osa-chat-button {
      display: none !important;
    }

    .osa-chat-widget.fullscreen .osa-chat-window {
      position: fixed !important;
      top: 0 !important;
      left: 0 !important;
      right: 0 !important;
      bottom: 0 !important;
      width: 100% !important;
      height: 100% !important;
      max-width: none !important;
      max-height: none !important;
      border-radius: 0 !important;
      display: flex !important;
    }

    .osa-chat-widget.fullscreen .osa-resize-handle {
      display: none !important;
    }

    .osa-chat-widget.fullscreen .osa-chat-header {
      border-radius: 0;
    }

    /* Browser code execution: the permission gate and what ran. */
    .osa-tool-panel {
      border: 1px solid var(--osa-border);
      border-radius: 12px;
      padding: 10px 12px;
      background: var(--osa-bg);
      font-size: 13px;
      display: flex;
      flex-direction: column;
      gap: 8px;
    }

    .osa-tool-panel-title {
      font-weight: 600;
    }

    .osa-tool-panel-description {
      color: var(--osa-text-light);
    }

    .osa-tool-code {
      background: #1f2937;
      color: #f9fafb;
      padding: 10px;
      border-radius: 8px;
      overflow: auto;
      max-height: 240px;
      margin: 0;
      font-size: 12px;
      font-family: 'SF Mono', Monaco, 'Courier New', monospace;
      white-space: pre;
    }

    .osa-py-kw { color: #c4b5fd; }
    .osa-py-bi { color: #93c5fd; }
    .osa-py-str { color: #86efac; }
    .osa-py-com { color: #9ca3af; font-style: italic; }
    .osa-py-num { color: #fcd34d; }
    .osa-py-dec { color: #f9a8d4; }

    .osa-tool-actions {
      display: flex;
      gap: 8px;
      align-items: center;
      flex-wrap: wrap;
    }

    .osa-tool-actions button {
      border: 1px solid var(--osa-border);
      background: var(--osa-bg);
      color: var(--osa-text);
      border-radius: 6px;
      padding: 5px 12px;
      font-size: 13px;
      cursor: pointer;
    }

    .osa-tool-actions button.osa-tool-run {
      background: var(--osa-primary);
      border-color: var(--osa-primary);
      color: #ffffff;
    }

    .osa-tool-autorun {
      display: flex;
      gap: 6px;
      align-items: center;
      color: var(--osa-text-light);
      font-size: 12px;
    }

    .osa-tool-status {
      color: var(--osa-text-light);
    }

    .osa-execution {
      margin: 0 0 8px 0;
      font-size: 13px;
    }

    .osa-execution summary {
      cursor: pointer;
      color: var(--osa-text-light);
    }

    .osa-execution-output {
      background: rgba(0,0,0,0.05);
      border-radius: 6px;
      padding: 8px;
      margin: 6px 0 0 0;
      max-height: 200px;
      overflow: auto;
      white-space: pre-wrap;
      font-size: 12px;
      font-family: 'SF Mono', Monaco, 'Courier New', monospace;
    }

    .osa-execution img {
      display: block;
      max-width: 100%;
      margin-top: 6px;
      border-radius: 6px;
      background: #ffffff;
    }
  `;

  // Escape text for interpolation into HTML, including quoted attribute values.
  // Serializing a text node escapes & < > but deliberately leaves both quote
  // characters alone, so the textContent trick alone is not enough here: this
  // helper's output is interpolated into href=""/title=""/value="" attributes,
  // where an unescaped quote closes the attribute and everything after it is
  // parsed as further attributes (an event handler, for instance). Citation
  // hover text is a verbatim span of a retrieved document, so that input is not
  // ours to trust. Escaped quotes still render as quotes in text and still copy
  // as quotes from a code block, which reads textContent.
  function escapeHtml(text) {
    const div = document.createElement('div');
    div.textContent = text;
    return div.innerHTML.replace(/"/g, '&quot;').replace(/'/g, '&#39;');
  }

  // Validate URL protocol to prevent javascript: XSS
  function isSafeUrl(url) {
    if (!url) return false;
    try {
      const parsed = new URL(url, window.location.origin);
      return parsed.protocol === 'http:' || parsed.protocol === 'https:';
    } catch {
      return false;
    }
  }

  // Copy text to clipboard
  async function copyToClipboard(text, button) {
    try {
      await navigator.clipboard.writeText(text);
      // Show success feedback
      const originalHtml = button.innerHTML;
      button.innerHTML = ICONS.check;
      button.classList.add('copied');
      setTimeout(() => {
        button.innerHTML = originalHtml;
        button.classList.remove('copied');
      }, 2000);
    } catch (e) {
      console.error('Failed to copy:', e);
    }
  }

  // Generate unique ID for code blocks
  let codeBlockId = 0;
  function getCodeBlockId() {
    return 'osa-code-' + (++codeBlockId);
  }

  // Render inline markdown (bold, italic, links, plain URLs, citation markers)
  // citationsByMarker: optional {"1": {source, title, cited_text}, ...} map.
  // When provided, a bare "[1]" (not followed by "(", so it never collides
  // with a real markdown link) whose number is a known marker renders as a
  // superscript link to its source; any other "[n]" is left as plain text.
  function renderInlineMarkdown(text, citationsByMarker) {
    if (!text) return '';

    let result = '';
    let remaining = text;

    while (remaining.length > 0) {
      const boldMatch = remaining.match(/\*\*(.+?)\*\*/);
      const italicMatch = remaining.match(/(?<!\*)\*([^*]+)\*(?!\*)/);
      const linkMatch = remaining.match(/\[([^\]]+)\]\(([^)]+)\)/);
      const urlMatch = remaining.match(/(?<!\]\()(https?:\/\/[^\s\)]+)/);
      let citationMatch = null;
      if (citationsByMarker) {
        // Scan every bracketed number, not just the first: prose of its own
        // like "see item [42]" must not hide a real marker later in the same
        // run. Matching only the first would leave that marker unlinked, and
        // if nothing else matched either, the rest of the run would be
        // emitted as plain text and the loop would exit.
        for (const candidate of remaining.matchAll(/\[(\d+)\](?!\()/g)) {
          if (citationsByMarker[candidate[1]]) {
            citationMatch = candidate;
            break;
          }
        }
      }

      const boldIndex = boldMatch ? remaining.indexOf(boldMatch[0]) : -1;
      const italicIndex = italicMatch ? remaining.indexOf(italicMatch[0]) : -1;
      const linkIndex = linkMatch ? remaining.indexOf(linkMatch[0]) : -1;
      const urlIndex = urlMatch ? remaining.indexOf(urlMatch[0]) : -1;
      const citationIndex = citationMatch ? citationMatch.index : -1;

      const indices = [boldIndex, italicIndex, linkIndex, urlIndex, citationIndex].filter(i => i !== -1);
      if (indices.length === 0) {
        result += escapeHtml(remaining);
        break;
      }
      const minIndex = Math.min(...indices);

      if (minIndex === boldIndex && boldMatch) {
        if (boldIndex > 0) result += escapeHtml(remaining.substring(0, boldIndex));
        result += '<strong>' + escapeHtml(boldMatch[1]) + '</strong>';
        remaining = remaining.substring(boldIndex + boldMatch[0].length);
      } else if (minIndex === italicIndex && italicMatch) {
        if (italicIndex > 0) result += escapeHtml(remaining.substring(0, italicIndex));
        result += '<em>' + escapeHtml(italicMatch[1]) + '</em>';
        remaining = remaining.substring(italicIndex + italicMatch[0].length);
      } else if (minIndex === linkIndex && linkMatch) {
        if (linkIndex > 0) result += escapeHtml(remaining.substring(0, linkIndex));
        // Validate URL to prevent javascript: XSS
        if (isSafeUrl(linkMatch[2])) {
          result += '<a href="' + escapeHtml(linkMatch[2]) + '" target="_blank" rel="noopener noreferrer">' + escapeHtml(linkMatch[1]) + '</a>';
        } else {
          result += escapeHtml(linkMatch[1]); // Just show text, no link
        }
        remaining = remaining.substring(linkIndex + linkMatch[0].length);
      } else if (minIndex === urlIndex && urlMatch) {
        if (urlIndex > 0) result += escapeHtml(remaining.substring(0, urlIndex));
        // Plain URLs are already validated by regex to start with https?://
        result += '<a href="' + escapeHtml(urlMatch[0]) + '" target="_blank" rel="noopener noreferrer">' + escapeHtml(urlMatch[0]) + '</a>';
        remaining = remaining.substring(urlIndex + urlMatch[0].length);
      } else if (minIndex === citationIndex && citationMatch) {
        if (citationIndex > 0) result += escapeHtml(remaining.substring(0, citationIndex));
        const citation = citationsByMarker[citationMatch[1]];
        const label = escapeHtml(citationMatch[1]);
        if (isSafeUrl(citation.source)) {
          // Every attribute value here is escaped inline rather than via a
          // pre-escaped local, so the "attribute values go through escapeHtml"
          // check in tests/test_frontend/test_widget_drift.py can stay literal.
          result += '<sup class="osa-citation"><a href="' + escapeHtml(citation.source) +
            '" target="_blank" rel="noopener noreferrer" title="' +
            escapeHtml(citation.cited_text || citation.title || '') + '">[' + label + ']</a></sup>';
        } else {
          result += '<sup class="osa-citation">[' + label + ']</sup>';
        }
        remaining = remaining.substring(citationIndex + citationMatch[0].length);
      }
    }

    return result;
  }

  // Full markdown to HTML converter
  function markdownToHtml(text, citationsByMarker) {
    if (!text) return '';

    const lines = text.split('\n');
    let result = '';
    let inCodeBlock = false;
    let codeBlockContent = [];
    let inTable = false;
    let tableRows = [];
    let currentList = [];
    let currentListType = null; // 'ul' or 'ol'

    const flushList = () => {
      if (currentList.length > 0 && currentListType) {
        result += '<' + currentListType + '>' + currentList.join('') + '</' + currentListType + '>';
        currentList = [];
        currentListType = null;
      }
    };

    const flushTable = () => {
      if (tableRows.length > 0) {
        let tableHtml = '<div class="osa-table-wrapper"><table class="osa-table">';
        tableRows.forEach((row, idx) => {
          const cells = row.split('|').filter(c => c.trim() !== '');
          // Skip separator row (contains only dashes and colons)
          if (cells.every(c => /^[\s\-:]+$/.test(c))) return;
          const tag = idx === 0 ? 'th' : 'td';
          tableHtml += '<tr>';
          cells.forEach(cell => {
            tableHtml += '<' + tag + '>' + renderInlineMarkdown(cell.trim(), citationsByMarker) + '</' + tag + '>';
          });
          tableHtml += '</tr>';
        });
        tableHtml += '</table></div>';
        result += tableHtml;
        tableRows = [];
        inTable = false;
      }
    };

    for (let lineIdx = 0; lineIdx < lines.length; lineIdx++) {
      const line = lines[lineIdx];

      // Handle code blocks
      if (line.trim().startsWith('```')) {
        if (inCodeBlock) {
          const codeContent = codeBlockContent.join('\n');
          const blockId = getCodeBlockId();
          result += '<pre data-code-id="' + blockId + '"><button class="osa-copy-btn" data-copy-target="' + blockId + '" title="Copy code">' + ICONS.copy + '</button><code>' + escapeHtml(codeContent) + '</code></pre>';
          codeBlockContent = [];
          inCodeBlock = false;
        } else {
          flushList();
          flushTable();
          inCodeBlock = true;
        }
        continue;
      }

      if (inCodeBlock) {
        codeBlockContent.push(line);
        continue;
      }

      // Handle tables (lines with | characters)
      if (line.includes('|') && (line.trim().startsWith('|') || line.match(/\|.*\|/))) {
        flushList();
        inTable = true;
        tableRows.push(line);
        continue;
      } else if (inTable) {
        flushTable();
      }

      // Handle horizontal rules
      if (/^[-*_]{3,}\s*$/.test(line.trim())) {
        flushList();
        flushTable();
        result += '<hr>';
        continue;
      }

      // Handle headers
      const headerMatch = line.match(/^(#{1,6})\s+(.+)$/);
      if (headerMatch) {
        flushList();
        const level = headerMatch[1].length;
        result += '<h' + level + '>' + renderInlineMarkdown(headerMatch[2], citationsByMarker) + '</h' + level + '>';
        continue;
      }

      // Handle bullet points (* item or - item)
      const bulletMatch = line.match(/^[\*\-]\s+(.+)$/);
      if (bulletMatch) {
        if (currentListType !== 'ul') flushList();
        currentListType = 'ul';
        currentList.push('<li>' + renderInlineMarkdown(bulletMatch[1], citationsByMarker) + '</li>');
        continue;
      }

      // Handle numbered lists
      const numberedMatch = line.match(/^\d+\.\s+(.+)$/);
      if (numberedMatch) {
        if (currentListType !== 'ol') flushList();
        currentListType = 'ol';
        currentList.push('<li>' + renderInlineMarkdown(numberedMatch[1], citationsByMarker) + '</li>');
        continue;
      }

      flushList();

      if (line.trim()) {
        // Handle inline code first
        let processedLine = line.replace(/`([^`]+)`/g, function(match, code) {
          return '<code>' + escapeHtml(code) + '</code>';
        });
        // Process inline markdown for non-code parts
        processedLine = processedLine.replace(/(<code[^>]*>.*?<\/code>)|([^<]+)/g, function(match, codeTag, text) {
          if (codeTag) return codeTag;
          if (text) return renderInlineMarkdown(text, citationsByMarker);
          return match;
        });

        result += '<p>' + processedLine + '</p>';
      }
    }

    // Flush any remaining content
    flushList();
    flushTable();
    if (inCodeBlock && codeBlockContent.length > 0) {
      const codeContent = codeBlockContent.join('\n');
      const blockId = getCodeBlockId();
      result += '<pre data-code-id="' + blockId + '"><button class="osa-copy-btn" data-copy-target="' + blockId + '" title="Copy code">' + ICONS.copy + '</button><code>' + escapeHtml(codeContent) + '</code></pre>';
    }

    return result || text;
  }

  // Validate message structure for security
  function isValidMessage(msg) {
    return msg &&
      typeof msg === 'object' &&
      typeof msg.role === 'string' &&
      (msg.role === 'user' || msg.role === 'assistant') &&
      typeof msg.content === 'string' &&
      msg.content.length < 100000; // Prevent DoS
  }

  // Older history entries may not have citations, and corrupted storage can
  // contain a non-array value. Normalize the optional field before rendering
  // so a malformed saved reply cannot crash widget initialization.
  function normalizePersistedMessage(msg) {
    if (!isValidMessage(msg)) return null;
    if (msg.role !== 'assistant') return { ...msg };
    return {
      ...msg,
      citations: Array.isArray(msg.citations)
        ? msg.citations.filter((citation) => citation && typeof citation === 'object'
          && !Array.isArray(citation)
          && Number.isInteger(citation.marker)
          && citation.marker > 0
          && typeof citation.source === 'string')
        : [],
      executions: normalizePersistedExecutions(msg.executions),
    };
  }

  // A reply's browser runs, as stored: the fields executionRecord writes, as
  // strings, bounded, and never images (see executionRecord).
  function normalizePersistedExecutions(executions) {
    if (!Array.isArray(executions)) return [];
    const text = (value, limit) => (typeof value === 'string' ? value.slice(0, limit) : '');
    return executions
      .filter((run) => run && typeof run === 'object' && !Array.isArray(run))
      .slice(0, MAX_BROWSER_RUNS_PER_REPLY)
      .map((run) => ({
        callId: text(run.callId, 256),
        tool: text(run.tool, 64),
        description: text(run.description, 500),
        code: text(run.code, 20000),
        status: text(run.status, 32),
        stdout: text(run.stdout, 4000),
        stderr: text(run.stderr, 4000),
        images: [],
      }));
  }

  // Older widget versions persisted citation markers at the stream delta
  // boundary (for example, "ru[1]nica"). Move only markers that belong to a
  // known citation, leaving unknown bracketed numbers and Markdown links
  // alone. New responses are normalized by the backend before this code runs;
  // this is a one-time repair for replies already in browser storage.
  function migrateLegacyCitationMarkers(content, citations) {
    if (typeof content !== 'string' || !content || !Array.isArray(citations) || !citations.length) {
      return content;
    }

    const knownMarkers = new Set(
      citations.map((citation) => String(citation.marker))
    );
    const markerPattern = /\[(\d+)\](?!\()/g;
    const sentenceEndAtEnd = /[.!?](?:\]\([^\)\n]*\)|["'”’)\]`*_])*$/;
    const sentenceEnd = /[.!?](?:\]\([^\)\n]*\)|["'”’)\]`*_])*(?=\s|$)/g;
    const blockBoundary = /\n[ \t]*(?:(?:[-*+]\s+)|(?:\d+[.)]\s+)|(?:#{1,6}\s+)|(?:>\s+)|(?:```)|(?:\n))/g;
    const markers = [];
    let match;

    const isInsideInlineCode = (position) => {
      let backtickCount = 0;
      let escaped = false;
      for (const character of content.slice(0, position)) {
        if (character === '`' && !escaped) backtickCount += 1;
        escaped = character === '\\' && !escaped;
        if (character !== '\\') escaped = false;
      }
      return backtickCount % 2 === 1;
    };

    while ((match = markerPattern.exec(content)) !== null) {
      if (knownMarkers.has(match[1]) && !isInsideInlineCode(match.index)) {
        markers.push({
          start: match.index,
          end: markerPattern.lastIndex,
          marker: match[1],
        });
      }
    }
    if (!markers.length) return content;

    const moves = [];
    for (const marker of markers) {
      const prefix = content.slice(0, marker.start).trimEnd();
      if (sentenceEndAtEnd.test(prefix)) continue;

      // A code-style index such as arr[1] is only exempted from migration
      // when it is actually marked as code (inline backticks, checked via
      // isInsideInlineCode above). A bare word immediately before [n] with
      // no other signal is indistinguishable from a real, unlinked citation
      // marker (e.g. "the documentation[1] explains"), so it is not treated
      // as code here; wrap array-index-style text in backticks to preserve it.

      sentenceEnd.lastIndex = marker.end;
      const sentence = sentenceEnd.exec(content);
      blockBoundary.lastIndex = marker.end;
      const block = blockBoundary.exec(content);
      const boundary = block && (!sentence || block.index < sentence.index) ? block : sentence;
      moves.push({
        marker: `[${marker.marker}]`,
        start: marker.start,
        end: marker.end,
        target: boundary
          ? (boundary === block ? boundary.index : boundary.index + boundary[0].length)
          : content.length,
      });
    }
    if (!moves.length) return content;

    const insertions = new Map();
    for (const move of moves) {
      const atTarget = insertions.get(move.target) || [];
      atTarget.push(move.marker);
      insertions.set(move.target, atTarget);
    }

    // Rebuild against the original positions so multiple markers can share a
    // sentence end without disturbing one another's offsets.
    const removals = new Set(moves.flatMap((move) => {
      const positions = [];
      for (let index = move.start; index < move.end; index += 1) positions.push(index);
      const lineStart = content.lastIndexOf('\n', move.start - 1) + 1;
      const linePrefix = content.slice(lineStart, move.start);
      const isListIndent = /^\s*(?:[-*+]\s+|\d+[.)]\s+)$/.test(linePrefix);
      if (!isListIndent) {
        for (let index = move.start - 1; index >= lineStart && /[ \t]/.test(content[index]); index -= 1) {
          positions.push(index);
        }
      }
      return positions;
    }));
    let cleaned = '';
    for (let index = 0; index <= content.length; index += 1) {
      const values = insertions.get(index);
      if (values) cleaned += values.join('');
      if (index < content.length && !removals.has(index)) cleaned += content[index];
    }
    return cleaned;
  }

  // Load chat history from localStorage
  function loadHistory() {
    let historyLoadFailed = false;
    let historyNeedsSave = false;
    try {
      const saved = localStorage.getItem(CONFIG.storageKey);
      if (saved) {
        const parsed = JSON.parse(saved);
        // Validate structure to prevent injection attacks
        let rawMessages;
        let historyVersion = 0;
        if (parsed && typeof parsed === 'object' && !Array.isArray(parsed) && Array.isArray(parsed.messages)) {
          // Stored format: { version, messages, sessionId }
          rawMessages = parsed.messages;
          sessionId = parsed.sessionId || null;
          historyVersion = Number.isInteger(parsed.version) ? parsed.version : 0;
        } else if (Array.isArray(parsed)) {
          // Legacy format: just messages array (backward compatible)
          rawMessages = parsed;
          sessionId = null;
        }
        if (rawMessages) {
          messages = rawMessages.map(normalizePersistedMessage).filter(Boolean);
          if (messages.length !== rawMessages.length || historyVersion < CHAT_HISTORY_VERSION) {
            historyNeedsSave = true;
          }
          if (historyVersion < CHAT_HISTORY_VERSION) {
            messages = messages.map((message) => {
              if (message.role !== 'assistant' || !message.citations.length) return message;
              const content = migrateLegacyCitationMarkers(message.content, message.citations);
              if (content === message.content) return message;
              historyNeedsSave = true;
              return { ...message, content };
            });
          }
          if (messages.length !== rawMessages.length) {
            console.warn('Some chat messages were invalid and filtered out');
          }
        }
      }
    } catch (e) {
      console.error('Failed to load chat history:', e);
      historyLoadFailed = true;
      queuePendingNotice('Saved chat history is corrupted and could not be loaded.');
    }
    if (messages.length === 0) {
      messages = [{ role: 'assistant', content: CONFIG.initialMessage }];
    }
    // A read/parse failure must never trigger a write: preserving the
    // existing storage value is safer than replacing it with the greeting.
    return !historyLoadFailed && historyNeedsSave;
  }

  // Save chat history to localStorage
  let saveErrorShown = false;
  function saveHistory() {
    if (!CONFIG.storageKey) {
      console.warn('[OSA] Cannot save history - no storage key configured');
      return;
    }

    try {
      // Persist only durable feedback state: drop transient flags and the
      // in-progress draft, and never persist a vote that has not been confirmed
      // by the server (so a reload can't show a false "recorded" state).
      const persistable = messages.map((m) => {
        const { _feedbackCommitting, _feedbackJustOpened, _responseId, feedbackDraft, ...rest } = m;
        if (rest.feedback && !rest.feedbackCommitted) delete rest.feedback;
        // Figures are shown for the life of the page and not stored.
        if (Array.isArray(rest.executions)) {
          rest.executions = rest.executions.map((run) => ({ ...run, images: [] }));
        }
        return rest;
      });
      const data = JSON.stringify({ version: CHAT_HISTORY_VERSION, messages: persistable, sessionId });
      localStorage.setItem(CONFIG.storageKey, data);
      saveErrorShown = false;
    } catch (e) {
      console.error('[OSA] localStorage save failed:', {
        errorName: e.name,
        errorMessage: e.message,
        messageCount: messages.length,
        isQuotaError: e.name === 'QuotaExceededError'
      });

      // Determine error type for better user messaging
      let errorMsg = 'Chat history could not be saved';
      const isQuotaError = e.name === 'QuotaExceededError';
      const isSecurityError = e.name === 'SecurityError';

      if (isQuotaError) {
        errorMsg = 'Storage full - conversation NOT saved. Clear browser data or export chat.';
      } else if (isSecurityError) {
        errorMsg = 'Browser privacy settings prevent saving. Enable local storage.';
      } else {
        errorMsg = 'Storage unavailable - conversation will be lost on refresh.';
      }

      // Show error (not just once - user needs to know every time save fails)
      const container = document.querySelector('.osa-chat-widget');
      if (container && !saveErrorShown) {
        showError(container, errorMsg);
        saveErrorShown = true; // Show once per session to avoid spam
      }

      // Re-throw so callers know save failed
      throw e;
    }
  }

  // Get page context (URL, title, and widget instructions) for contextual answers
  function getPageContext() {
    // Widget instructions are always sent if configured, even if page context is off
    const hasWidgetInstructions = typeof CONFIG.widgetInstructions === 'string'
      && CONFIG.widgetInstructions.trim() !== '';
    const hasPageContext = CONFIG.allowPageContext && pageContextEnabled;

    if (!hasPageContext && !hasWidgetInstructions) {
      return null;
    }

    const context = {};
    if (hasPageContext) {
      context.url = window.location.href;
      context.title = document.title || null;
    }
    if (hasWidgetInstructions) {
      context.widget_instructions = CONFIG.widgetInstructions;
    }
    return context;
  }

  // Load page context preference from localStorage
  function loadPageContextPreference() {
    if (!CONFIG.allowPageContext) {
      pageContextEnabled = false;
      return;
    }
    try {
      const saved = localStorage.getItem(CONFIG.pageContextStorageKey);
      if (saved !== null) {
        pageContextEnabled = saved === 'true';
      } else {
        pageContextEnabled = CONFIG.pageContextDefaultEnabled;
      }
    } catch (e) {
      pageContextEnabled = CONFIG.pageContextDefaultEnabled;
    }
  }

  // Save page context preference to localStorage
  function savePageContextPreference() {
    try {
      localStorage.setItem(CONFIG.pageContextStorageKey, pageContextEnabled.toString());
    } catch (e) {
      console.warn('Could not save page context preference:', e);
    }
  }

  // Load user settings from localStorage
  function loadUserSettings() {
    const storageKey = `osa-settings-${CONFIG.communityId}`;
    try {
      const saved = localStorage.getItem(storageKey);
      if (!saved) {
        userSettings = { apiKey: null, model: null, keyProvider: null };
        return;
      }

      let parsed;
      try {
        parsed = JSON.parse(saved);
      } catch (jsonErr) {
        console.error('[OSA] Saved settings contain invalid JSON:', jsonErr.message);
        queuePendingNotice('Saved settings are corrupted. Using defaults.');
        userSettings = { apiKey: null, model: null, keyProvider: null };
        // Clear corrupted data
        try { localStorage.removeItem(storageKey); } catch {}
        return;
      }

      // Validate API key format if present: either an Anthropic or an
      // OpenRouter key. keyProvider is never read from storage directly;
      // it is always re-derived from the key itself below, so settings
      // saved before this phase (with no keyProvider at all) still work.
      if (parsed.apiKey && !isValidApiKey(parsed.apiKey)) {
        console.error('[OSA] Saved API key has invalid format, ignoring');
        queuePendingNotice('Your saved API key is invalid and was ignored.');
        parsed.apiKey = null;
      }

      // Validate model format if present
      if (parsed.model && typeof parsed.model === 'string') {
        if (!isValidModelId(parsed.model)) {
          console.error('[OSA] Saved model has invalid format, ignoring');
          queuePendingNotice('Your saved model selection is invalid and was ignored.');
          parsed.model = null;
        }
      }

      userSettings = {
        apiKey: parsed.apiKey || null,
        model: parsed.model || null,
        keyProvider: inferKeyProvider(parsed.apiKey || '')
      };
    } catch (e) {
      // localStorage access error
      console.error('[OSA] Cannot access localStorage for settings:', e.message);
      queuePendingNotice('Cannot access browser storage. Settings will not persist.');
      userSettings = { apiKey: null, model: null, keyProvider: null };
    }
  }

  // Save user settings to localStorage
  function saveUserSettings() {
    const storageKey = `osa-settings-${CONFIG.communityId}`;
    try {
      localStorage.setItem(storageKey, JSON.stringify(userSettings));
    } catch (e) {
      console.error('[OSA] Could not save user settings:', e);
      // Show error to user - this is critical
      const container = document.querySelector('.osa-chat-widget');
      if (container) {
        showError(container, 'Could not save settings. Storage may be full or disabled. Your settings will not persist.');
      }
      throw e; // Re-throw so caller knows save failed
    }
  }

  // Disable widget when configuration is invalid
  function disableWidget(container, message) {
    if (!container) return;

    showError(container, message);

    // Disable input and send button
    const input = container.querySelector('.osa-chat-input input');
    const sendBtn = container.querySelector('.osa-send-btn');

    if (input) {
      input.disabled = true;
      input.placeholder = 'Widget unavailable';
    }
    if (sendBtn) {
      sendBtn.disabled = true;
      sendBtn.style.opacity = '0.5';
      sendBtn.style.cursor = 'not-allowed';
    }
  }

  // Fetch community config (default model + widget display settings) from API
  async function fetchCommunityConfig() {
    // Validate communityId before making request
    if (!isValidCommunityId(CONFIG.communityId)) {
      console.error('[OSA] Invalid communityId, cannot fetch default model');
      const container = document.querySelector('.osa-chat-widget');
      if (container && isOpen) {
        disableWidget(container, 'Invalid community configuration. Please check your widget setup.');
      }
      return;
    }

    try {
      const response = await fetch(`${CONFIG.apiEndpoint}/${CONFIG.communityId}`, {
        method: 'GET',
        signal: AbortSignal.timeout(5000)
      });

      if (!response.ok) {
        console.error(`[OSA] Community config fetch failed: HTTP ${response.status}`);
        const container = document.querySelector('.osa-chat-widget');
        if (container && isOpen) {
          disableWidget(container, `Failed to load community configuration (HTTP ${response.status}). Please try again later.`);
        }
        return;
      }

      const data = await response.json();
      if (data && data.default_model) {
        communityDefaultModel = data.default_model;
        console.log(`[OSA] Loaded default model: ${communityDefaultModel}`);
      } else {
        console.error('[OSA] Community default model not found in API response');
        const container = document.querySelector('.osa-chat-widget');
        if (container && isOpen) {
          disableWidget(container, 'Community configuration is incomplete. Please contact support.');
        }
      }

      // Offered models drive the settings model menu; DEFAULT_MODELS remains
      // the fallback if this is missing (older backend) or empty.
      if (data && Array.isArray(data.offered_models) && data.offered_models.length > 0) {
        offeredModels = data.offered_models.map(m => ({ value: m.id, label: m.label }));
      } else {
        console.warn(
          '[OSA] Community config response has no offered_models; falling back to DEFAULT_MODELS. ' +
          'This is expected against an older backend during a rolling deploy, but should not persist.'
        );
      }

      // Apply widget display config from API for fields not explicitly set by the embedder.
      // Use explicit null/undefined checks (not truthiness) so that a null initial_message
      // from the API correctly replaces the hardcoded HED default.
      if (data && data.widget) {
        const w = data.widget;
        let changed = false;
        if (w.title != null && !_userSetKeys.has('title')) {
          CONFIG.title = w.title;
          changed = true;
        }
        if ('initial_message' in w && !_userSetKeys.has('initialMessage')) {
          CONFIG.initialMessage = w.initial_message || '';
          changed = true;
        }
        if (w.placeholder != null && !_userSetKeys.has('placeholder')) {
          CONFIG.placeholder = w.placeholder;
          changed = true;
        }
        if (w.suggested_questions != null && !_userSetKeys.has('suggestedQuestions')) {
          CONFIG.suggestedQuestions = w.suggested_questions;
          changed = true;
        }
        if (w.theme_color != null && !_userSetKeys.has('themeColor')) {
          CONFIG.themeColor = w.theme_color;
          changed = true;
        }
        if (w.logo_url != null && !_userSetKeys.has('logo')) {
          // Resolve path-only logo URLs (starting with '/') against the API endpoint
          if (w.logo_url.startsWith('/')) {
            CONFIG.logo = CONFIG.apiEndpoint + w.logo_url;
          } else {
            CONFIG.logo = w.logo_url;
          }
          changed = true;
        }

        if (changed) {
          applyWidgetConfig();
        }
      } else if (data) {
        console.warn('[OSA] API response missing widget config; using local defaults');
      }

      setUpBrowserTools(data);
    } catch (e) {
      console.error('[OSA] Could not fetch community config:', e.message || e);
      const container = document.querySelector('.osa-chat-widget');
      if (container && isOpen) {
        disableWidget(container, 'Network error loading configuration. Please check your connection and try again.');
      }
    }
  }

  // --- Browser code execution (#431) ---------------------------------------
  //
  // A community that configures client tools gets a Python runtime in this
  // page, loaded from a separate file whose hash this script carries (see
  // RUNTIME_BUNDLE_INTEGRITY). Everything here degrades to "no code execution"
  // rather than failing the widget: the tools are declared to the server only
  // once the runtime has loaded, so a page that blocks it never receives a
  // request to run code it cannot answer.

  // How many times one reply may run code before the widget stops answering.
  // A real analysis is a handful of runs (load, compute, plot, fix an error);
  // a model looping on a failing call is not, and each run is a request.
  const MAX_BROWSER_RUNS_PER_REPLY = 10;

  // Where the runtime bundle lives: next to this script. The pop-out window
  // runs this script inline, so it is handed the URL through its config.
  // SRI pins the bytes, so which URL serves them is not a trust decision.
  function runtimeBundleUrl() {
    const base = WIDGET_SCRIPT_URL || CONFIG.widgetScriptUrl;
    if (typeof base !== 'string' || !base) return null;
    try {
      return new URL('osa-runtime.bundle.js', base).href;
    } catch {
      return null;
    }
  }

  function loadRuntimeBundle() {
    const url = runtimeBundleUrl();
    if (!url) {
      console.warn('[OSA] The widget script URL is unknown, so the browser runtime cannot be located. Code execution is off.');
      return Promise.resolve(null);
    }
    return new Promise((resolve) => {
      const script = document.createElement('script');
      script.src = url;
      // The browser refuses the file before any of it runs unless it matches.
      script.integrity = RUNTIME_BUNDLE_INTEGRITY;
      script.crossOrigin = 'anonymous';
      script.async = true;
      script.onload = () => resolve(window.OSARuntime || null);
      script.onerror = () => {
        console.warn(
          '[OSA] The browser runtime did not load: the page blocked it, it was unreachable, ' +
          'or it did not match its integrity hash. Code execution is off; the assistant still answers.'
        );
        resolve(null);
      };
      document.head.appendChild(script);
    });
  }

  function setUpBrowserTools(data) {
    const tools = data && Array.isArray(data.client_tools) ? data.client_tools : [];
    const python = data && data.runtime && data.runtime.python;
    if (tools.length === 0 || !python || browserToolsReady) return;
    browserToolsReady = loadRuntimeBundle().then((api) => {
      if (!api || typeof api.ClientToolController !== 'function' || typeof api.PyodideRuntime !== 'function') {
        return null;
      }
      try {
        browserRuntime = new api.PyodideRuntime({
          runtime: python,
          onProgress: onRuntimeProgress,
        });
        browserTools = new api.ClientToolController({ runtime: browserRuntime, tools, gate: askToRunCode });
      } catch (err) {
        console.error('[OSA] Could not set up the browser runtime:', err);
        browserRuntime = null;
        browserTools = null;
        return null;
      }
      if (isOpen) preloadRuntime();
      return browserTools;
    });
  }

  // Boot on open rather than on the first run, for a community that asks.
  function preloadRuntime() {
    if (!browserRuntime || !browserRuntime.preloadsOnOpen) return;
    browserRuntime.boot().catch((err) => {
      // Not shown: nobody asked for anything yet. The next run boots again and
      // reports the failure in its result, which the assistant then explains.
      console.warn('[OSA] Preloading the browser runtime failed:', err && err.message);
    });
  }

  // The client tools this page can run, once known. Waits briefly for the
  // config and the runtime, so a question typed the moment the page opens is
  // not sent without them; after that it resolves at once.
  async function declaredClientTools() {
    const deadline = new Promise((resolve) => setTimeout(() => resolve(null), 3000));
    if (communityConfigReady) await Promise.race([communityConfigReady, deadline]);
    if (!browserToolsReady) return [];
    const tools = await Promise.race([browserToolsReady, deadline]);
    return tools ? tools.declared : [];
  }

  function onRuntimeProgress(event) {
    if (!toolActivity || toolActivity.phase !== 'running') return;
    const phase = event && event.phase;
    if (phase === 'loading_runtime') {
      toolActivity.progress = 'Starting Python in your browser...';
    } else if ((phase === 'loading_package' || phase === 'installing') && event.package) {
      const count = Number.isInteger(event.total) ? ` (${event.index + 1} of ${event.total})` : '';
      toolActivity.progress = `Loading ${event.package}${count}...`;
    } else {
      return;
    }
    const container = document.querySelector('.osa-chat-widget');
    if (container) renderMessages(container);
  }

  // The gate the controller calls. Resolves with the person's decision.
  function askToRunCode(prompt) {
    return new Promise((resolve) => {
      toolActivity = {
        phase: 'asking',
        prompt,
        decide: (decision, alwaysRun) => {
          if (decision === 'run') {
            if (alwaysRun && browserTools) browserTools.autoRun = true;
            toolActivity = { phase: 'running', prompt, progress: null };
          } else {
            toolActivity = null;
          }
          const container = document.querySelector('.osa-chat-widget');
          if (container) renderMessages(container);
          resolve(decision);
        },
      };
      const container = document.querySelector('.osa-chat-widget');
      if (container) renderMessages(container);
    });
  }

  // What the reader is shown of a run, kept on the reply. The images are shown
  // now and never stored: three figures can be megabytes, and browser storage
  // for a whole conversation is a few.
  function executionRecord(request, result) {
    const args = (request && request.args) || {};
    return {
      callId: String(request.call_id || ''),
      tool: String(request.tool || ''),
      description: typeof args.description === 'string' ? args.description.slice(0, 500) : '',
      code: typeof args.code === 'string' ? args.code.slice(0, 20000) : '',
      status: String(result.status || ''),
      stdout: String(result.stdout || '').slice(0, 4000),
      stderr: String(result.stderr || '').slice(0, 4000),
      images: Array.isArray(result.images) ? result.images : [],
    };
  }

  // Answer one tool_request and record it on the reply it belongs to.
  async function answerToolRequest(container, request, messageIndex) {
    if (!browserTools) {
      // Tools are declared only once the controller exists, so the server
      // should never ask. If it does, the parked call is abandoned (and
      // repaired) by the next message.
      throw new Error('The assistant asked to run code, but this page has no browser runtime.');
    }
    const args = (request && request.args) || {};
    if (request.tool !== 'get_full_output') {
      toolActivity = {
        phase: 'running',
        prompt: {
          code: typeof args.code === 'string' ? args.code : '',
          description: typeof args.description === 'string' ? args.description : '',
        },
        progress: null,
      };
    }
    isThinking = false;
    renderMessages(container);
    let result;
    try {
      result = await browserTools.answer(request);
    } finally {
      toolActivity = null;
    }
    const message = messages[messageIndex];
    if (message && request.tool !== 'get_full_output') {
      message.executions = (message.executions || []).concat(executionRecord(request, result));
    }
    renderMessages(container);
    return result;
  }

  // The request headers every chat call carries: content type and any BYOK key.
  function chatRequestHeaders() {
    const headers = { 'Content-Type': 'application/json' };
    // The provider is checked explicitly rather than treating "not anthropic"
    // as "openrouter", so an unrecognized provider never silently sends the
    // key on the wrong header.
    if (userSettings.apiKey) {
      if (userSettings.keyProvider === 'anthropic') {
        headers['X-Anthropic-API-Key'] = userSettings.apiKey;
      } else if (userSettings.keyProvider === 'openrouter') {
        headers['X-OpenRouter-Key'] = userSettings.apiKey;
      } else {
        console.error('[OSA] BYOK key has unknown provider; not sent with the request:', userSettings.keyProvider);
      }
    }
    return headers;
  }

  // Turn a failed response into an Error carrying the most useful message.
  async function responseError(response) {
    let errorMessage = `Request failed (${response.status})`;
    try {
      const error = await response.json();
      if (error && typeof error.detail === 'string') {
        errorMessage = error.detail.substring(0, 500);
      } else if (error && typeof error.error === 'string') {
        errorMessage = error.error.substring(0, 500);
      }
    } catch {
      // Response wasn't JSON - use status-based message
      if (response.status >= 500) {
        errorMessage = 'The service is temporarily unavailable. Please try again later.';
      } else if (response.status === 429) {
        errorMessage = 'Too many requests. Please wait a moment and try again.';
      } else if (response.status === 403) {
        errorMessage = 'Access denied. Please complete the security verification.';
      }
    }
    return new Error(errorMessage);
  }

  // Send a browser run's result and return the stream that continues the reply.
  async function postResume(request, result) {
    const body = {
      session_id: request.session_id || sessionId,
      result,
      client_tools: browserTools ? browserTools.declared : [],
    };
    const pageContext = getPageContext();
    if (pageContext) body.page_context = pageContext;
    if (userSettings.model) body.model = userSettings.model;
    const response = await fetch(`${CONFIG.apiEndpoint}/${CONFIG.communityId}/chat/resume`, {
      method: 'POST',
      headers: chatRequestHeaders(),
      body: JSON.stringify(body),
      signal: AbortSignal.timeout(120000),
    });
    if (!response.ok) throw await responseError(response);
    const contentType = response.headers.get('content-type') || '';
    if (!contentType.includes('text/event-stream')) {
      throw new Error('Invalid response from server');
    }
    return response;
  }

  // The panel shown while a tool_request is answered, as HTML.
  function toolPanelHtml() {
    if (!toolActivity) return '';
    const prompt = toolActivity.prompt || {};
    const highlight = window.OSARuntime && window.OSARuntime.highlightPython;
    const codeHtml = highlight ? highlight(prompt.code || '') : escapeHtml(prompt.code || '');
    const description = prompt.description
      ? `<div class="osa-tool-panel-description">${escapeHtml(prompt.description)}</div>`
      : '';
    if (toolActivity.phase === 'asking') {
      return `
        <div class="osa-tool-panel" role="group" aria-label="Run code in your browser?">
          <div class="osa-tool-panel-title">Run this Python in your browser?</div>
          ${description}
          <pre class="osa-tool-code"><code>${codeHtml}</code></pre>
          <div class="osa-tool-actions">
            <button type="button" class="osa-tool-run">Run</button>
            <button type="button" class="osa-tool-deny">Don't run</button>
            <label class="osa-tool-autorun"><input type="checkbox" class="osa-tool-autorun-input"> Run without asking until I reload</label>
          </div>
        </div>`;
    }
    const status = toolActivity.progress || 'Running Python in your browser...';
    return `
      <div class="osa-tool-panel" role="status">
        <div class="osa-tool-panel-title">Running Python</div>
        ${description}
        <div class="osa-tool-actions">
          <span class="osa-tool-status">${escapeHtml(status)}</span>
          <button type="button" class="osa-tool-stop">Stop</button>
        </div>
      </div>`;
  }

  // What ran for a reply, as HTML: one collapsible entry per run.
  function executionsHtml(executions) {
    if (!Array.isArray(executions) || executions.length === 0) return '';
    const highlight = window.OSARuntime && window.OSARuntime.highlightPython;
    const label = {
      ok: 'Ran Python',
      error: 'Python raised an error',
      denied: 'Not run',
      timeout: 'Stopped: took too long',
      cancelled: 'Stopped',
      oom: 'Stopped: out of memory',
    };
    return executions.map((run) => {
      const title = `${label[run.status] || 'Python'}${run.description ? ': ' + run.description : ''}`;
      const code = run.code
        ? `<pre class="osa-tool-code"><code>${highlight ? highlight(run.code) : escapeHtml(run.code)}</code></pre>`
        : '';
      const stdout = run.stdout ? `<pre class="osa-execution-output">${escapeHtml(run.stdout)}</pre>` : '';
      const stderr = run.stderr && run.status !== 'ok'
        ? `<pre class="osa-execution-output">${escapeHtml(run.stderr)}</pre>`
        : '';
      const images = (Array.isArray(run.images) ? run.images : [])
        .filter((image) => image && image.mime === 'image/png' && typeof image.data_base64 === 'string'
          && /^[A-Za-z0-9+/]+=*$/.test(image.data_base64))
        .map((image) => `<img alt="Figure produced by the code" src="data:image/png;base64,${image.data_base64}">`)
        .join('');
      // Figures open by default: they are the point of most runs.
      return `
        <details class="osa-execution"${images ? ' open' : ''}>
          <summary>${escapeHtml(title)}</summary>
          ${code}${stdout}${stderr}${images}
        </details>`;
    }).join('');
  }

  // Update DOM elements to reflect current CONFIG values (called after API config load)
  function applyWidgetConfig() {
    const container = document.querySelector('.osa-chat-widget');
    if (!container) return;

    // Apply theme color if configured (must be valid #RRGGBB hex)
    if (CONFIG.themeColor && /^#[0-9a-fA-F]{6}$/.test(CONFIG.themeColor)) {
      container.style.setProperty('--osa-primary', CONFIG.themeColor);
      // Derive a darker shade for hover states
      const r = parseInt(CONFIG.themeColor.slice(1, 3), 16);
      const g = parseInt(CONFIG.themeColor.slice(3, 5), 16);
      const b = parseInt(CONFIG.themeColor.slice(5, 7), 16);
      const darker = '#' +
        Math.max(0, r - 25).toString(16).padStart(2, '0') +
        Math.max(0, g - 25).toString(16).padStart(2, '0') +
        Math.max(0, b - 25).toString(16).padStart(2, '0');
      container.style.setProperty('--osa-primary-dark', darker);
    }

    // Apply disclaimer colors if configured (must be valid CSS color: hex, named, rgb, hsl)
    const cssColorPattern = /^(#[0-9a-fA-F]{3,8}|[a-zA-Z]+|rgba?\([^)]+\)|hsla?\([^)]+\))$/;
    if (CONFIG.disclaimerColor && cssColorPattern.test(CONFIG.disclaimerColor.trim())) {
      container.style.setProperty('--osa-disclaimer-color', CONFIG.disclaimerColor.trim());
    }
    if (CONFIG.disclaimerBackground && cssColorPattern.test(CONFIG.disclaimerBackground.trim())) {
      container.style.setProperty('--osa-disclaimer-bg', CONFIG.disclaimerBackground.trim());
    }

    // Update header title
    const titleEl = container.querySelector('.osa-chat-title');
    if (titleEl) {
      const badge = titleEl.querySelector('.osa-experimental-badge');
      titleEl.textContent = CONFIG.title;
      if (badge) titleEl.appendChild(badge);
    }

    // Update tooltip
    const tooltip = container.querySelector('.osa-chat-tooltip');
    if (tooltip) {
      tooltip.textContent = 'Ask me about ' + CONFIG.title.replace(' Assistant', '');
    }

    // Update input placeholder
    const input = container.querySelector('.osa-chat-input input');
    if (input) {
      input.placeholder = CONFIG.placeholder;
    }

    // Update initial assistant message if chat has only the default greeting
    if (messages.length === 1 && messages[0].role === 'assistant') {
      messages[0].content = CONFIG.initialMessage;
      renderMessages(container);
    }

    // Update suggested questions
    renderSuggestions(container);

    // Update avatar with community logo if available
    const avatar = container.querySelector('.osa-chat-avatar');
    if (avatar && CONFIG.logo) {
      const fallback = avatar.innerHTML;
      const img = document.createElement('img');
      img.src = CONFIG.logo;
      img.alt = CONFIG.title;
      img.onerror = function() {
        console.warn('[OSA] Failed to load community logo:', CONFIG.logo);
        avatar.innerHTML = fallback;
        img.onerror = null;
      };
      avatar.innerHTML = '';
      avatar.appendChild(img);
    }

    // Update loading label if currently loading
    const loadingLabel = container.querySelector('.osa-loading-label');
    if (loadingLabel) {
      loadingLabel.textContent = isThinking ? 'Thinking...' : CONFIG.title;
    }
  }

  // Open settings modal
  function openSettings(container) {
    // Don't open settings if chat window is closed
    if (!isOpen) return;

    const overlay = container.querySelector('.osa-settings-overlay');
    const apiKeyInput = container.querySelector('#osa-settings-api-key');
    const modelSelect = container.querySelector('#osa-settings-model');
    const customModelField = container.querySelector('#osa-settings-custom-model-field');
    const customModelInput = container.querySelector('#osa-settings-custom-model');
    const modelHint = container.querySelector('#osa-settings-model-hint');

    // Rebuild the model options from the live offered_models list (falls
    // back to DEFAULT_MODELS until fetchCommunityConfig resolves), so a
    // config that loads after the widget's initial render is still
    // reflected the next time settings are opened.
    if (modelSelect) {
      const options = getModelMenuOptions()
        .filter(m => m.value !== communityDefaultModel)
        .map(m => `<option value="${escapeHtml(m.value)}">${escapeHtml(m.label)}</option>`)
        .join('');
      modelSelect.innerHTML = `<option value="default">Default (Community Setting)</option>${options}<option value="custom">Custom</option>`;
    }

    // Update default option label with community default model
    if (modelSelect) {
      const defaultOption = modelSelect.querySelector('option[value="default"]');
      if (defaultOption) {
        if (!communityDefaultModel) {
          // Make it obvious something is wrong
          defaultOption.textContent = 'Default (ERROR: Not configured)';
          defaultOption.disabled = true;
          console.error('[OSA] Cannot populate model selector - no default model loaded');
        } else {
          // Use human-readable label if available
          const modelLabel = getModelLabel(communityDefaultModel);
          defaultOption.textContent = `Default (${modelLabel})`;
          defaultOption.disabled = false;
        }
      }
    }

    // Populate form with current settings
    if (apiKeyInput) {
      apiKeyInput.value = userSettings.apiKey || '';
    }
    if (modelSelect) {
      // Check if current model is in the offered list
      const isDefaultModel = userSettings.model === null || getModelMenuOptions().some(m => m.value === userSettings.model);
      if (isDefaultModel) {
        modelSelect.value = userSettings.model || 'default';
        if (customModelField) customModelField.style.display = 'none';
      } else {
        // Custom model
        modelSelect.value = 'custom';
        if (customModelInput) customModelInput.value = userSettings.model;
        if (customModelField) customModelField.style.display = 'block';
      }
    }

    // Update hint with current default model
    if (modelHint) {
      if (communityDefaultModel) {
        const modelLabel = getModelLabel(communityDefaultModel);
        modelHint.textContent = `Community default: ${modelLabel}`;
        modelHint.style.color = '';  // Reset to default color
      } else {
        modelHint.textContent = 'ERROR: Default model not loaded. Widget may not function correctly.';
        modelHint.style.color = '#e53e3e';  // Red color for error
      }
    }

    if (overlay) {
      overlay.classList.add('open');
    }
  }

  // Close settings modal
  function closeSettings(container) {
    const overlay = container.querySelector('.osa-settings-overlay');
    if (overlay) {
      overlay.classList.remove('open');
    }
  }

  // Save settings from modal
  function saveSettings(container) {
    const apiKeyInput = container.querySelector('#osa-settings-api-key');
    const modelSelect = container.querySelector('#osa-settings-model');
    const customModelInput = container.querySelector('#osa-settings-custom-model');

    // Get values
    const apiKey = apiKeyInput ? apiKeyInput.value.trim() : '';
    const modelSelection = modelSelect ? modelSelect.value : 'default';

    // Validate API key format if provided: either an Anthropic or an
    // OpenRouter key.
    if (apiKey && !isValidApiKey(apiKey)) {
      showError(container, 'Invalid API key format. Expected an Anthropic key (sk-ant-...) or an OpenRouter key (sk-or-v1-[64 hex chars]).');
      return;
    }

    // Determine final model value
    let model = null;
    if (modelSelection === 'custom') {
      model = customModelInput ? customModelInput.value.trim() : null;
      if (!model) {
        showError(container, 'Please enter a custom model name');
        return;
      }
      if (!isValidModelId(model)) {
        showError(container, 'Invalid model format. Expected a Claude model id or provider/model-name');
        return;
      }
    } else if (modelSelection !== 'default') {
      model = modelSelection;
    }

    // Update settings. keyProvider is always re-derived from the key
    // itself, never stored as an independent choice.
    userSettings.apiKey = apiKey || null;
    userSettings.model = model;
    userSettings.keyProvider = inferKeyProvider(apiKey);

    // Save to localStorage
    try {
      saveUserSettings();
    } catch (e) {
      // Error already shown by saveUserSettings()
      // Don't close modal if save failed
      return;
    }

    // Close modal only if save succeeded
    closeSettings(container);
  }

  // --- Feedback -----------------------------------------------------------

  // Low-level POST to the feedback endpoint. In production the request goes
  // through the Cloudflare Worker proxy; in development CONFIG.apiEndpoint
  // points directly to the backend. Best-effort: never throws to the caller.
  async function postFeedback(payload) {
    try {
      const response = await fetch(`${CONFIG.apiEndpoint}/feedback`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ community_id: CONFIG.communityId, ...payload }),
        signal: AbortSignal.timeout(10000),
      });
      if (!response.ok) {
        console.warn('[OSA] Feedback submission returned', response.status);
        return false;
      }
      return true;
    } catch (e) {
      console.error('[OSA] Failed to submit feedback:', e);
      return false;
    }
  }

  // Select a thumbs up/down on a specific assistant reply. One vote per reply
  // per browser session (stored in localStorage). Thumbs-up commits immediately;
  // thumbs-down reveals an optional "what went wrong?" box and commits when the
  // user sends/skips (or when the vote is flushed on send/reset/close).
  function submitResponseFeedback(container, msgIndex, sentiment) {
    const msg = messages[msgIndex];
    if (!msg || msg.role !== 'assistant') return;
    if (msg.feedback) return; // already voted on this reply
    if (sentiment !== 'up' && sentiment !== 'down') return;

    msg.feedback = sentiment;
    if (sentiment === 'down') {
      // Defer the post; reveal the optional comment box first.
      msg._feedbackJustOpened = true;
      renderMessages(container);
    } else {
      renderMessages(container); // show the selection immediately
      commitResponseFeedback(container, msgIndex, { interactive: true });
    }
  }

  function isSameResponseMessage(original, current) {
    return current === original
      || (original?._responseId && current?._responseId === original._responseId)
      || (original?.requestId && current?.requestId === original.requestId);
  }

  // Post a per-response vote (with the optional down-vote comment) exactly once.
  // Confirm-then-commit: the "Thanks!" / committed state is only shown AFTER the
  // POST succeeds, so a failure never leaves a false success (in the UI or in
  // localStorage). interactive=true (Send/Skip/up click) surfaces failures so the
  // user can retry; interactive=false (a flush on send/reset/close) is best-effort
  // and never writes to the UI, since the conversation may be mid-teardown.
  async function commitResponseFeedback(container, msgIndex, { interactive = false } = {}) {
    const msg = messages[msgIndex];
    if (!msg || !msg.feedback) return;
    if (msg.feedbackCommitted || msg._feedbackCommitting) return;
    msg._feedbackCommitting = true;

    const sentiment = msg.feedback;
    const comment = (msg.feedbackDraft || '').trim();

    const ok = await postFeedback({
      feedback_type: 'response',
      sentiment,
      comment: comment || null,
      request_id: msg.requestId || null,
      session_id: sessionId || null,
      message_index: msgIndex,
    });
    // The streaming completion path may replace the assistant object while
    // this request is in flight. Re-acquire the current object so the result
    // is not applied only to the stale object captured before the await.
    const currentMsg = messages[msgIndex];
    const sameResponse = isSameResponseMessage(msg, currentMsg);
    if (!currentMsg || currentMsg.role !== 'assistant' || !sameResponse) {
      return;
    }
    currentMsg._feedbackCommitting = false;

    if (ok) {
      currentMsg.feedbackCommitted = true;
      delete currentMsg.feedbackDraft;
      delete currentMsg._feedbackJustOpened;
      // Reveal "Thanks!" (and replace any open box). Safe in every path: harmless
      // on a hidden window, and a no-op for a reply already removed by a reset.
      renderMessages(container);
      try {
        saveHistory();
      } catch (e) {
        console.error('[OSA] Failed to persist feedback locally:', e);
      }
      return;
    }

    // Failed. An up-vote reverts to unvoted; a down-vote keeps its pending box
    // (and the typed comment) so it can be retried on the next Send or flush.
    if (sentiment === 'up') delete currentMsg.feedback;
    if (!interactive) {
      // Best-effort flush during teardown: do not touch the (possibly hidden or
      // already-reset) UI. A pending down stays pending and retries next flush.
      console.warn('[OSA] Feedback flush did not send; will retry on next attempt.');
      return;
    }
    if (sentiment === 'down') currentMsg._feedbackJustOpened = true; // refocus the box
    renderMessages(container);
    showError(container, 'Could not send feedback. Please try again.');
  }

  // Commit any pending (down) vote whose comment box is still open, so leaving
  // it open and then sending/resetting/closing never silently drops the vote.
  // Best-effort (interactive=false): failures are not surfaced into a tearing-down UI.
  function flushPendingResponseFeedback(container) {
    messages.forEach((msg, idx) => {
      if (msg && msg.role === 'assistant' && msg.feedback
          && !msg.feedbackCommitted && !msg._feedbackCommitting) {
        commitResponseFeedback(container, idx, { interactive: false });
      }
    });
  }

  // Open the general (free-text) feedback modal
  function openFeedback(container) {
    const overlay = container.querySelector('.osa-feedback-overlay');
    if (!overlay) return;
    const textarea = container.querySelector('#osa-feedback-text');
    const thanks = container.querySelector('.osa-feedback-modal-thanks');
    const form = container.querySelector('.osa-feedback-modal-form');
    if (textarea) textarea.value = '';
    if (thanks) thanks.style.display = 'none';
    if (form) form.style.display = 'block';
    overlay.classList.add('open');
    if (textarea) textarea.focus();
  }

  function closeFeedback(container) {
    const overlay = container.querySelector('.osa-feedback-overlay');
    if (overlay) overlay.classList.remove('open');
  }

  // Send free-text general feedback (not tied to a single reply)
  async function submitGeneralFeedback(container) {
    const textarea = container.querySelector('#osa-feedback-text');
    const comment = textarea ? textarea.value.trim() : '';
    if (!comment) {
      showError(container, 'Please enter some feedback first.');
      return;
    }
    if (comment.length > 5000) {
      showError(container, 'Feedback is too long (5000 character max).');
      return;
    }

    // Guard against a double-click submitting the comment twice while the POST
    // is in flight (each would store a separate row).
    const sendBtn = container.querySelector('.osa-feedback-send-btn');
    if (sendBtn) {
      if (sendBtn.disabled) return;
      sendBtn.disabled = true;
    }

    let ok = false;
    try {
      ok = await postFeedback({
        feedback_type: 'general',
        comment,
        session_id: sessionId || null,
        page_url: (typeof window !== 'undefined' && window.location) ? window.location.href : null,
      });
    } finally {
      if (sendBtn) sendBtn.disabled = false;
    }

    if (ok) {
      const thanks = container.querySelector('.osa-feedback-modal-thanks');
      const form = container.querySelector('.osa-feedback-modal-form');
      if (form) form.style.display = 'none';
      if (thanks) thanks.style.display = 'block';
      setTimeout(() => closeFeedback(container), 1500);
    } else {
      showError(container, 'Could not send feedback. Please try again later.');
    }
  }

  // Check backend health status
  async function checkBackendStatus() {
    const statusDot = document.querySelector('.osa-status-dot');
    const statusText = document.querySelector('.osa-status-text');

    if (!statusDot || !statusText) return;

    try {
      const response = await fetch(`${CONFIG.apiEndpoint}/health`, {
        method: 'GET',
        signal: AbortSignal.timeout(5000)
      });

      if (response.ok) {
        backendOnline = true;
        statusDot.className = 'osa-status-dot';
        statusText.textContent = 'Online';

        // Extract version and commit SHA from health response
        try {
          const data = await response.json();
          if (data.backend) {
            if (data.backend.version) {
              backendVersion = data.backend.version;
            }
            if (data.backend.commit_sha) {
              backendCommitSha = data.backend.commit_sha;
            }
            updateFooterVersion();
          }
        } catch (jsonErr) {
          console.debug('[OSA] Could not parse health response:', jsonErr.message);
        }
      } else {
        backendOnline = false;
        statusDot.className = 'osa-status-dot offline';
        statusText.textContent = 'Offline';
        console.error(`[OSA] Backend health check failed: HTTP ${response.status}`);
        const container = document.querySelector('.osa-chat-widget');
        if (container && isOpen) {
          showError(container, `Backend service unavailable (HTTP ${response.status}). Please try again later.`);
        }
      }
    } catch (e) {
      backendOnline = false;
      statusDot.className = 'osa-status-dot offline';
      statusText.textContent = 'Offline';
      console.error('[OSA] Backend health check error:', e.message || e);
      const container = document.querySelector('.osa-chat-widget');
      if (container && isOpen) {
        showError(container, 'Cannot connect to backend service. Check your network connection.');
      }
    }
  }

  // Update footer with version info
  function updateFooterVersion() {
    const versionSpan = document.querySelector('.osa-version');
    if (versionSpan) {
      let versionText = '';
      if (backendVersion) {
        versionText = ` v${backendVersion}`;
      }
      if (backendCommitSha) {
        // Show short SHA (first 7 characters)
        const shortSha = backendCommitSha.substring(0, 7);
        versionText += ` (${shortSha})`;
      }
      versionSpan.textContent = versionText;
    }
  }

  // Update status display
  function updateStatusDisplay(online) {
    const statusDot = document.querySelector('.osa-status-dot');
    const statusText = document.querySelector('.osa-status-text');

    if (!statusDot || !statusText) return;

    if (online) {
      backendOnline = true;
      statusDot.className = 'osa-status-dot';
      statusText.textContent = 'Online';
    } else {
      backendOnline = false;
      statusDot.className = 'osa-status-dot offline';
      statusText.textContent = 'Offline';
    }
  }

  // Create and inject styles
  function injectStyles() {
    const style = document.createElement('style');
    style.textContent = STYLES;
    document.head.appendChild(style);
  }

  // Setup resize functionality
  function setupResize(chatWindow) {
    const resizeHandle = chatWindow.querySelector('.osa-resize-handle');
    if (!resizeHandle) return;

    let isResizing = false;
    let startX, startY, startWidth, startHeight;

    resizeHandle.addEventListener('mousedown', (e) => {
      isResizing = true;
      startX = e.clientX;
      startY = e.clientY;
      startWidth = chatWindow.offsetWidth;
      startHeight = chatWindow.offsetHeight;
      e.preventDefault();
    });

    document.addEventListener('mousemove', (e) => {
      if (!isResizing) return;

      // Resize from top-left corner (since window is anchored bottom-right)
      const newWidth = startWidth - (e.clientX - startX);
      const newHeight = startHeight - (e.clientY - startY);

      // Set minimum and maximum sizes
      if (newWidth >= 300 && newWidth <= 600) {
        chatWindow.style.width = newWidth + 'px';
      }
      if (newHeight >= 350 && newHeight <= 800) {
        chatWindow.style.height = newHeight + 'px';
      }
    });

    document.addEventListener('mouseup', () => {
      isResizing = false;
    });
  }

  // Create the widget DOM
  function createWidget() {
    const container = document.createElement('div');
    container.className = 'osa-chat-widget' + (CONFIG.fullscreen ? ' fullscreen' : '');

    const experimentalBadge = CONFIG.showExperimentalBadge
      ? '<span class="osa-experimental-badge">Experimental</span>'
      : '';

    container.innerHTML = `
      <button class="osa-chat-button" aria-label="Open chat">
        ${ICONS.chat}
      </button>
      <div class="osa-chat-tooltip">Ask me about ${escapeHtml(CONFIG.title.replace(' Assistant', ''))}</div>
      <div class="osa-chat-window">
        <div class="osa-resize-handle"></div>
        <div class="osa-chat-header">
          <div class="osa-chat-avatar">${ICONS.brain}</div>
          <div class="osa-chat-title-area">
            <h3 class="osa-chat-title">
              ${escapeHtml(CONFIG.title)}
              ${experimentalBadge}
            </h3>
            <div class="osa-chat-status">
              <span class="osa-status-dot checking"></span>
              <span class="osa-status-text">Checking...</span>
            </div>
          </div>
          <div class="osa-header-actions">
            <button class="osa-header-btn osa-settings-btn-open" title="Settings">
              ${ICONS.settings}
            </button>
            <button class="osa-header-btn osa-popout-btn" title="Open in new window" style="display: ${CONFIG.fullscreen ? 'none' : 'flex'}">
              ${ICONS.popout}
            </button>
            <button class="osa-header-btn osa-reset-btn" title="Clear chat">
              ${ICONS.reset}
            </button>
            <button class="osa-header-btn osa-close-btn" title="Close" style="display: ${CONFIG.fullscreen ? 'none' : 'flex'}">
              ${ICONS.close}
            </button>
          </div>
        </div>
        <div class="osa-chat-messages"></div>
        <div class="osa-suggestions" style="display: none;">
          <span class="osa-suggestions-label">Try asking:</span>
          <div class="osa-suggestions-list"></div>
        </div>
        <div class="osa-turnstile-container" style="display: none;"></div>
        <div class="osa-error" style="display: none;"></div>
        <div class="osa-warning" style="display: none;"></div>
        <div class="osa-chat-input">
          <input type="text" placeholder="${escapeHtml(CONFIG.placeholder)}" />
          <button class="osa-send-btn" aria-label="Send">
            ${ICONS.send}
          </button>
        </div>
        <div class="osa-ai-disclaimer" style="display: ${CONFIG.disclaimerEnabled ? 'flex' : 'none'}">
          <span class="osa-disclaimer-text">${escapeHtml(CONFIG.disclaimerText || '')}</span>
          <button type="button" class="osa-feedback-link">Send feedback</button>
        </div>
        <div class="osa-combined-footer">
          <div class="osa-page-context-toggle" style="display: ${CONFIG.allowPageContext ? 'flex' : 'none'}">
            <input type="checkbox" id="osa-page-context-checkbox" ${pageContextEnabled ? 'checked' : ''} />
            <label for="osa-page-context-checkbox">${escapeHtml(CONFIG.pageContextLabel)}</label>
          </div>
          <div class="osa-footer-powered">
            Powered by <a href="${escapeHtml(CONFIG.repoUrl)}" target="_blank" rel="noopener noreferrer">OSA</a><span class="osa-version"></span>
          </div>
        </div>
        <div class="osa-settings-overlay">
        <div class="osa-settings-modal">
          <div class="osa-settings-header">
            <h3 class="osa-settings-title">Settings</h3>
            <button class="osa-settings-close-btn" aria-label="Close settings">
              ${ICONS.close}
            </button>
          </div>
          <div class="osa-settings-body">
            <div class="osa-settings-field">
              <label class="osa-settings-label" for="osa-settings-api-key">
                API Key (Optional)
              </label>
              <input
                type="password"
                id="osa-settings-api-key"
                class="osa-settings-input"
                placeholder="sk-ant-... or sk-or-v1-..."
                autocomplete="off"
              />
              <span class="osa-settings-hint">
                Use your own Anthropic or OpenRouter API key for testing. Stored locally in your browser.
              </span>
            </div>
            <div class="osa-settings-field">
              <label class="osa-settings-label" for="osa-settings-model">
                Model Selection
              </label>
              <select id="osa-settings-model" class="osa-settings-select">
                <option value="default">Default (Community Setting)</option>
                ${getModelMenuOptions().filter(m => m.value !== communityDefaultModel).map(m => `<option value="${escapeHtml(m.value)}">${escapeHtml(m.label)}</option>`).join('')}
                <option value="custom">Custom</option>
              </select>
              <span class="osa-settings-hint" id="osa-settings-model-hint">
                Select a model or use the community default
              </span>
            </div>
            <div class="osa-settings-field" id="osa-settings-custom-model-field" style="display: none;">
              <label class="osa-settings-label" for="osa-settings-custom-model">
                Model name, requires your own <a href="https://openrouter.ai/models" target="_blank" rel="noopener noreferrer" style="color: var(--osa-primary); text-decoration: underline;">OpenRouter</a> key
              </label>
              <input
                type="text"
                id="osa-settings-custom-model"
                class="osa-settings-input"
                placeholder="provider/model-name"
                autocomplete="off"
              />
            </div>
          </div>
          <div class="osa-settings-footer">
            <button class="osa-settings-btn osa-settings-btn-cancel">
              Cancel
            </button>
            <button class="osa-settings-btn osa-settings-btn-save">
              Save
            </button>
          </div>
        </div>
      </div>
        <div class="osa-settings-overlay osa-feedback-overlay">
        <div class="osa-settings-modal osa-feedback-modal">
          <div class="osa-settings-header">
            <h3 class="osa-settings-title">Send feedback</h3>
            <button class="osa-settings-close-btn osa-feedback-close-btn" aria-label="Close feedback">
              ${ICONS.close}
            </button>
          </div>
          <div class="osa-settings-body osa-feedback-modal-form">
            <div class="osa-settings-field">
              <label class="osa-settings-label" for="osa-feedback-text">
                Tell us what's working or what could be better
              </label>
              <textarea
                id="osa-feedback-text"
                class="osa-settings-input osa-feedback-textarea"
                rows="4"
                maxlength="5000"
                placeholder="Your feedback helps the maintainers improve this assistant..."
              ></textarea>
              <span class="osa-settings-hint">
                Shared with the ${escapeHtml(CONFIG.title.replace(' Assistant', ''))} community maintainers. Please do not include personal information.
              </span>
            </div>
          </div>
          <div class="osa-feedback-modal-thanks" style="display: none;">
            Thanks for your feedback!
          </div>
          <div class="osa-settings-footer">
            <button class="osa-settings-btn osa-settings-btn-cancel osa-feedback-cancel-btn">
              Cancel
            </button>
            <button class="osa-settings-btn osa-settings-btn-save osa-feedback-send-btn">
              Send
            </button>
          </div>
        </div>
      </div>
      </div>
    `;
    document.body.appendChild(container);

    // Setup resize
    const chatWindow = container.querySelector('.osa-chat-window');
    setupResize(chatWindow);

    return container;
  }

  // Render messages
  function renderMessages(container) {
    const messagesEl = container.querySelector('.osa-chat-messages');
    messagesEl.innerHTML = '';

    messages.forEach((msg, msgIndex) => {
      // The streaming handler keeps an empty assistant entry so the final
      // response can update it in place. While the model is thinking, that
      // state must stay invisible: the loading bubble below is the assistant
      // response placeholder until the first answer text arrives.
      if (isLoading && msg.role === 'assistant' && !msg.content && !(msg.executions && msg.executions.length)
        && msgIndex === messages.length - 1) {
        return;
      }

      const msgEl = document.createElement('div');
      msgEl.className = `osa-message ${msg.role}`;

      const label = msg.role === 'user' ? 'You' : CONFIG.title;

      // Build a marker -> citation lookup for this message (empty for a
      // message with no citations, e.g. every OpenRouter-answered reply).
      const citationsByMarker = {};
      const citations = Array.isArray(msg.citations) ? msg.citations : [];
      citations.forEach((c) => {
        if (c && typeof c.marker !== 'undefined') citationsByMarker[c.marker] = c;
      });

      const content = msg.role === 'assistant'
        ? markdownToHtml(msg.content, citationsByMarker)
        : escapeHtml(msg.content);

      // Compact numbered source list under the answer, when anything was cited.
      let sourcesRow = '';
      if (msg.role === 'assistant' && citations.length) {
        const items = citations.map((c) => {
          const sourceLabel = escapeHtml(String(c.title || c.source || ''));
          const inner = isSafeUrl(c.source)
            ? '<a href="' + escapeHtml(c.source) + '" target="_blank" rel="noopener noreferrer">' + sourceLabel + '</a>'
            : '<span>' + sourceLabel + '</span>';
          return '<li><span class="osa-source-marker">[' + escapeHtml(String(c.marker)) + ']</span> ' + inner + '</li>';
        }).join('');
        sourcesRow = '<ul class="osa-message-sources">' + items + '</ul>';
      }

      // Add copy button for assistant messages
      const copyBtn = msg.role === 'assistant'
        ? `<button class="osa-message-copy-btn" data-msg-index="${msgIndex}" title="Copy as markdown">${ICONS.copy}</button>`
        : '';

      // Per-response feedback (thumbs up/down) for assistant replies, but not
      // the canned opening greeting (index 0). Up is a one-click count; down
      // reveals an optional "what went wrong?" box before it is committed.
      const showFeedback = msg.role === 'assistant' && msgIndex > 0;
      const fb = msg.feedback;
      const committed = !!msg.feedbackCommitted;
      const pendingDown = fb === 'down' && !committed;
      let feedbackRow = '';
      if (showFeedback) {
        const upBtn = `<button class="osa-feedback-btn osa-feedback-up${fb === 'up' ? ' selected' : ''}" data-feedback="up" aria-pressed="${fb === 'up'}" title="Helpful">${ICONS.thumbUp}</button>`;
        const downBtn = `<button class="osa-feedback-btn osa-feedback-down${fb === 'down' ? ' selected' : ''}" data-feedback="down" aria-pressed="${fb === 'down'}" title="Not helpful">${ICONS.thumbDown}</button>`;
        if (pendingDown) {
          feedbackRow = `<div class="osa-message-feedback recorded" data-msg-index="${msgIndex}">
            ${upBtn}${downBtn}
            <div class="osa-feedback-comment">
              <textarea class="osa-feedback-comment-input" rows="2" maxlength="5000" placeholder="What went wrong? (optional)">${escapeHtml(msg.feedbackDraft || '')}</textarea>
              <div class="osa-feedback-comment-actions">
                <button type="button" class="osa-feedback-skip">Skip</button>
                <button type="button" class="osa-feedback-send">Send</button>
              </div>
            </div>
          </div>`;
        } else {
          feedbackRow = `<div class="osa-message-feedback${fb ? ' recorded' : ''}" data-msg-index="${msgIndex}">
            ${upBtn}${downBtn}
            <span class="osa-feedback-thanks"${committed ? '' : ' style="display:none;"'}>Thanks!</span>
          </div>`;
        }
      }

      msgEl.innerHTML = `
        <div class="osa-message-header">
          <span class="osa-message-label">${escapeHtml(label)}</span>
          ${copyBtn}
        </div>
        ${msg.role === 'assistant' ? executionsHtml(msg.executions) : ''}
        <div class="osa-message-content">${content}</div>
        ${sourcesRow}
        ${feedbackRow}
      `;
      messagesEl.appendChild(msgEl);
    });

    // Add event listeners for copy buttons
    // Code block copy buttons
    messagesEl.querySelectorAll('.osa-copy-btn[data-copy-target]').forEach(btn => {
      btn.addEventListener('click', (e) => {
        e.stopPropagation();
        const codeId = btn.getAttribute('data-copy-target');
        const pre = messagesEl.querySelector(`pre[data-code-id="${codeId}"]`);
        if (pre) {
          const code = pre.querySelector('code');
          if (code) {
            copyToClipboard(code.textContent, btn);
          }
        }
      });
    });

    // Message copy buttons (copy markdown source)
    messagesEl.querySelectorAll('.osa-message-copy-btn[data-msg-index]').forEach(btn => {
      btn.addEventListener('click', (e) => {
        e.stopPropagation();
        const msgIndex = parseInt(btn.getAttribute('data-msg-index'), 10);
        if (messages[msgIndex] && messages[msgIndex].content) {
          copyToClipboard(messages[msgIndex].content, btn);
        }
      });
    });

    // Per-response thumbs up/down buttons
    messagesEl.querySelectorAll('.osa-message-feedback .osa-feedback-btn[data-feedback]').forEach(btn => {
      btn.addEventListener('click', (e) => {
        e.stopPropagation();
        const row = btn.closest('.osa-message-feedback');
        const msgIndex = parseInt(row.getAttribute('data-msg-index'), 10);
        const sentiment = btn.getAttribute('data-feedback');
        submitResponseFeedback(container, msgIndex, sentiment);
      });
    });

    // Optional comment box for a pending thumbs-down
    messagesEl.querySelectorAll('.osa-message-feedback .osa-feedback-comment').forEach(box => {
      const row = box.closest('.osa-message-feedback');
      const msgIndex = parseInt(row.getAttribute('data-msg-index'), 10);
      const textarea = box.querySelector('.osa-feedback-comment-input');
      if (textarea) {
        textarea.addEventListener('input', () => {
          if (messages[msgIndex]) messages[msgIndex].feedbackDraft = textarea.value;
        });
        // Focus once, right after the box first appears (not on every re-render).
        if (messages[msgIndex] && messages[msgIndex]._feedbackJustOpened) {
          messages[msgIndex]._feedbackJustOpened = false;
          textarea.focus();
        }
      }
      box.querySelector('.osa-feedback-send')?.addEventListener('click', (e) => {
        e.stopPropagation();
        if (textarea && messages[msgIndex]) messages[msgIndex].feedbackDraft = textarea.value;
        commitResponseFeedback(container, msgIndex, { interactive: true });
      });
      box.querySelector('.osa-feedback-skip')?.addEventListener('click', (e) => {
        e.stopPropagation();
        if (messages[msgIndex]) messages[msgIndex].feedbackDraft = '';
        commitResponseFeedback(container, msgIndex, { interactive: true });
      });
    });

    if (toolActivity) {
      // In place of the loading dots: nothing is loading, the reply is waiting
      // on the person or on code running in this page.
      const panelEl = document.createElement('div');
      panelEl.innerHTML = toolPanelHtml();
      messagesEl.appendChild(panelEl);
      const decide = (decision) => {
        const activity = toolActivity;
        if (!activity || activity.phase !== 'asking') return;
        const always = panelEl.querySelector('.osa-tool-autorun-input');
        activity.decide(decision, !!(always && always.checked));
      };
      panelEl.querySelector('.osa-tool-run')?.addEventListener('click', () => decide('run'));
      panelEl.querySelector('.osa-tool-deny')?.addEventListener('click', () => decide('deny'));
      panelEl.querySelector('.osa-tool-stop')?.addEventListener('click', (e) => {
        e.currentTarget.disabled = true;
        if (browserTools) browserTools.cancel();
      });
    } else if (isLoading) {
      const loadingEl = document.createElement('div');
      loadingEl.className = 'osa-loading';
      const loadingLabelText = isThinking ? 'Thinking...' : CONFIG.title;
      loadingEl.innerHTML = `
        <span class="osa-loading-label">${escapeHtml(loadingLabelText)}</span>
        <div class="osa-loading-dots">
          <span class="osa-loading-dot"></span>
          <span class="osa-loading-dot"></span>
          <span class="osa-loading-dot"></span>
        </div>
      `;
      messagesEl.appendChild(loadingEl);
    }

    messagesEl.scrollTop = messagesEl.scrollHeight;
  }

  // Render suggestions
  function renderSuggestions(container) {
    const suggestionsEl = container.querySelector('.osa-suggestions');
    const suggestionsListEl = container.querySelector('.osa-suggestions-list');

    // Only show suggestions if there's just the initial message
    if (messages.length <= 1 && !isLoading) {
      suggestionsListEl.innerHTML = CONFIG.suggestedQuestions.map(q =>
        `<button class="osa-suggestion">${escapeHtml(q)}</button>`
      ).join('');
      suggestionsEl.style.display = 'block';
    } else {
      suggestionsEl.style.display = 'none';
    }
  }

  // Show error
  function showError(container, message) {
    const errorEl = container.querySelector('.osa-error');
    errorEl.textContent = message;
    errorEl.style.display = 'block';
    setTimeout(() => {
      errorEl.style.display = 'none';
    }, 5000);
  }

  function showWarning(container, message) {
    const warningEl = container.querySelector('.osa-warning');
    if (!warningEl) return;
    warningEl.textContent = message;
    warningEl.style.display = 'block';
    setTimeout(() => {
      warningEl.style.display = 'none';
    }, 10000);
  }

  // Parse SSE (Server-Sent Events) format
  // Returns parsed event object or null if line is not a data event
  function parseSSE(line) {
    if (!line || !line.startsWith('data: ')) {
      return null;
    }
    try {
      const jsonStr = line.substring(6); // Remove 'data: ' prefix
      return JSON.parse(jsonStr);
    } catch (error) {
      console.warn('[OSA] Failed to parse SSE line:', line, error);
      return null;
    }
  }

  // Apply the authoritative completion payload to the active assistant
  // message. Kept separate from the stream loop so the state transition can
  // be tested without depending on a live model or browser network.
  function applyDoneEvent(messageList, messageIndex, event, streamedContent) {
    const message = messageList[messageIndex];
    if (!message) return '';

    if (event.request_id && typeof event.request_id === 'string') {
      message.requestId = event.request_id;
    }
    if (Array.isArray(event.citations)) {
      message.citations = event.citations;
    }

    const finalContent = typeof event.content === 'string'
      ? event.content
      : streamedContent;
    // A reply that ran code is kept even when it ends with no text: what ran,
    // and any figure it drew, is part of the answer the reader asked for.
    const ranCode = Array.isArray(message.executions) && message.executions.length > 0;
    if (finalContent || ranCode) {
      messageList[messageIndex] = {
        ...message,
        content: finalContent,
      };
    } else {
      messageList.splice(messageIndex, 1);
    }
    return finalContent;
  }

  // Handle streaming response from API
  // SSE Event formats:
  //   data: {"event": "content", "content": "text chunk"}
  //   data: {"event": "thinking"}
  //   data: {"event": "tool_start", "name": "tool_name", "input": {...}}
  //   data: {"event": "tool_end", "name": "tool_name", "output": "result"}
  //   data: {"event": "citation", "marker": 1, "source": "...", "title": "...", "cited_text": "..."}
  //   data: {"event": "done", "content": "final answer", "citations": [...]}
  //   data: {"event": "tool_request", "call_id": "...", "tool": "...", "args": {...},
  //          "content": "text so far", "citations": [...]}  (instead of done)
  //   data: {"event": "error", "message": "error description"}
  //
  // A browser-execution reply is several runs the reader sees as one message.
  // A run that ends on tool_request resolves with {toolRequest, messageIndex};
  // the caller answers it and streams the next run into the same message by
  // passing `continuation` = {messageIndex}. Any other run resolves with null.
  async function handleStreamingResponse(response, container, continuation = null) {
    const reader = response.body.getReader();
    const decoder = new TextDecoder();
    let buffer = '';
    let accumulatedContent = '';
    let lastUpdateTime = 0;
    let lastChunkTime = Date.now();
    const UPDATE_THROTTLE_MS = 100; // Update UI every 100ms max
    const STREAM_TIMEOUT_MS = 60000; // 60 seconds with no data = timeout
    let receivedDoneEvent = false;
    let receivedFirstContent = false;
    let toolRequest = null;

    // Create placeholder assistant message (not rendered yet - loading dots stay
    // visible), or continue the one an earlier run of this reply wrote into.
    let messageIndex;
    if (continuation && messages[continuation.messageIndex]) {
      messageIndex = continuation.messageIndex;
    } else {
      messages.push({ role: 'assistant', content: '', citations: [], _responseId: createResponseId() });
      messageIndex = messages.length - 1;
    }
    // What earlier runs of this reply already wrote. This run's text follows it.
    const earlier = continuation ? (messages[messageIndex].content || '') : '';
    const compose = (text) => (earlier && text ? `${earlier}\n\n${text}` : earlier || text);

    try {
      while (true) {
        // Check for stream timeout
        const now = Date.now();
        if (now - lastChunkTime > STREAM_TIMEOUT_MS) {
          console.error('[OSA] Stream timeout - no data received for', STREAM_TIMEOUT_MS, 'ms');
          throw new Error('Stream timeout - server stopped responding');
        }

        const { done, value } = await reader.read();

        if (done) {
          break;
        }

        lastChunkTime = Date.now(); // Reset timeout on each chunk

        // Decode chunk and add to buffer
        buffer += decoder.decode(value, { stream: true });

        // Process complete lines (SSE format uses \n\n as delimiter)
        const lines = buffer.split('\n');
        buffer = lines.pop() || ''; // Keep incomplete line in buffer

        for (const line of lines) {
          const event = parseSSE(line);
          if (!event) continue;

          if (event.event === 'content' && event.content) {
            // Hide loading dots on first content chunk
            if (!receivedFirstContent) {
              receivedFirstContent = true;
              isLoading = false;
              isThinking = false;
            }

            // Accumulate content
            accumulatedContent += event.content;

            // Throttle UI updates for performance
            const now = Date.now();
            if (now - lastUpdateTime >= UPDATE_THROTTLE_MS) {
              messages[messageIndex].content = compose(accumulatedContent);
              renderMessages(container);
              lastUpdateTime = now;
            }
          } else if (event.event === 'thinking') {
            // Carries no reasoning text; only swaps the loading label to
            // "Thinking...". Scoped to before the first content chunk so a
            // thinking event arriving between tool calls mid-answer does not
            // make the label flicker under already-rendered content.
            if (!receivedFirstContent && !isThinking) {
              isThinking = true;
              renderMessages(container);
            }
          } else if (event.event === 'tool_start') {
            // Log tool execution for debugging
            console.log('[OSA] Tool started:', event.name, event.input);
          } else if (event.event === 'tool_end') {
            // Log tool completion
            console.log('[OSA] Tool completed:', event.name);
          } else if (event.event === 'citation') {
            // A source was cited for the first time. The backend announces
            // metadata before sending the marker as its own content chunk, so
            // the next render can link it immediately. The final 'done' event
            // replaces that raw stream with canonical sentence placement.
            if (typeof event.marker !== 'undefined' && event.source) {
              messages[messageIndex].citations = messages[messageIndex].citations || [];
              messages[messageIndex].citations.push({
                marker: event.marker,
                source: event.source,
                title: event.title || '',
                cited_text: event.cited_text || '',
              });
            }
          } else if (event.event === 'session') {
            // Capture session ID early (sent at stream start). request_id is
            // intentionally NOT sent here; it arrives on the 'done' event so it
            // only attaches to a reply that completed successfully.
            if (event.session_id && typeof event.session_id === 'string') {
              sessionId = event.session_id;
            }
          } else if (event.event === 'warning') {
            // Display warning banner (e.g., conversation getting long)
            const warningMsg = event.message || 'Warning';
            console.warn('[OSA] Warning:', warningMsg);
            showWarning(container, warningMsg);
          } else if (event.event === 'done') {
            // Finalize message and capture session ID
            receivedDoneEvent = true;
            if (event.session_id && typeof event.session_id === 'string') {
              sessionId = event.session_id;
            }
            // The backend's done.content is canonical and replaces any raw
            // citation boundaries accumulated while streaming.
            const finalContent = applyDoneEvent(
              messages,
              messageIndex,
              {
                ...event,
                content: compose(typeof event.content === 'string' ? event.content : accumulatedContent),
              },
              compose(accumulatedContent),
            );
            accumulatedContent = finalContent;
            renderMessages(container);
            try {
              saveHistory();
            } catch (saveError) {
              console.error('[OSA] Failed to save history:', saveError);
              showError(container, 'Warning: Unable to save conversation');
            }
            updateStatusDisplay(true);
            return null; // Successfully completed
          } else if (event.event === 'tool_request') {
            // The run ended on a call for this browser to answer. No `done`
            // follows: the reply is not finished. content and citations are
            // this run's canonical text, as done would have carried them.
            toolRequest = event;
            if (event.session_id && typeof event.session_id === 'string') {
              sessionId = event.session_id;
            }
            const runText = typeof event.content === 'string' ? event.content : accumulatedContent;
            messages[messageIndex].content = compose(runText);
            if (Array.isArray(event.citations)) {
              messages[messageIndex].citations = event.citations;
            }
            accumulatedContent = runText;
          } else if (event.event === 'error') {
            // Backend sent an error event
            const errorMsg = event.message || 'An error occurred during response generation';
            console.error('[OSA] Backend error event:', errorMsg);

            // Show partial content with error indicator
            const shown = compose(accumulatedContent);
            if (shown) {
              messages[messageIndex].content = shown + `\n\n_[Error: ${errorMsg}]_`;
            } else {
              messages[messageIndex].content = `_[Error: ${errorMsg}]_`;
            }
            renderMessages(container);
            try {
              saveHistory();
            } catch (saveError) {
              console.error('[OSA] Failed to save history:', saveError);
            }

            throw new Error(`Backend streaming error: ${errorMsg}`);
          } else if (event.event) {
            // Unknown event type - log for debugging
            console.warn('[OSA] Unknown SSE event type:', event.event, event);
          }
        }
      }

      if (toolRequest) {
        renderMessages(container);
        return { toolRequest, messageIndex };
      }

      // Stream ended without receiving 'done' event - this is abnormal
      if (!receivedDoneEvent) {
        console.error('[OSA] Stream ended without done event');

        if (compose(accumulatedContent)) {
          messages[messageIndex].content = compose(accumulatedContent) +
            '\n\n_[Response may be incomplete - connection ended unexpectedly]_';
          renderMessages(container);
          try {
            saveHistory();
          } catch (saveError) {
            console.error('[OSA] Failed to save history:', saveError);
          }
          updateStatusDisplay(false);
          showError(container, 'Connection ended unexpectedly. Response may be incomplete.');
        } else {
          throw new Error('Stream ended without content or completion signal');
        }
      }

    } catch (error) {
      console.error('[OSA] Streaming error:', error);

      // Keep partial content if we have any, including what earlier runs of
      // this reply wrote and any code they ran.
      const shown = compose(accumulatedContent);
      const ran = messages[messageIndex] && messages[messageIndex].executions && messages[messageIndex].executions.length;
      if (shown || ran) {
        const errorType = error.name || 'Error';
        let userMessage = 'Stream interrupted';

        if (error.name === 'AbortError') {
          userMessage = 'Connection timeout';
        } else if (error.message && error.message.includes('timeout')) {
          userMessage = 'Stream timeout';
        } else if (error.message && error.message.includes('Backend streaming error')) {
          // Backend error already handled above, don't modify message
          throw error;
        }

        messages[messageIndex].content = (shown ? `${shown}\n\n` : '') + `_[${userMessage}]_`;
        renderMessages(container);
        try {
          saveHistory();
        } catch (saveError) {
          console.error('[OSA] Failed to save history:', saveError);
        }
      } else {
        // No content received - remove placeholder message
        messages.splice(messageIndex, 1);
      }

      throw error; // Re-throw to be handled by sendMessage
    } finally {
      // Always release the reader to free resources
      if (reader) {
        try {
          reader.releaseLock();
        } catch (releaseError) {
          // Log cleanup failures - they indicate serious issues
          console.error('[OSA] Failed to release stream reader:', {
            errorName: releaseError.name,
            errorMessage: releaseError.message
          });
        }
      }
    }
  }

  // Send message to API
  async function sendMessage(container, question) {
    if (isLoading || !question.trim()) return;

    // Commit any open thumbs-down comment box before the conversation moves on.
    flushPendingResponseFeedback(container);

    isLoading = true;
    isThinking = false;

    // Track message indices to avoid corruption on error
    const userMessageIndex = messages.length;
    messages.push({ role: 'user', content: question });
    let assistantMessageCreated = false;

    renderMessages(container);
    renderSuggestions(container);

    const input = container.querySelector('.osa-chat-input input');
    const sendBtn = container.querySelector('.osa-send-btn');
    const resetBtn = container.querySelector('.osa-reset-btn');
    input.value = '';
    input.disabled = true;
    sendBtn.disabled = true;
    resetBtn.disabled = true;

    try {
      const body = { message: question.trim() };

      // Include session ID for conversation continuity
      if (sessionId) {
        body.session_id = sessionId;
      }

      // Add page context if enabled
      const pageContext = getPageContext();
      if (pageContext) {
        body.page_context = pageContext;
      }

      // Add Turnstile token if available
      if (turnstileToken) {
        body.cf_turnstile_response = turnstileToken;
      }

      // Add model selection if set
      if (userSettings.model) {
        body.model = userSettings.model;
      }

      // Enable streaming if configured
      if (CONFIG.streamingEnabled) {
        body.stream = true;
      }

      // The client tools this page can run. Only a streaming reply can carry a
      // tool_request, so they are declared only for one.
      if (CONFIG.streamingEnabled) {
        const declared = await declaredClientTools();
        if (declared.length > 0) {
          body.client_tools = declared;
        }
      }

      if (!isValidCommunityId(CONFIG.communityId)) {
        throw new Error('Invalid community configuration. Please reload the page.');
      }

      // BYOK keys ride on the header matching their provider (inferred from the
      // key's own prefix; see inferKeyProvider and chatRequestHeaders).
      const response = await fetch(`${CONFIG.apiEndpoint}/${CONFIG.communityId}/chat`, {
        method: 'POST',
        headers: chatRequestHeaders(),
        body: JSON.stringify(body),
        signal: AbortSignal.timeout(120000), // 2 minute timeout for connection + streaming
      });

      if (!response.ok) {
        throw await responseError(response);
      }

      // Extract session ID from response header (set by streaming responses)
      const headerSessionId = response.headers.get('X-Session-ID');
      if (headerSessionId) {
        sessionId = headerSessionId;
      }

      // Check if response is streaming (SSE)
      const contentType = response.headers.get('content-type') || '';
      if (CONFIG.streamingEnabled && contentType.includes('text/event-stream')) {
        // Handle streaming response
        assistantMessageCreated = true; // handleStreamingResponse creates assistant message
        let outcome = await handleStreamingResponse(response, container);
        // A reply that runs code in this page is several runs. Each ends on a
        // tool_request; its answer goes back on /chat/resume, whose stream
        // continues the same message. Each run is a new request, so the 2
        // minute timeout above bounds one run, not the whole reply.
        let runs = 0;
        while (outcome && outcome.toolRequest) {
          runs += 1;
          if (runs > MAX_BROWSER_RUNS_PER_REPLY) {
            // Left unanswered on purpose: the next message abandons the
            // parked call, which the server repairs.
            throw new Error(`Stopped after ${MAX_BROWSER_RUNS_PER_REPLY} code runs in one reply. Ask again to continue.`);
          }
          const result = await answerToolRequest(container, outcome.toolRequest, outcome.messageIndex);
          const resumed = await postResume(outcome.toolRequest, result);
          outcome = await handleStreamingResponse(resumed, container, { messageIndex: outcome.messageIndex });
        }
      } else {
        // Non-streaming response (either streaming not enabled, or fallback)
        if (CONFIG.streamingEnabled) {
          console.warn('[OSA] Expected streaming response but got content-type:', contentType);
          console.warn('[OSA] Falling back to non-streaming mode');
        }

        const data = await response.json();
        if (data && typeof data.session_id === 'string') {
          sessionId = data.session_id;
        }
        const answer = (data && data.message && typeof data.message.content === 'string') ? data.message.content : null;
        if (!answer) {
          throw new Error('Invalid response from server');
        }
        const assistantMsg = { role: 'assistant', content: answer };
        if (data && typeof data.request_id === 'string') {
          assistantMsg.requestId = data.request_id;
        }
        if (data && Array.isArray(data.citations)) {
          assistantMsg.citations = data.citations;
        }
        messages.push(assistantMsg);
        try {
          saveHistory();
        } catch (saveError) {
          console.error('[OSA] Failed to save history:', saveError);
          showError(container, 'Warning: Unable to save conversation');
        }
        updateStatusDisplay(true);
      }

    } catch (error) {
      // Categorize error for better user messaging
      let userMessage = 'Failed to get response';

      if (error.name === 'AbortError') {
        userMessage = 'Request timed out. Please try again.';
      } else if (error.name === 'TypeError' && error.message.includes('fetch')) {
        userMessage = 'Network error. Please check your connection.';
      } else if (error.message && error.message.includes('JSON')) {
        userMessage = 'Invalid response from server. Please try again.';
      } else if (error.message && error.message.includes('Backend streaming error')) {
        userMessage = error.message.replace('Backend streaming error: ', '');
      } else if (error.message && error.message.includes('Stream')) {
        userMessage = 'Connection interrupted. Please try again.';
      } else if (error.message) {
        userMessage = error.message;
      }

      console.error('[OSA] Send message error:', error);
      showError(container, userMessage);

      // Clean up messages based on what was created
      // If streaming was attempted, handleStreamingResponse manages its own assistant message
      // We only need to remove the user message if no assistant response exists
      if (assistantMessageCreated) {
        // handleStreamingResponse created an assistant message
        // If it has content (partial or complete), keep both user and assistant messages
        // If it has no content, handleStreamingResponse already removed it, so remove user message too
        const lastMessage = messages[messages.length - 1];
        if (lastMessage && lastMessage.role === 'user' && messages.length === userMessageIndex + 1) {
          // No assistant message remains, remove user message
          messages.splice(userMessageIndex, 1);
        }
      } else {
        // No streaming attempted, no assistant message created, remove user message
        if (messages.length > userMessageIndex && messages[userMessageIndex].role === 'user') {
          messages.splice(userMessageIndex, 1);
        }
      }

      try {
        saveHistory();
      } catch (saveError) {
        console.error('[OSA] Failed to save history after error:', saveError);
        // saveHistory already showed error to user
      }
      updateStatusDisplay(false);
    } finally {
      isLoading = false;
      isThinking = false;
      input.disabled = false;
      sendBtn.disabled = false;
      resetBtn.disabled = messages.length <= 1;
      input.focus();
      renderMessages(container);
      renderSuggestions(container);

      // Reset Turnstile for next request
      if (turnstileWidgetId !== null && window.turnstile) {
        window.turnstile.reset(turnstileWidgetId);
        turnstileToken = null;
      }
    }
  }

  // Initialize Turnstile if configured
  function initTurnstile(container) {
    if (!CONFIG.turnstileSiteKey || !window.turnstile) return;

    const turnstileContainer = container.querySelector('.osa-turnstile-container');
    if (!turnstileContainer) {
      console.error('Turnstile container not found');
      return;
    }
    turnstileContainer.style.display = 'flex';

    try {
      turnstileWidgetId = window.turnstile.render(turnstileContainer, {
        sitekey: CONFIG.turnstileSiteKey,
        callback: function(token) {
          turnstileToken = token;
        },
        'error-callback': function(error) {
          console.error('Turnstile error:', error);
          showError(container, 'Security verification failed. Please refresh the page.');
        },
        'expired-callback': function() {
          turnstileToken = null;
          console.warn('Turnstile token expired');
        }
      });
    } catch (e) {
      console.error('Failed to initialize Turnstile:', e);
      showError(container, 'Could not initialize security verification.');
    }
  }

  // Reset chat
  function resetChat(container) {
    if (messages.length <= 1 || isLoading) return;
    // Commit any open thumbs-down comment before the history is cleared.
    flushPendingResponseFeedback(container);
    messages = [{ role: 'assistant', content: CONFIG.initialMessage }];
    sessionId = null; // Clear session to start fresh on next message
    try {
      saveHistory();
    } catch (saveError) {
      console.error('[OSA] Failed to save history on reset:', saveError);
    }
    renderMessages(container);
    renderSuggestions(container);
  }

  // Toggle chat window
  function toggleChat(container) {
    isOpen = !isOpen;
    const chatWindow = container.querySelector('.osa-chat-window');
    const button = container.querySelector('.osa-chat-button');
    const tooltip = container.querySelector('.osa-chat-tooltip');

    if (isOpen) {
      chatWindow.classList.add('open');
      container.classList.add('chat-open');
      button.innerHTML = ICONS.close;
      button.setAttribute('aria-label', 'Close chat');
      container.querySelector('.osa-chat-input input').focus();
      // Hide tooltip when chat opens
      if (tooltip) tooltip.classList.remove('visible');
      // Surface any notice queued by an init-time failure (see loadUserSettings/loadHistory).
      flushPendingNotice(container);
      preloadRuntime();
    } else {
      // Commit any open thumbs-down comment box on close.
      flushPendingResponseFeedback(container);
      chatWindow.classList.remove('open');
      container.classList.remove('chat-open');
      button.innerHTML = ICONS.chat;
      button.setAttribute('aria-label', 'Open chat');
    }
  }

  // Open chat in a new popup window
  async function openPopout() {
    const popoutBtn = document.querySelector('.osa-popout-btn');

    // Reuse existing popup if still open
    if (chatPopup && !chatPopup.closed) {
      chatPopup.focus();
      return;
    }

    // Prevent double-clicks during async operation
    if (popoutBtn?.disabled) return;

    if (popoutBtn) {
      popoutBtn.disabled = true;
      popoutBtn.setAttribute('aria-busy', 'true');
      popoutBtn.title = 'Opening...';
    }

    try {
      // Find the script URL (prefer stored URL, fallback to DOM query)
      let scriptUrl = WIDGET_SCRIPT_URL;
      if (!scriptUrl) {
        const scripts = document.querySelectorAll('script[src*="osa-chat-widget"]');
        if (scripts.length === 0) {
          console.warn('OSA Chat Widget: Could not find widget script tag for pop-out.');
          alert('Could not find widget script URL. Pop-out is not available.');
          return;
        }
        if (scripts.length > 1) {
          console.warn(`OSA Chat Widget: Found ${scripts.length} matching script tags, using the last one.`);
        }
        scriptUrl = scripts[scripts.length - 1].src;
      }

      // Fetch the script content
      let scriptCode = '';
      try {
        const response = await fetch(scriptUrl);
        if (!response.ok) {
          console.error('[OSA] Failed to fetch widget script:', {
            url: scriptUrl,
            status: response.status,
            statusText: response.statusText
          });
          alert(`Failed to load widget for pop-out (HTTP ${response.status}). Please try again.`);
          return;
        }
        scriptCode = await response.text();
      } catch (e) {
        console.error('[OSA] Failed to fetch widget script:', {
          url: scriptUrl,
          error: e.message || e,
          stack: e.stack
        });
        alert('Failed to load widget for pop-out. Please try again.');
        return;
      }

      // Validate script content
      if (!scriptCode || scriptCode.trim().length === 0) {
        console.error('Widget script content is empty');
        alert('Failed to load widget for pop-out. Please try again.');
        return;
      }

      // Create popup config with fullscreen mode. The pop-out runs this script
      // inline, so it is told where the script lives: the runtime bundle is
      // found next to it.
      const popupConfig = { ...CONFIG, fullscreen: true, widgetScriptUrl: scriptUrl };

      // Serialize config safely (escape script-breaking sequences)
      let configJson;
      try {
        configJson = JSON.stringify(popupConfig)
          .replace(/</g, '\\u003c')
          .replace(/>/g, '\\u003e');
      } catch (e) {
        console.error('Failed to serialize widget config:', e);
        alert('Failed to prepare widget configuration for pop-out.');
        return;
      }

      // Calculate responsive popup size
      const width = Math.min(500, Math.floor(window.screen.availWidth * 0.9));
      const height = Math.min(700, Math.floor(window.screen.availHeight * 0.9));
      const left = Math.floor((window.screen.availWidth - width) / 2);
      const top = Math.floor((window.screen.availHeight - height) / 2);
      const features = `width=${width},height=${height},left=${left},top=${top},menubar=no,toolbar=no,location=no,status=no,resizable=yes`;

      // Create the popup HTML with config set BEFORE the widget script runs
      const popupHtml = `<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0">
  <title>${escapeHtml(CONFIG.title)}</title>
  <style>
    body {
      margin: 0;
      padding: 0;
      height: 100vh;
      overflow: hidden;
    }
  </style>
</head>
<body>
  <script>
    // Pre-configure widget before it initializes
    window.__OSA_CHAT_CONFIG__ = ${configJson};
  <\/script>
  <script>
    // Widget code (will pick up __OSA_CHAT_CONFIG__ if present)
    ${scriptCode}
  <\/script>
</body>
</html>`;

      // Open the popup window
      const popup = window.open('', '_blank', features);
      if (popup) {
        try {
          popup.document.write(popupHtml);
          popup.document.close();
          chatPopup = popup;

          // Clean up reference when popup closes
          const checkClosed = setInterval(() => {
            if (popup.closed) {
              clearInterval(checkClosed);
              chatPopup = null;
            }
          }, 1000);
        } catch (e) {
          console.error('Failed to write popup content:', e);
          popup.close();
          alert('Failed to initialize the pop-out window. Please try again.');
          return;
        }
      } else {
        alert('Please allow popups to open the chat in a new window.');
      }
    } finally {
      // Reset button state
      if (popoutBtn) {
        popoutBtn.disabled = false;
        popoutBtn.setAttribute('aria-busy', 'false');
        popoutBtn.title = 'Open in new window';
      }
    }
  }

  // Initialize widget
  function init() {
    // Check for pre-configured settings (used by pop-out windows)
    if (window.__OSA_CHAT_CONFIG__) {
      Object.assign(CONFIG, window.__OSA_CHAT_CONFIG__);
    }

    loadPageContextPreference();
    loadUserSettings();
    const historyNeedsSave = loadHistory();
    injectStyles();
    const container = createWidget();

    if (historyNeedsSave) {
      try {
        saveHistory();
      } catch (saveError) {
        console.error('[OSA] Failed to persist migrated chat history:', saveError);
        queuePendingNotice('Saved chat history was repaired but could not be persisted.');
      }
    }

    // Fetch community default model (async, non-blocking). Kept as a promise
    // so the first message can wait briefly for the client tools it declares.
    communityConfigReady = fetchCommunityConfig();

    renderMessages(container);
    renderSuggestions(container);

    // Query required DOM elements with null checks
    const chatButton = container.querySelector('.osa-chat-button');
    const closeBtn = container.querySelector('.osa-close-btn');
    const resetBtn = container.querySelector('.osa-reset-btn');
    const popoutBtn = container.querySelector('.osa-popout-btn');
    const settingsBtn = container.querySelector('.osa-settings-btn-open');
    const input = container.querySelector('.osa-chat-input input');
    const sendBtn = container.querySelector('.osa-send-btn');
    const suggestionsList = container.querySelector('.osa-suggestions-list');

    // Settings modal elements
    const settingsOverlay = container.querySelector('.osa-settings-overlay');
    const settingsCloseBtn = container.querySelector('.osa-settings-close-btn');
    const settingsCancelBtn = container.querySelector('.osa-settings-btn-cancel');
    const settingsSaveBtn = container.querySelector('.osa-settings-btn-save');
    const modelSelect = container.querySelector('#osa-settings-model');
    const customModelField = container.querySelector('#osa-settings-custom-model-field');

    // Verify all required elements exist
    if (!chatButton || !closeBtn || !resetBtn || !input || !sendBtn || !suggestionsList) {
      console.error('OSA Chat Widget: Required DOM elements not found. Widget may not function correctly.');
    }

    // Update reset button state
    if (resetBtn) {
      resetBtn.disabled = messages.length <= 1;
    }

    // Event listeners with null checks
    chatButton?.addEventListener('click', () => toggleChat(container));
    closeBtn?.addEventListener('click', () => toggleChat(container));
    resetBtn?.addEventListener('click', () => resetChat(container));
    popoutBtn?.addEventListener('click', () => openPopout());
    settingsBtn?.addEventListener('click', () => openSettings(container));

    if (sendBtn && input) {
      sendBtn.addEventListener('click', () => sendMessage(container, input.value));
      input.addEventListener('keypress', (e) => {
        if (e.key === 'Enter') sendMessage(container, input.value);
      });
    }

    suggestionsList?.addEventListener('click', (e) => {
      if (e.target.classList.contains('osa-suggestion')) {
        sendMessage(container, e.target.textContent);
      }
    });

    // Page context toggle
    const pageContextCheckbox = container.querySelector('#osa-page-context-checkbox');
    pageContextCheckbox?.addEventListener('change', (e) => {
      pageContextEnabled = e.target.checked;
      savePageContextPreference();
    });

    // Settings modal event listeners
    settingsCloseBtn?.addEventListener('click', () => closeSettings(container));
    settingsCancelBtn?.addEventListener('click', () => closeSettings(container));
    settingsSaveBtn?.addEventListener('click', () => saveSettings(container));

    // Close settings modal when clicking outside
    settingsOverlay?.addEventListener('click', (e) => {
      if (e.target === settingsOverlay) {
        closeSettings(container);
      }
    });

    // Feedback link + modal event listeners
    const feedbackLink = container.querySelector('.osa-feedback-link');
    const feedbackOverlay = container.querySelector('.osa-feedback-overlay');
    const feedbackCloseBtn = container.querySelector('.osa-feedback-close-btn');
    const feedbackCancelBtn = container.querySelector('.osa-feedback-cancel-btn');
    const feedbackSendBtn = container.querySelector('.osa-feedback-send-btn');

    feedbackLink?.addEventListener('click', () => openFeedback(container));
    feedbackCloseBtn?.addEventListener('click', () => closeFeedback(container));
    feedbackCancelBtn?.addEventListener('click', () => closeFeedback(container));
    feedbackSendBtn?.addEventListener('click', () => submitGeneralFeedback(container));
    feedbackOverlay?.addEventListener('click', (e) => {
      if (e.target === feedbackOverlay) {
        closeFeedback(container);
      }
    });

    // Show/hide custom model input based on selection
    modelSelect?.addEventListener('change', (e) => {
      if (customModelField) {
        customModelField.style.display = e.target.value === 'custom' ? 'block' : 'none';
      }
    });

    // Check backend status
    checkBackendStatus();

    // Show tooltip after a short delay (only if not fullscreen mode)
    if (!CONFIG.fullscreen) {
      const tooltip = container.querySelector('.osa-chat-tooltip');
      if (tooltip) {
        setTimeout(() => {
          tooltip.classList.add('visible');
          // Auto-hide tooltip after 8 seconds
          setTimeout(() => {
            tooltip.classList.remove('visible');
          }, 8000);
        }, 1500);
      }
    }

    // Initialize Turnstile if the script is loaded
    if (window.turnstile) {
      initTurnstile(container);
    } else {
      window.addEventListener('load', () => {
        if (window.turnstile) initTurnstile(container);
      });
    }

    // In fullscreen mode, open the chat immediately
    if (CONFIG.fullscreen) {
      isOpen = true;
      const chatWindow = container.querySelector('.osa-chat-window');
      chatWindow?.classList.add('open');
      // Surface any notice queued by an init-time failure (see loadUserSettings/loadHistory).
      flushPendingNotice(container);
      setTimeout(() => {
        input?.focus();
      }, 100);
    }
  }

  // Validate communityId contains only safe characters (alphanumeric, hyphens, underscores)
  function isValidCommunityId(id) {
    return typeof id === 'string' && /^[a-zA-Z0-9_-]+$/.test(id);
  }

  // Expose configuration for customization
  window.OSAChatWidget = {
    setConfig: function(options) {
      const opts = { ...options };
      // Validate communityId if provided
      if (opts.communityId && !isValidCommunityId(opts.communityId)) {
        console.error('[OSA] Invalid communityId:', opts.communityId);
        return;
      }
      // Track which keys the embedder explicitly set (before auto-derivation)
      for (const key of Object.keys(opts)) {
        _userSetKeys.add(key);
      }
      // Auto-derive storageKey from communityId if communityId changed but storageKey wasn't explicitly set
      if (opts.communityId && !opts.storageKey) {
        opts.storageKey = `osa-chat-history-${opts.communityId}`;
      }
      Object.assign(CONFIG, opts);
    },
    getConfig: function() {
      return { ...CONFIG };
    },
    // Manually initialize the widget (use with data-no-auto-init on the script tag)
    init: function() {
      if (!initialized) {
        initialized = true;
        init();
      }
    }
  };

  // Keep the state reducer testable without exposing it in normal embeds.
  if (window.__OSA_TEST__) {
    window.OSAChatWidget.__applyDoneEvent = applyDoneEvent;
    window.OSAChatWidget.__migrateLegacyCitationMarkers = migrateLegacyCitationMarkers;
    window.OSAChatWidget.__isSameResponseMessage = isSameResponseMessage;
  }

  // Auto-init unless the script tag has data-no-auto-init attribute
  let initialized = false;
  const currentScript = document.currentScript;
  const noAutoInit = currentScript && currentScript.hasAttribute('data-no-auto-init');

  if (!noAutoInit) {
    function scheduleInit() {
      setTimeout(function() {
        if (!initialized) {
          initialized = true;
          init();
        }
      }, 0);
    }
    if (document.readyState === 'loading') {
      document.addEventListener('DOMContentLoaded', scheduleInit);
    } else {
      scheduleInit();
    }
  }
})();
