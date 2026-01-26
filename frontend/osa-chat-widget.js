/**
 * OSA Chat Widget
 * A floating chat assistant for Open Science tools (HED, BIDS, etc.)
 * Connects to OSA Cloudflare Worker for secure access.
 * Version: 2026-01-26-v2
 */

(function() {
  'use strict';

  // Auto-detect environment based on hostname
  // Production: Only osa-demo.pages.dev (exact match) routes to production API
  // Development: All other *.osa-demo.pages.dev subdomains (preview/branch deployments)
  //              route to dev API for testing without affecting production data
  const hostname = window.location.hostname;
  const isProduction = hostname === 'osa-demo.pages.dev';
  const isDev = !isProduction && (
                hostname.endsWith('.osa-demo.pages.dev') ||
                hostname.includes('localhost') ||
                hostname.includes('127.0.0.1'));

  // Configuration (can be customized via OSAChatWidget.setConfig)
  const CONFIG = {
    // Community identifier - determines which assistant to use
    // Endpoints will be: /${communityId}/ask, /${communityId}/chat
    communityId: 'hed',
    // Route to dev worker for all non-production deployments (preview branches, localhost)
    // or production worker for osa-demo.pages.dev (production only)
    apiEndpoint: isDev
      ? 'https://osa-worker-dev.shirazi-10f.workers.dev'
      : 'https://osa-worker.shirazi-10f.workers.dev',
    storageKey: 'osa-chat-history-hed',
    // Turnstile: disabled for now (not set up yet)
    turnstileSiteKey: null,
    // Customizable branding (defaults shown are for HED community)
    title: 'HED Assistant',
    initialMessage: 'Hi! I\'m the HED Assistant. I can help with HED (Hierarchical Event Descriptors), annotation, validation, and related tools. What would you like to know?',
    placeholder: 'Ask about HED...',
    suggestedQuestions: [
      'What is HED and how is it used?',
      'How do I annotate an event with HED tags?',
      'What tools are available for working with HED?',
      'Explain this HED validation error.'
    ],
    showExperimentalBadge: true,
    repoUrl: 'https://github.com/OpenScience-Collective/osa',
    repoName: 'Open Science Assistant',
    // Page context awareness - sends current page URL/title to help the assistant
    // provide more contextually relevant answers
    allowPageContext: true,  // Show the checkbox option
    pageContextDefaultEnabled: true,  // Default state of checkbox
    pageContextStorageKey: 'osa-page-context-enabled',
    pageContextLabel: 'Share page URL to help answer questions',
    // Fullscreen mode (for pop-out windows)
    fullscreen: false
  };

  // Log environment for debugging
  if (isDev) {
    console.log('[OSA] Using DEV backend:', CONFIG.apiEndpoint);
  }

  // Default model options for settings dropdown
  const DEFAULT_MODELS = [
    { value: 'openai/gpt-5.2-chat', label: 'GPT-5.2 Chat' },
    { value: 'openai/gpt-5-mini', label: 'GPT-5 Mini' },
    { value: 'anthropic/claude-haiku-4.5', label: 'Claude Haiku 4.5' },
    { value: 'anthropic/claude-sonnet-4.5', label: 'Claude Sonnet 4.5' },
    { value: 'google/gemini-3-flash-preview', label: 'Gemini 3 Flash' },
    { value: 'google/gemini-3-pro-preview', label: 'Gemini 3 Pro' },
    { value: 'moonshotai/kimi-k2-0905', label: 'Kimi K2' },
    { value: 'qwen/qwen3-235b-a22b-2507', label: 'Qwen3 235B' }
  ];

  // State
  let isOpen = false;
  let isLoading = false;
  let messages = [];
  let turnstileToken = null;
  let turnstileWidgetId = null;
  let backendOnline = null; // null = checking, true = online, false = offline
  let backendVersion = null; // Backend version from health check
  let backendCommitSha = null; // Backend git commit SHA from health check
  let pageContextEnabled = true; // Runtime state for page context toggle
  let chatPopup = null; // Reference to pop-out window (prevents duplicates)
  let userSettings = { apiKey: null, model: null }; // User settings (BYOK and model selection)
  let communityDefaultModel = null; // Community's default model from API

  // Store script URL at load time for reliable pop-out
  const WIDGET_SCRIPT_URL = document.currentScript?.src || null;

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
    settings: '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" fill="currentColor"><circle cx="12" cy="5" r="2"></circle><circle cx="12" cy="12" r="2"></circle><circle cx="12" cy="19" r="2"></circle></svg>'
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
      width: 380px;
      max-width: calc(100vw - 40px);
      height: 520px;
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

    .osa-chat-footer {
      padding: 8px 16px;
      border-top: 1px solid var(--osa-border);
      text-align: center;
      font-size: 11px;
      color: var(--osa-text-light);
    }

    .osa-chat-footer a {
      color: var(--osa-text-light);
      text-decoration: none;
    }

    .osa-chat-footer a:hover {
      color: var(--osa-primary);
      text-decoration: underline;
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

    .osa-page-context-toggle {
      padding: 6px 16px;
      font-size: 11px;
      color: var(--osa-text-light);
      display: flex;
      align-items: center;
      gap: 6px;
    }

    .osa-page-context-toggle input[type="checkbox"] {
      width: 11px;
      height: 11px;
      margin: 0;
      cursor: pointer;
      accent-color: var(--osa-primary);
    }

    .osa-page-context-toggle label {
      cursor: pointer;
      user-select: none;
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
  `;

  // Escape HTML for user messages
  function escapeHtml(text) {
    const div = document.createElement('div');
    div.textContent = text;
    return div.innerHTML;
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

  // Render inline markdown (bold, italic, links, plain URLs)
  function renderInlineMarkdown(text) {
    if (!text) return '';

    let result = '';
    let remaining = text;

    while (remaining.length > 0) {
      const boldMatch = remaining.match(/\*\*(.+?)\*\*/);
      const italicMatch = remaining.match(/(?<!\*)\*([^*]+)\*(?!\*)/);
      const linkMatch = remaining.match(/\[([^\]]+)\]\(([^)]+)\)/);
      const urlMatch = remaining.match(/(?<!\]\()(https?:\/\/[^\s\)]+)/);

      const boldIndex = boldMatch ? remaining.indexOf(boldMatch[0]) : -1;
      const italicIndex = italicMatch ? remaining.indexOf(italicMatch[0]) : -1;
      const linkIndex = linkMatch ? remaining.indexOf(linkMatch[0]) : -1;
      const urlIndex = urlMatch ? remaining.indexOf(urlMatch[0]) : -1;

      const indices = [boldIndex, italicIndex, linkIndex, urlIndex].filter(i => i !== -1);
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
      }
    }

    return result;
  }

  // Full markdown to HTML converter
  function markdownToHtml(text) {
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
            tableHtml += '<' + tag + '>' + renderInlineMarkdown(cell.trim()) + '</' + tag + '>';
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
        result += '<h' + level + '>' + renderInlineMarkdown(headerMatch[2]) + '</h' + level + '>';
        continue;
      }

      // Handle bullet points (* item or - item)
      const bulletMatch = line.match(/^[\*\-]\s+(.+)$/);
      if (bulletMatch) {
        if (currentListType !== 'ul') flushList();
        currentListType = 'ul';
        currentList.push('<li>' + renderInlineMarkdown(bulletMatch[1]) + '</li>');
        continue;
      }

      // Handle numbered lists
      const numberedMatch = line.match(/^\d+\.\s+(.+)$/);
      if (numberedMatch) {
        if (currentListType !== 'ol') flushList();
        currentListType = 'ol';
        currentList.push('<li>' + renderInlineMarkdown(numberedMatch[1]) + '</li>');
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
          if (text) return renderInlineMarkdown(text);
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

  // Load chat history from localStorage
  function loadHistory() {
    let historyLoadFailed = false;
    try {
      const saved = localStorage.getItem(CONFIG.storageKey);
      if (saved) {
        const parsed = JSON.parse(saved);
        // Validate structure to prevent injection attacks
        if (Array.isArray(parsed)) {
          messages = parsed.filter(isValidMessage);
          if (messages.length !== parsed.length) {
            console.warn('Some chat messages were invalid and filtered out');
          }
        }
      }
    } catch (e) {
      console.error('Failed to load chat history:', e);
      historyLoadFailed = true;
    }
    if (messages.length === 0) {
      messages = [{ role: 'assistant', content: CONFIG.initialMessage }];
    }
    return historyLoadFailed;
  }

  // Save chat history to localStorage
  let saveErrorShown = false;
  function saveHistory() {
    try {
      localStorage.setItem(CONFIG.storageKey, JSON.stringify(messages));
      saveErrorShown = false;
    } catch (e) {
      console.error('Failed to save chat history:', e);
      // Show error once per session to avoid spam
      if (!saveErrorShown) {
        const container = document.querySelector('.osa-chat-widget');
        if (container) {
          showError(container, 'Chat history could not be saved. Storage may be full or disabled.');
        }
        saveErrorShown = true;
      }
    }
  }

  // Get page context (URL and title) for contextual answers
  function getPageContext() {
    if (!CONFIG.allowPageContext || !pageContextEnabled) {
      return null;
    }
    return {
      url: window.location.href,
      title: document.title || null
    };
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
        userSettings = { apiKey: null, model: null };
        return;
      }

      let parsed;
      try {
        parsed = JSON.parse(saved);
      } catch (jsonErr) {
        console.error('[OSA] Saved settings contain invalid JSON:', jsonErr.message);
        const container = document.querySelector('.osa-chat-widget');
        if (container && isOpen) {
          showError(container, 'Saved settings are corrupted. Using defaults.');
        }
        userSettings = { apiKey: null, model: null };
        // Clear corrupted data
        try { localStorage.removeItem(storageKey); } catch {}
        return;
      }

      // Validate API key format if present
      if (parsed.apiKey) {
        // Basic format validation: sk-or-v1-[hex]
        if (!/^sk-or-v1-[0-9a-f]{64}$/i.test(parsed.apiKey)) {
          console.error('[OSA] Saved API key has invalid format, ignoring');
          parsed.apiKey = null;
        }
      }

      // Validate model format if present
      if (parsed.model && typeof parsed.model === 'string') {
        // Validate model format: provider/model-name
        if (!/^[a-zA-Z0-9_-]+\/[a-zA-Z0-9._-]+$/.test(parsed.model)) {
          console.error('[OSA] Saved model has invalid format, ignoring');
          parsed.model = null;
        }
      }

      userSettings = {
        apiKey: parsed.apiKey || null,
        model: parsed.model || null
      };
    } catch (e) {
      // localStorage access error
      console.error('[OSA] Cannot access localStorage for settings:', e.message);
      const container = document.querySelector('.osa-chat-widget');
      if (container && isOpen) {
        showError(container, 'Cannot access browser storage. Settings will not persist.');
      }
      userSettings = { apiKey: null, model: null };
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

  // Fetch community default model from API
  async function fetchCommunityDefaultModel() {
    // Validate communityId before making request
    if (!isValidCommunityId(CONFIG.communityId)) {
      console.error('[OSA] Invalid communityId, cannot fetch default model');
      communityDefaultModel = 'openai/gpt-oss-120b'; // Hardcoded fallback (Cerebras provider)
      return;
    }

    try {
      const response = await fetch(`${CONFIG.apiEndpoint}/communities/${CONFIG.communityId}`, {
        method: 'GET',
        signal: AbortSignal.timeout(5000)
      });

      if (!response.ok) {
        console.error(`[OSA] Community default model fetch failed: HTTP ${response.status}`);
        communityDefaultModel = 'openai/gpt-oss-120b'; // Hardcoded fallback (Cerebras provider)
        const container = document.querySelector('.osa-chat-widget');
        if (container && isOpen) {
          showError(container, `Could not load default model settings (HTTP ${response.status}). Using fallback.`);
        }
        return;
      }

      const data = await response.json();
      if (data && data.default_model) {
        communityDefaultModel = data.default_model;
      } else {
        console.error('[OSA] Community default model not found in API response');
        communityDefaultModel = 'openai/gpt-oss-120b';
        const container = document.querySelector('.osa-chat-widget');
        if (container && isOpen) {
          showError(container, 'Default model settings incomplete. Using fallback.');
        }
      }
    } catch (e) {
      console.error('[OSA] Could not fetch community default model:', e.message || e);
      communityDefaultModel = 'openai/gpt-oss-120b';
      const container = document.querySelector('.osa-chat-widget');
      if (container && isOpen) {
        showError(container, 'Network error loading model settings. Using fallback.');
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

    // Update default option label with community default model
    if (modelSelect) {
      const defaultOption = modelSelect.querySelector('option[value="default"]');
      if (defaultOption) {
        const modelLabel = communityDefaultModel || 'Community Setting';
        defaultOption.textContent = `Default (${modelLabel})`;
      }
    }

    // Populate form with current settings
    if (apiKeyInput) {
      apiKeyInput.value = userSettings.apiKey || '';
    }
    if (modelSelect) {
      // Check if current model is in the default list
      const isDefaultModel = userSettings.model === null || DEFAULT_MODELS.some(m => m.value === userSettings.model);
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
    if (modelHint && communityDefaultModel) {
      modelHint.textContent = `Community default: ${communityDefaultModel}`;
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

    // Validate API key format if provided
    if (apiKey && !/^sk-or-v1-[0-9a-f]{64}$/i.test(apiKey)) {
      showError(container, 'Invalid API key format. Expected: sk-or-v1-[64 hex chars]');
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
      // Validate custom model format: provider/model-name
      if (!/^[a-zA-Z0-9_-]+\/[a-zA-Z0-9._-]+$/.test(model)) {
        showError(container, 'Invalid model format. Expected: provider/model-name');
        return;
      }
    } else if (modelSelection !== 'default') {
      model = modelSelection;
    }

    // Update settings
    userSettings.apiKey = apiKey || null;
    userSettings.model = model;

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
        <div class="osa-chat-input">
          <input type="text" placeholder="${escapeHtml(CONFIG.placeholder)}" />
          <button class="osa-send-btn" aria-label="Send">
            ${ICONS.send}
          </button>
        </div>
        <div class="osa-page-context-toggle" style="display: ${CONFIG.allowPageContext ? 'flex' : 'none'}">
          <input type="checkbox" id="osa-page-context-checkbox" ${pageContextEnabled ? 'checked' : ''} />
          <label for="osa-page-context-checkbox">${escapeHtml(CONFIG.pageContextLabel)}</label>
        </div>
        <div class="osa-chat-footer">
          <a href="${escapeHtml(CONFIG.repoUrl)}" target="_blank" rel="noopener noreferrer">
            Powered by ${escapeHtml(CONFIG.repoName)}<span class="osa-version"></span>
          </a>
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
                OpenRouter API Key (Optional)
              </label>
              <input
                type="password"
                id="osa-settings-api-key"
                class="osa-settings-input"
                placeholder="sk-or-v1-..."
                autocomplete="off"
              />
              <span class="osa-settings-hint">
                Use your own API key for testing. Stored locally in your browser.
              </span>
            </div>
            <div class="osa-settings-field">
              <label class="osa-settings-label" for="osa-settings-model">
                Model Selection
              </label>
              <select id="osa-settings-model" class="osa-settings-select">
                <option value="default">Default (Community Setting)</option>
                ${DEFAULT_MODELS.map(m => `<option value="${escapeHtml(m.value)}">${escapeHtml(m.label)}</option>`).join('')}
                <option value="custom">Custom</option>
              </select>
              <span class="osa-settings-hint" id="osa-settings-model-hint">
                Select a model or use the community default
              </span>
            </div>
            <div class="osa-settings-field" id="osa-settings-custom-model-field" style="display: none;">
              <label class="osa-settings-label" for="osa-settings-custom-model">
                Model name (<a href="https://openrouter.ai/models" target="_blank" rel="noopener noreferrer" style="color: var(--osa-primary); text-decoration: underline;">from OpenRouter</a>)
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
      const msgEl = document.createElement('div');
      msgEl.className = `osa-message ${msg.role}`;

      const label = msg.role === 'user' ? 'You' : CONFIG.title;
      const content = msg.role === 'assistant' ? markdownToHtml(msg.content) : escapeHtml(msg.content);

      // Add copy button for assistant messages
      const copyBtn = msg.role === 'assistant'
        ? `<button class="osa-message-copy-btn" data-msg-index="${msgIndex}" title="Copy as markdown">${ICONS.copy}</button>`
        : '';

      msgEl.innerHTML = `
        <div class="osa-message-header">
          <span class="osa-message-label">${escapeHtml(label)}</span>
          ${copyBtn}
        </div>
        <div class="osa-message-content">${content}</div>
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

    if (isLoading) {
      const loadingEl = document.createElement('div');
      loadingEl.className = 'osa-loading';
      loadingEl.innerHTML = `
        <span class="osa-loading-label">${escapeHtml(CONFIG.title)}</span>
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

  // Send message to API
  async function sendMessage(container, question) {
    if (isLoading || !question.trim()) return;

    isLoading = true;
    messages.push({ role: 'user', content: question });
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
      const body = { question: question.trim() };

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

      if (!isValidCommunityId(CONFIG.communityId)) {
        throw new Error('Invalid community configuration. Please reload the page.');
      }

      const headers = {
        'Content-Type': 'application/json',
      };

      // Add BYOK API key if set
      if (userSettings.apiKey) {
        headers['X-OpenRouter-Key'] = userSettings.apiKey;
      }

      const response = await fetch(`${CONFIG.apiEndpoint}/${CONFIG.communityId}/ask`, {
        method: 'POST',
        headers: headers,
        body: JSON.stringify(body),
      });

      if (!response.ok) {
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
        throw new Error(errorMessage);
      }

      const data = await response.json();
      const answer = (data && typeof data.answer === 'string') ? data.answer : null;
      if (!answer) {
        throw new Error('Invalid response from server');
      }
      messages.push({ role: 'assistant', content: answer });
      saveHistory();
      updateStatusDisplay(true);

    } catch (error) {
      console.error('Chat error:', error);
      showError(container, error.message || 'Failed to get response');
      // Remove the user message on error and sync localStorage
      messages.pop();
      saveHistory();
      updateStatusDisplay(false);
    } finally {
      isLoading = false;
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
    messages = [{ role: 'assistant', content: CONFIG.initialMessage }];
    saveHistory();
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
    } else {
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

      // Create popup config with fullscreen mode
      const popupConfig = { ...CONFIG, fullscreen: true };

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
    loadHistory();
    injectStyles();
    const container = createWidget();

    // Fetch community default model (async, non-blocking)
    fetchCommunityDefaultModel();

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
      setTimeout(() => {
        input?.focus();
      }, 100);
    }
  }

  // Validate communityId contains only safe characters (alphanumeric, hyphens, underscores)
  function isValidCommunityId(id) {
    return typeof id === 'string' && /^[a-zA-Z0-9_-]+$/.test(id);
  }

  // Start when DOM is ready, deferred to allow setConfig calls before init
  function scheduleInit() {
    setTimeout(init, 0);
  }
  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', scheduleInit);
  } else {
    scheduleInit();
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
      // Auto-derive storageKey from communityId if communityId changed but storageKey wasn't explicitly set
      if (opts.communityId && !opts.storageKey) {
        opts.storageKey = `osa-chat-history-${opts.communityId}`;
      }
      Object.assign(CONFIG, opts);
    },
    getConfig: function() {
      return { ...CONFIG };
    }
  };
})();
