// Conversations for the notebook builder's tests (E4.1, #667), as the widget stores them:
// a flat list of run records in message order (executionRecord, normalizePersistedExecutions).
//
// Where these come from, so nobody mistakes them for more than they are:
//   - `RUN_CARD_FIXTURES` runs are REAL: code, stdout, stderr and traceback recorded from
//     Pyodide by record-run-card-fixtures.js. Their code is not ERP code; it is kept as
//     recorded rather than rewritten.
//   - The ERP conversation is HAND-AUTHORED, shaped like board B1 (.context/designs/
//     nemar-ai-ux/Notebook.dc.html): five steps on on002718, step 4 failed once and was
//     retried. It was not exported from a live chat (that needs a browser session on
//     develop-demo.osc.earth); its code is written to compile, which the Python test
//     checks, not to be run. `stepTitle` is what E2.4 will record from the plan step.
//   - The denied, timeout, cancelled and oom runs are hand-authored status shapes.
// No mocks: nothing here replaces code under test; it is input data.
import { RUN_CARD_FIXTURES } from './run-card-fixtures.js';

let callSerial = 0;

/** A stored run record with every field the widget writes, over the given overrides. */
export function run(overrides) {
  callSerial += 1;
  return {
    callId: `call-${callSerial}`,
    tool: 'execute_code',
    description: '',
    code: '',
    status: 'ok',
    stdout: '',
    stderr: '',
    traceback: '',
    local: false,
    images: [],
    workspaceNote: '',
    ...overrides,
  };
}

function recorded(name, overrides = {}) {
  const fixture = RUN_CARD_FIXTURES[name];
  return run({
    description: `Recorded run: ${name}`,
    code: fixture.code,
    status: fixture.status,
    stdout: fixture.stdout,
    stderr: fixture.stderr,
    traceback: fixture.traceback,
    ...overrides,
  });
}

const ERP_STEPS = {
  count: [
    'import numpy as np',
    'import eegprep_lean',
    '',
    'store = index.store("sub-002/eeg/sub-002_task-FaceRecognition_eeg.set")',
    'events = store.events()',
    'faces = events[events["trial_type"].str.match("(famous|unfamiliar|scrambled)_")]',
    'print(faces["trial_type"].str.split("_").str[0].value_counts())',
  ].join('\n'),
  epochs: [
    'import numpy as np',
    'from eegprep_lean import plot_window',
    '',
    'group = store.group("eeg_250hz")',
    'window = await eegprep_lean.read_window(index, store, group=group)',
    'rate = window.rate',
    'onsets = (faces["onset"].to_numpy() * rate).astype(int)',
    'before, after = int(0.2 * rate), int(0.6 * rate)',
    'try:',
    '    import scipy.signal as sig',
    'except ImportError:',
    '    sig = None',
    '# %% this comment would split a cell in a percent script',
    'epochs = np.stack([window.data[:, o - before : o + after] for o in onsets if o > before])',
    'print(epochs.shape)',
  ].join('\n'),
  average: [
    'import numpy as np',
    'import matplotlib.pyplot as plt',
    'from scipy.signal import (',
    '    butter,',
    '    sosfiltfilt,  # zero-phase',
    ')',
    '',
    'sos = butter(4, 30, btype="low", fs=rate, output="sos")',
    'conditions = faces["trial_type"].str.split("_").str[0].to_numpy()[: len(epochs)]',
    'erp = {name: sosfiltfilt(sos, epochs[conditions == name].mean(axis=0), axis=-1) for name in set(conditions)}',
    'print({name: value.shape for name, value in erp.items()})',
  ].join('\n'),
  plot: [
    'import matplotlib.pyplot as plt',
    'import numpy as np',
    '',
    'time_ms = np.arange(-before, after) / rate * 1000',
    'fig, ax = plt.subplots(figsize=(6, 3))',
    'for name in ("famous", "scrambled"):',
    '    ax.plot(time_ms, erp[name][64:68].mean(axis=0), label=name)',
    'ax.set_xlabel("ms")',
    'ax.legend()',
    '# peak?',
    'fig',
  ].join('\n'),
  summary: [
    'import numpy as np',
    '',
    'peak = {name: float(erp[name][64:68].mean(axis=0).min()) for name in ("famous", "scrambled")}',
    'print(peak)',
  ].join('\n'),
};

/** Board B1: five steps on on002718, the first try of step 4 failed. */
const erpExecutions = () => [
  run({
    description: 'Counted face onsets per condition',
    stepTitle: 'Count face onsets per condition',
    code: ERP_STEPS.count,
    stdout: 'famous 300\nunfamiliar 300\nscrambled 300\n',
  }),
  run({
    description: 'Cut epochs from -200 to 600 ms',
    stepTitle: 'Cut epochs from −200 to 600 ms',
    code: ERP_STEPS.epochs,
    stdout: '(882, 64, 200)\n',
  }),
  run({
    description: 'Averaged epochs per condition',
    code: ERP_STEPS.average,
    stdout: "{'famous': (64, 200), 'scrambled': (64, 200)}\n",
  }),
  recorded('keyErrorWithLine', { description: 'Plotted famous vs scrambled ERPs' }),
  run({
    description: 'Plotted famous vs scrambled ERPs',
    stepTitle: 'Plotted famous vs scrambled ERPs',
    code: ERP_STEPS.plot,
    local: true,
  }),
  run({
    description: '',
    code: ERP_STEPS.summary,
    stdout: "{'famous': -4.1, 'scrambled': -2.3}\n",
  }),
];

/** Everything that is not a success, with a real error where the fixtures have one. */
const notOkExecutions = () => [
  run({ description: 'Loaded the whole recording at once', code: 'data = await big_read()', status: 'denied' }),
  run({
    description: 'Computed a spectrum for every subject',
    code: 'for s in subjects:\n    await spectrum(s)',
    status: 'timeout',
    stderr: 'The run took longer than 60 s and was stopped.',
  }),
  run({ description: 'Epoching, cancelled by the reader', code: 'epochs = cut(window)', status: 'cancelled' }),
  run({
    description: 'Averaged every subject',
    code: 'avg = np.zeros((10**9, 64))',
    status: 'oom',
    stderr: 'The run used more memory than the budget.',
  }),
  recorded('syntaxError'),
  recorded('errorWithoutLine'),
];

/**
 * Source lines that Jupytext's percent format treats specially, in code that really is
 * Python: a comment that looks like a cell marker (at any indent) or like a magic, text
 * inside a string, and a line with trailing blanks. A script that is not written with
 * care splits a cell or turns a comment into a magic when it is read back.
 */
const TRICKY_CODE = [
  'x = 1  ',
  '# %% a comment that begins like a cell marker',
  'if x:',
  '    # %% an indented one',
  '    #%% and one without the space',
  '    y = 0',
  '# %% [markdown]',
  '# %pip install not-a-real-package',
  '# %%time',
  '#!ls',
  '# ! shell?',
  '# +',
  '# -',
  '# why?',
  'ls',
  'text = """',
  '%not_a_magic = in a string',
  '# %% not a marker, in a string',
  '!also not a shell command',
  '"""',
  '# %matplotlib inline \\',
  '# continued',
  'print(x, "\u00e9\u2014\u00b5")',
].join('\n');

const base = {
  datasetId: 'on002718',
  title: 'Famous vs scrambled ERPs in on002718',
  createdAt: '2026-11-10T09:30:00.000Z',
  communityName: 'NEMAR Assistant',
  zarrBase: 'https://zarr.nemar.org',
};

/** Named conversations; each call returns fresh objects, so a test may edit its copy. */
export const NOTEBOOK_CONVERSATIONS = {
  erp: () => ({ ...base, executions: erpExecutions() }),
  // The two real successful runs among the recorded fixtures, with their real errors.
  recordedFixtures: () => ({
    ...base,
    title: 'Recorded runs',
    zarrBase: undefined,
    executions: Object.keys(RUN_CARD_FIXTURES).map((name) => recorded(name)),
  }),
  notOk: () => ({ ...base, executions: [...notOkExecutions(), ...erpExecutions().slice(0, 1)] }),
  onlyFailures: () => ({ ...base, executions: notOkExecutions() }),
  // Lines a percent-format script has to treat with care; see TRICKY_CODE.
  tricky: () => ({ ...base, executions: [run({ description: 'Tricky lines', code: TRICKY_CODE }), run({ code: 'y = 2' })] }),
  empty: () => ({ ...base, executions: [] }),
};
