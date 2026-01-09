/**
 * OSA Chat Widget
 * A floating chat assistant for Open Science tools (HED, BIDS, etc.)
 * Connects to OSA Cloudflare Worker for secure access.
 */

(function() {
  'use strict';

  // Configuration (can be customized via OSAChatWidget.setConfig)
  const CONFIG = {
    apiEndpoint: 'https://osa-worker.shirazi-10f.workers.dev',
    storageKey: 'osa-chat-history',
    turnstileSiteKey: null,
    // Customizable branding
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
    pageContextLabel: 'Share page URL to help answer questions'
  };

  // State
  let isOpen = false;
  let isLoading = false;
  let messages = [];
  let turnstileToken = null;
  let turnstileWidgetId = null;
  let backendOnline = null; // null = checking, true = online, false = offline
  let pageContextEnabled = true; // Runtime state for page context toggle

  // Icons (SVG)
  const ICONS = {
    chat: '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M21 15a2 2 0 0 1-2 2H7l-4 4V5a2 2 0 0 1 2-2h14a2 2 0 0 1 2 2z"></path></svg>',
    close: '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><line x1="18" y1="6" x2="6" y2="18"></line><line x1="6" y1="6" x2="18" y2="18"></line></svg>',
    send: '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><line x1="22" y1="2" x2="11" y2="13"></line><polygon points="22 2 15 22 11 13 2 9 22 2"></polygon></svg>',
    reset: '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M3 12a9 9 0 1 0 9-9 9.75 9.75 0 0 0-6.74 2.74L3 8"/><path d="M3 3v5h5"/></svg>',
    brain: '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M12 5a3 3 0 1 0-5.997.125 4 4 0 0 0-2.526 5.77 4 4 0 0 0 .556 6.588A4 4 0 1 0 12 18Z"/><path d="M12 5a3 3 0 1 1 5.997.125 4 4 0 0 1 2.526 5.77 4 4 0 0 1-.556 6.588A4 4 0 1 1 12 18Z"/><path d="M15 13a4.5 4.5 0 0 1-3-4 4.5 4.5 0 0 1-3 4"/></svg>',
    copy: '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><rect x="9" y="9" width="13" height="13" rx="2" ry="2"></rect><path d="M5 15H4a2 2 0 0 1-2-2V4a2 2 0 0 1 2-2h9a2 2 0 0 1 2 2v1"></path></svg>',
    check: '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><polyline points="20 6 9 17 4 12"></polyline></svg>'
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
      } else {
        backendOnline = false;
        statusDot.className = 'osa-status-dot offline';
        statusText.textContent = 'Offline';
      }
    } catch (e) {
      backendOnline = false;
      statusDot.className = 'osa-status-dot offline';
      statusText.textContent = 'Offline';
      console.warn('Backend health check failed:', e);
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
    container.className = 'osa-chat-widget';

    const experimentalBadge = CONFIG.showExperimentalBadge
      ? '<span class="osa-experimental-badge">Experimental</span>'
      : '';

    container.innerHTML = `
      <button class="osa-chat-button" aria-label="Open chat">
        ${ICONS.chat}
      </button>
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
            <button class="osa-header-btn osa-reset-btn" title="Clear chat">
              ${ICONS.reset}
            </button>
            <button class="osa-header-btn osa-close-btn" title="Close">
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
            Powered by ${escapeHtml(CONFIG.repoName)}
          </a>
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

      const response = await fetch(`${CONFIG.apiEndpoint}/hed/ask`, {
        method: 'POST',
        headers: {
          'Content-Type': 'application/json',
        },
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

    if (isOpen) {
      chatWindow.classList.add('open');
      button.innerHTML = ICONS.close;
      button.setAttribute('aria-label', 'Close chat');
      container.querySelector('.osa-chat-input input').focus();
    } else {
      chatWindow.classList.remove('open');
      button.innerHTML = ICONS.chat;
      button.setAttribute('aria-label', 'Open chat');
    }
  }

  // Initialize widget
  function init() {
    loadPageContextPreference();
    loadHistory();
    injectStyles();
    const container = createWidget();

    renderMessages(container);
    renderSuggestions(container);

    // Query required DOM elements with null checks
    const chatButton = container.querySelector('.osa-chat-button');
    const closeBtn = container.querySelector('.osa-close-btn');
    const resetBtn = container.querySelector('.osa-reset-btn');
    const input = container.querySelector('.osa-chat-input input');
    const sendBtn = container.querySelector('.osa-send-btn');
    const suggestionsList = container.querySelector('.osa-suggestions-list');

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

    // Check backend status
    checkBackendStatus();

    // Initialize Turnstile if the script is loaded
    if (window.turnstile) {
      initTurnstile(container);
    } else {
      window.addEventListener('load', () => {
        if (window.turnstile) initTurnstile(container);
      });
    }
  }

  // Start when DOM is ready
  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', init);
  } else {
    init();
  }

  // Expose configuration for customization
  window.OSAChatWidget = {
    setConfig: function(options) {
      Object.assign(CONFIG, options);
    },
    getConfig: function() {
      return { ...CONFIG };
    }
  };
})();
