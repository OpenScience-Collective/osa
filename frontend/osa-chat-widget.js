/**
 * OSA Chat Widget
 * A floating chat assistant for Open Science tools (HED, BIDS, etc.)
 * Connects to OSA Cloudflare Worker for secure access.
 */

(function() {
  'use strict';

  // Configuration
  const CONFIG = {
    apiEndpoint: 'https://osa-worker.shirazi-10f.workers.dev',
    storageKey: 'osa-chat-history',
    turnstileSiteKey: null, // Set when Turnstile is enabled
  };

  // Suggested questions for users
  const SUGGESTED_QUESTIONS = [
    'What is HED and how is it used?',
    'How do I annotate an event with HED tags?',
    'What tools are available for working with HED?',
    'Explain this HED validation error.'
  ];

  // Initial greeting message
  const INITIAL_MESSAGE = {
    role: 'assistant',
    content: 'Hi! I\'m the Open Science Assistant. I can help with HED (Hierarchical Event Descriptors), BIDS, and other open science tools. What would you like to know?'
  };

  // State
  let isOpen = false;
  let isLoading = false;
  let messages = [];
  let turnstileToken = null;
  let turnstileWidgetId = null;

  // Icons (SVG)
  const ICONS = {
    chat: '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M21 15a2 2 0 0 1-2 2H7l-4 4V5a2 2 0 0 1 2-2h14a2 2 0 0 1 2 2z"></path></svg>',
    close: '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><line x1="18" y1="6" x2="6" y2="18"></line><line x1="6" y1="6" x2="18" y2="18"></line></svg>',
    send: '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><line x1="22" y1="2" x2="11" y2="13"></line><polygon points="22 2 15 22 11 13 2 9 22 2"></polygon></svg>',
    reset: '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M3 12a9 9 0 1 0 9-9 9.75 9.75 0 0 0-6.74 2.74L3 8"/><path d="M3 3v5h5"/></svg>'
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
      --osa-shadow: 0 10px 25px rgba(0, 0, 0, 0.1);
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
      padding: 16px;
      background: var(--osa-primary);
      color: white;
      display: flex;
      align-items: center;
      justify-content: space-between;
    }

    .osa-chat-header h3 {
      margin: 0;
      font-size: 16px;
      font-weight: 600;
    }

    .osa-header-actions {
      display: flex;
      gap: 8px;
    }

    .osa-header-btn {
      background: transparent;
      border: none;
      color: white;
      cursor: pointer;
      padding: 4px;
      border-radius: 4px;
      display: flex;
      align-items: center;
      justify-content: center;
      opacity: 0.8;
      transition: opacity 0.2s;
    }

    .osa-header-btn:hover {
      opacity: 1;
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
      gap: 12px;
    }

    .osa-message {
      max-width: 85%;
      padding: 10px 14px;
      border-radius: 12px;
      word-wrap: break-word;
    }

    .osa-message.user {
      align-self: flex-end;
      background: var(--osa-user-bg);
      color: var(--osa-user-text);
      border-bottom-right-radius: 4px;
    }

    .osa-message.assistant {
      align-self: flex-start;
      background: var(--osa-assistant-bg);
      color: var(--osa-text);
      border-bottom-left-radius: 4px;
    }

    .osa-message.assistant code {
      background: #e5e7eb;
      padding: 2px 6px;
      border-radius: 4px;
      font-size: 13px;
    }

    .osa-message.assistant pre {
      background: #1f2937;
      color: #f9fafb;
      padding: 12px;
      border-radius: 8px;
      overflow-x: auto;
      margin: 8px 0;
    }

    .osa-message.assistant pre code {
      background: transparent;
      padding: 0;
      color: inherit;
    }

    .osa-suggestions {
      padding: 12px 16px;
      border-top: 1px solid var(--osa-border);
      display: flex;
      flex-wrap: wrap;
      gap: 8px;
    }

    .osa-suggestion {
      background: var(--osa-assistant-bg);
      border: 1px solid var(--osa-border);
      border-radius: 16px;
      padding: 6px 12px;
      font-size: 12px;
      cursor: pointer;
      transition: background 0.2s;
      color: var(--osa-text);
    }

    .osa-suggestion:hover {
      background: #e5e7eb;
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
      gap: 4px;
      padding: 10px 14px;
    }

    .osa-loading span {
      width: 8px;
      height: 8px;
      background: var(--osa-text-light);
      border-radius: 50%;
      animation: osa-bounce 1.4s infinite ease-in-out both;
    }

    .osa-loading span:nth-child(1) { animation-delay: -0.32s; }
    .osa-loading span:nth-child(2) { animation-delay: -0.16s; }

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
  `;

  // Load chat history from localStorage
  function loadHistory() {
    try {
      const saved = localStorage.getItem(CONFIG.storageKey);
      if (saved) {
        messages = JSON.parse(saved);
      }
    } catch (e) {
      console.warn('Failed to load chat history:', e);
    }
    if (messages.length === 0) {
      messages = [INITIAL_MESSAGE];
    }
  }

  // Save chat history to localStorage
  function saveHistory() {
    try {
      localStorage.setItem(CONFIG.storageKey, JSON.stringify(messages));
    } catch (e) {
      console.warn('Failed to save chat history:', e);
    }
  }

  // Simple markdown to HTML converter
  function markdownToHtml(text) {
    return text
      // Code blocks
      .replace(/```(\w*)\n([\s\S]*?)```/g, '<pre><code>$2</code></pre>')
      // Inline code
      .replace(/`([^`]+)`/g, '<code>$1</code>')
      // Bold
      .replace(/\*\*([^*]+)\*\*/g, '<strong>$1</strong>')
      // Italic
      .replace(/\*([^*]+)\*/g, '<em>$1</em>')
      // Links
      .replace(/\[([^\]]+)\]\(([^)]+)\)/g, '<a href="$2" target="_blank" rel="noopener">$1</a>')
      // Line breaks
      .replace(/\n/g, '<br>');
  }

  // Create and inject styles
  function injectStyles() {
    const style = document.createElement('style');
    style.textContent = STYLES;
    document.head.appendChild(style);
  }

  // Create the widget DOM
  function createWidget() {
    const container = document.createElement('div');
    container.className = 'osa-chat-widget';
    container.innerHTML = `
      <button class="osa-chat-button" aria-label="Open chat">
        ${ICONS.chat}
      </button>
      <div class="osa-chat-window">
        <div class="osa-chat-header">
          <h3>Open Science Assistant</h3>
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
        <div class="osa-suggestions"></div>
        <div class="osa-turnstile-container" style="display: none;"></div>
        <div class="osa-error" style="display: none;"></div>
        <div class="osa-chat-input">
          <input type="text" placeholder="Ask about HED, BIDS, or other tools..." />
          <button class="osa-send-btn" aria-label="Send">
            ${ICONS.send}
          </button>
        </div>
      </div>
    `;
    document.body.appendChild(container);
    return container;
  }

  // Render messages
  function renderMessages(container) {
    const messagesEl = container.querySelector('.osa-chat-messages');
    messagesEl.innerHTML = '';

    messages.forEach(msg => {
      const msgEl = document.createElement('div');
      msgEl.className = `osa-message ${msg.role}`;
      msgEl.innerHTML = msg.role === 'assistant' ? markdownToHtml(msg.content) : msg.content;
      messagesEl.appendChild(msgEl);
    });

    if (isLoading) {
      const loadingEl = document.createElement('div');
      loadingEl.className = 'osa-message assistant osa-loading';
      loadingEl.innerHTML = '<span></span><span></span><span></span>';
      messagesEl.appendChild(loadingEl);
    }

    messagesEl.scrollTop = messagesEl.scrollHeight;
  }

  // Render suggestions
  function renderSuggestions(container) {
    const suggestionsEl = container.querySelector('.osa-suggestions');

    // Only show suggestions if there's just the initial message
    if (messages.length <= 1) {
      suggestionsEl.innerHTML = SUGGESTED_QUESTIONS.map(q =>
        `<button class="osa-suggestion">${q}</button>`
      ).join('');
      suggestionsEl.style.display = 'flex';
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
    input.value = '';
    input.disabled = true;
    sendBtn.disabled = true;

    try {
      const body = { question: question.trim() };

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
        const error = await response.json();
        throw new Error(error.detail || error.error || 'Request failed');
      }

      const data = await response.json();
      messages.push({ role: 'assistant', content: data.answer });
      saveHistory();

    } catch (error) {
      console.error('Chat error:', error);
      showError(container, error.message || 'Failed to get response');
      // Remove the user message on error
      messages.pop();
    } finally {
      isLoading = false;
      input.disabled = false;
      sendBtn.disabled = false;
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
    turnstileContainer.style.display = 'flex';

    turnstileWidgetId = window.turnstile.render(turnstileContainer, {
      sitekey: CONFIG.turnstileSiteKey,
      callback: function(token) {
        turnstileToken = token;
      },
    });
  }

  // Reset chat
  function resetChat(container) {
    messages = [INITIAL_MESSAGE];
    saveHistory();
    renderMessages(container);
    renderSuggestions(container);
  }

  // Toggle chat window
  function toggleChat(container) {
    isOpen = !isOpen;
    const window = container.querySelector('.osa-chat-window');
    const button = container.querySelector('.osa-chat-button');

    if (isOpen) {
      window.classList.add('open');
      button.innerHTML = ICONS.close;
      container.querySelector('.osa-chat-input input').focus();
    } else {
      window.classList.remove('open');
      button.innerHTML = ICONS.chat;
    }
  }

  // Initialize widget
  function init() {
    loadHistory();
    injectStyles();
    const container = createWidget();

    renderMessages(container);
    renderSuggestions(container);

    // Event listeners
    container.querySelector('.osa-chat-button').addEventListener('click', () => toggleChat(container));
    container.querySelector('.osa-close-btn').addEventListener('click', () => toggleChat(container));
    container.querySelector('.osa-reset-btn').addEventListener('click', () => resetChat(container));

    const input = container.querySelector('.osa-chat-input input');
    const sendBtn = container.querySelector('.osa-send-btn');

    sendBtn.addEventListener('click', () => sendMessage(container, input.value));
    input.addEventListener('keypress', (e) => {
      if (e.key === 'Enter') sendMessage(container, input.value);
    });

    container.querySelector('.osa-suggestions').addEventListener('click', (e) => {
      if (e.target.classList.contains('osa-suggestion')) {
        sendMessage(container, e.target.textContent);
      }
    });

    // Initialize Turnstile if the script is loaded
    if (window.turnstile) {
      initTurnstile(container);
    } else {
      // Wait for Turnstile to load
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
    }
  };
})();
