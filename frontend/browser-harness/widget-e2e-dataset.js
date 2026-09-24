// Lets a manual or scripted Chrome run drive setDataset/setConfig({notebookUrl})
// without a devtools console: widget-e2e.html?dataset=<id>&zarr=true|false&notebookUrl=<url>
//
// An external file, not an inline <script>, because the harness serves this page
// under nemar.org's production Content-Security-Policy (script-src 'self'
// 'wasm-unsafe-eval' <cdn>, no 'unsafe-inline'); it runs after osa-chat-widget.js,
// so window.OSAChatWidget already exists.
//
// - dataset absent entirely: setDataset is never called (the "never set" state).
// - dataset=            (empty): setDataset(null) (explicitly "no dataset").
// - dataset=<id>&zarr=true|false: setDataset({id, zarr: true|false}).
// - dataset=<id>, no zarr param: setDataset({id}) (zarr unknown).
(function () {
  const params = new URLSearchParams(window.location.search);
  if (params.has('dataset')) {
    const id = params.get('dataset');
    if (id === '') {
      window.OSAChatWidget.setDataset(null);
    } else {
      const zarrParam = params.get('zarr');
      const value = { id };
      if (zarrParam === 'true') value.zarr = true;
      else if (zarrParam === 'false') value.zarr = false;
      window.OSAChatWidget.setDataset(value);
    }
  }
  const notebookUrl = params.get('notebookUrl');
  if (notebookUrl) {
    window.OSAChatWidget.setConfig({ notebookUrl });
  }
})();
