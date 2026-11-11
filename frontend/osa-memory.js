/**
 * The browser memory budget (E5.1): the pure logic behind the Settings field, the heap
 * line on a run card and the plan card's warning, with no DOM and no widget state.
 *
 * The reader picks how much of their browser's memory one step of an analysis may use.
 * The widget sends the choice with each request as `page_context.memory_budget_mb`, the
 * server states it in the prompt so the model sizes its reads (src/api/routers/community.py
 * PageContext, which accepts only these four sizes), and after each run the widget reports
 * the WebAssembly heap against it.
 *
 * The widget is a classic script and loads this module only through the runtime bundle
 * (OSARuntime.memory), which comes later than its own first paint. What it must know before
 * then, to read a saved setting, it holds as its own copy of `MEMORY_BUDGETS_MB` and
 * `DEFAULT_MEMORY_BUDGET_MB`; test-widget-memory-budget.js fails when the two differ.
 *
 * The heap is the size of the Python runtime's WebAssembly memory after a run
 * (osa-worker-core.js). That memory only grows, so a run's figure includes what earlier
 * runs left, the interpreter itself (about 20 MB) and the packages loaded; it is the
 * number that decides whether the tab survives, which is why it is the one reported.
 */

/** The budgets offered per step, in MB. The server accepts exactly these. */
export const MEMORY_BUDGETS_MB = Object.freeze([256, 512, 1024, 2048]);

/** What a reader who never chose gets, and what an unusable stored value reads as. */
export const DEFAULT_MEMORY_BUDGET_MB = 512;

/** The most a stored heap size may be: a 32-bit WebAssembly memory cannot pass 4 GB. */
export const MAX_HEAP_MB = 4096;

const BYTES_PER_MB = 1024 * 1024;

/** Whether `value` is one of the offered budgets, as a number. */
export function isBudgetMb(value) {
  return Number.isInteger(value) && MEMORY_BUDGETS_MB.includes(value);
}

/** A budget as stored: an offered size, else the default. */
export function normalizeBudgetMb(value) {
  return isBudgetMb(value) ? value : DEFAULT_MEMORY_BUDGET_MB;
}

/** A budget as a Settings choice reads: 256 MB, 1 GB. Everywhere else uses `mbLabel`. */
export function sizeLabel(mb) {
  return mb >= 1024 && mb % 1024 === 0 ? `${mb / 1024} GB` : `${mb} MB`;
}

/** The size of a heap in whole MB, from the bytes the worker measured; null when it did not. */
export function heapMbFromBytes(bytes) {
  if (typeof bytes !== 'number' || !Number.isFinite(bytes) || bytes <= 0) return null;
  return Math.max(1, Math.min(Math.round(bytes / BYTES_PER_MB), MAX_HEAP_MB));
}

/** A stored heap size: a whole number of MB from 1 to MAX_HEAP_MB, else null. */
export function normalizeHeapMb(value) {
  return Number.isInteger(value) && value >= 1 ? Math.min(value, MAX_HEAP_MB) : null;
}

/** A budget or a heap as every line of text writes it: 512 MB, 1024 MB (never "1 GB"). */
export function mbLabel(mb) {
  return `${mb} MB`;
}

/**
 * What a run card says about a run's heap, or null when the run has no figure.
 *
 * `exceeded` is when the heap is past the budget. `grew` is when this run is the one that
 * took it there or further, and `held` is when an earlier run did and this one added
 * nothing: the heap only grows, so after one large read every later run, `print(1)`
 * included, reports the same over-budget figure, and only the first should be blamed.
 * `previousMb` is the heap the run before it in the chat reported, or null when there was
 * none. A heap that is smaller than the previous one belongs to a runtime started since
 * (a reload, a restart), whose first run to report it is the one that grew it.
 * `budgetMb` is the budget the run was under, which a run keeps so a card read later does
 * not change with Settings.
 *
 * @param {unknown} heapMb
 * @param {unknown} budgetMb
 * @param {unknown} [previousMb]
 * @returns {{text: string, exceeded: boolean, grew: boolean, held: boolean, heapMb: number, budgetMb: number} | null}
 */
export function heapReport(heapMb, budgetMb, previousMb = null) {
  const heap = normalizeHeapMb(heapMb);
  if (heap === null) return null;
  const budget = normalizeBudgetMb(budgetMb);
  const previous = normalizeHeapMb(previousMb);
  const exceeded = heap > budget;
  const held = exceeded && previous !== null && heap === previous;
  return {
    text: `Heap ${mbLabel(heap)} of ${mbLabel(budget)}`,
    exceeded,
    grew: exceeded && !held,
    held,
    heapMb: heap,
    budgetMb: budget,
  };
}

/**
 * The line under a run whose heap is past its budget, or ''. A run that took the heap
 * there names its step; `step` is the plan step the run was ({step, title}), or null for a
 * run no plan covers. A run that only reports what an earlier one left says so instead of
 * blaming itself.
 */
export function overBudgetWarning(report, step) {
  if (!report || !report.exceeded) return '';
  if (report.held) {
    return `The runtime is still holding ${mbLabel(report.heapMb)} from earlier steps; its memory does not shrink between runs.`;
  }
  const what = step && Number.isInteger(step.step) && step.step > 0
    ? `Step ${step.step}${step.title ? `, ${step.title},` : ''}`
    : 'This run';
  return `${what} used more than the ${mbLabel(report.budgetMb)} budget. `
    + 'Ask for smaller pieces, or raise the budget in Settings.';
}

/**
 * The plan card's warning when its largest step is expected to pass the budget, or ''.
 * Only a figure the model gave counts: no estimate, nothing to warn about.
 */
export function planBudgetWarning(estPeakMb, budgetMb) {
  const budget = normalizeBudgetMb(budgetMb);
  if (!Number.isFinite(estPeakMb) || estPeakMb <= budget) return '';
  return `That is more than your ${mbLabel(budget)} memory budget. `
    + 'Edit the steps to read in smaller pieces, or raise the budget in Settings.';
}
