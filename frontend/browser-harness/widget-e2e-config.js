// Points the widget at widget_e2e.py, which serves the API beside the page.
window.__OSA_CHAT_CONFIG__ = {
  apiEndpoint: `${window.location.origin}/api`,
  communityId: 'browsertest',
  storageKey: 'osa-widget-e2e',
  pageContextDefaultEnabled: false,
};
