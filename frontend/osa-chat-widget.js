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
    // Suggestions for a page that names a dataset with setDataset (#477): templates
    // {text, needs_zarr} with {dataset_id}, {subject} and {task} blanks, filled from
    // what the page passed (see datasetQuestions). Empty keeps suggestedQuestions on
    // every page.
    datasetSuggestedQuestions: [],
    // The launcher's shape (#436): 'bubble' is today's single chat button; 'capsule'
    // adds a notebook and an HPC placeholder icon that expand above it. Loaded from
    // the community config the same way theme_color is, unless the embedder sets it.
    launcher: 'bubble',
    // Tooltip text beside the collapsed launcher. null keeps the hardcoded
    // "Ask me about <title>" every community has always had.
    launcherLabel: null,
    // The widget's appearance (#469): 'light' (every community's default), 'auto'
    // (follow the reader's device), or 'dark'. The community config offers 'light'
    // or 'auto'; a host page with its own theme switch passes the reader's choice
    // with OSAChatWidget.setColorScheme, which outranks the community's value.
    colorScheme: 'light',
    // The notebook site's base URL (a sibling PR builds it); setDataset's active
    // notebook button opens `${notebookUrl}open.html?community=...&dataset=...`.
    // /osa/ names this widget's project on the shared notebook.osc.earth plane,
    // per OSC's subdomain-per-plane, path-per-project naming rule.
    notebookUrl: 'https://notebook.osc.earth/osa/',
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
    // Where this script was loaded from, for a copy that cannot tell (one run
    // without a script URL of its own). The browser runtime is found next to it.
    // The pop-out loads the script by its address (#470), so it can tell, and is
    // handed this as well.
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
  // A first visit, with no remembered look: the launcher waits, hidden, for the
  // community config (#475). See revealLauncher.
  let launcherWaiting = false;
  // How long the launcher waits for the config before showing the defaults anyway.
  // A variable, not a constant, only so the test hooks can shorten it. A soft
  // bound in a background tab, whose timers the browser slows; the config's own
  // arrival, which ends the wait too, is not a timer.
  let LAUNCHER_WAIT_MS = 1500;
  // What revealLauncher runs once the launcher is on screen (the tooltip's timer).
  const launcherShownCallbacks = [];
  // The display fields' values before a remembered look was applied (#475), so the
  // fresh config can start from them: the server leaves a default out (a bubble
  // launcher, the light scheme, an unset color), and a field it leaves out must
  // end at its default, not at what the last load remembered. Null once used, or
  // when nothing was remembered.
  let displayDefaults = null;
  const DISPLAY_KEYS = ['title', 'initialMessage', 'placeholder', 'suggestedQuestions',
    'datasetSuggestedQuestions', 'themeColor',
    'userBubbleColor', 'themeTextColor', 'accentColor', 'userBubbleTextColor', 'logo', 'launcher',
    'launcherLabel', 'colorScheme'];
  // The header avatar as createWidget draws it, for a config that drops its logo.
  let defaultAvatarHtml = null;
  // Browser code execution (#431). browserToolsReady is assigned, to a pending
  // promise, as soon as the config says the community runs code; it resolves to
  // the controller once the runtime bundle has loaded and passed its integrity
  // check, or to null if either fails. The rest are set when it resolves.
  let browserToolsReady = null; // Promise resolving to the controller, or null
  let browserTools = null; // OSARuntime.ClientToolController
  let browserRuntime = null; // The OSARuntime.PyodideRuntime it drives
  let runtimeApi = null; // The OSARuntime global itself, captured when it loaded
  let browserToolsSetup = null; // {tools, python} from the config, kept for a retry
  let browserToolsUnavailable = null; // {reason, detail} while code execution is off
  let browserToolsRetried = false;
  // The persistent workspace (#433): one OSARuntime.WorkspaceStore per
  // community, created lazily once the runtime bundle is loaded (it needs
  // runtimeApi.WorkspaceStore) and reused for the life of the page.
  let workspaceStore = null;
  // Whether the workspace's Delete button is one click from deleting: reset
  // whenever Settings opens or closes, so a stray click days later cannot
  // land on a confirmation nobody meant to leave armed.
  let workspaceDeleteConfirming = false;
  // What the tool panel shows while a tool_request is answered:
  // {phase: 'asking', prompt, decide} while the person is asked,
  // {phase: 'running', prompt, progress} while code runs, else null.
  let toolActivity = null;
  // The runtime's own last boot-progress display, kept independent of toolActivity:
  // a preload_on: first_message boot can advance through several steps before the
  // reader ever sees a Run gate, and toolActivity does not exist yet to receive them
  // (onRuntimeProgress only writes into a 'running' toolActivity). Seeded onto a
  // freshly-created running activity so the panel shows the real current step
  // instead of a blank bar; cleared whenever the runtime leaves `booting`, so a
  // later run never inherits a stale step from an earlier boot.
  let lastBootProgress = null;
  // True once this session's reader has sent a message, so a runtime bundle or
  // config that finishes loading afterward still knows to boot immediately under
  // preload_on: first_message rather than waiting for a run.
  let firstMessageSent = false;
  // The dataset on screen (#436), set by the embedder's page script via setDataset:
  // null when there is none (never set, or explicitly cleared), or {id, zarr, subject,
  // task} where zarr is true, false, or undefined (not known yet), and subject and
  // task are BIDS labels or undefined (#477). Read by the capsule's notebook icon and
  // the dataset-page suggestions (datasetQuestions); may be set before init() runs,
  // since the DOM does not exist to render into yet -- the value just sits here until
  // renderLauncherIcons and renderSuggestions read it.
  let currentDataset = null;
  // The capsule's open tab (#470): 'chat' or 'notebook'. Only a capsule widget
  // ever has a notebook tab; a bubble widget is always on chat.
  let activeTab = 'chat';
  // The notebook shown in the notebook tab (#470): its frame is created the first
  // time the tab opens and kept, hidden, while chat is open, so the reader's Python
  // session and edits survive switching back and forth. See ensureNotebookFrame.
  //   state: 'idle' (no frame), 'loading' (frame loading, no word from the
  //          notebook yet), 'ready' (the notebook reported ready), 'failed'
  //   setup: the notebook's last setup status: null (none yet), 'running',
  //          'done', 'error' or 'none' (a starter with no setup cell)
  //   failure: why it failed, for the fallback's text: 'startup' or 'timeout'
  const notebookEmbed = {
    frame: null, datasetId: null, state: 'idle', setup: null, failure: null,
    loadTimer: null, readyTimer: null, sentScheme: null
  };
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

  // This script's own element and address, kept at load time (document.currentScript
  // is null once the script has run). The pop-out loads the same address with the
  // element's integrity and crossorigin (#470), read when it opens.
  const WIDGET_SCRIPT = document.currentScript || null;
  const WIDGET_SCRIPT_URL = WIDGET_SCRIPT?.src || null;

  // The browser Python runtime ships as a separate file, loaded only for a
  // community that configures client tools, and verified against this hash.
  // The versioned embed pins THIS file by SRI, so the hash here extends that
  // pin to the runtime: a runtime that does not match is refused by the
  // browser before any of it runs. Written by scripts/build-runtime-bundle.js;
  // CI rebuilds and fails if the committed bundle or this line is stale.
  // BEGIN GENERATED: runtime bundle integrity
  const RUNTIME_BUNDLE_INTEGRITY = 'sha384-MpNwiHNmaJuDp3ZsjArh0vZEHgyyVupVlc3K8yMs+s0wq+FAn3X5TYWAlaLXp+SK';
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
    thumbDown: '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M17 14V2"/><path d="M9 18.12 10 14H4.17a2 2 0 0 1-1.92-2.56l2.33-8A2 2 0 0 1 6.5 2H20a2 2 0 0 1 2 2v8a2 2 0 0 1-2 2h-2.76a2 2 0 0 0-1.79 1.11L12 22a3.13 3.13 0 0 1-3-3.88Z"/></svg>',
    // The capsule launcher's other two positions (#436): a page with lines for the
    // notebook, three stacked racks for HPC.
    notebook: '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M15 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V7Z"/><path d="M14 2v4a2 2 0 0 0 2 2h4"/><path d="M10 9H8"/><path d="M16 13H8"/><path d="M16 17H8"/></svg>',
    hpc: '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><rect x="3" y="3" width="18" height="4" rx="1"/><rect x="3" y="10" width="18" height="4" rx="1"/><rect x="3" y="17" width="18" height="4" rx="1"/><line x1="7" y1="5" x2="7.01" y2="5"/><line x1="7" y1="12" x2="7.01" y2="12"/><line x1="7" y1="19" x2="7.01" y2="19"/></svg>'
  };

  // CSS Styles
  const STYLES = `
    .osa-chat-widget {
      --osa-primary: #2563eb;
      --osa-primary-dark: #1d4ed8;
      /* Text/icons drawn ON a --osa-primary surface (header, launcher, Run, Send, Save). */
      --osa-on-primary: #ffffff;
      /* --osa-primary used as a FOREGROUND on the white panel (links, borders, focus
         rings, accent-color). Tracks --osa-primary by default, so an unset accent_color
         changes nothing: this is exactly today's behavior. accent_color arrives as the
         inline --osa-accent-on-light, not as --osa-accent itself: an inline value would
         outrank the dark rule below, which has to replace it (a color chosen to read
         on white is often too dark to read on the dark panel). */
      --osa-accent: var(--osa-accent-on-light, var(--osa-primary));
      --osa-bg: #ffffff;
      --osa-text: #1f2937;
      --osa-text-light: #6b7280;
      --osa-border: #e5e7eb;
      --osa-user-bg: #2563eb;
      --osa-user-text: #ffffff;
      --osa-assistant-bg: #f3f4f6;
      --osa-shadow: 0 10px 25px rgba(0, 0, 0, 0.15);
      /* The widget draws its own light (or, with .osa-dark, dark) surfaces, so the
         browser's own parts (scrollbars, native inputs, checkboxes) have to match
         them rather than a dark host page's color-scheme (#469). */
      color-scheme: light;
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
      color: var(--osa-on-primary);
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

    /* The three-icon capsule launcher (#436). Every selector here is scoped under
       .osa-launcher-capsule, an element that exists ONLY when launcher: capsule
       converts the DOM (see applyLauncherMode): a bubble-mode widget never has one,
       so none of these rules ever match it and its markup and computed styles stay
       exactly as they were before this feature existed.
       Base (below 601px): the icons lay out in a ROW to the left of the chat button,
       and .osa-chat-window keeps its unmodified rule above (opens above, as today).
       The @media override past 601px switches to the vertical stack, opening the
       chat window to the LEFT instead. Both directions rely on the same trick: the
       fixed container sets only bottom+right (never top/left), so as hidden
       icons reveal and the container grows, it grows away from the anchored corner
       and the last child (the chat button) never moves. */
    .osa-launcher-capsule {
      position: fixed;
      bottom: 20px;
      right: 20px;
      /* Higher than .osa-chat-window's 10000: two fixed elements with EQUAL
         z-index stack by DOM order, and .osa-chat-window follows the capsule in
         the markup, so a tie would paint the window over the icon tooltips
         (measured: an icon-tooltip's own 10001 is scoped to this stacking
         context and never compared against the window's). This element's own
         z-index is what has to beat the window's, not its descendants'. */
      z-index: 10002;
      display: flex;
      flex-direction: row;
      align-items: center;
      gap: 10px;
      /* The hidden icons keep their place while the panel is closed (so the
         chat button never moves, and the indicator's positions hold), which
         leaves this box covering the page above the chat button; clicks there
         go to the page. The buttons themselves take clicks back, below. */
      pointer-events: none;
    }

    /* The pill: 7px beyond the icons on every side, drawn behind them, without
       padding on .osa-launcher-capsule itself (padding would shift the fixed
       bottom/right anchor and move the chat button). It grows out of the chat
       button when the panel opens (#470): toward the left in the row layout,
       upward in the column layout past 601px. */
    .osa-launcher-capsule::before {
      content: '';
      position: absolute;
      inset: -7px;
      background: var(--osa-bg);
      border-radius: 30px;
      box-shadow: var(--osa-shadow);
      z-index: -1;
      opacity: 0;
      transform: scaleX(0.35);
      transform-origin: 100% 50%;
      transition: opacity 200ms ease, transform 280ms cubic-bezier(0.22, 1, 0.36, 1);
    }

    .osa-chat-widget.chat-open .osa-launcher-capsule::before {
      opacity: 1;
      transform: none;
    }

    /* A first visit, waiting for the community config (#475): the launcher stays out
       of sight and nothing animates, so it appears already in the community's look.
       revealLauncher removes this once the config arrives, or after LAUNCHER_WAIT_MS. */
    .osa-chat-widget.osa-launcher-waiting .osa-chat-button,
    .osa-chat-widget.osa-launcher-waiting .osa-chat-tooltip,
    .osa-chat-widget.osa-launcher-waiting .osa-launcher-capsule {
      visibility: hidden;
    }

    .osa-chat-widget.osa-launcher-waiting,
    .osa-chat-widget.osa-launcher-waiting *,
    .osa-chat-widget.osa-launcher-waiting *::before,
    .osa-chat-widget.osa-launcher-waiting *::after {
      transition: none !important;
    }

    /* A pop-out opening on the notebook tab (#470) is drawn on it at once, rather
       than fading through from chat; see showTabAtOnce. */
    .osa-chat-widget.osa-tab-at-once *,
    .osa-chat-widget.osa-tab-at-once *::before,
    .osa-chat-widget.osa-tab-at-once *::after {
      transition: none !important;
    }

    /* The filled circle behind the open tab (#470): slides between the chat and
       notebook circles; positionIndicator moves it to its button's offset. */
    .osa-capsule-indicator {
      position: absolute;
      left: 0;
      top: 0;
      width: 46px;
      height: 46px;
      border-radius: 50%;
      background: var(--osa-primary);
      box-shadow: 0 4px 12px rgba(0, 0, 0, 0.2);
      z-index: 0;
      opacity: 0;
      pointer-events: none;
      transition: transform 380ms cubic-bezier(0.34, 1.25, 0.64, 1), opacity 160ms ease;
    }

    .osa-chat-widget.chat-open .osa-capsule-indicator {
      opacity: 1;
    }

    /* Inside the capsule, the flex parent positions the chat button; its own
       fixed/bottom/right (still true for a bubble-mode widget) would fight that.
       46px, as are the other circles: about 15% larger than the Send button. At
       rest it is drawn larger; see the rule after the next. */
    .osa-launcher-capsule .osa-chat-button {
      position: relative;
      bottom: auto;
      right: auto;
      flex-shrink: 0;
      width: 46px;
      height: 46px;
      z-index: 1;
      pointer-events: auto;
      box-sizing: border-box;
      border: 1.5px solid transparent;
      transition: transform 0.2s, scale 280ms cubic-bezier(0.22, 1, 0.36, 1), translate 280ms cubic-bezier(0.22, 1, 0.36, 1),
        background-color 200ms ease, color 200ms ease, border-color 200ms ease, box-shadow 200ms ease;
    }

    .osa-launcher-capsule .osa-chat-button svg {
      width: 21px;
      height: 21px;
    }

    /* At rest, with the panel closed, the chat circle is drawn 25% larger (58px,
       its icon about 26px), so the launcher is easy to see; it settles to 46px as
       the panel opens (#490). Drawn larger rather than laid out larger: its box
       stays 46px, so the indicator, the pill, the other circles and the panel's
       offset are the same open or closed, and nothing reflows while it shrinks.
       The translate puts its bottom-right corner where the 46px box's is, 20px
       from the window's edges, as the bubble's; since the translate is the growth
       past the box on each side, 23px x (58/46 - 1) = 6px, that corner holds
       still through the whole transition, because scale and translate share one
       duration and curve. A browser without the scale and translate properties
       draws today's 46px. */
    .osa-chat-widget:not(.chat-open) .osa-launcher-capsule .osa-chat-button {
      scale: 1.2609;
      translate: -6px -6px;
    }

    /* Hovered at rest, it grows 5% more, as every launcher does, with its corner
       held in the same place: the scale and translate take the hover in place of
       the shared transform: scale(1.05), which would grow it around its center
       and push the corner out, on a curve of its own. 1.2609 x 1.05 is 1.3239,
       and 23px x 0.3239 is 7.45px. */
    .osa-chat-widget:not(.chat-open) .osa-launcher-capsule .osa-chat-button:hover {
      transform: none;
      scale: 1.3239;
      translate: -7.45px -7.45px;
    }

    /* Open: the indicator is the chat circle's fill, so the button itself is clear. */
    .osa-chat-widget.chat-open .osa-launcher-capsule .osa-chat-button,
    .osa-chat-widget.chat-open .osa-launcher-capsule .osa-chat-button:hover {
      background: transparent;
      box-shadow: none;
    }

    /* The notebook is the open tab: chat becomes an outlined circle. The ring is
       the accent mixed into the panel, with the plain border for a browser
       without color-mix. */
    .osa-chat-widget.chat-open.osa-tab-notebook .osa-launcher-capsule .osa-chat-button {
      color: var(--osa-accent);
      border-color: var(--osa-border);
      border-color: color-mix(in srgb, var(--osa-accent) 45%, var(--osa-bg));
    }

    .osa-launcher-icon {
      position: relative;
      z-index: 1;
      width: 46px;
      height: 46px;
      flex-shrink: 0;
      box-sizing: border-box;
      padding: 0;
      border-radius: 50%;
      border: 1.5px solid var(--osa-border);
      background: var(--osa-assistant-bg);
      color: var(--osa-text-light);
      display: flex;
      align-items: center;
      justify-content: center;
      cursor: default;
      /* Hidden until the panel opens, but in place: see .osa-launcher-capsule. */
      opacity: 0;
      transform: scale(0.55);
      visibility: hidden;
      pointer-events: none;
      transition: background-color 200ms ease, color 200ms ease, border-color 200ms ease,
        opacity 220ms ease, transform 280ms cubic-bezier(0.22, 1, 0.36, 1), visibility 0s linear 280ms;
    }

    /* Opening: the notebook circle arrives 30ms after the pill starts, HPC 70ms. */
    .osa-chat-widget.chat-open .osa-launcher-icon {
      opacity: 1;
      transform: none;
      visibility: visible;
      pointer-events: auto;
      transition: background-color 200ms ease, color 200ms ease, border-color 200ms ease,
        opacity 220ms ease 30ms, transform 280ms cubic-bezier(0.22, 1, 0.36, 1) 30ms, visibility 0s;
    }

    .osa-chat-widget.chat-open .osa-hpc-btn {
      transition-delay: 0ms, 0ms, 0ms, 70ms, 70ms, 0s;
    }

    .osa-launcher-icon svg {
      width: 21px;
      height: 21px;
    }

    /* Available (the notebook, once a Zarr copy exists): an outlined circle in the
       accent, which becomes the open tab when clicked. Unavailable and coming-soon
       (the default above) share the muted look; only the badge tells them apart,
       per #436. */
    .osa-launcher-icon.osa-icon-available {
      background: transparent;
      color: var(--osa-accent);
      border-color: var(--osa-border);
      border-color: color-mix(in srgb, var(--osa-accent) 45%, var(--osa-bg));
      cursor: pointer;
    }

    .osa-chat-widget.chat-open .osa-launcher-icon.osa-icon-available:hover {
      transform: scale(1.05);
    }

    /* The open tab: the indicator behind it is its fill. */
    .osa-launcher-icon.osa-icon-available.osa-tab-current {
      border-color: transparent;
      color: var(--osa-on-primary);
    }

    /* The notebook is starting while another tab is open: a ring turns on its circle. */
    .osa-notebook-ring {
      position: absolute;
      top: -1.5px;
      left: -1.5px;
      width: 46px;
      height: 46px;
      opacity: 0;
      pointer-events: none;
      transition: opacity 200ms ease;
      animation: osa-spin 1s linear infinite;
    }

    .osa-notebook-ring circle {
      fill: none;
      stroke: var(--osa-primary);
      stroke-width: 2.5;
      stroke-linecap: round;
      stroke-dasharray: 34 101;
    }

    /* The notebook failed, or its setup did, while another tab is open: a dot on
       its circle, beside the tooltip and label that say which. */
    .osa-launcher-icon.osa-notebook-attention::after {
      content: '';
      position: absolute;
      top: 0;
      right: 0;
      width: 10px;
      height: 10px;
      border-radius: 50%;
      background: #dc2626;
      box-shadow: 0 0 0 2px var(--osa-bg);
    }

    .osa-chat-widget.osa-dark .osa-launcher-icon.osa-notebook-attention::after {
      background: #f87171;
    }

    .osa-launcher-icon.osa-notebook-busy .osa-notebook-ring {
      opacity: 1;
    }

    @keyframes osa-spin {
      to { transform: rotate(360deg); }
    }

    .osa-icon-badge {
      position: absolute;
      top: -5px;
      right: -9px;
      background: var(--osa-text-light);
      color: #ffffff;
      font-size: 8.5px;
      font-weight: 700;
      line-height: 1;
      padding: 3px 5px;
      border-radius: 7px;
      text-transform: uppercase;
      letter-spacing: 0.04em;
    }

    /* Same look as .osa-chat-tooltip, positioned relative to its own icon (which sits
       in a row or a column depending on viewport) rather than at a fixed offset. */
    .osa-icon-tooltip {
      position: absolute;
      right: calc(100% + 12px);
      top: 50%;
      transform: translate(6px, -50%);
      background: var(--osa-bg);
      color: var(--osa-text);
      padding: 8px 12px;
      border-radius: 8px;
      box-shadow: var(--osa-shadow);
      font-size: 13px;
      font-weight: 500;
      white-space: nowrap;
      z-index: 10001;
      opacity: 0;
      pointer-events: none;
      transition: opacity 140ms ease, transform 140ms ease;
    }

    .osa-launcher-capsule .osa-chat-button:hover .osa-icon-tooltip,
    .osa-launcher-capsule .osa-chat-button:focus-visible .osa-icon-tooltip,
    .osa-launcher-icon:hover .osa-icon-tooltip,
    .osa-launcher-icon:focus-visible .osa-icon-tooltip {
      opacity: 1;
      transform: translate(0, -50%);
    }

    /* The chat button's own tooltip exists only while the panel is open (it says
       what a click does then); closed, the collapsed launcher's label is shown. */
    .osa-chat-widget:not(.chat-open) .osa-launcher-capsule .osa-chat-button .osa-icon-tooltip {
      display: none;
    }

    /* The collapsed label, beside the chat circle as it is drawn at rest (58px,
       #490): the same 10px gap and vertical center the bubble's label has. It is
       hidden while the panel is open, when the circle is 46px. */
    .osa-chat-widget.osa-capsule .osa-chat-tooltip {
      right: 88px;
      bottom: 29px;
    }

    @media (min-width: 601px) {
      .osa-launcher-capsule {
        flex-direction: column;
      }

      .osa-launcher-capsule::before {
        transform: scaleY(0.35);
        transform-origin: 50% 100%;
      }

      /* Opens to the LEFT of the capsule instead of above it; today's rule below
         (bottom: 90px, right: 20px, max-height: calc(100vh - 120px)) is what a
         narrow viewport keeps. 20 + 46 + 7 + 12 are the capsule's own offset,
         diameter, the pill's reach beyond it, and a gap, so the window sits
         beside the pill with no overlap. The transition is scoped to capsule
         mode alone (this selector never matches a bubble-mode widget, which must
         render exactly as it always has): the community config can still be
         resolving when the reader opens the chat, and launcher: capsule arriving
         a moment later would otherwise snap an already-open panel to its new
         position instead of easing into it. */
      .osa-chat-widget.osa-capsule .osa-chat-window {
        right: calc(20px + 46px + 7px + 12px);
        bottom: 20px;
        max-width: calc(100vw - 20px - 46px - 7px - 12px - 20px);
        max-height: calc(100vh - 50px);
        transition: right 0.2s ease, bottom 0.2s ease, max-height 0.2s ease;
      }
    }

    /* The chat and notebook views (#470), capsule mode only: applyLauncherMode
       moves the chat's own elements into the first and adds the second, so a
       bubble-mode widget's markup is untouched. Switching is a fade-through: the
       old view fades out in 110ms, then the new one fades in (210ms, after 90ms)
       from 98.5% scale. A view that is off is also inert and, once faded,
       hidden, so it is neither focusable nor read; the notebook's frame is only
       ever hidden, never removed or display:none, so its Python keeps running. */
    .osa-capsule .osa-views {
      position: relative;
      flex: 1 1 auto;
      min-height: 0;
    }

    .osa-capsule .osa-view {
      position: absolute;
      inset: 0;
      display: flex;
      flex-direction: column;
      min-height: 0;
      transition: opacity 110ms ease-in, transform 110ms ease-in, visibility 0s linear 110ms;
    }

    .osa-capsule .osa-view.osa-view-on {
      transition: opacity 210ms ease-out 90ms, transform 280ms cubic-bezier(0.22, 1, 0.36, 1) 90ms, visibility 0s;
    }

    .osa-capsule .osa-view.osa-view-off {
      opacity: 0;
      transform: scale(0.985);
      visibility: hidden;
      pointer-events: none;
    }

    .osa-notebook-frame {
      flex: 1 1 auto;
      width: 100%;
      min-height: 0;
      border: 0;
      display: block;
      background: var(--osa-bg);
    }

    /* Chrome keeps a resize drag with the page even over the frame (the Chrome
       check drags across it with and without this rule); this is for a browser
       that hands the pointer to whatever frame is under it. */
    .osa-chat-window.osa-resizing .osa-notebook-frame {
      pointer-events: none;
    }

    .osa-notebook-overlay {
      position: absolute;
      inset: 0;
      display: flex;
      flex-direction: column;
      align-items: center;
      justify-content: center;
      gap: 14px;
      padding: 24px;
      box-sizing: border-box;
      text-align: center;
      background: var(--osa-bg);
      color: var(--osa-text);
      transition: opacity 280ms ease, visibility 0s;
    }

    .osa-notebook-overlay.osa-overlay-hidden {
      opacity: 0;
      visibility: hidden;
      pointer-events: none;
      transition: opacity 280ms ease, visibility 0s linear 280ms;
    }

    .osa-notebook-overlay-title {
      font-size: 15px;
      font-weight: 600;
    }

    .osa-notebook-overlay-text {
      font-size: 13px;
      color: var(--osa-text-light);
      max-width: 300px;
    }

    .osa-notebook-bar-track {
      width: 60%;
      max-width: 220px;
      height: 4px;
      border-radius: 999px;
      background: var(--osa-border);
      overflow: hidden;
    }

    .osa-notebook-bar {
      width: 40%;
      height: 100%;
      border-radius: 999px;
      background: var(--osa-primary);
      animation: osa-bar 1.4s cubic-bezier(0.4, 0, 0.2, 1) infinite;
    }

    @keyframes osa-bar {
      0% { transform: translateX(-100%); }
      100% { transform: translateX(260%); }
    }

    .osa-notebook-actions {
      display: flex;
      gap: 8px;
      flex-wrap: wrap;
      justify-content: center;
    }

    .osa-notebook-actions a,
    .osa-notebook-actions button {
      font: inherit;
      font-size: 13px;
      padding: 6px 12px;
      border-radius: 6px;
      border: 1px solid var(--osa-border);
      background: var(--osa-bg);
      color: var(--osa-text);
      cursor: pointer;
      text-decoration: none;
    }

    .osa-notebook-actions .osa-notebook-retry {
      background: var(--osa-primary);
      border-color: var(--osa-primary);
      color: var(--osa-on-primary);
    }

    /* The header's two titles, one per tab, in the same place. */
    .osa-capsule .osa-chat-title-area {
      display: grid;
    }

    .osa-capsule .osa-ttl {
      grid-area: 1 / 1;
      min-width: 0;
      transition: opacity 180ms ease, transform 240ms cubic-bezier(0.22, 1, 0.36, 1), visibility 0s;
    }

    .osa-capsule .osa-ttl.osa-ttl-off {
      opacity: 0;
      transform: translateY(6px);
      visibility: hidden;
      pointer-events: none;
      transition: opacity 180ms ease, transform 240ms cubic-bezier(0.22, 1, 0.36, 1), visibility 0s linear 240ms;
    }

    .osa-notebook-status-text {
      overflow: hidden;
      text-overflow: ellipsis;
      white-space: nowrap;
    }

    /* The chat's own header buttons have nothing to do on the notebook tab.
       Hidden, not removed, so the header does not reflow as the tabs switch. The
       pop-out button stays: the pop-out opens on the notebook tab too (#470). */
    .osa-capsule .osa-settings-btn-open,
    .osa-capsule .osa-reset-btn {
      transition: opacity 160ms ease, background 0.2s, visibility 0s;
    }

    .osa-capsule.osa-tab-notebook .osa-settings-btn-open,
    .osa-capsule.osa-tab-notebook .osa-reset-btn {
      opacity: 0;
      visibility: hidden;
      pointer-events: none;
      transition: opacity 160ms ease, background 0.2s, visibility 0s linear 160ms;
    }

    /* The pop-out's tabs (#470): the pop-out has no launcher, so the panel's Chat
       and Notebook tabs are a strip under its header. The open tab is underlined
       in theme_color; one that cannot open now is muted, its reason in its
       tooltip; the notebook's tab carries the circle's cues as a small mark. */
    .osa-tab-strip {
      display: flex;
      gap: 4px;
      flex: 0 0 auto;
      padding: 0 12px;
      background: var(--osa-bg);
      border-bottom: 1px solid var(--osa-border);
    }

    .osa-strip-tab {
      display: inline-flex;
      align-items: center;
      gap: 6px;
      margin: 0 0 -1px;
      padding: 10px 12px 8px;
      border: 0;
      border-bottom: 2px solid transparent;
      background: transparent;
      color: var(--osa-text);
      font: inherit;
      font-size: 13px;
      font-weight: 600;
      cursor: pointer;
      transition: color 160ms ease, border-color 200ms ease;
    }

    .osa-strip-tab svg {
      width: 16px;
      height: 16px;
      flex-shrink: 0;
    }

    .osa-strip-tab[aria-pressed="true"] {
      color: var(--osa-accent);
      border-bottom-color: var(--osa-primary);
    }

    .osa-strip-tab[aria-disabled="true"] {
      color: var(--osa-text-light);
      cursor: default;
    }

    .osa-strip-tab:focus-visible {
      outline: 2px solid var(--osa-accent);
      outline-offset: -2px;
      border-radius: 6px 6px 0 0;
    }

    .osa-strip-cue {
      display: none;
      width: 8px;
      height: 8px;
      box-sizing: border-box;
      border-radius: 50%;
    }

    .osa-strip-tab.osa-notebook-attention .osa-strip-cue {
      display: block;
      background: #dc2626;
    }

    .osa-chat-widget.osa-dark .osa-strip-tab.osa-notebook-attention .osa-strip-cue {
      background: #f87171;
    }

    .osa-strip-tab.osa-notebook-busy .osa-strip-cue {
      display: block;
      border: 2px solid var(--osa-primary);
      border-right-color: transparent;
      animation: osa-spin 1s linear infinite;
    }

    /* Reduced motion: every change is immediate except a short plain fade
       between the views, and nothing turns or slides. The chat circle's resting
       size (#490) is among them: it is 58px or 46px, never between. */
    @media (prefers-reduced-motion: reduce) {
      .osa-launcher-capsule::before,
      .osa-capsule-indicator,
      .osa-launcher-icon,
      .osa-chat-widget.chat-open .osa-launcher-icon,
      .osa-launcher-capsule .osa-chat-button,
      .osa-capsule .osa-ttl,
      .osa-capsule .osa-ttl.osa-ttl-off,
      .osa-strip-tab {
        transition-duration: 1ms !important;
        transition-delay: 0ms !important;
      }

      .osa-capsule .osa-view,
      .osa-capsule .osa-view.osa-view-off {
        transition: opacity 120ms linear, visibility 0s linear 120ms !important;
        transform: none !important;
      }

      .osa-capsule .osa-view.osa-view-on {
        transition: opacity 120ms linear, visibility 0s !important;
      }

      .osa-notebook-bar {
        animation: none;
        width: 60%;
      }

      .osa-notebook-ring,
      .osa-strip-tab.osa-notebook-busy .osa-strip-cue {
        animation: none;
      }
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
      /* Text with no color of its own would otherwise inherit the host page's,
         unreadable on this panel when the page is dark (#469). */
      color: var(--osa-text);
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
      color: var(--osa-on-primary);
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

    .osa-chat-title,
    .osa-notebook-heading {
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
      color: var(--osa-on-primary);
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
      color: var(--osa-accent);
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
      color: var(--osa-accent);
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
      color: var(--osa-accent);
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
      color: var(--osa-accent);
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
      border-color: var(--osa-accent);
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
      color: var(--osa-on-primary);
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

    /* Mid-conversation, a dataset new to the conversation (#477): a short row of
       chips above the input rather than the opening screen's full-width list. */
    .osa-suggestions-compact {
      padding: 8px 16px;
    }

    .osa-suggestions-compact .osa-suggestions-label {
      margin-bottom: 6px;
      text-transform: none;
      letter-spacing: 0;
    }

    .osa-suggestions-compact .osa-suggestions-list {
      flex-direction: row;
      flex-wrap: wrap;
    }

    .osa-suggestions-compact .osa-suggestion {
      padding: 5px 10px;
      font-size: 12px;
      border-radius: 14px;
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
      /* Explicit, never the browser's: a dark host page otherwise paints this
         field dark inside the light panel (#469). */
      background: var(--osa-bg);
      color: var(--osa-text);
      transition: border-color 0.2s;
    }

    .osa-chat-input input:focus {
      border-color: var(--osa-accent);
    }

    .osa-chat-input input:disabled {
      background: #f9fafb;
    }

    .osa-send-btn {
      width: 40px;
      height: 40px;
      border-radius: 50%;
      background: var(--osa-primary);
      color: var(--osa-on-primary);
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
      accent-color: var(--osa-accent);
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
      color: var(--osa-accent);
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
      background: var(--osa-bg);
      color: var(--osa-text);
      transition: border-color 0.2s;
      box-sizing: border-box;
    }

    .osa-settings-input:focus {
      border-color: var(--osa-accent);
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
      color: var(--osa-text);
    }

    .osa-settings-select:focus {
      border-color: var(--osa-accent);
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
      color: var(--osa-on-primary);
    }

    .osa-settings-btn-save:hover {
      background: var(--osa-primary-dark);
    }

    .osa-workspace-actions {
      display: flex;
      flex-wrap: wrap;
      gap: 8px;
      margin-top: 8px;
    }

    .osa-workspace-actions .osa-settings-btn {
      padding: 8px 14px;
      background: transparent;
      color: var(--osa-text);
      border: 1px solid var(--osa-border);
    }

    .osa-workspace-actions .osa-settings-btn:hover {
      background: var(--osa-border);
    }

    .osa-workspace-delete-btn.osa-workspace-confirm {
      background: #e53e3e;
      color: white;
      border-color: #e53e3e;
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
      color: var(--osa-on-primary);
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

    .osa-tool-progress-row {
      display: flex;
      align-items: center;
      gap: 8px;
      flex: 1 1 auto;
      min-width: 0;
    }

    .osa-tool-progress {
      flex: 1 1 90px;
      min-width: 60px;
      height: 6px;
      border-radius: 999px;
      background: var(--osa-border);
      overflow: hidden;
    }

    .osa-tool-progress-fill {
      height: 100%;
      border-radius: 999px;
      background: var(--osa-primary);
      transition: width 0.2s ease;
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
      /* Stays white on the dark panel too: a figure drawn with a transparent
         background keeps its dark axes and labels readable. */
      background: #ffffff;
    }

    .osa-execution-local-note {
      color: var(--osa-accent);
      font-weight: 600;
      margin: 6px 0;
    }

    .osa-execution-workspace-note {
      color: #dc2626;
      background: #fef2f2;
      border-radius: 6px;
      padding: 6px 8px;
      margin: 6px 0 0 0;
      font-size: 12px;
    }

    .osa-rerun-actions {
      margin-top: 6px;
    }

    .osa-rerun-actions button,
    .osa-rerun-buttons button {
      border: 1px solid var(--osa-border);
      background: var(--osa-bg);
      color: var(--osa-text);
      border-radius: 6px;
      padding: 5px 12px;
      font-size: 13px;
      cursor: pointer;
    }

    .osa-rerun-buttons button.osa-rerun-run {
      background: var(--osa-primary);
      border-color: var(--osa-primary);
      color: var(--osa-on-primary);
    }

    .osa-rerun-buttons button:disabled {
      opacity: 0.6;
      cursor: not-allowed;
    }

    .osa-rerun {
      margin-top: 8px;
      padding-top: 8px;
      border-top: 1px dashed var(--osa-border);
    }

    .osa-rerun-label {
      display: block;
      font-size: 12px;
      color: var(--osa-text-light);
      margin-bottom: 4px;
    }

    .osa-rerun-textarea {
      display: block;
      width: 100%;
      box-sizing: border-box;
      min-height: 80px;
      margin-top: 4px;
      padding: 8px;
      border-radius: 8px;
      border: 1px solid var(--osa-border);
      background: #1f2937;
      color: #f9fafb;
      font-size: 12px;
      font-family: 'SF Mono', Monaco, 'Courier New', monospace;
      resize: vertical;
    }

    .osa-rerun-note {
      color: var(--osa-text-light);
      font-size: 12px;
      margin: 4px 0;
    }

    .osa-rerun-buttons {
      display: flex;
      gap: 8px;
      align-items: center;
      margin-top: 6px;
      flex-wrap: wrap;
    }

    .osa-rerun-result-status {
      font-weight: 600;
      margin-top: 8px;
    }

    /* Dark appearance (#469). Only a widget whose color_scheme is "auto" (on a dark
       device), or whose host page chose 'dark' (setColorScheme or setConfig), ever has
       .osa-dark, so none of these rules reach any other widget; every rule above is
       unchanged.
       The tokens carry most of it; the rules after them replace the colors the
       stylesheet above writes out literally. */
    .osa-chat-widget.osa-dark {
      color-scheme: dark;
      --osa-bg: #111827;
      --osa-text: #e5e7eb;
      --osa-text-light: #9ca3af;
      --osa-border: #374151;
      --osa-assistant-bg: #1f2937;
      /* A shadow alone vanishes against a dark host page, so every raised surface
         (the panel, the launcher, the tooltips) also gets a hairline edge. */
      --osa-shadow: 0 0 0 1px #374151, 0 10px 25px rgba(0, 0, 0, 0.5);
      /* The community's accent_color was chosen to read on white. The dark panel
         uses the theme color itself (NEMAR's teal: 8.0:1 here, 6.6:1 on the
         assistant's bubble), or, when that is too dark to read on either, the
         lighter --osa-accent-on-dark that applyColorScheme derives from it (see
         darkAccentFor). */
      --osa-accent: var(--osa-accent-on-dark, var(--osa-primary));
    }

    .osa-chat-widget.osa-dark .osa-icon-badge {
      color: #111827;
    }

    .osa-chat-widget.osa-dark .osa-message-content code {
      background: rgba(255, 255, 255, 0.1);
    }

    /* Code stays a dark block, one step darker than the panel, with an edge so it
       does not merge into the assistant's bubble. */
    .osa-chat-widget.osa-dark .osa-message-content pre,
    .osa-chat-widget.osa-dark .osa-tool-code,
    .osa-chat-widget.osa-dark .osa-rerun-textarea {
      background: #030712;
      box-shadow: inset 0 0 0 1px var(--osa-border);
    }

    .osa-chat-widget.osa-dark .osa-message-content pre code {
      background: transparent;
    }

    .osa-chat-widget.osa-dark .osa-table th {
      background: rgba(255, 255, 255, 0.06);
    }

    .osa-chat-widget.osa-dark .osa-table tr:nth-child(even) {
      background: rgba(255, 255, 255, 0.03);
    }

    .osa-chat-widget.osa-dark .osa-message-copy-btn:hover {
      background: rgba(255, 255, 255, 0.08);
    }

    .osa-chat-widget.osa-dark .osa-feedback-up.selected,
    .osa-chat-widget.osa-dark .osa-feedback-modal-thanks {
      color: #4ade80;
    }

    .osa-chat-widget.osa-dark .osa-feedback-down.selected {
      color: #f87171;
    }

    .osa-chat-widget.osa-dark .osa-suggestion:hover {
      background: #374151;
      border-color: #4b5563;
    }

    .osa-chat-widget.osa-dark .osa-chat-input input:disabled {
      background: #1f2937;
    }

    .osa-chat-widget.osa-dark .osa-send-btn:disabled {
      background: #4b5563;
    }

    .osa-chat-widget.osa-dark .osa-error,
    .osa-chat-widget.osa-dark .osa-execution-workspace-note {
      color: #fca5a5;
      background: rgba(220, 38, 38, 0.15);
    }

    .osa-chat-widget.osa-dark .osa-error {
      border-top-color: rgba(248, 113, 113, 0.35);
    }

    .osa-chat-widget.osa-dark .osa-warning {
      color: #fcd34d;
      background: rgba(245, 158, 11, 0.12);
      border-top-color: rgba(252, 211, 77, 0.3);
    }

    .osa-chat-widget.osa-dark .osa-resize-handle::before {
      border-left-color: rgba(255, 255, 255, 0.3);
      border-top-color: rgba(255, 255, 255, 0.3);
    }

    /* The configured disclaimer colors, like accent_color, were chosen for the
       light panel; the dark panel uses the same amber family. */
    .osa-chat-widget.osa-dark .osa-ai-disclaimer {
      color: #fdba74;
      background: rgba(251, 146, 60, 0.12);
    }

    .osa-chat-widget.osa-dark .osa-settings-overlay {
      background: rgba(0, 0, 0, 0.6);
    }

    .osa-chat-widget.osa-dark .osa-settings-modal {
      box-shadow: var(--osa-shadow);
    }

    .osa-chat-widget.osa-dark .osa-execution-output {
      background: rgba(255, 255, 255, 0.06);
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

  // ---- Capsule launcher (#436): notebook and HPC icons above the chat button ----

  // A dataset id, as the page embedding the widget names it: matches what
  // setDataset's contract promises the notebook site (the URL it builds
  // encodeURIComponent's it regardless, but the widget still refuses a
  // malformed id up front rather than silently opening a bad link).
  function isValidDatasetId(id) {
    return typeof id === 'string' && /^[A-Za-z0-9._-]{1,64}$/.test(id);
  }

  // A BIDS label, as setDataset's subject and task are (#477): alphanumeric, no
  // "sub-" or "task-" prefix.
  function isValidBidsLabel(label) {
    return typeof label === 'string' && /^[A-Za-z0-9]{1,64}$/.test(label);
  }

  // Accepts only an absolute https: URL, or http: on localhost/127.0.0.1 for tests
  // (the notebook site itself is served over plain http in a local dev server).
  // Returns the normalized URL (always ending in '/') or null if invalid.
  function normalizeNotebookUrl(value) {
    if (typeof value !== 'string' || !value) return null;
    let parsed;
    try {
      parsed = new URL(value);
    } catch {
      return null;
    }
    const isLocalHttp = parsed.protocol === 'http:' &&
      (parsed.hostname === 'localhost' || parsed.hostname === '127.0.0.1');
    if (parsed.protocol !== 'https:' && !isLocalHttp) return null;
    // A base URL carries no query or fragment: handleNotebookClick appends its
    // own '?community=...&dataset=...' after it, so a query here would produce
    // a URL with two '?' (and the trailing-slash fix below would land the
    // slash INSIDE that query/fragment, after its last character, not at the
    // end of the path). Reject rather than silently mangle it.
    if (parsed.search || parsed.hash) return null;
    let href = parsed.href;
    if (!href.endsWith('/')) href += '/';
    return href;
  }

  // Build one capsule icon button: the SVG, a hidden "Soon" badge (shown only for
  // HPC), and a hover/focus tooltip using the .osa-chat-tooltip look.
  function buildLauncherIcon(name, svg) {
    const button = document.createElement('button');
    button.type = 'button';
    button.className = `osa-launcher-icon osa-${name}-btn`;
    button.innerHTML = svg;
    const badge = document.createElement('span');
    badge.className = 'osa-icon-badge';
    badge.textContent = 'Soon';
    badge.style.display = 'none';
    button.appendChild(badge);
    const tooltip = document.createElement('span');
    tooltip.className = 'osa-icon-tooltip';
    // The button's own aria-label already states this text; without this, a
    // screen reader would read it twice (once for the label, once for this
    // span's text content, which the label duplicates verbatim). The
    // pre-existing .osa-chat-tooltip (the collapsed launcher's own tooltip)
    // is untouched: it has no button-owning aria-label to duplicate, since
    // the chat button's aria-label is just "Open chat"/"Close chat".
    tooltip.setAttribute('aria-hidden', 'true');
    button.appendChild(tooltip);
    return button;
  }

  // What the notebook icon shows for the current dataset state (#436): four states,
  // from setDataset's value and nothing else (the community does not change this;
  // every community with launcher: capsule shows the same four states). When the
  // notebook is the open tab, clicking it closes the panel, as the chat circle
  // does when chat is (#470).
  function notebookIconState() {
    if (!currentDataset) {
      return {
        available: false,
        label: 'Notebook: open a dataset page to start a notebook',
        tooltip: 'Open a dataset page to start a notebook'
      };
    }
    if (currentDataset.zarr === true) {
      if (isOpen && activeTab === 'notebook') {
        return { available: true, label: `Close ${CONFIG.title}`, tooltip: 'Close' };
      }
      // The frame for this dataset failed, or its setup did: say so on the circle,
      // for a reader who left the tab before it finished.
      if (notebookEmbed.frame && notebookEmbed.datasetId === currentDataset.id) {
        if (notebookEmbed.state === 'failed') {
          const tip = `The ${currentDataset.id} notebook did not open here`;
          return { available: true, attention: true, label: `Notebook: ${tip}`, tooltip: tip };
        }
        if (notebookEmbed.state === 'ready' && notebookEmbed.setup === 'error') {
          const tip = `Setup did not finish in the ${currentDataset.id} notebook`;
          return { available: true, attention: true, label: `Notebook: ${tip}`, tooltip: tip };
        }
      }
      return {
        available: true,
        label: `Open ${currentDataset.id} in a Python notebook`,
        tooltip: `Open ${currentDataset.id} in a Python notebook`
      };
    }
    if (currentDataset.zarr === false) {
      return {
        available: false,
        label: 'Notebook: this dataset has no Zarr copy',
        tooltip: 'This dataset has no Zarr copy yet, so there is nothing to open in a notebook'
      };
    }
    // zarr is undefined: known dataset, not yet known whether it has a Zarr copy.
    return {
      available: false,
      label: 'Notebook: checking for a Zarr copy',
      tooltip: 'Checking whether this dataset has a Zarr copy'
    };
  }

  function isNotebookAvailable() {
    return !!currentDataset && currentDataset.zarr === true;
  }

  // The notebook site's address for the dataset on screen: the notebook site's
  // contract (open.html validates both parameters, writes a starter notebook into
  // JupyterLite's own storage, and redirects into it).
  // CONFIG.notebookUrl always holds a normalized URL: its default is one, and both
  // of its writers (setConfig, and the pre-set config init() reads) normalize what
  // they are given or drop it with a warning.
  function notebookUrlFor(datasetId) {
    return `${CONFIG.notebookUrl}open.html?community=${encodeURIComponent(CONFIG.communityId)}` +
      `&dataset=${encodeURIComponent(datasetId)}`;
  }

  // The origin the notebook's messages must come from, and the only one the
  // widget's own messages are addressed to.
  function notebookOrigin() {
    return new URL(CONFIG.notebookUrl).origin;
  }

  // Apply the capsule's current state to the DOM: each button's availability,
  // label and tooltip, which tab is open, and the notebook's busy ring. Safe to
  // call any time (setDataset, a config load, a tab switch, capsule creation): a
  // no-op wherever a button does not exist yet (bubble mode, or before
  // applyLauncherMode has run).
  function renderLauncherIcons(container) {
    const hpcButton = container.querySelector('.osa-hpc-btn');
    if (hpcButton) {
      hpcButton.setAttribute('aria-disabled', 'true');
      hpcButton.setAttribute('aria-label', 'HPC submission, coming soon');
      hpcButton.classList.remove('osa-icon-available');
      const tooltip = hpcButton.querySelector('.osa-icon-tooltip');
      if (tooltip) tooltip.textContent = 'HPC submission is coming soon';
      const badge = hpcButton.querySelector('.osa-icon-badge');
      if (badge) badge.style.display = '';
    }

    const notebookButton = container.querySelector('.osa-notebook-btn');
    if (notebookButton) {
      const state = notebookIconState();
      const current = isOpen && activeTab === 'notebook';
      notebookButton.setAttribute('aria-disabled', state.available ? 'false' : 'true');
      notebookButton.setAttribute('aria-label', state.label);
      notebookButton.setAttribute('aria-pressed', current ? 'true' : 'false');
      notebookButton.classList.toggle('osa-icon-available', state.available);
      notebookButton.classList.toggle('osa-tab-current', current);
      // Python is starting, or the notebook still loading, while the reader is
      // elsewhere: the ring says so on the circle they would click.
      const busy = !current && state.available && notebookStarting();
      notebookButton.classList.toggle('osa-notebook-busy', busy);
      notebookButton.classList.toggle('osa-notebook-attention', !!state.attention);
      const tooltip = notebookButton.querySelector('.osa-icon-tooltip');
      if (tooltip) tooltip.textContent = state.tooltip;
      const badge = notebookButton.querySelector('.osa-icon-badge');
      if (badge) badge.style.display = 'none';
    }

    const chatButton = container.querySelector('.osa-launcher-capsule .osa-chat-button');
    if (chatButton) {
      const current = isOpen && activeTab === 'chat';
      let label = 'Open chat';
      let tooltip = '';
      if (current) {
        label = `Close ${CONFIG.title}`;
        tooltip = 'Close';
      } else if (isOpen) {
        label = `Chat with ${CONFIG.title}`;
        tooltip = 'Chat';
      }
      chatButton.setAttribute('aria-label', label);
      chatButton.setAttribute('aria-pressed', current ? 'true' : 'false');
      const tip = chatButton.querySelector('.osa-icon-tooltip');
      if (tip) tip.textContent = tooltip;
    }

    renderTabStrip(container);
  }

  // Python is starting, or the notebook still loading: the cue a notebook circle or
  // tab shows while the reader is on another tab.
  function notebookStarting() {
    return notebookEmbed.state === 'loading' ||
      (notebookEmbed.state === 'ready' && notebookEmbed.setup === 'running');
  }

  // The pop-out's tab strip (#470), from the same state as the capsule's circles:
  // which tab is open, whether the notebook can open, and its busy and attention
  // cues. The visible "Notebook" stays its name; the reason it cannot open, or what
  // happened to it, is its tooltip. Only a pop-out has a strip; elsewhere a no-op.
  function renderTabStrip(container) {
    const strip = container.querySelector('.osa-tab-strip');
    if (!strip) return;
    const chatTab = strip.querySelector('.osa-strip-chat');
    const notebookTab = strip.querySelector('.osa-strip-notebook');
    chatTab.setAttribute('aria-pressed', activeTab === 'chat' ? 'true' : 'false');
    const current = activeTab === 'notebook';
    const state = notebookIconState();
    notebookTab.setAttribute('aria-pressed', current ? 'true' : 'false');
    notebookTab.setAttribute('aria-disabled', state.available ? 'false' : 'true');
    if (current) {
      notebookTab.removeAttribute('title');
    } else {
      notebookTab.title = state.tooltip;
    }
    notebookTab.classList.toggle('osa-notebook-busy', !current && state.available && notebookStarting());
    notebookTab.classList.toggle('osa-notebook-attention', !current && !!state.attention);
  }

  // The pop-out's tabs (#470): a pop-out has no launcher to hold the capsule's
  // circles, so its panel's Chat and Notebook tabs are a strip under the header.
  // Each is a button, pressed while its tab is open, as the circles are. Both call
  // setTab, as the circles do; the open tab's own button does nothing, since the
  // pop-out's panel has no closed state.
  function buildTabStrip(container) {
    const strip = document.createElement('div');
    strip.className = 'osa-tab-strip';
    strip.setAttribute('role', 'group');
    strip.setAttribute('aria-label', 'Tabs');
    strip.innerHTML = `
      <button type="button" class="osa-strip-tab osa-strip-chat" aria-pressed="true">${ICONS.chat}<span>Chat</span></button>
      <button type="button" class="osa-strip-tab osa-strip-notebook" aria-pressed="false">${ICONS.notebook}<span>Notebook</span><span class="osa-strip-cue" aria-hidden="true"></span></button>`;
    strip.querySelector('.osa-strip-chat').addEventListener('click', () => {
      if (activeTab === 'chat') return;
      setTab(container, 'chat');
      container.querySelector('.osa-chat-input input')?.focus();
    });
    const notebookTab = strip.querySelector('.osa-strip-notebook');
    notebookTab.addEventListener('click', () => {
      // aria-disabled is the guard, as on the notebook circle, re-checked against
      // the dataset itself so the two cannot drift apart.
      if (notebookTab.getAttribute('aria-disabled') === 'true' || !isNotebookAvailable()) return;
      if (activeTab !== 'notebook') setTab(container, 'notebook');
    });
    return strip;
  }

  // Move the filled indicator behind the open tab's circle. Positions come from
  // the layout itself (each button's offset in the capsule), so the row and column
  // layouts need nothing of their own.
  function positionIndicator(container) {
    const indicator = container.querySelector('.osa-capsule-indicator');
    if (!indicator) return;
    const target = activeTab === 'notebook'
      ? container.querySelector('.osa-notebook-btn')
      : container.querySelector('.osa-launcher-capsule .osa-chat-button');
    if (!target) return;
    indicator.style.transform = `translate(${target.offsetLeft}px, ${target.offsetTop}px)`;
  }

  // Open a tab (#470): the views fade through, the header's title and buttons
  // follow, and the indicator slides (or, in a pop-out, the tab strip follows).
  // Opening the notebook tab creates its frame the first time. A bubble widget has
  // no tabs, so this is a no-op there.
  function setTab(container, tab) {
    if (!container.classList.contains('osa-capsule')) return;
    if (tab === 'notebook' && !isNotebookAvailable()) tab = 'chat';
    activeTab = tab;
    container.classList.toggle('osa-tab-notebook', tab === 'notebook');
    container.classList.toggle('osa-tab-chat', tab === 'chat');
    for (const [name, on] of [['chat', tab === 'chat'], ['notebook', tab === 'notebook']]) {
      const view = container.querySelector(`.osa-view-${name}`);
      if (view) {
        view.classList.toggle('osa-view-on', on);
        view.classList.toggle('osa-view-off', !on);
        view.toggleAttribute('inert', !on);
      }
      const title = container.querySelector(`.osa-ttl-${name}`);
      if (title) {
        title.classList.toggle('osa-ttl-off', !on);
        title.setAttribute('aria-hidden', on ? 'false' : 'true');
      }
    }
    if (tab === 'notebook') ensureNotebookFrame(container);
    renderNotebookStatus(container);
    renderLauncherIcons(container);
    positionIndicator(container);
  }

  // setTab without its motion: the views, titles and header buttons take their
  // places at once. A pop-out opening on the notebook tab (#470) is drawn there
  // from its first frame; with the fade, the window would open on the chat and
  // fade to the notebook as it starts drawing (measured in Chrome). Layout is
  // forced once with transitions off, so the new state is where the next switch
  // starts from.
  function showTabAtOnce(container, tab) {
    container.classList.add('osa-tab-at-once');
    setTab(container, tab);
    void container.offsetWidth;
    container.classList.remove('osa-tab-at-once');
  }

  // The chat circle (#470): closed, it opens the panel on chat; open on chat, it
  // closes it; open on another tab, it goes back to chat. A bubble widget's
  // button only ever toggles, exactly as before.
  function handleChatButtonClick(container) {
    if (container.classList.contains('osa-capsule') && isOpen && activeTab !== 'chat') {
      setTab(container, 'chat');
      container.querySelector('.osa-chat-input input')?.focus();
      return;
    }
    if (!isOpen) setTab(container, 'chat');
    toggleChat(container);
  }

  // The notebook circle: aria-disabled is the guard (not the disabled attribute, so
  // the button stays focusable and its reason stays reachable), re-checked against
  // currentDataset itself so the two can never drift apart. Opens the notebook as
  // the panel's tab (#470), opening the panel if it is closed; on the notebook tab
  // already, it closes the panel, as the chat circle does on chat.
  function handleNotebookClick(notebookButton) {
    if (notebookButton.getAttribute('aria-disabled') === 'true') return;
    if (!isNotebookAvailable()) return;
    const container = notebookButton.closest('.osa-chat-widget');
    if (!container) return;
    if (isOpen && activeTab === 'notebook') {
      toggleChat(container);
      return;
    }
    setTab(container, 'notebook');
    if (!isOpen) toggleChat(container);
  }

  // The notebook's frame, created on first use and whenever the dataset on screen
  // has changed since (#470). Kept, hidden, while another tab is open. A frame
  // that never hears from the notebook falls back to a link that opens it in a
  // browser tab of its own: 12 seconds after the frame loads (a page that refused
  // to be framed, or a host page whose policy refuses the frame, still loads, as
  // an error page, and never speaks), or 45 seconds after it was created at all.
  // A failed frame is kept too, so coming back to the tab shows the same fallback
  // rather than quietly starting over, and a notebook that was only slow still
  // recovers when it reports ready; Try again is what makes a fresh one.
  // Variables, not constants, only so the test hooks can shorten them.
  let NOTEBOOK_AFTER_LOAD_MS = 12000;
  let NOTEBOOK_TOTAL_MS = 45000;

  function ensureNotebookFrame(container) {
    if (!isNotebookAvailable()) return;
    const datasetId = currentDataset.id;
    if (notebookEmbed.frame && notebookEmbed.datasetId === datasetId) return;
    const view = container.querySelector('.osa-view-notebook');
    if (!view) return;
    discardNotebookFrame();
    const frame = document.createElement('iframe');
    frame.className = 'osa-notebook-frame';
    frame.title = `Python notebook for ${datasetId}`;
    frame.setAttribute('allow', 'clipboard-read; clipboard-write');
    notebookEmbed.frame = frame;
    notebookEmbed.datasetId = datasetId;
    notebookEmbed.state = 'loading';
    notebookEmbed.setup = null;
    notebookEmbed.failure = null;
    notebookEmbed.sentScheme = null;
    frame.addEventListener('load', () => {
      if (notebookEmbed.frame !== frame) return;
      // The notebook takes a theme before it is ready and applies it then.
      postNotebookTheme(true);
      if (notebookEmbed.state === 'loading') {
        clearTimeout(notebookEmbed.loadTimer);
        notebookEmbed.loadTimer = setTimeout(() => failNotebook(container, frame, 'timeout'), NOTEBOOK_AFTER_LOAD_MS);
      }
    });
    notebookEmbed.readyTimer = setTimeout(() => failNotebook(container, frame, 'timeout'), NOTEBOOK_TOTAL_MS);
    view.insertBefore(frame, view.firstChild);
    frame.src = notebookUrlFor(datasetId);
    renderNotebookStatus(container);
  }

  function discardNotebookFrame() {
    clearTimeout(notebookEmbed.loadTimer);
    clearTimeout(notebookEmbed.readyTimer);
    if (notebookEmbed.frame) notebookEmbed.frame.remove();
    notebookEmbed.frame = null;
    notebookEmbed.datasetId = null;
    notebookEmbed.state = 'idle';
    notebookEmbed.setup = null;
    notebookEmbed.failure = null;
    notebookEmbed.sentScheme = null;
  }

  function failNotebook(container, frame, failure) {
    if (notebookEmbed.frame !== frame || notebookEmbed.state === 'ready') return;
    clearTimeout(notebookEmbed.loadTimer);
    clearTimeout(notebookEmbed.readyTimer);
    notebookEmbed.state = 'failed';
    notebookEmbed.failure = failure;
    console.warn(`[OSA] The notebook for ${notebookEmbed.datasetId} did not ${failure === 'startup' ? 'start' : 'load in time'}; offering it in a tab of its own.`);
    renderNotebookStatus(container);
    renderLauncherIcons(container);
  }

  // Pass the widget's effective scheme to the notebook, which follows it (#469).
  // Addressed to the notebook's own origin, so it reaches nothing else; sent only
  // when it changed, unless forced (a fresh load, or the notebook saying it is ready).
  function postNotebookTheme(force) {
    const frame = notebookEmbed.frame;
    if (!frame || !frame.contentWindow) return;
    const origin = notebookOrigin();
    const scheme = isDarkScheme() ? 'dark' : 'light';
    if (!force && scheme === notebookEmbed.sentScheme) return;
    notebookEmbed.sentScheme = scheme;
    try {
      frame.contentWindow.postMessage({ target: 'osa-notebook', type: 'theme', scheme }, origin);
    } catch (e) {
      console.warn('[OSA] Could not pass the color scheme to the notebook:', e);
    }
  }

  // The notebook's own messages (notebook/osa-bridge.js): only from the frame this
  // widget made, and only from the notebook site's origin.
  function handleNotebookMessage(event) {
    const frame = notebookEmbed.frame;
    if (!frame || event.source !== frame.contentWindow) return;
    if (event.origin !== notebookOrigin()) return;
    const data = event.data;
    if (!data || typeof data !== 'object' || data.source !== 'osa-notebook') return;
    const container = document.querySelector('.osa-chat-widget');
    if (!container) return;
    if (data.type === 'ready') {
      clearTimeout(notebookEmbed.loadTimer);
      clearTimeout(notebookEmbed.readyTimer);
      notebookEmbed.state = 'ready';
      notebookEmbed.failure = null;
      postNotebookTheme(true);
    } else if (data.type === 'setup' && ['running', 'done', 'error', 'none'].includes(data.status)) {
      notebookEmbed.setup = data.status;
    } else if (data.type === 'error' && data.phase === 'startup') {
      failNotebook(container, frame, 'startup');
      return;
    } else if (data.type === 'theme') {
      // The notebook's answer to a theme message: nothing to show, but a theme it
      // could not apply leaves it in the wrong scheme, which a developer should see.
      if (data.applied === false) {
        console.warn(`[OSA] The notebook could not apply the ${data.scheme} theme.`);
      }
      return;
    } else {
      console.warn('[OSA] Ignoring a message from the notebook this widget does not recognize:', data);
      return;
    }
    renderNotebookStatus(container);
    renderLauncherIcons(container);
  }

  // The notebook tab's header line and its loading and fallback overlays.
  function renderNotebookStatus(container) {
    const text = container.querySelector('.osa-notebook-status-text');
    const loading = container.querySelector('.osa-notebook-loading');
    const fallback = container.querySelector('.osa-notebook-fallback');
    if (!text || !loading || !fallback) return;
    const id = notebookEmbed.datasetId || (currentDataset && currentDataset.id) || '';
    let status = 'Opening the notebook…';
    if (notebookEmbed.state === 'failed') {
      status = 'The notebook did not open here';
    } else if (notebookEmbed.state === 'ready') {
      status = {
        done: 'Python ready',
        none: 'Ready',
        error: 'Setup did not finish; see the notebook',
      }[notebookEmbed.setup] || 'Starting Python…';
    }
    text.textContent = id ? `${id} · ${status}` : status;
    loading.classList.toggle('osa-overlay-hidden', notebookEmbed.state !== 'loading');
    fallback.classList.toggle('osa-overlay-hidden', notebookEmbed.state !== 'failed');
    const link = fallback.querySelector('.osa-notebook-open-tab');
    if (link && id) link.href = notebookUrlFor(id);
    const reason = fallback.querySelector('.osa-notebook-overlay-text');
    if (reason) {
      reason.textContent = notebookEmbed.failure === 'startup'
        ? 'The notebook loaded but could not start here.'
        : 'The notebook did not load in this page. This page may not allow it, or it may be slow to start.';
    }
  }

  // The notebook view (#470): the frame goes in first, when the tab first opens;
  // the overlays cover it while it loads, and if it never speaks.
  function buildNotebookView() {
    const view = document.createElement('div');
    view.className = 'osa-view osa-view-notebook osa-view-off';
    view.setAttribute('inert', '');
    view.innerHTML = `
      <div class="osa-notebook-overlay osa-notebook-loading osa-overlay-hidden" role="status">
        <div class="osa-notebook-overlay-title">Opening the notebook</div>
        <div class="osa-notebook-bar-track"><div class="osa-notebook-bar"></div></div>
        <div class="osa-notebook-overlay-text">Python runs here, in this browser tab. The first time takes longer, while it downloads.</div>
      </div>
      <div class="osa-notebook-overlay osa-notebook-fallback osa-overlay-hidden" role="alert">
        <div class="osa-notebook-overlay-title">The notebook did not open here</div>
        <div class="osa-notebook-overlay-text"></div>
        <div class="osa-notebook-actions">
          <button type="button" class="osa-notebook-retry">Try again</button>
          <a class="osa-notebook-open-tab" target="_blank" rel="noopener">Open in a new tab</a>
        </div>
      </div>`;
    view.querySelector('.osa-notebook-retry').addEventListener('click', () => {
      const container = view.closest('.osa-chat-widget');
      if (!container) return;
      discardNotebookFrame();
      ensureNotebookFrame(container);
      renderLauncherIcons(container);
    });
    return view;
  }

  // Convert an already-rendered bubble widget into a capsule community's, in place:
  // the launcher and the panel's tabs. Called both right after creation
  // (CONFIG.launcher was already 'capsule', e.g. set via setConfig before init, or a
  // pop-out's preset) and again whenever the community config arrives with launcher:
  // capsule after the bubble was already built (the ordinary case:
  // fetchCommunityConfig resolves after createWidget). A no-op for every
  // bubble-mode widget, which is what keeps a bubble community's markup and computed
  // styles exactly as they were before this feature existed.
  //
  // A pop-out (fullscreen) has no launcher at all, so it gets the panel's tabs with
  // a strip under the header in place of the circles (#470). `osa-capsule` on the
  // container marks a capsule community's widget, with its tabs, in either; the
  // launcher itself is `.osa-launcher-capsule`, and only a page's widget has one.
  function applyLauncherMode(container) {
    if (CONFIG.launcher !== 'capsule' || container.classList.contains('osa-capsule')) return;

    const chatButton = container.querySelector('.osa-chat-button');
    if (!chatButton) return;

    if (!CONFIG.fullscreen) buildCapsuleLauncher(chatButton);

    // The panel: the header's two titles, and the chat and notebook views.
    const chatWindow = container.querySelector('.osa-chat-window');
    const header = chatWindow && chatWindow.querySelector('.osa-chat-header');
    const titleArea = header && header.querySelector('.osa-chat-title-area');
    if (titleArea) {
      const chatTitle = document.createElement('div');
      chatTitle.className = 'osa-ttl osa-ttl-chat';
      while (titleArea.firstChild) chatTitle.appendChild(titleArea.firstChild);
      const notebookTitle = document.createElement('div');
      notebookTitle.className = 'osa-ttl osa-ttl-notebook osa-ttl-off';
      notebookTitle.setAttribute('aria-hidden', 'true');
      notebookTitle.innerHTML = `
        <h3 class="osa-notebook-heading">Notebook</h3>
        <div class="osa-chat-status"><span class="osa-notebook-status-text"></span></div>`;
      titleArea.appendChild(chatTitle);
      titleArea.appendChild(notebookTitle);
    }
    if (header) {
      const views = document.createElement('div');
      views.className = 'osa-views';
      const chatView = document.createElement('div');
      chatView.className = 'osa-view osa-view-chat osa-view-on';
      // Everything between the header and the overlays is the chat's own.
      let node = header.nextElementSibling;
      while (node && !node.classList.contains('osa-settings-overlay')) {
        const next = node.nextElementSibling;
        chatView.appendChild(node);
        node = next;
      }
      views.appendChild(chatView);
      views.appendChild(buildNotebookView());
      header.after(views);
      if (CONFIG.fullscreen) header.after(buildTabStrip(container));
    }

    container.classList.add('osa-capsule', 'osa-tab-chat');

    renderLauncherIcons(container);
    positionIndicator(container);
    // The row and column layouts put the circles in different places.
    if (!CONFIG.fullscreen) window.addEventListener('resize', () => positionIndicator(container));
    // The notebook tab's frame speaks through messages (#470). Only a capsule has
    // that frame, so only a capsule listens.
    window.addEventListener('message', handleNotebookMessage);
  }

  // The capsule's launcher (#436): moves the EXISTING .osa-chat-button node (its
  // listeners intact) into a new wrapper alongside two new icon buttons, rather
  // than rebuilding the widget's markup from scratch.
  function buildCapsuleLauncher(chatButton) {
    const capsule = document.createElement('div');
    capsule.className = 'osa-launcher-capsule';
    chatButton.parentNode.insertBefore(capsule, chatButton);

    const indicator = document.createElement('div');
    indicator.className = 'osa-capsule-indicator';
    indicator.setAttribute('aria-hidden', 'true');
    const hpcButton = buildLauncherIcon('hpc', ICONS.hpc);
    const notebookButton = buildLauncherIcon('notebook', ICONS.notebook);
    notebookButton.insertAdjacentHTML('beforeend',
      '<svg class="osa-notebook-ring" viewBox="0 0 46 46" aria-hidden="true"><circle cx="23" cy="23" r="21.5"></circle></svg>');
    // DOM order is bottom-to-top / left-to-right: hpc, notebook, chat. A fixed
    // container anchored on bottom+right only (never top/left) grows away from
    // that corner as hidden siblings reveal, so the LAST child, the chat button,
    // never moves regardless of how many icons appear before it.
    // This is also the Tab order (Tab follows DOM order, not visual position),
    // and it matches the visual order in both layouts: top-to-bottom on desktop
    // (HPC above notebook above chat) and left-to-right at 600px and under
    // (HPC, then notebook, then chat, reading toward the bubble). Keep the
    // appendChild calls in this order for that reason, not just habit. The
    // indicator goes first and is drawn behind the three.
    capsule.appendChild(indicator);
    capsule.appendChild(hpcButton);
    capsule.appendChild(notebookButton);
    capsule.appendChild(chatButton);

    // The capsule's chat circle always shows the chat icon (a bubble opened before
    // the community config arrived swapped it for a close icon), and its tooltip
    // says what a click does while the panel is open.
    chatButton.innerHTML = ICONS.chat;
    const chatTooltip = document.createElement('span');
    chatTooltip.className = 'osa-icon-tooltip';
    chatTooltip.setAttribute('aria-hidden', 'true');
    chatButton.appendChild(chatTooltip);

    notebookButton.addEventListener('click', () => handleNotebookClick(notebookButton));
    // The HPC button has no click action (#436: coming soon everywhere); the
    // listener exists only so a click event never bubbles into anything else.
    hpcButton.addEventListener('click', () => {});
  }

  // Undo applyLauncherMode (#475): a load drawn from a remembered capsule whose
  // fresh config no longer asks for one goes back to the bubble's own markup, a
  // pop-out's tab strip included. The capsule's window listeners stay; with its
  // elements gone they find nothing.
  function revertLauncherMode(container) {
    if (!container.classList.contains('osa-capsule')) return;
    discardNotebookFrame();
    activeTab = 'chat';
    const capsule = container.querySelector('.osa-launcher-capsule');
    const chatButton = capsule && capsule.querySelector('.osa-chat-button');
    if (chatButton) {
      chatButton.removeAttribute('aria-pressed');
      chatButton.innerHTML = isOpen ? ICONS.close : ICONS.chat;
      chatButton.setAttribute('aria-label', isOpen ? 'Close chat' : 'Open chat');
      capsule.before(chatButton);
    }
    if (capsule) capsule.remove();
    const titleArea = container.querySelector('.osa-chat-title-area');
    const chatTitle = titleArea && titleArea.querySelector('.osa-ttl-chat');
    if (chatTitle) {
      while (chatTitle.firstChild) titleArea.insertBefore(chatTitle.firstChild, chatTitle);
      chatTitle.remove();
    }
    const notebookTitle = titleArea && titleArea.querySelector('.osa-ttl-notebook');
    if (notebookTitle) notebookTitle.remove();
    const strip = container.querySelector('.osa-tab-strip');
    if (strip) strip.remove();
    const views = container.querySelector('.osa-views');
    if (views) {
      const chatView = views.querySelector('.osa-view-chat');
      while (chatView && chatView.firstChild) views.before(chatView.firstChild);
      views.remove();
    }
    container.classList.remove('osa-capsule', 'osa-tab-chat', 'osa-tab-notebook');
  }

  // Apply a setDataset(value) call: validates, stores the result (even before the
  // widget's DOM exists, so a call made before init() is applied once it does), and
  // re-renders if the capsule is already there. A different dataset, or one without
  // a Zarr copy any more, takes the notebook tab back to chat; its frame is replaced
  // the next time the tab opens (#470).
  function applySetDataset(value) {
    if (value === null) {
      currentDataset = null;
    } else if (value && typeof value === 'object') {
      if (!isValidDatasetId(value.id)) {
        console.warn('[OSA] setDataset: invalid dataset id, ignoring:', value.id);
        return;
      }
      let zarr;
      if (value.zarr === true || value.zarr === false || value.zarr === undefined) {
        zarr = value.zarr;
      } else {
        console.warn('[OSA] setDataset: invalid zarr value, ignoring:', value.zarr);
        return;
      }
      // subject and task only fill question blanks (#477), so a bad one is dropped
      // alone: refusing the whole call would keep the previous dataset on screen, and
      // with it the notebook icon, for a mistake in a fact that gates nothing.
      const facts = {};
      for (const fact of ['subject', 'task']) {
        if (value[fact] === undefined) continue;
        if (isValidBidsLabel(value[fact])) {
          facts[fact] = value[fact];
        } else {
          console.warn(`[OSA] setDataset: invalid ${fact} label, dropping it:`, value[fact]);
        }
      }
      currentDataset = { id: value.id, zarr, subject: facts.subject, task: facts.task };
    } else {
      console.warn('[OSA] setDataset: invalid value, ignoring:', value);
      return;
    }
    const container = document.querySelector('.osa-chat-widget');
    if (!container) return;
    // Stale: the dataset on screen can no longer open a notebook (null, or no Zarr
    // copy), or it is a different dataset from the one the frame was made for.
    const stale = !!notebookEmbed.frame &&
      (!isNotebookAvailable() || notebookEmbed.datasetId !== currentDataset?.id);
    if (stale) {
      discardNotebookFrame();
      if (activeTab === 'notebook') setTab(container, isNotebookAvailable() ? 'notebook' : 'chat');
    } else if (activeTab === 'notebook' && !isNotebookAvailable()) {
      setTab(container, 'chat');
    }
    renderNotebookStatus(container);
    renderLauncherIcons(container);
    if (container.querySelector('.osa-suggestions')) renderSuggestions(container);
    forwardDatasetToPopout();
  }

  // An open pop-out has no host page of its own to call setDataset, so it is told
  // the dataset on screen when it opens (window.__OSA_DATASET__, in openPopout) and
  // again whenever the host page names another, as applyColorSchemeEverywhere does
  // for the color scheme (#477: its suggestions are about the dataset). A pop-out
  // whose script is still loading has no API yet, and its init() reads the preset,
  // over anything its API was told first, so the preset is kept current too.
  function forwardDatasetToPopout() {
    if (!chatPopup || chatPopup.closed) return;
    let popupWidget = null;
    let dataset;
    try {
      // The pop-out's own copy, for its preset and its API alike, as openPopout's.
      dataset = toPopupRealm(chatPopup, currentDataset);
      chatPopup.__OSA_DATASET__ = dataset;
      popupWidget = chatPopup.OSAChatWidget;
    } catch (e) {
      console.warn('[OSA] Could not reach the pop-out to pass the dataset on:', e);
      return;
    }
    if (!popupWidget || typeof popupWidget.setDataset !== 'function') return;
    try {
      popupWidget.setDataset(dataset);
    } catch (e) {
      console.error('[OSA] The pop-out failed to apply the dataset:', e);
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
    if (msg.role !== 'assistant') {
      const { dataset, ...rest } = msg;
      return isValidDatasetId(dataset) ? { ...rest, dataset } : rest;
    }
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
    return executions
      .filter((run) => run && typeof run === 'object' && !Array.isArray(run))
      .slice(0, MAX_BROWSER_RUNS_PER_REPLY)
      .map((run) => ({
        callId: clipField(run.callId, 'callId'),
        tool: clipField(run.tool, 'tool'),
        description: clipField(run.description, 'description'),
        code: clipField(run.code, 'code'),
        status: clipField(run.status, 'status'),
        stdout: clipField(run.stdout, 'stdout'),
        stderr: clipField(run.stderr, 'stderr'),
        images: [],
        // Whether the READER ran this themselves (runLocal), not the
        // assistant: always a real boolean, never left undefined, so a run
        // read back from storage renders identically to one just produced.
        local: run.local === true,
        workspaceNote: clipField(run.workspaceNote, 'workspaceNote'),
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
        // Figures are shown for the life of the page and not stored. An open
        // "Edit and run" editor, its draft and its live echo are the same
        // kind of transient, in-page-only state as the feedback flags above:
        // a reload always starts back at the plain recorded view.
        if (Array.isArray(rest.executions)) {
          rest.executions = rest.executions.map((run) => {
            const { _editing, _draft, _runningLocal, _localResult, ...keep } = run;
            return { ...keep, images: [] };
          });
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
  // However the fetch ends (applied, refused, unreachable), a launcher waiting for
  // it is shown (#475).
  // Copy a community config's `widget` block into CONFIG, for every field the
  // embedder has not set itself (setConfig outranks the community). Returns whether
  // anything changed. Explicit null/undefined checks, not truthiness, so that a null
  // initial_message from the API correctly replaces the hardcoded HED default. Used
  // for the fresh config, and for the one remembered from the last load (#475).
  function applyWidgetDisplay(w) {
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
    if (w.dataset_suggested_questions != null && !_userSetKeys.has('datasetSuggestedQuestions')) {
      CONFIG.datasetSuggestedQuestions = w.dataset_suggested_questions;
      changed = true;
    }
    if (w.theme_color != null && !_userSetKeys.has('themeColor')) {
      CONFIG.themeColor = w.theme_color;
      changed = true;
    }
    if (w.user_bubble_color != null && !_userSetKeys.has('userBubbleColor')) {
      CONFIG.userBubbleColor = w.user_bubble_color;
      changed = true;
    }
    if (w.theme_text_color != null && !_userSetKeys.has('themeTextColor')) {
      CONFIG.themeTextColor = w.theme_text_color;
      changed = true;
    }
    if (w.accent_color != null && !_userSetKeys.has('accentColor')) {
      CONFIG.accentColor = w.accent_color;
      changed = true;
    }
    if (w.user_bubble_text_color != null && !_userSetKeys.has('userBubbleTextColor')) {
      CONFIG.userBubbleTextColor = w.user_bubble_text_color;
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
    if (w.launcher != null && !_userSetKeys.has('launcher')) {
      CONFIG.launcher = w.launcher;
      changed = true;
    }
    if (w.launcher_label != null && !_userSetKeys.has('launcherLabel')) {
      CONFIG.launcherLabel = w.launcher_label;
      changed = true;
    }
    if (w.color_scheme != null && !_userSetKeys.has('colorScheme')) {
      if (COMMUNITY_COLOR_SCHEMES.includes(w.color_scheme)) {
        CONFIG.colorScheme = w.color_scheme;
        changed = true;
      } else {
        warnInvalidColorScheme('color_scheme from the community config', w.color_scheme, COMMUNITY_COLOR_SCHEMES);
      }
    }
    return changed;
  }

  // The community's look, remembered from the last load (#475), so the widget is
  // drawn in it from the first frame instead of in the built-in defaults until the
  // config request returns. Only the `widget` block the server sent, applied through
  // applyWidgetDisplay's checks exactly as a fresh one is; the fresh config still
  // arrives, wins, and replaces it. The API endpoint is stored beside it, so a page
  // pointed at another deployment starts fresh.
  function widgetMemoryKey(communityId = CONFIG.communityId) {
    return `osa-widget-config-${communityId}`;
  }

  function readRememberedWidget() {
    try {
      const raw = localStorage.getItem(widgetMemoryKey());
      if (!raw) return null;
      const saved = JSON.parse(raw);
      if (!saved || typeof saved !== 'object' || saved.apiEndpoint !== CONFIG.apiEndpoint) return null;
      const widget = saved.widget;
      if (!widget || typeof widget !== 'object' || Array.isArray(widget)) return null;
      return widget;
    } catch (e) {
      console.warn('[OSA] Ignoring the remembered widget config:', e.message || e);
      return null;
    }
  }

  // `requested` is the community and endpoint the config was fetched for, which a
  // setConfig during the fetch may no longer match.
  function rememberWidget(widget, requested) {
    try {
      localStorage.setItem(widgetMemoryKey(requested.communityId),
        JSON.stringify({ apiEndpoint: requested.apiEndpoint, widget }));
    } catch (e) {
      // Storage full or blocked: the next load draws the defaults first, as before.
      console.warn('[OSA] Could not remember the widget config:', e.message || e);
    }
  }

  // Show a launcher that was waiting for the community config (#475). The waiting
  // class also turns transitions off, so reading the layout first commits the
  // community's colors while they are off; removing the class then shows the button
  // already in them, with no fade from the defaults.
  function revealLauncher() {
    launcherWaiting = false;
    const container = document.querySelector('.osa-chat-widget');
    if (container && container.classList.contains('osa-launcher-waiting')) {
      container.getBoundingClientRect();
      container.classList.remove('osa-launcher-waiting');
    }
    for (const run of launcherShownCallbacks.splice(0)) run();
  }

  // Run `fn` once the launcher is on screen: now, or when a first visit's wait ends.
  function whenLauncherShown(fn) {
    if (launcherWaiting) launcherShownCallbacks.push(fn);
    else fn();
  }

  async function fetchCommunityConfig() {
    try {
      await loadCommunityConfig();
    } finally {
      revealLauncher();
    }
  }

  async function loadCommunityConfig() {
    const requested = { communityId: CONFIG.communityId, apiEndpoint: CONFIG.apiEndpoint };
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

      // Apply widget display config from API for fields not explicitly set by the embedder,
      // and remember it for the next load (#475).
      if (data && data.widget && typeof data.widget === 'object' && !Array.isArray(data.widget)) {
        if (displayDefaults) {
          // A remembered look was drawn first: back to the defaults, then the fresh
          // config, so that it wins for every field, including one it leaves out.
          for (const key of DISPLAY_KEYS) {
            if (!_userSetKeys.has(key)) CONFIG[key] = displayDefaults[key];
          }
          displayDefaults = null;
          applyWidgetDisplay(data.widget);
          applyWidgetConfig();
        } else if (applyWidgetDisplay(data.widget)) {
          applyWidgetConfig();
        }
        rememberWidget(data.widget, requested);
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

  // Mirrors MAX_BROWSER_RUNS_PER_REPLY in src/core/limits.py, which the server
  // enforces: the run with no budget left refuses a further browser call in
  // writing. The widget stops at the same number so an older server cannot
  // make it loop either. test-widget-tools.js checks the two agree.
  const MAX_BROWSER_RUNS_PER_REPLY = 20;

  // How long to wait before each retry of a result the worker's per-minute
  // limit refused. That limit counts every resume, and a reply that runs code
  // quickly can reach it. The code has already run and the parked call stays
  // answerable for 15 minutes, so the result is worth waiting for rather than
  // dropping. The limiter's window is 60 seconds.
  const RESUME_RATE_LIMIT_WAITS_MS = Object.freeze([30000, 35000]);

  // How much of each field of a run survives on the reply and in storage.
  // executionRecord writes with these and normalizePersistedExecutions reads
  // back with them; if they disagreed, a reload would quietly change what a
  // run shows.
  const EXECUTION_FIELD_LIMITS = Object.freeze({
    callId: 256,
    tool: 64,
    description: 500,
    code: 20000,
    status: 32,
    stdout: 4000,
    stderr: 4000,
    // A save-failure note (see withWorkspaceNote, osa-controller.js), shown
    // regardless of the run's own status, unlike stderr below.
    workspaceNote: 2000,
  });

  // The heading of a run's record, by result status.
  const EXECUTION_LABELS = Object.freeze({
    ok: 'Ran Python',
    error: 'Python raised an error',
    denied: 'Not run',
    timeout: 'Stopped: took too long',
    cancelled: 'Stopped',
    oom: 'Stopped: out of memory',
  });

  // Where the runtime bundle lives: next to this script. A copy with no script
  // URL of its own is handed one through its config (widgetScriptUrl).
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

  // Code execution is off for this page. Logged in one fixed shape, so a
  // systemic cause (a stale hash after a deploy, an embedder's policy) is
  // recognizable in any report of it, and kept for getBrowserRuntimeStatus.
  function markBrowserToolsUnavailable(reason, detail) {
    browserToolsUnavailable = { reason, detail: detail || '' };
    console.warn(
      `[OSA] browser-runtime-unavailable reason=${reason}${detail ? `: ${detail}` : ''}. ` +
      'Code execution is off; the assistant still answers.'
    );
  }

  // Resolves with the OSARuntime global the bundle defines, or null.
  function loadRuntimeBundle() {
    const url = runtimeBundleUrl();
    if (!url) {
      markBrowserToolsUnavailable('no-script-url', 'the widget cannot tell where it was loaded from');
      return Promise.resolve(null);
    }
    return new Promise((resolve) => {
      const script = document.createElement('script');
      script.src = url;
      // The browser refuses the file before any of it runs unless it matches.
      script.integrity = RUNTIME_BUNDLE_INTEGRITY;
      script.crossOrigin = 'anonymous';
      script.async = true;
      script.onload = () => {
        const api = window.OSARuntime || null;
        // Loaded and verified, yet it defined nothing: it threw while running,
        // or something on the page removed the global.
        if (!api) markBrowserToolsUnavailable('bad-bundle', 'it loaded but did not define the runtime');
        resolve(api);
      };
      script.onerror = () => {
        // The browser does not say which: blocked by the page's policy,
        // unreachable, or not matching its integrity hash.
        markBrowserToolsUnavailable('load-failed', url);
        resolve(null);
      };
      document.head.appendChild(script);
    });
  }

  function setUpBrowserTools(data) {
    const tools = data && Array.isArray(data.client_tools) ? data.client_tools : [];
    const python = data && data.runtime && data.runtime.python;
    if (tools.length === 0 || !python || browserToolsSetup) return;
    // The wheels the community adds to Pyodide, served by the same API that sent
    // their hashes.
    const lock = data.runtime_lock && data.runtime_lock.packages
      ? { packages: data.runtime_lock.packages, baseUrl: `${CONFIG.apiEndpoint}/${CONFIG.communityId}/runtime/` }
      : null;
    browserToolsSetup = { tools, python, lock };
    startBrowserTools();
  }

  function startBrowserTools() {
    const { tools, python, lock } = browserToolsSetup;
    browserToolsUnavailable = null;
    browserToolsReady = loadRuntimeBundle().then((api) => {
      if (!api) return null;
      if (typeof api.ClientToolController !== 'function' || typeof api.PyodideRuntime !== 'function'
        || !api.GATE_DECISION) {
        markBrowserToolsUnavailable('bad-bundle', 'the runtime it defined is incomplete');
        return null;
      }
      try {
        browserRuntime = new api.PyodideRuntime({
          runtime: python,
          lock,
          onProgress: onRuntimeProgress,
          onStateChange: onRuntimeStateChange,
        });
        // One workspace per community (#433), constructed here so the
        // controller can persist through it and the Settings panel can read
        // and export through the same instance (getWorkspaceStore).
        workspaceStore = new api.WorkspaceStore({ community: CONFIG.communityId });
        browserTools = new api.ClientToolController({
          runtime: browserRuntime,
          tools,
          gate: askToRunCode,
          workspace: workspaceStore,
        });
        runtimeApi = api;
      } catch (err) {
        browserRuntime = null;
        browserTools = null;
        workspaceStore = null;
        markBrowserToolsUnavailable('setup-failed', err && err.message);
        return null;
      }
      if (isOpen) preloadRuntime();
      // The reader may have already sent their first message while this bundle
      // was still loading (the runtime object did not exist yet to boot then);
      // catch up now that it does.
      if (firstMessageSent) preloadRuntimeOnFirstMessage();
      return browserTools;
    });
  }

  // A failed load may have been a network blip rather than a block or a bad
  // hash, which the browser does not tell apart, so it is retried once, the
  // next time the chat is opened. Only once: a block or a bad hash fails the
  // same way every time.
  function retryBrowserToolsOnce() {
    if (!browserToolsUnavailable || browserToolsUnavailable.reason !== 'load-failed' || browserToolsRetried) return;
    browserToolsRetried = true;
    startBrowserTools();
  }

  // `state` describes the WIRING: whether a bundle was requested, is loading, failed,
  // or is ready to hand the controller a runtime to boot. It does NOT describe Pyodide
  // itself, which boots separately (lazily under first_run, eagerly under widget_open
  // or first_message) and can be idle, booting, ready, failed or terminated at any
  // point after `state` already reads 'ready'. `runtime` carries that separately, from
  // the PyodideRuntime instance's own `.state` (see RUNTIME_STATE in osa-runtime.js),
  // and is null whenever no such instance exists yet (every state but 'ready').
  function browserRuntimeStatus() {
    if (!browserToolsSetup) return { state: 'off', reason: null, runtime: null };
    if (browserToolsUnavailable) return { state: 'unavailable', ...browserToolsUnavailable, runtime: null };
    if (!browserTools) return { state: 'loading', reason: null, runtime: null };
    return { state: 'ready', reason: null, runtime: browserRuntime ? browserRuntime.state : null };
  }

  // The workspace exists only for a community that declares client tools, and
  // only once the runtime bundle (which carries WorkspaceStore) has loaded;
  // null otherwise, which every caller below treats as "nothing to persist
  // or show" rather than an error.
  // The SAME instance the controller persists through (see startBrowserTools):
  // one open IndexedDB connection per community, not two, and the Settings
  // panel's view of "what is saved" is never a beat behind the controller's.
  function getWorkspaceStore() {
    return workspaceStore;
  }

  function formatWorkspaceBytes(n) {
    if (!Number.isFinite(n) || n < 1024) return `${Math.max(0, Math.round(n || 0))} B`;
    const units = ['KB', 'MB', 'GB'];
    let value = n / 1024;
    let unit = 0;
    while (value >= 1024 && unit < units.length - 1) {
      value /= 1024;
      unit += 1;
    }
    return `${value.toFixed(1)} ${units[unit]}`;
  }

  // Shown only for a community that declares client tools (getWorkspaceStore
  // is null until the runtime bundle carrying WorkspaceStore has loaded,
  // which only happens for one, see startBrowserTools). Re-run every time
  // Settings opens, the same way the model dropdown is, so a workspace
  // written after the panel last opened is reflected.
  async function refreshWorkspacePanel(container) {
    const field = container.querySelector('.osa-workspace-field');
    const usage = container.querySelector('.osa-workspace-usage');
    const deleteBtn = container.querySelector('.osa-workspace-delete-btn');
    if (!field) return;
    workspaceDeleteConfirming = false;
    if (deleteBtn) {
      deleteBtn.textContent = 'Delete workspace';
      deleteBtn.classList.remove('osa-workspace-confirm');
    }
    const store = getWorkspaceStore();
    if (!store) {
      field.style.display = 'none';
      return;
    }
    field.style.display = '';
    if (usage) usage.textContent = 'Checking workspace size...';
    try {
      const bytes = await store.sizeUsed();
      if (usage) usage.textContent = `Using ${formatWorkspaceBytes(bytes)} in this browser.`;
    } catch (err) {
      if (usage) usage.textContent = `Workspace size is not available: ${(err && err.message) || err}`;
    }
  }

  async function downloadWorkspace(container) {
    const store = getWorkspaceStore();
    if (!store) return;
    try {
      const bytes = await store.exportZip();
      const blob = new Blob([bytes], { type: 'application/zip' });
      const url = URL.createObjectURL(blob);
      const link = document.createElement('a');
      link.href = url;
      link.download = `${CONFIG.communityId}-workspace.zip`;
      document.body.appendChild(link);
      link.click();
      link.remove();
      // Freed after the click has had a chance to start the download, rather
      // than immediately: revoking too early can cancel the download itself.
      setTimeout(() => URL.revokeObjectURL(url), 5000);
    } catch (err) {
      console.error('[OSA] Workspace export failed:', err);
      showError(container, `Could not build the workspace download: ${(err && err.message) || err}`);
    }
  }

  // A second click on the same button confirms, rather than confirm()/
  // alert(): those are unusable in an embedded, possibly-sandboxed iframe,
  // and this widget uses no browser dialog anywhere else.
  async function handleWorkspaceDeleteClick(container) {
    const deleteBtn = container.querySelector('.osa-workspace-delete-btn');
    if (!workspaceDeleteConfirming) {
      workspaceDeleteConfirming = true;
      if (deleteBtn) {
        deleteBtn.textContent = 'Confirm delete';
        deleteBtn.classList.add('osa-workspace-confirm');
      }
      return;
    }
    workspaceDeleteConfirming = false;
    if (deleteBtn) {
      deleteBtn.textContent = 'Delete workspace';
      deleteBtn.classList.remove('osa-workspace-confirm');
    }
    const store = getWorkspaceStore();
    if (!store) return;
    const result = await store.deleteAll();
    if (!result.ok) {
      showError(container, `Could not delete the workspace: ${result.reason}`);
      return;
    }
    await refreshWorkspacePanel(container);
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

  // Boot as soon as the reader's first message is sent, rather than waiting for a
  // run, for a community configured with preload_on: first_message. Overlaps the
  // Python download with the model's first turn instead of paying for it serially.
  // Fire-and-forget exactly like preloadRuntime(): boot() is idempotent, so calling
  // this more than once (open + first message, or a retry once the bundle loads)
  // never starts a second boot.
  function preloadRuntimeOnFirstMessage() {
    if (!browserRuntime || !browserRuntime.preloadsOnFirstMessage) return;
    browserRuntime.boot().catch((err) => {
      console.warn('[OSA] Preloading the browser runtime on first message failed:', err && err.message);
    });
  }

  // The client tools this page can run, once known. Waits briefly for the
  // config and the runtime, so a question typed the moment the page opens is
  // not sent without them; after that it resolves at once.
  function declaredClientTools() {
    return declaredClientToolsWithin(communityConfigReady, () => browserToolsReady, 3000);
  }

  // ONE deadline covers both waits, deliberately: it bounds how long a first
  // message can be held, and a deadline per wait would double that. The tools
  // are read through a getter because they only exist once the config is in.
  async function declaredClientToolsWithin(configReady, toolsReady, ms) {
    let timer = null;
    const deadline = new Promise((resolve) => {
      timer = setTimeout(() => resolve(null), ms);
    });
    try {
      if (configReady) await Promise.race([configReady, deadline]);
      const ready = toolsReady();
      if (!ready) return [];
      const tools = await Promise.race([ready, deadline]);
      return tools ? tools.declared : [];
    } finally {
      clearTimeout(timer);
    }
  }

  // Re-render from outside a click handler, where no container is at hand.
  // A render failure is logged rather than thrown: the callers are the gate
  // and runtime progress, and neither may stop because the display failed.
  // A gate that never resolves leaves the server's call parked with nobody to
  // answer it and the widget frozen.
  function renderIfMounted() {
    const container = document.querySelector('.osa-chat-widget');
    if (!container) return;
    try {
      renderMessages(container);
    } catch (err) {
      console.error('[OSA] Could not render the conversation:', err);
    }
  }

  // The two shapes toolActivity takes, built only here.
  function askingActivity(prompt, decide) {
    return { phase: 'asking', prompt, decide };
  }

  function runningActivity(prompt) {
    // A preload_on: first_message boot can already be under way when the reader
    // clicks Run, having advanced past steps this panel never existed to receive
    // (onRuntimeProgress only writes into a 'running' toolActivity, and there was
    // none until now). Seed the panel with the runtime's last known step instead
    // of a blank bar; null whenever the runtime is not actually mid-boot, so a
    // run that starts with no preload under way behaves exactly as before.
    const progress = (browserRuntime && browserRuntime.state === 'booting') ? lastBootProgress : null;
    return { phase: 'running', prompt, progress };
  }

  // Shown when the interpreter has loaded and its packages are about to: the
  // point where a reader might wonder whether this happens every time, so it
  // says it does not. No size, which depends on the community and the cache.
  const RUNTIME_LOADED_LABEL =
    'Python started. It downloads once, and your browser keeps it after that.';

  // The code and description a tool_request carries, as strings. The request
  // came from the model, so neither is assumed to be one.
  function requestPrompt(request) {
    const args = (request && request.args) || {};
    return {
      code: typeof args.code === 'string' ? args.code : '',
      description: typeof args.description === 'string' ? args.description : '',
    };
  }

  function onRuntimeProgress(event) {
    const phase = event && event.phase;
    let text;
    if (phase === 'loading_runtime') {
      text = 'Starting Python in your browser...';
    } else if (phase === 'runtime_loaded') {
      text = RUNTIME_LOADED_LABEL;
    } else if ((phase === 'loading_package' || phase === 'installing') && event.package) {
      text = `Loading ${event.package}...`;
    } else if (phase === 'prelude') {
      text = 'Setting up the Python environment...';
    } else {
      return;
    }
    const rawStep = Number.isInteger(event.step) && event.step > 0 ? event.step : null;
    const rawSteps = Number.isInteger(event.steps) && event.steps > 0 ? event.steps : null;
    const hasBar = rawStep !== null && rawSteps !== null && rawStep <= rawSteps;
    const progress = { text, step: hasBar ? rawStep : null, steps: hasBar ? rawSteps : null };
    // Cached independent of toolActivity (see runningActivity()): a
    // preload_on: first_message boot can emit several of these before any
    // 'running' panel exists to show them.
    lastBootProgress = progress;
    if (!toolActivity || toolActivity.phase !== 'running') return;
    toolActivity.progress = progress;
    renderIfMounted();
  }

  // Boot progress describes a boot. Once the runtime leaves `booting`, for
  // ready, failed, terminated or idle, the boot's last label and bar no longer
  // say what is happening, so the panel goes back to its running label until
  // the answer ends it, and a later boot's runningActivity() has nothing stale
  // to inherit.
  function onRuntimeStateChange(state) {
    if (state !== 'booting') lastBootProgress = null;
    if (state === 'booting' || !toolActivity || toolActivity.phase !== 'running') return;
    if (!toolActivity.progress) return;
    toolActivity.progress = null;
    renderIfMounted();
  }

  // The gate the controller calls. Resolves with the person's decision, and
  // always resolves: see renderIfMounted.
  function askToRunCode(prompt) {
    return new Promise((resolve) => {
      toolActivity = askingActivity(prompt, (decision, alwaysRun) => {
        try {
          if (decision === runtimeApi.GATE_DECISION.RUN) {
            if (alwaysRun && browserTools) browserTools.autoRun = true;
            toolActivity = runningActivity(prompt);
          } else {
            toolActivity = null;
          }
          renderIfMounted();
        } finally {
          resolve(decision);
        }
      });
      renderIfMounted();
    });
  }

  function clipField(value, field) {
    return typeof value === 'string' ? value.slice(0, EXECUTION_FIELD_LIMITS[field]) : '';
  }

  // What the reader is shown of a run, kept on the reply. The images are shown
  // now and never stored: three figures can be megabytes, and browser storage
  // for a whole conversation is a few.
  function executionRecord(request, result) {
    const prompt = requestPrompt(request);
    return {
      callId: clipField(request && request.call_id, 'callId'),
      tool: clipField(request && request.tool, 'tool'),
      description: clipField(prompt.description, 'description'),
      code: clipField(prompt.code, 'code'),
      status: clipField(result && result.status, 'status'),
      stdout: clipField(result && result.stdout, 'stdout'),
      stderr: clipField(result && result.stderr, 'stderr'),
      images: result && Array.isArray(result.images) ? result.images : [],
      local: false,
      // Non-enumerable on `result` (osa-controller.js's withWorkspaceNote),
      // so a plain property read is the only way to see it; it never rode
      // along in what was sent to the server.
      workspaceNote: clipField(result && result.workspaceFailureNote, 'workspaceNote'),
    };
  }

  // The same shape, for a run the READER started with "Edit and run"
  // (ClientToolController#runLocal) rather than one the assistant asked for:
  // no request came from the server, so this is built from the edited code
  // and description directly instead of requestPrompt().
  function localExecutionRecord(tool, description, code, result) {
    return {
      callId: clipField(result && result.call_id, 'callId'),
      tool: clipField(tool, 'tool'),
      description: clipField(description, 'description'),
      code: clipField(code, 'code'),
      status: clipField(result && result.status, 'status'),
      stdout: clipField(result && result.stdout, 'stdout'),
      stderr: clipField(result && result.stderr, 'stderr'),
      images: result && Array.isArray(result.images) ? result.images : [],
      local: true,
      // The reader's own run never reaches the server at all, so this note
      // is the ONLY place a save failure on it is ever visible to anyone.
      workspaceNote: clipField(result && result.workspaceFailureNote, 'workspaceNote'),
    };
  }

  // The run record a rerun control's data-msg-index/data-run-index name, or
  // null if either index is stale (the reply was cleared from under it).
  function runAt(el) {
    const msgIndex = parseInt(el.getAttribute('data-msg-index'), 10);
    const runIndex = parseInt(el.getAttribute('data-run-index'), 10);
    const message = messages[msgIndex];
    return (message && Array.isArray(message.executions) && message.executions[runIndex]) || null;
  }

  // Run the reader's own edit of a recorded run: consent is the click
  // itself, so this skips the permission gate entirely (runLocal), shares
  // the runtime and its namespace with the assistant's own runs, and sends
  // nothing to the server. The result becomes its own entry in the reply's
  // runs (kept there so it survives a reload, see executionsHtml/saveHistory)
  // and, while the editor stays open, is also shown live right under it.
  async function runEditedCode(container, msgIndex, runIndex) {
    const message = messages[msgIndex];
    const run = message && Array.isArray(message.executions) && message.executions[runIndex];
    if (!run || !browserTools) return;
    const blocked = localRunBlockedReason();
    if (blocked) {
      showError(container, blocked);
      return;
    }
    const code = clipField(typeof run._draft === 'string' ? run._draft : (run.code || ''), 'code');
    if (code.trim() === '') {
      showError(container, 'There is no code to run.');
      return;
    }
    run._runningLocal = true;
    renderMessages(container);
    let outcome;
    try {
      outcome = await browserTools.runLocal(code, { description: run.description || '', session: sessionId || '' });
    } catch (err) {
      // runLocal's own contract is to never throw for anything that can
      // happen while running; this is defensive, for a genuine bug here or
      // in the controller, so a failure here cannot leave the UI stuck on
      // "Running your code..." forever with nothing logged and no way out.
      run._runningLocal = false;
      console.error('[OSA] runLocal failed unexpectedly:', err);
      renderMessages(container);
      showError(container, `Could not run this code: ${(err && err.message) || err}`);
      return;
    }
    run._runningLocal = false;
    if (!outcome.ok) {
      renderMessages(container);
      showError(container, `Could not run this code: ${outcome.reason}`);
      return;
    }
    const record = localExecutionRecord(run.tool, run.description || '', code, outcome.result);
    message.executions = message.executions.concat(record);
    run._localResult = {
      callId: record.callId,
      status: record.status,
      stdout: record.stdout,
      stderr: record.stderr,
      images: record.images,
      workspaceNote: record.workspaceNote,
    };
    renderMessages(container);
    try {
      saveHistory();
    } catch (saveError) {
      // saveHistory() already shows the SPECIFIC reason on screen (storage
      // full, privacy settings blocking localStorage); a second, generic
      // message here would overwrite it with something less useful. Matches
      // commitResponseFeedback's own saveHistory() catch, the other place a
      // save failure here must not cost the reader the real reason.
      console.error('[OSA] Failed to save history:', saveError);
    }
  }

  // Answer one tool_request and record it on the reply it belongs to.
  //
  // Workspace persistence (#433) happens INSIDE browserTools.answer(): the
  // controller is handed a WorkspaceStore when it is constructed (see
  // startBrowserTools) and writes a run's files there itself, before this
  // function's caller ever sees the result, so `result` here already
  // reflects any save failure (a dropped artifact, a stderr note).
  async function answerToolRequest(container, request, messageIndex) {
    if (!browserTools) {
      // Tools are declared only once the controller exists, so the server
      // should never ask. If it does, the parked call is abandoned (and
      // repaired) by the next message, which declares nothing.
      throw new Error(
        'Code execution is not available on this page, so the code could not run. ' +
        'Send your message again and the assistant will answer without it.'
      );
    }
    const runsCode = request.tool !== runtimeApi.FULL_OUTPUT_TOOL_NAME;
    let result;
    try {
      if (runsCode) toolActivity = runningActivity(requestPrompt(request));
      isThinking = false;
      renderMessages(container);
      result = await browserTools.answer(request);
    } finally {
      // Cleared however the answer ends, so the panel can never outlive it.
      toolActivity = null;
    }
    const message = messages[messageIndex];
    if (message && runsCode) {
      message.executions = (message.executions || []).concat(executionRecord(request, result));
    }
    renderMessages(container);
    return result;
  }

  // Record on the reply itself that it stopped, since a reply that only ran
  // code has no text to carry the error the banner shows for a few seconds.
  function noteStoppedReply(messageIndex, error) {
    const message = messages[messageIndex];
    if (!message) return;
    const reason = (error && error.message) || 'an error occurred';
    message.content = `${message.content ? `${message.content}\n\n` : ''}_[The reply stopped: ${reason}]_`;
  }

  // Answer tool_requests until the reply finishes. Each run of a reply that
  // runs code ends on one; its answer goes back on /chat/resume, whose stream
  // continues the same message. `steps` answers, resumes and streams, and is a
  // parameter so this loop, which owns the budget and the bookkeeping, can be
  // tested without a network or a runtime.
  async function continueBrowserReply(outcome, steps) {
    let runs = 0;
    let current = outcome;
    while (current && current.toolRequest) {
      const { toolRequest, messageIndex } = current;
      runs += 1;
      let resumed;
      try {
        if (runs > MAX_BROWSER_RUNS_PER_REPLY) {
          // Left unanswered on purpose: the next message abandons the parked
          // call, which the server repairs.
          throw new Error(`it ran code ${MAX_BROWSER_RUNS_PER_REPLY} times, the most one reply may`);
        }
        const result = await steps.answer(toolRequest, messageIndex);
        resumed = await steps.resume(toolRequest, result);
      } catch (error) {
        noteStoppedReply(messageIndex, error);
        throw error;
      }
      // Streaming reports its own failures into the message.
      current = await steps.stream(resumed, messageIndex);
    }
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
        // The worker says which limit in `details`, which is the useful half.
        const details = typeof error.details === 'string' ? `: ${error.details}` : '';
        errorMessage = `${error.error}${details}`.substring(0, 500);
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

  // Whether a 429 came from the worker's per-minute limit, the one worth
  // waiting out. Its hourly budgets are not: they last the hour.
  async function isPerMinuteLimit(response) {
    try {
      const body = await response.clone().json();
      return typeof body.details === 'string' && /per minute/i.test(body.details);
    } catch {
      return false;
    }
  }

  // Send a browser run's result and return the stream that continues the reply.
  async function postResume(request, result, waits = RESUME_RATE_LIMIT_WAITS_MS) {
    const body = {
      session_id: request.session_id || sessionId,
      result,
      client_tools: browserTools ? browserTools.declared : [],
    };
    const pageContext = getPageContext();
    if (pageContext) body.page_context = pageContext;
    if (userSettings.model) body.model = userSettings.model;
    for (let attempt = 0; ; attempt += 1) {
      const response = await fetch(`${CONFIG.apiEndpoint}/${CONFIG.communityId}/chat/resume`, {
        method: 'POST',
        headers: chatRequestHeaders(),
        body: JSON.stringify(body),
        signal: AbortSignal.timeout(120000),
      });
      if (response.status === 429 && attempt < waits.length && await isPerMinuteLimit(response)) {
        const container = document.querySelector('.osa-chat-widget');
        if (container) {
          showWarning(container, 'Many code runs in a short time: waiting briefly before continuing the reply.');
        }
        await new Promise((resolve) => setTimeout(resolve, waits[attempt]));
        continue;
      }
      if (!response.ok) throw await responseError(response);
      const contentType = response.headers.get('content-type') || '';
      if (!contentType.includes('text/event-stream')) {
        throw new Error('Invalid response from server');
      }
      return response;
    }
  }

  // Model-written code as HTML: highlighted once the runtime has loaded,
  // escaped plainly before that. Either way every character is escaped.
  function highlightOrEscape(code) {
    return runtimeApi ? runtimeApi.highlightPython(code) : escapeHtml(code);
  }

  // The panel shown while a tool_request is answered, as HTML.
  function toolPanelHtml(activity) {
    if (!activity) return '';
    const prompt = activity.prompt || {};
    const description = prompt.description
      ? `<div class="osa-tool-panel-description">${escapeHtml(prompt.description)}</div>`
      : '';
    if (activity.phase === 'asking') {
      return `
        <div class="osa-tool-panel" role="group" aria-label="Run code in your browser?">
          <div class="osa-tool-panel-title">Run this Python in your browser?</div>
          ${description}
          <pre class="osa-tool-code"><code>${highlightOrEscape(prompt.code || '')}</code></pre>
          <div class="osa-tool-actions">
            <button type="button" class="osa-tool-run">Run</button>
            <button type="button" class="osa-tool-deny">Don't run</button>
            <label class="osa-tool-autorun"><input type="checkbox" class="osa-tool-autorun-input"> Run without asking until I reload</label>
          </div>
        </div>`;
    }
    const progress = activity.progress;
    const status = progress ? progress.text : 'Running Python in your browser...';
    // A determinate bar only once the boot reports a step within a known
    // total; before that (or once the boot is done and code is actually
    // running) the label stands alone rather than showing a bar frozen at
    // whatever step last reported.
    const hasBar = Boolean(progress) && progress.step !== null && progress.steps !== null;
    const percent = hasBar ? Math.round((progress.step / progress.steps) * 100) : 0;
    const bar = hasBar
      ? `<div class="osa-tool-progress" role="progressbar" aria-valuemin="0" aria-valuemax="${progress.steps}" ` +
        `aria-valuenow="${progress.step}" aria-label="${escapeHtml(status)}">` +
        `<div class="osa-tool-progress-fill" style="width: ${percent}%"></div></div>`
      : '';
    return `
      <div class="osa-tool-panel" role="status">
        <div class="osa-tool-panel-title">Running Python</div>
        ${description}
        <div class="osa-tool-actions">
          <div class="osa-tool-progress-row">
            <span class="osa-tool-status">${escapeHtml(status)}</span>
            ${bar}
          </div>
          <button type="button" class="osa-tool-stop">Stop</button>
        </div>
      </div>`;
  }

  // Only a PNG whose data is plain base64 reaches an <img src>: the data is
  // interpolated into an attribute, so anything else could close it.
  function isShowableImage(image) {
    return !!image && image.mime === 'image/png' && typeof image.data_base64 === 'string'
      && /^[A-Za-z0-9+/]+=*$/.test(image.data_base64);
  }

  // Whether this page can run code the reader wrote themselves: the runtime
  // exists only once a community declares client tools AND the bundle has
  // loaded (startBrowserTools), which is also everything runLocal() itself
  // needs. A page with neither shows no "Edit and run" affordance at all,
  // rather than one that fails the moment it is clicked.
  function canRunLocalCode() {
    return !!browserTools;
  }

  // Why Run is disabled right now, or null when it is free. Read fresh at
  // render time: booting and busy are the ONE shared runtime's state, never
  // this run record's own, so two edited-and-open records always agree.
  function localRunBlockedReason() {
    if (!browserTools) return 'Python is not available on this page.';
    if (browserRuntime && runtimeApi && browserRuntime.state === runtimeApi.RUNTIME_STATE.BOOTING) {
      return 'Python is starting in your browser. Try again in a moment.';
    }
    if (browserTools.busy) return 'Busy: the runtime is already running code.';
    return null;
  }

  // stdout, stderr and figures, bounded and escaped exactly the way a
  // recorded run's own body already is. Shared by the normal per-run block
  // and by the live echo runEditedCode() shows under an open editor, so the
  // two can never disagree about what "bounded on screen" means.
  function runOutputHtml(run) {
    const stdout = run.stdout ? `<pre class="osa-execution-output">${escapeHtml(run.stdout)}</pre>` : '';
    const stderr = run.stderr && run.status !== 'ok'
      ? `<pre class="osa-execution-output">${escapeHtml(run.stderr)}</pre>`
      : '';
    // Shown regardless of status, unlike stderr above: a save failure matters
    // most on a run whose Python succeeded (status ok), and for the reader's
    // own runs this is the ONLY place it is ever visible to anyone, since
    // that call never reaches the server for the model to mention it from.
    const workspaceNote = run.workspaceNote
      ? `<div class="osa-execution-workspace-note">${escapeHtml(run.workspaceNote)}</div>`
      : '';
    const images = (Array.isArray(run.images) ? run.images : [])
      .filter(isShowableImage)
      .map((image) => `<img alt="Figure produced by the code" src="data:image/png;base64,${image.data_base64}">`)
      .join('');
    return `${stdout}${stderr}${workspaceNote}${images}`;
  }

  // The inline "Edit and run" editor for one run record: a labeled textarea
  // prefilled with the code, Run/Cancel, and -- while the reader's own
  // attempt is in flight or just finished -- its status and output, right
  // there under the editor. _draft/_runningLocal/_localResult are transient,
  // in-memory-only fields on the run record (never saved, see saveHistory);
  // reading and writing them here is what lets this survive a re-render
  // triggered by something else, such as runtime progress, without losing
  // what the reader was typing.
  function rerunEditorHtml(run, msgIndex, runIndex) {
    const draft = typeof run._draft === 'string' ? run._draft : (run.code || '');
    const running = run._runningLocal === true;
    const blocked = running ? null : localRunBlockedReason();
    const busyNote = blocked
      ? `<div class="osa-rerun-note">${escapeHtml(blocked)}</div>`
      : '';
    const controls = running
      ? `<div class="osa-rerun-buttons">
          <span class="osa-rerun-note">Running your code...</span>
          <button type="button" class="osa-rerun-stop" data-msg-index="${msgIndex}" data-run-index="${runIndex}">Stop</button>
        </div>`
      : `<div class="osa-rerun-buttons">
          <button type="button" class="osa-rerun-run" data-msg-index="${msgIndex}" data-run-index="${runIndex}"${blocked ? ' disabled' : ''}>Run</button>
          <button type="button" class="osa-rerun-cancel" data-msg-index="${msgIndex}" data-run-index="${runIndex}">Cancel</button>
        </div>`;
    let result = '';
    if (run._localResult) {
      const label = Object.prototype.hasOwnProperty.call(EXECUTION_LABELS, run._localResult.status)
        ? EXECUTION_LABELS[run._localResult.status]
        : 'Python';
      result = `
        <div class="osa-execution-local-note">This run is yours. The assistant has not seen it.</div>
        <div class="osa-rerun-result-status">${escapeHtml(label)}</div>
        ${runOutputHtml(run._localResult)}`;
    }
    return `
      <div class="osa-rerun">
        <label class="osa-rerun-label">Edit the code and run it yourself
          <textarea class="osa-rerun-textarea" data-msg-index="${msgIndex}" data-run-index="${runIndex}"
            maxlength="${EXECUTION_FIELD_LIMITS.code}"${running ? ' readonly' : ''}>${escapeHtml(draft)}</textarea>
        </label>
        ${busyNote}
        ${controls}
        ${result}
      </div>`;
  }

  // What ran for a reply, as HTML: one collapsible entry per run. Every field
  // is escaped: the description is the model's, the output is whatever the
  // code printed, and a stored record is whatever storage holds.
  //
  // msgIndex names the reply these runs belong to, so the edit/run/cancel
  // controls below know which record to act on; omitted, no "Edit and run"
  // controls render at all (canRunLocalCode() still gates them first).
  //
  // A run whose OWN editor is open and has a live result (run._localResult)
  // is shown inline under that editor rather than as its own separate
  // block, even though it is already a real entry in `executions` (kept
  // there so it survives a reload once the editor closes): the two would
  // otherwise show the exact same run twice in one render.
  function executionsHtml(executions, msgIndex) {
    if (!Array.isArray(executions) || executions.length === 0) return '';
    const mirrored = new Set(
      executions
        .filter((run) => run && run._editing === true && run._localResult && typeof run._localResult.callId === 'string')
        .map((run) => run._localResult.callId)
    );
    const canEdit = canRunLocalCode();
    return executions.map((run, runIndex) => {
      if (mirrored.has(run.callId)) return '';
      const isLocal = run.local === true;
      const label = Object.prototype.hasOwnProperty.call(EXECUTION_LABELS, run.status)
        ? EXECUTION_LABELS[run.status]
        : 'Python';
      const title = `${isLocal ? 'Your run: ' : ''}${label}${run.description ? `: ${run.description}` : ''}`;
      const code = run.code
        ? `<pre class="osa-tool-code"><code>${highlightOrEscape(run.code)}</code></pre>`
        : '';
      const localNote = isLocal
        ? '<div class="osa-execution-local-note">This run is yours. The assistant has not seen it.</div>'
        : '';
      const output = runOutputHtml(run);
      const hasImages = Array.isArray(run.images) && run.images.some(isShowableImage);
      const editing = run._editing === true;
      let rerun = '';
      if (canEdit && typeof msgIndex === 'number') {
        rerun = editing
          ? rerunEditorHtml(run, msgIndex, runIndex)
          : `<div class="osa-rerun-actions">
              <button type="button" class="osa-rerun-open" data-msg-index="${msgIndex}" data-run-index="${runIndex}">Edit and run</button>
            </div>`;
      }
      // Figures, a local run, or an open editor all open by default: each is
      // the point of looking at that particular run.
      return `
        <details class="osa-execution"${(isLocal || editing || hasImages) ? ' open' : ''}>
          <summary>${escapeHtml(title)}</summary>
          ${localNote}${code}${output}${rerun}
        </details>`;
    }).join('');
  }

  // A malformed value is named here rather than dropped in silence: an embedder's
  // typo through setConfig() (or a hand-edited widget: block) otherwise looks
  // exactly like "not set", with no way to tell the two apart short of reading
  // this source.
  // The color schemes a widget can have (#469); see CONFIG.colorScheme. A host page
  // may choose any of the three; a community config only 'light' or 'auto', because a
  // widget forced dark on a light page is the host page's call, not the community's
  // (WidgetConfig.color_scheme refuses 'dark' on the server as well).
  const COLOR_SCHEMES = ['light', 'dark', 'auto'];
  const COMMUNITY_COLOR_SCHEMES = ['light', 'auto'];

  function isValidColorScheme(value) {
    return COLOR_SCHEMES.includes(value);
  }

  function warnInvalidColorScheme(what, value, allowed) {
    const expected = allowed.map(v => `'${v}'`).join(', ');
    console.warn(`[OSA] Ignoring invalid ${what} ${JSON.stringify(value)}; expected one of ${expected}`);
  }

  // The dark surfaces the accent is read on: the panel itself (borders, focus rings,
  // the footer checkbox) and the assistant's bubble, where links sit, which is the
  // lighter of the two and so the one that decides. The same values as --osa-bg and
  // --osa-assistant-bg under .osa-dark, which a test holds them to.
  const DARK_PANEL_BG = '#111827';
  const DARK_BUBBLE_BG = '#1f2937';
  // The stylesheet's own --osa-primary, for a community with no theme_color (also
  // held to the stylesheet by a test).
  const DEFAULT_PRIMARY = '#2563eb';
  // WCAG AA for body text: the accent colors links and the local-run note.
  const MIN_TEXT_CONTRAST = 4.5;
  const HEX_COLOR = /^#[0-9a-fA-F]{6}$/;

  // WCAG relative luminance of a #RRGGBB color.
  function relativeLuminance(hex) {
    const channel = (i) => {
      const c = parseInt(hex.slice(i, i + 2), 16) / 255;
      return c <= 0.03928 ? c / 12.92 : ((c + 0.055) / 1.055) ** 2.4;
    };
    return 0.2126 * channel(1) + 0.7152 * channel(3) + 0.0722 * channel(5);
  }

  function contrastRatio(a, b) {
    const [hi, lo] = [relativeLuminance(a), relativeLuminance(b)].sort((x, y) => y - x);
    return (hi + 0.05) / (lo + 0.05);
  }

  function readsOnDark(color) {
    return Math.min(contrastRatio(color, DARK_PANEL_BG), contrastRatio(color, DARK_BUBBLE_BG)) >= MIN_TEXT_CONTRAST;
  }

  // The accent the dark panel uses, from the community's theme color: null when that
  // color already reads on both dark surfaces (the stylesheet's var(--osa-primary)
  // stands, so NEMAR's teal is used exactly), otherwise the color mixed with white in
  // 10% steps until it does. A theme color that is not #RRGGBB is one
  // applyWidgetConfig never applies (and warns about), so the stylesheet's default is
  // what is on screen, and so what is measured.
  function darkAccentFor(themeColor) {
    const primary = HEX_COLOR.test(themeColor || '') ? themeColor : DEFAULT_PRIMARY;
    if (readsOnDark(primary)) return null;
    const rgb = [1, 3, 5].map(i => parseInt(primary.slice(i, i + 2), 16));
    // Step 10 is white, which reads on both surfaces, so the loop always returns.
    for (let step = 1; step <= 10; step++) {
      const mixed = '#' + rgb
        .map(c => Math.round(c + (255 - c) * step / 10).toString(16).padStart(2, '0'))
        .join('');
      if (readsOnDark(mixed)) return mixed;
    }
  }

  // Follows the device's setting while colorScheme is 'auto'. Created the first time
  // a widget is 'auto', so a 'light' widget never registers a listener at all.
  let darkSchemeQuery = null;
  let deviceSchemeWarned = false;

  // Whether the widget draws its dark appearance right now.
  function isDarkScheme() {
    if (CONFIG.colorScheme === 'dark') return true;
    return CONFIG.colorScheme === 'auto' && !!darkSchemeQuery && darkSchemeQuery.matches;
  }

  // Start following the device's setting (see darkSchemeQuery). A browser with no
  // matchMedia leaves 'auto' light, and one whose query cannot report a change keeps
  // the setting the page opened with; the console says so, once. Safari before 14
  // has only the older addListener, which is used when addEventListener is missing.
  function watchDeviceScheme() {
    if (typeof window.matchMedia !== 'function') {
      if (!deviceSchemeWarned) {
        deviceSchemeWarned = true;
        console.warn("[OSA] This browser has no matchMedia, so color_scheme 'auto' cannot follow the device and stays light.");
      }
      return;
    }
    darkSchemeQuery = window.matchMedia('(prefers-color-scheme: dark)');
    const follow = () => {
      const mounted = document.querySelector('.osa-chat-widget');
      if (mounted) applyColorScheme(mounted);
    };
    if (typeof darkSchemeQuery.addEventListener === 'function') {
      darkSchemeQuery.addEventListener('change', follow);
    } else if (typeof darkSchemeQuery.addListener === 'function') {
      darkSchemeQuery.addListener(follow);
    } else {
      console.warn("[OSA] This browser cannot report a change of the device's color scheme; 'auto' keeps the setting the page opened with.");
    }
  }

  // Put .osa-dark on the container, or take it off, to match CONFIG.colorScheme, and
  // set the dark panel's accent from the current theme color: only when that color
  // is too dark to read there, and cleared otherwise so a changed theme color does
  // not keep a stale one. A 'light' widget gets neither, so its markup is exactly
  // what it was before dark mode existed. Runs from createWidget, from
  // applyWidgetConfig, from setColorScheme and setConfig (through
  // applyColorSchemeEverywhere), and from the device-change listener in
  // watchDeviceScheme while colorScheme is 'auto'.
  function applyColorScheme(container) {
    const darkAccent = CONFIG.colorScheme === 'light' ? null : darkAccentFor(CONFIG.themeColor);
    if (darkAccent) {
      container.style.setProperty('--osa-accent-on-dark', darkAccent);
    } else if (container.style.getPropertyValue('--osa-accent-on-dark')) {
      container.style.removeProperty('--osa-accent-on-dark');
    }

    if (CONFIG.colorScheme === 'auto' && !darkSchemeQuery) watchDeviceScheme();
    container.classList.toggle('osa-dark', isDarkScheme());
    // The notebook tab follows the widget's scheme (#470).
    postNotebookTheme(false);
  }

  // After the host page changes the scheme: this page's widget, if mounted, and an
  // open pop-out, which is its own copy of the widget and has no host page of its
  // own to tell it. The pop-out shares this page's origin (it is opened from here),
  // so its API is reachable; a pop-out still loading has no API yet and reads the
  // scheme from its preset instead (see openPopout), which is kept current here for
  // that reason. Reaching the pop-out and the pop-out applying the scheme fail for
  // different reasons, so they are reported apart: the second is a bug in the
  // pop-out's own copy of this code.
  function applyColorSchemeEverywhere() {
    const container = document.querySelector('.osa-chat-widget');
    if (container) applyColorScheme(container);
    if (!chatPopup || chatPopup.closed) return;
    let popupWidget = null;
    try {
      chatPopup.__OSA_HOST_COLOR_SCHEME__ = CONFIG.colorScheme;
      popupWidget = chatPopup.OSAChatWidget;
    } catch (e) {
      console.warn('[OSA] Could not reach the pop-out to pass the color scheme on:', e);
      return;
    }
    if (!popupWidget || typeof popupWidget.setColorScheme !== 'function') return;
    try {
      popupWidget.setColorScheme(CONFIG.colorScheme);
    } catch (e) {
      console.error('[OSA] The pop-out failed to apply the color scheme:', e);
    }
  }

  // Once per field and value: a remembered config is applied before the widget is
  // drawn and again when the fresh one arrives, and one bad color needs one warning.
  const warnedInvalidColors = new Set();
  function warnInvalidColor(field, value) {
    const key = `${field}:${JSON.stringify(value)}`;
    if (warnedInvalidColors.has(key)) return;
    warnedInvalidColors.add(key);
    console.warn(`[OSA] Ignoring invalid ${field} (not a recognized color): ${JSON.stringify(value)}`);
  }

  // Update DOM elements to reflect current CONFIG values (called after API config load)
  function applyWidgetConfig() {
    const container = document.querySelector('.osa-chat-widget');
    if (!container) return;

    // Convert to the capsule launcher if launcher: capsule just arrived from the
    // community config (the ordinary case: this resolves after createWidget()
    // already built the bubble). A no-op once already converted, or in bubble mode.
    // The other way only after a remembered capsule (#475): the fresh config says
    // bubble.
    if (CONFIG.launcher !== 'capsule') revertLauncherMode(container);
    applyLauncherMode(container);
    renderLauncherIcons(container);

    applyThemeProperties(container);

    applyColorScheme(container);

    // Update header title
    const titleEl = container.querySelector('.osa-chat-title');
    if (titleEl) {
      const badge = titleEl.querySelector('.osa-experimental-badge');
      titleEl.textContent = CONFIG.title;
      if (badge) titleEl.appendChild(badge);
    }

    // Who feedback goes to: drawn before the config arrives, so kept current here, or
    // a first visit tells the reader it goes to the default community's maintainers.
    const feedbackCommunity = container.querySelector('.osa-feedback-community');
    if (feedbackCommunity) feedbackCommunity.textContent = CONFIG.title.replace(' Assistant', '');

    // Update tooltip: launcher_label if set, else the hardcoded default (#436).
    const tooltip = container.querySelector('.osa-chat-tooltip');
    if (tooltip) {
      tooltip.textContent = CONFIG.launcherLabel || ('Ask me about ' + CONFIG.title.replace(' Assistant', ''));
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
    const shownLogo = avatar && avatar.querySelector('img');
    if (avatar && !CONFIG.logo && shownLogo && defaultAvatarHtml !== null) {
      avatar.innerHTML = defaultAvatarHtml;
    }
    if (avatar && CONFIG.logo && !(shownLogo && shownLogo.getAttribute('src') === CONFIG.logo)) {
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

  // The community's colors as custom properties on the widget. Also run by
  // createWidget before the widget joins the page, so a remembered look (#475) is
  // there from the first frame, with nothing to fade from.
  function applyThemeProperties(container) {
    // Apply theme color if configured (must be valid #RRGGBB hex)
    if (CONFIG.themeColor) {
      if (/^#[0-9a-fA-F]{6}$/.test(CONFIG.themeColor)) {
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
      } else {
        warnInvalidColor('themeColor', CONFIG.themeColor);
      }
    } else {
      // Unset (the default, or a remembered color the fresh config dropped): the
      // stylesheet's own value.
      container.style.removeProperty('--osa-primary');
      container.style.removeProperty('--osa-primary-dark');
    }

    // The reader's bubbles have their own color, so a theme_color alone leaves
    // them the platform blue every community has had (must be valid #RRGGBB hex).
    if (CONFIG.userBubbleColor) {
      if (/^#[0-9a-fA-F]{6}$/.test(CONFIG.userBubbleColor)) {
        container.style.setProperty('--osa-user-bg', CONFIG.userBubbleColor);
      } else {
        warnInvalidColor('userBubbleColor', CONFIG.userBubbleColor);
      }
    } else {
      container.style.removeProperty('--osa-user-bg');
    }

    // Text/icons drawn ON a theme_color surface (header, launcher, Run, Send, Save).
    // Left at the stylesheet's own white default when unset, so a community that never
    // names this sees no change.
    if (CONFIG.themeTextColor) {
      if (/^#[0-9a-fA-F]{6}$/.test(CONFIG.themeTextColor)) {
        container.style.setProperty('--osa-on-primary', CONFIG.themeTextColor);
      } else {
        warnInvalidColor('themeTextColor', CONFIG.themeTextColor);
      }
    } else {
      container.style.removeProperty('--osa-on-primary');
    }

    // theme_color used as a FOREGROUND on the white panel (links, borders, focus rings,
    // native checkbox accent-color). Left at the stylesheet's own `var(--osa-primary)`
    // default when unset, so it tracks theme_color exactly as it always has. Set as
    // --osa-accent-on-light so the dark panel can replace it (see the stylesheet).
    if (CONFIG.accentColor) {
      if (/^#[0-9a-fA-F]{6}$/.test(CONFIG.accentColor)) {
        container.style.setProperty('--osa-accent-on-light', CONFIG.accentColor);
      } else {
        warnInvalidColor('accentColor', CONFIG.accentColor);
      }
    } else {
      container.style.removeProperty('--osa-accent-on-light');
    }

    // Text in the reader's own bubbles, painted on user_bubble_color (or the platform
    // blue, if that is unset too). Left at the stylesheet's own white default otherwise.
    if (CONFIG.userBubbleTextColor) {
      if (/^#[0-9a-fA-F]{6}$/.test(CONFIG.userBubbleTextColor)) {
        container.style.setProperty('--osa-user-text', CONFIG.userBubbleTextColor);
      } else {
        warnInvalidColor('userBubbleTextColor', CONFIG.userBubbleTextColor);
      }
    } else {
      container.style.removeProperty('--osa-user-text');
    }

    // Apply disclaimer colors if configured (must be valid CSS color: hex, named, rgb, hsl)
    const cssColorPattern = /^(#[0-9a-fA-F]{3,8}|[a-zA-Z]+|rgba?\([^)]+\)|hsla?\([^)]+\))$/;
    if (CONFIG.disclaimerColor) {
      if (cssColorPattern.test(CONFIG.disclaimerColor.trim())) {
        container.style.setProperty('--osa-disclaimer-color', CONFIG.disclaimerColor.trim());
      } else {
        warnInvalidColor('disclaimerColor', CONFIG.disclaimerColor);
      }
    }
    if (CONFIG.disclaimerBackground) {
      if (cssColorPattern.test(CONFIG.disclaimerBackground.trim())) {
        container.style.setProperty('--osa-disclaimer-bg', CONFIG.disclaimerBackground.trim());
      } else {
        warnInvalidColor('disclaimerBackground', CONFIG.disclaimerBackground);
      }
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

    refreshWorkspacePanel(container).catch((err) => console.error('[OSA] Workspace panel refresh failed:', err));
  }

  // Close settings modal
  function closeSettings(container) {
    const overlay = container.querySelector('.osa-settings-overlay');
    if (overlay) {
      overlay.classList.remove('open');
    }
    workspaceDeleteConfirming = false;
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
      // The notebook's frame would take the drag once the pointer crosses it.
      chatWindow.classList.add('osa-resizing');
      e.preventDefault();
    });

    document.addEventListener('mousemove', (e) => {
      if (!isResizing) return;

      // Resize from top-left corner (since window is anchored bottom-right)
      const newWidth = startWidth - (e.clientX - startX);
      const newHeight = startHeight - (e.clientY - startY);

      // Set minimum and maximum sizes. The capsule holds a notebook too (#470), so
      // it may grow to most of the viewport; a bubble keeps its limits.
      const capsule = !!chatWindow.closest('.osa-capsule');
      const maxWidth = capsule ? Math.max(600, Math.min(1400, window.innerWidth - 120)) : 600;
      const maxHeight = capsule ? Math.max(800, window.innerHeight - 40) : 800;
      if (newWidth >= 300 && newWidth <= maxWidth) {
        chatWindow.style.width = newWidth + 'px';
      }
      if (newHeight >= 350 && newHeight <= maxHeight) {
        chatWindow.style.height = newHeight + 'px';
      }
    });

    document.addEventListener('mouseup', () => {
      isResizing = false;
      chatWindow.classList.remove('osa-resizing');
    });
  }

  // Create the widget DOM
  function createWidget() {
    const container = document.createElement('div');
    container.className = 'osa-chat-widget' + (CONFIG.fullscreen ? ' fullscreen' : '') +
      (launcherWaiting ? ' osa-launcher-waiting' : '');

    const experimentalBadge = CONFIG.showExperimentalBadge
      ? '<span class="osa-experimental-badge">Experimental</span>'
      : '';

    container.innerHTML = `
      <button class="osa-chat-button" aria-label="Open chat">
        ${ICONS.chat}
      </button>
      <div class="osa-chat-tooltip">${escapeHtml(CONFIG.launcherLabel || ('Ask me about ' + CONFIG.title.replace(' Assistant', '')))}</div>
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
                Model name, requires your own <a href="https://openrouter.ai/models" target="_blank" rel="noopener noreferrer" style="color: var(--osa-accent); text-decoration: underline;">OpenRouter</a> key
              </label>
              <input
                type="text"
                id="osa-settings-custom-model"
                class="osa-settings-input"
                placeholder="provider/model-name"
                autocomplete="off"
              />
            </div>
            <div class="osa-settings-field osa-workspace-field" style="display: none;">
              <label class="osa-settings-label">Workspace</label>
              <span class="osa-settings-hint osa-workspace-usage">Checking workspace size...</span>
              <div class="osa-workspace-actions">
                <button type="button" class="osa-settings-btn osa-workspace-download-btn">
                  Download workspace (.zip)
                </button>
                <button type="button" class="osa-settings-btn osa-workspace-delete-btn">
                  Delete workspace
                </button>
              </div>
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
                Shared with the <span class="osa-feedback-community">${escapeHtml(CONFIG.title.replace(' Assistant', ''))}</span> community maintainers. Please do not include personal information.
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
    defaultAvatarHtml = container.querySelector('.osa-chat-avatar')?.innerHTML ?? null;
    // Before the widget joins the page, so its first style is already the
    // community's (a remembered look, #475, or an embedder's setConfig colors).
    applyThemeProperties(container);
    document.body.appendChild(container);

    // Setup resize
    const chatWindow = container.querySelector('.osa-chat-window');
    setupResize(chatWindow);

    // Convert to the capsule launcher if CONFIG.launcher is already 'capsule' at
    // creation time (an embedder that set it via setConfig before init()). The
    // ordinary case, launcher arriving later from the community config, is applied
    // again from applyWidgetConfig().
    applyLauncherMode(container);

    // A host page that chose its scheme before init() gets it with no flash of the
    // light panel; a community's 'auto' is applied once its config arrives.
    applyColorScheme(container);

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
      // A reply that has run code is shown even before it has text: the record
      // of what ran is already part of it.
      const ranCode = Array.isArray(msg.executions) && msg.executions.length > 0;
      if (isLoading && msg.role === 'assistant' && !msg.content && !ranCode && msgIndex === messages.length - 1) {
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
        ${msg.role === 'assistant' ? executionsHtml(msg.executions, msgIndex) : ''}
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

    // "Edit and run": open the inline editor for a specific run.
    messagesEl.querySelectorAll('.osa-rerun-open[data-msg-index]').forEach((btn) => {
      btn.addEventListener('click', (e) => {
        e.stopPropagation();
        const run = runAt(btn);
        if (!run) return;
        run._editing = true;
        run._draft = run.code || '';
        renderMessages(container);
      });
    });

    // Discard the edit and go back to showing the record as it was.
    messagesEl.querySelectorAll('.osa-rerun-cancel[data-msg-index]').forEach((btn) => {
      btn.addEventListener('click', (e) => {
        e.stopPropagation();
        const run = runAt(btn);
        if (!run) return;
        delete run._editing;
        delete run._draft;
        delete run._localResult;
        renderMessages(container);
      });
    });

    // Remember what the reader is typing across a re-render triggered by
    // something unrelated (runtime progress, a new message), the same way
    // the feedback comment box's draft already does.
    messagesEl.querySelectorAll('.osa-rerun-textarea[data-msg-index]').forEach((textarea) => {
      const run = runAt(textarea);
      if (!run) return;
      textarea.addEventListener('input', () => {
        run._draft = textarea.value;
      });
    });

    messagesEl.querySelectorAll('.osa-rerun-run[data-msg-index]').forEach((btn) => {
      btn.addEventListener('click', (e) => {
        e.stopPropagation();
        const msgIndex = parseInt(btn.getAttribute('data-msg-index'), 10);
        const runIndex = parseInt(btn.getAttribute('data-run-index'), 10);
        runEditedCode(container, msgIndex, runIndex);
      });
    });

    messagesEl.querySelectorAll('.osa-rerun-stop[data-msg-index]').forEach((btn) => {
      btn.addEventListener('click', (e) => {
        e.stopPropagation();
        e.currentTarget.disabled = true;
        // cancelLocal(), never cancel(): this button is the reader's own run,
        // which can be outstanding at the same time as an assistant call
        // queued behind it, and each Stop must reach only its own run.
        if (browserTools) browserTools.cancelLocal();
      });
    });

    if (toolActivity) {
      // In place of the loading dots: nothing is loading, the reply is waiting
      // on the person or on code running in this page.
      const panelEl = document.createElement('div');
      panelEl.innerHTML = toolPanelHtml(toolActivity);
      messagesEl.appendChild(panelEl);
      const decide = (decision) => {
        const activity = toolActivity;
        if (!activity || activity.phase !== 'asking') return;
        const always = panelEl.querySelector('.osa-tool-autorun-input');
        activity.decide(decision, !!(always && always.checked));
      };
      panelEl.querySelector('.osa-tool-run')?.addEventListener('click', () => decide(runtimeApi.GATE_DECISION.RUN));
      panelEl.querySelector('.osa-tool-deny')?.addEventListener('click', () => decide(runtimeApi.GATE_DECISION.DENY));
      panelEl.querySelector('.osa-tool-stop')?.addEventListener('click', (e) => {
        e.currentTarget.disabled = true;
        // cancel(), never cancelLocal(): this button is the assistant's own
        // call, which can be queued behind a reader's run still in progress,
        // and each Stop must reach only its own run.
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

  // The dataset-page suggestions (#477) for the dataset on screen, in the community's
  // order: each template with its blanks filled from setDataset's facts, skipping one
  // with a blank the page did not fill, and one marked needs_zarr unless the dataset
  // has a Zarr copy.
  const DATASET_BLANK_RE = /\{([^{}]*)\}/g;
  function datasetQuestions(dataset) {
    if (!dataset || !Array.isArray(CONFIG.datasetSuggestedQuestions)) return [];
    const facts = new Map([
      ['dataset_id', dataset.id],
      ['subject', dataset.subject],
      ['task', dataset.task],
    ]);
    const questions = [];
    for (const template of CONFIG.datasetSuggestedQuestions) {
      if (!template || typeof template.text !== 'string') continue;
      if (template.needs_zarr === true && dataset.zarr !== true) continue;
      let filled = true;
      const text = template.text.replace(DATASET_BLANK_RE, (blank, name) => {
        const value = facts.get(name);
        if (typeof value !== 'string') {
          filled = false;
          return blank;
        }
        return value;
      });
      if (filled) questions.push(text);
    }
    return questions;
  }

  // Whether the reader has already sent a message from this dataset's page in this
  // conversation (sendMessage records it on the message), so the compact row is
  // offered once per dataset and not on every page load after.
  function datasetInConversation(id) {
    return messages.some((m) => m.role === 'user' && m.dataset === id);
  }

  // What the suggestions area shows, or null: on the opening screen, up to three
  // questions about the dataset on screen, or the general list when there is no
  // dataset or no template fits; mid-conversation, a compact row of up to two for a
  // dataset the conversation has not been on yet (#477).
  function suggestionsToShow() {
    if (isLoading) return null;
    const aboutDataset = datasetQuestions(currentDataset);
    if (messages.length <= 1) {
      const questions = aboutDataset.length ? aboutDataset.slice(0, 3) : CONFIG.suggestedQuestions;
      return questions.length ? { questions, label: 'Try asking:', compact: false } : null;
    }
    if (aboutDataset.length && !datasetInConversation(currentDataset.id)) {
      return { questions: aboutDataset.slice(0, 2), label: `About ${currentDataset.id}:`, compact: true };
    }
    return null;
  }

  // Render suggestions
  function renderSuggestions(container) {
    const suggestionsEl = container.querySelector('.osa-suggestions');
    const suggestionsListEl = container.querySelector('.osa-suggestions-list');
    const labelEl = container.querySelector('.osa-suggestions-label');
    const shown = suggestionsToShow();
    if (shown) {
      suggestionsListEl.innerHTML = shown.questions.map(q =>
        `<button class="osa-suggestion">${escapeHtml(q)}</button>`
      ).join('');
      if (labelEl) labelEl.textContent = shown.label;
      suggestionsEl.classList.toggle('osa-suggestions-compact', shown.compact);
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

  // One reply's text across its runs: what earlier runs wrote, then this one's.
  function composeReply(earlier, text) {
    return earlier && text ? `${earlier}\n\n${text}` : earlier || text;
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
    const compose = (text) => composeReply(earlier, text);

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

        const composed = compose(accumulatedContent);
        if (composed) {
          messages[messageIndex].content = composed +
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

    // Boot the browser runtime now if this community preloads on first message,
    // so the Python download overlaps this turn instead of waiting for a Run gate.
    // Fires once per session; a runtime that is not built yet catches up in
    // startBrowserTools() once it is (see firstMessageSent there).
    if (!firstMessageSent) {
      firstMessageSent = true;
      preloadRuntimeOnFirstMessage();
    }

    // Track message indices to avoid corruption on error
    const userMessageIndex = messages.length;
    // The dataset on screen when it was sent (#477): what datasetInConversation reads.
    const userMessage = { role: 'user', content: question };
    if (currentDataset) userMessage.dataset = currentDataset.id;
    messages.push(userMessage);
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

      if (!isValidCommunityId(CONFIG.communityId)) {
        throw new Error('Invalid community configuration. Please reload the page.');
      }

      // The client tools this page can run. Only a streaming reply can carry a
      // tool_request, so they are declared only for one.
      if (CONFIG.streamingEnabled) {
        const declared = await declaredClientTools();
        if (declared.length > 0) {
          body.client_tools = declared;
        }
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
        const outcome = await handleStreamingResponse(response, container);
        // A reply that runs code in this page is several runs. Each run is a
        // new request, so the 2 minute timeout above bounds one run, not the
        // whole reply.
        await continueBrowserReply(outcome, {
          answer: (request, index) => answerToolRequest(container, request, index),
          resume: postResume,
          stream: (resumed, index) => handleStreamingResponse(resumed, container, { messageIndex: index }),
        });
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
    // The capsule's chat circle keeps its icon when open, and its label follows
    // the open tab (renderLauncherIcons); a bubble swaps to a close icon (#470).
    const capsule = container.classList.contains('osa-capsule');

    if (isOpen) {
      chatWindow.classList.add('open');
      container.classList.add('chat-open');
      if (!capsule) {
        button.innerHTML = ICONS.close;
        button.setAttribute('aria-label', 'Close chat');
      }
      if (activeTab === 'chat') container.querySelector('.osa-chat-input input').focus();
      // Hide tooltip when chat opens
      if (tooltip) tooltip.classList.remove('visible');
      // Surface any notice queued by an init-time failure (see loadUserSettings/loadHistory).
      flushPendingNotice(container);
      retryBrowserToolsOnce();
      preloadRuntime();
    } else {
      // Commit any open thumbs-down comment box on close.
      flushPendingResponseFeedback(container);
      chatWindow.classList.remove('open');
      container.classList.remove('chat-open');
      if (!capsule) {
        button.innerHTML = ICONS.chat;
        button.setAttribute('aria-label', 'Open chat');
      }
    }
    if (capsule) {
      renderLauncherIcons(container);
      positionIndicator(container);
    }
  }

  // The script element the pop-out copies (#470): this script's own, or, for a copy
  // that has none (run inline by an embedder), the page's widget tag.
  function widgetScriptForPopout() {
    if (WIDGET_SCRIPT && WIDGET_SCRIPT.src) return WIDGET_SCRIPT;
    const scripts = document.querySelectorAll('script[src*="osa-chat-widget"]');
    if (scripts.length > 1) {
      console.warn(`OSA Chat Widget: Found ${scripts.length} matching script tags, using the last one.`);
    }
    return scripts.length ? scripts[scripts.length - 1] : null;
  }

  // A value, as the pop-out's own: serialized here and parsed by the pop-out's own
  // JSON, so the pop-out holds plain data of its own realm rather than this page's
  // objects, which it would otherwise share with a page that can change or go away.
  function toPopupRealm(popup, value) {
    return popup.JSON.parse(JSON.stringify(value));
  }

  // The pop-out's document: no script of its own, inline or otherwise. openPopout
  // adds the widget's script element to it, by address.
  function popoutDocumentHtml() {
    return `<!DOCTYPE html>
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
    .osa-popout-failure {
      margin: 0;
      padding: 24px;
      font: 14px/1.5 system-ui, -apple-system, sans-serif;
      color: #1f2937;
    }
  </style>
</head>
<body></body>
</html>`;
  }

  // The pop-out's script did not run: the browser refused it (the page's
  // script-src, or an integrity that does not match) or it never arrived. Said in
  // the pop-out itself, so it does not sit blank, and here for a developer.
  function showPopoutFailure(popup, scriptUrl) {
    console.error(`[OSA] The pop-out could not load the widget from ${scriptUrl}: ` +
      "this page's Content Security Policy (script-src) or the widget tag's integrity refused it, or it did not arrive.");
    try {
      const doc = popup.document;
      if (!doc.body || doc.querySelector('.osa-popout-failure')) return;
      const message = doc.createElement('p');
      message.className = 'osa-popout-failure';
      message.setAttribute('role', 'alert');
      message.textContent = 'The assistant could not load in this window. Close it and use the chat on the page instead.';
      doc.body.appendChild(message);
    } catch (e) {
      console.warn('[OSA] Could not show the pop-out\'s failure message:', e);
    }
  }

  // Open chat in a new popup window. The pop-out is an about:blank window of this
  // page's own origin, and so inherits this page's Content Security Policy: it
  // runs no inline script, which a host page without script-src 'unsafe-inline'
  // would refuse (#470). Its document is written without one, its presets are set
  // on its window from here, and the widget arrives as a script element with this
  // script's own address, integrity and crossorigin, which the host page's policy
  // already allows, since it loaded this script.
  function openPopout() {
    // Reuse existing popup if still open
    if (chatPopup && !chatPopup.closed) {
      chatPopup.focus();
      return;
    }

    const source = widgetScriptForPopout();
    if (!source) {
      console.warn('OSA Chat Widget: Could not find widget script tag for pop-out.');
      alert('Could not find widget script URL. Pop-out is not available.');
      return;
    }
    const scriptUrl = source.src;

    // The pop-out is fullscreen, and is told where the script lives, as a copy with
    // no address of its own would need to be.
    const popupConfig = { ...CONFIG, fullscreen: true, widgetScriptUrl: scriptUrl };
    // The pop-out fetches the community config again, which would replace a
    // scheme the host page chose with the community's own; this marks it as the
    // host's, so the pop-out keeps it (#469).
    const hostColorScheme = _userSetKeys.has('colorScheme') ? CONFIG.colorScheme : null;

    // Serialized before any window opens, so a config that cannot be is refused
    // without leaving an empty one behind.
    let configJson;
    let datasetJson;
    try {
      configJson = JSON.stringify(popupConfig);
      datasetJson = JSON.stringify(currentDataset);
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

    const popup = window.open('', '_blank', features);
    if (!popup) {
      alert('Please allow popups to open the chat in a new window.');
      return;
    }
    try {
      const doc = popup.document;
      doc.write(popoutDocumentHtml());
      doc.close();
      // The presets the pop-out's init() reads, set before its script can run:
      // the config, the host's color scheme, the dataset on screen (#477), and
      // the tab the reader was on (#470), which the pop-out opens on.
      popup.__OSA_CHAT_CONFIG__ = popup.JSON.parse(configJson);
      popup.__OSA_HOST_COLOR_SCHEME__ = hostColorScheme;
      popup.__OSA_DATASET__ = popup.JSON.parse(datasetJson);
      popup.__OSA_TAB__ = activeTab;
      const script = doc.createElement('script');
      // The host page's own pin, when it has one: SRI, and the CORS mode a
      // cross-origin script needs for the browser to check it.
      for (const name of ['integrity', 'crossorigin']) {
        if (source.hasAttribute(name)) script.setAttribute(name, source.getAttribute(name));
      }
      script.addEventListener('error', () => showPopoutFailure(popup, scriptUrl));
      script.src = scriptUrl;
      doc.body.appendChild(script);
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
    }
  }

  // Initialize widget
  function init() {
    // Check for pre-configured settings (used by pop-out windows)
    if (window.__OSA_CHAT_CONFIG__) {
      const preset = { ...window.__OSA_CHAT_CONFIG__ };
      // The notebook's address is framed and messaged (#470), so it is held to
      // setConfig's rule here too.
      if ('notebookUrl' in preset) {
        const normalized = normalizeNotebookUrl(preset.notebookUrl);
        if (normalized) {
          preset.notebookUrl = normalized;
        } else {
          console.warn('[OSA] Invalid notebookUrl, ignoring:', preset.notebookUrl);
          delete preset.notebookUrl;
        }
      }
      Object.assign(CONFIG, preset);
    }
    // A pop-out whose opener's host page chose the scheme (see openPopout).
    if (isValidColorScheme(window.__OSA_HOST_COLOR_SCHEME__)) {
      CONFIG.colorScheme = window.__OSA_HOST_COLOR_SCHEME__;
      _userSetKeys.add('colorScheme');
    }
    // A pop-out is told the dataset its opener had on screen (openPopout, #477);
    // null there means none. Validated exactly as a host page's setDataset is.
    if (window.__OSA_DATASET__ !== undefined) applySetDataset(window.__OSA_DATASET__);

    // The community's look from the last load, before anything is drawn (#475).
    // With none, on a first visit, the launcher waits for the config, briefly,
    // rather than showing the built-in defaults and then changing.
    const remembered = isValidCommunityId(CONFIG.communityId) ? readRememberedWidget() : null;
    if (remembered) {
      displayDefaults = Object.fromEntries(DISPLAY_KEYS.map((key) => [key, CONFIG[key]]));
      applyWidgetDisplay(remembered);
    }
    launcherWaiting = !remembered && !CONFIG.fullscreen && isValidCommunityId(CONFIG.communityId);
    // Scheduled before anything is built, so no exception on the way can leave the
    // launcher hidden for good.
    if (launcherWaiting) setTimeout(revealLauncher, LAUNCHER_WAIT_MS);

    loadPageContextPreference();
    loadUserSettings();
    const historyNeedsSave = loadHistory();
    injectStyles();
    const container = createWidget();
    // Everything else the remembered config sets (the logo, the title, the
    // greeting), still before the first frame.
    if (remembered) applyWidgetConfig();

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
    chatButton?.addEventListener('click', () => handleChatButtonClick(container));
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

    // Workspace panel (#433), inside Settings.
    const workspaceDownloadBtn = container.querySelector('.osa-workspace-download-btn');
    const workspaceDeleteBtn = container.querySelector('.osa-workspace-delete-btn');
    workspaceDownloadBtn?.addEventListener('click', () => downloadWorkspace(container));
    workspaceDeleteBtn?.addEventListener('click', () => handleWorkspaceDeleteClick(container));

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
        // 1.5 seconds after the launcher is on screen: at once, or once a first
        // visit's wait ends (#475), so the tooltip never shows before its button.
        whenLauncherShown(() => setTimeout(() => {
          tooltip.classList.add('visible');
          // Auto-hide tooltip after 8 seconds
          setTimeout(() => {
            tooltip.classList.remove('visible');
          }, 8000);
        }, 1500));
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
      // A pop-out opens on the tab its opener was on (#470). setTab is a no-op for
      // a bubble community's pop-out, and takes a notebook the dataset cannot open
      // back to chat.
      if (window.__OSA_TAB__ === 'notebook') showTabAtOnce(container, 'notebook');
      // Surface any notice queued by an init-time failure (see loadUserSettings/loadHistory).
      flushPendingNotice(container);
      setTimeout(() => {
        if (activeTab === 'chat') input?.focus();
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
      // The notebook site's base URL (#436): validated and normalized here so an
      // invalid value is dropped on its own, rather than aborting the whole call
      // the way an invalid communityId does.
      if ('notebookUrl' in opts) {
        const normalized = normalizeNotebookUrl(opts.notebookUrl);
        if (normalized) {
          opts.notebookUrl = normalized;
        } else {
          console.warn('[OSA] Invalid notebookUrl, ignoring:', opts.notebookUrl);
          delete opts.notebookUrl;
        }
      }
      if ('colorScheme' in opts && !isValidColorScheme(opts.colorScheme)) {
        warnInvalidColorScheme('colorScheme', opts.colorScheme, COLOR_SCHEMES);
        delete opts.colorScheme;
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
      if ('colorScheme' in opts) applyColorSchemeEverywhere();
    },
    // The reader's light or dark choice on the host page (#469): 'light', 'dark', or
    // 'auto' (follow the device). Outranks the community's color_scheme, may be
    // called before or after init(), and reaches an open pop-out too.
    setColorScheme: function(value) {
      if (!isValidColorScheme(value)) {
        warnInvalidColorScheme('colorScheme', value, COLOR_SCHEMES);
        return;
      }
      _userSetKeys.add('colorScheme');
      CONFIG.colorScheme = value;
      applyColorSchemeEverywhere();
    },
    getConfig: function() {
      return { ...CONFIG };
    },
    // The dataset on screen (#436): null (no dataset, or not known yet) or
    // {id, zarr, subject, task} (#477). May be called before init(); see applySetDataset.
    setDataset: function(value) {
      applySetDataset(value);
    },
    // Whether this page can run the assistant's code, and if not, why:
    // {state: 'off' | 'loading' | 'ready' | 'unavailable', reason, detail?, runtime}.
    // `state` is the bundle/controller WIRING, not Pyodide itself: `runtime` is
    // Pyodide's own boot state ('idle' | 'booting' | 'ready' | 'failed' |
    // 'terminated', from PyodideRuntime.state / RUNTIME_STATE in osa-runtime.js),
    // null until `state` is 'ready' (no PyodideRuntime instance exists before
    // then). A community configured with preload_on: 'first_message' or
    // 'widget_open' can show `runtime: 'booting'` well before any Run gate; a
    // 'first_run' community stays 'idle' until the model actually asks to run
    // code. For an embedder checking their page's policy, and for support.
    getBrowserRuntimeStatus: function() {
      return browserRuntimeStatus();
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
    window.OSAChatWidget.__firstPaint = {
      setWait(ms) {
        LAUNCHER_WAIT_MS = ms;
      },
      waiting: () => launcherWaiting,
    };
    window.OSAChatWidget.__applyDoneEvent = applyDoneEvent;
    window.OSAChatWidget.__migrateLegacyCitationMarkers = migrateLegacyCitationMarkers;
    window.OSAChatWidget.__isSameResponseMessage = isSameResponseMessage;
    window.OSAChatWidget.__notebook = {
      // The notebook tab's own state (#470), read-only, and its two timeouts.
      state: () => ({ ...notebookEmbed, frame: undefined, loadTimer: undefined, readyTimer: undefined }),
      setTimeouts: (afterLoadMs, totalMs) => {
        NOTEBOOK_AFTER_LOAD_MS = afterLoadMs;
        NOTEBOOK_TOTAL_MS = totalMs;
      },
    };
    window.OSAChatWidget.__browser = {
      MAX_BROWSER_RUNS_PER_REPLY,
      EXECUTION_FIELD_LIMITS,
      answerToolRequest,
      composeReply,
      continueBrowserReply,
      declaredClientToolsWithin,
      executionRecord,
      executionsHtml,
      handleStreamingResponse,
      loadRuntimeBundle,
      normalizePersistedExecutions,
      postResume,
      runtimeBundleUrl,
      toolPanelHtml,
      declaredClientTools,
      setUpBrowserTools,
      getBrowserTools: () => browserTools,
      getBrowserRuntime: () => browserRuntime,
      // A test builds its own ClientToolController/PyodideRuntime pair over
      // the real test worker (test-workers/executing.js, the same one
      // test-controller.js drives directly) and swaps it in here, so a
      // click on Run or Stop exercises the real controller without booting
      // real Pyodide. setUpBrowserTools()'s own pair (real Pyodide, never
      // booted by these tests) is still what sets runtimeApi.
      setBrowserTools: (tools) => { browserTools = tools; },
      setBrowserRuntime: (runtime) => { browserRuntime = runtime; },
      getConfig: () => CONFIG,
      getToolActivity: () => toolActivity,
      setToolActivity: (activity) => { toolActivity = activity; },
      onRuntimeProgress,
      onRuntimeStateChange,
      runningActivity,
      getMessages: () => messages,
      setMessages: (list) => { messages = list; },
      // The Settings workspace panel (#433): workspaceStore is normally
      // set only by setUpBrowserTools once a runtime bundle loads, which
      // needs IndexedDB behind it to mean anything; a test sets it directly
      // to exercise the panel's own logic against a REAL WorkspaceStore
      // (genuinely unavailable under happy-dom, exactly as it is genuinely
      // unavailable under Bun -- see test-controller.js's own comment on
      // this) without booting a runtime at all.
      setWorkspaceStore: (store) => { workspaceStore = store; },
      formatWorkspaceBytes,
      refreshWorkspacePanel,
      downloadWorkspace,
      handleWorkspaceDeleteClick,
      closeSettings,
      // The editable re-run panel.
      localExecutionRecord,
      runEditedCode,
      canRunLocalCode,
      localRunBlockedReason,
      renderMessages,
      saveHistory,
      loadHistory,
      getSessionId: () => sessionId,
      setSessionId: (value) => { sessionId = value; },
    };
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
