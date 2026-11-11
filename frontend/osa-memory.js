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

/** A size in MB as a reader reads it: 256 MB, 1 GB. */
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

/**
 * What a run card says about a run's heap, or null when the run has no figure.
 * `exceeded` is when the heap passed the budget; `budgetMb` is the budget the run was
 * under, which a run keeps so a card read later does not change with Settings.
 *
 * @param {unknown} heapMb
 * @param {unknown} budgetMb
 * @returns {{text: string, exceeded: boolean, heapMb: number, budgetMb: number} | null}
 */
export function heapReport(heapMb, budgetMb) {
  const heap = normalizeHeapMb(heapMb);
  if (heap === null) return null;
  const budget = normalizeBudgetMb(budgetMb);
  return { text: `Heap ${heap} MB of ${budget} MB`, exceeded: heap > budget, heapMb: heap, budgetMb: budget };
}

/**
 * The line under a run whose heap passed its budget, naming the step. `step` is the plan
 * step the run was ({step, title}), or null for a run no plan covers.
 */
export function overBudgetWarning(report, step) {
  if (!report || !report.exceeded) return '';
  const what = step && Number.isInteger(step.step) && step.step > 0
    ? `Step ${step.step}${step.title ? `, ${step.title},` : ''}`
    : 'This run';
  return `${what} used more than the ${sizeLabel(report.budgetMb)} budget. `
    + 'Ask for smaller pieces, or raise the budget in Settings.';
}

/**
 * The plan card's warning when its largest step is expected to pass the budget, or ''.
 * Only a figure the model gave counts: no estimate, nothing to warn about.
 */
export function planBudgetWarning(estPeakMb, budgetMb) {
  const budget = normalizeBudgetMb(budgetMb);
  if (!Number.isFinite(estPeakMb) || estPeakMb <= budget) return '';
  return `That is more than your ${sizeLabel(budget)} memory budget. `
    + 'Edit the steps to read in smaller pieces, or raise the budget in Settings.';
}
