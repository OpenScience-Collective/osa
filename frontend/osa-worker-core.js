/**
 * The worker's own logic: boot, install, seal, execute (epic #429, phase #431).
 *
 * WHY THIS IS A FUNCTION AND NOT PART OF A TEMPLATE
 *
 * The worker runs from a blob, so its source is a string. This logic used to be
 * written inside a template literal in `buildWorkerSource`, which had two costs.
 * It could only be exercised in a browser, so the half of the runtime that
 * matters most had no CI coverage at all. And a template literal interprets
 * escapes in its own source, so a `\n` or a backtick written there changed the
 * generated worker, which happened four times.
 *
 * `buildWorkerSource` now embeds this function with `Function.prototype.toString`
 * and calls it. An interpolated VALUE is inserted verbatim, so the escape hazard
 * is gone for everything in here, and the same function runs in `bun` against the
 * real Pyodide from npm (see `test-worker-core.js`).
 *
 * THE ONE RULE: THIS FUNCTION MUST BE SELF-CONTAINED
 *
 * It is serialized, so it loses its closure. It may use its parameters and the
 * globals every JavaScript runtime has, and nothing else from this module. A
 * reference to a module-level name would work in every test that imports it and
 * be a ReferenceError inside the worker. The test suite therefore runs the
 * STRINGIFIED copy, never the imported one.
 *
 * @param {object} config - Everything decided at build time, as plain data.
 * @param {string} config.indexURL - Where the Pyodide distribution lives.
 * @param {string[]} config.preload - Pyodide packages loaded at boot.
 * @param {string[]} config.allowInstall - Wheels installed at boot, deps false.
 * @param {string[]} config.indexUrls - Package indexes micropip may use at boot.
 * @param {string[]} config.fetchAllow - What executed code may reach once sealed.
 * @param {Object<string, object>} config.lockPackages - Lock entries the community adds
 *   to Pyodide's own lock, each file_name already an absolute URL. Often empty.
 * @param {string} config.prelude - The community's Python, run after the seal; '' for none.
 * @param {{helpers: string, outputCapture: string, dataClient: string, namespaceSeal: string}} config.python
 *   Generated Python sources, run in the order boot() documents.
 * @param {object} env - The platform, which differs between a browser worker and a test.
 * @param {(indexURL: string, options?: object) => Promise<object>} env.load - Load Pyodide,
 *   with extra loadPyodide options when there is a lock to hand it.
 * @param {(indexURL: string) => Promise<object>} [env.stockLock] - Read the Pyodide
 *   distribution's own lock. Needed only when there are lock packages to merge into it.
 * @param {(prefixes: string[]) => void} env.seal - Narrow the JS egress allowlist.
 * @param {(message: object) => void} env.send - Post a protocol message to the host.
 * @returns {{boot: () => Promise<void>, execute: (data: object) => Promise<void>, handle: (data: object) => Promise<void>}}
 */
export function createWorkerRuntime(config, env) {
  // A wasm out-of-memory aborts the instance without a Python exception, so it
  // can only be recognized from the JavaScript error that escapes. A Python
  // MemoryError, which CAN be caught, is classified in the Python harness.
  const OOM_PATTERN =
    /out of memory|Cannot enlarge memory|memory access out of bounds|Aborted\(OOM\)|RangeError: Array buffer allocation failed/i;

  // send is checked before anything else, because it is how every other problem
  // is reported. Without it the only signal left is an exception at startup.
  if (!env || typeof env.send !== 'function') {
    throw new TypeError('createWorkerRuntime needs env.send to report anything');
  }

  // The rest of the shape, checked by name before boot touches any of it. A
  // missing piece used to surface as a generic runtime error from somewhere
  // inside loadPyodide or runPython, indistinguishable from a real failure.
  function configProblem() {
    const isStringList = (value) => Array.isArray(value) && value.every((item) => typeof item === 'string');
    if (typeof env.load !== 'function') return 'env.load is not a function';
    if (typeof env.seal !== 'function') return 'env.seal is not a function';
    if (!config || typeof config !== 'object') return 'config is missing';
    if (typeof config.indexURL !== 'string' || config.indexURL === '') return 'config.indexURL is not a non-empty string';
    for (const key of ['preload', 'allowInstall', 'indexUrls', 'fetchAllow']) {
      if (!isStringList(config[key])) return `config.${key} is not a list of strings`;
    }
    const lock = config.lockPackages;
    if (!lock || typeof lock !== 'object' || Array.isArray(lock)) return 'config.lockPackages is not an object';
    if (Object.keys(lock).length > 0 && typeof env.stockLock !== 'function') {
      return 'env.stockLock is not a function, and there are lock packages to merge';
    }
    if (typeof config.prelude !== 'string') return 'config.prelude is not a string';
    for (const key of ['helpers', 'outputCapture', 'dataClient', 'namespaceSeal']) {
      if (!config.python || typeof config.python[key] !== 'string' || config.python[key] === '') {
        return `config.python.${key} is not a non-empty string`;
      }
    }
    return null;
  }

  let pyodide = null;
  // Created once and reused, so variables survive across turns: a reader who
  // computes something in one message and plots it in the next is the normal
  // case. It is a namespace of its own rather than pyodide.globals, so the host
  // helpers in `internals` are unreachable from executed code, which could
  // otherwise redefine the import gate that decides whether the NEXT execution
  // may run.
  let userNamespace = null;
  let internals = null;
  let busy = false;

  // The whole boot's step budget, and the step currently starting. Both are
  // set once at the top of boot(), before anything else runs, so `steps`
  // never changes mid-boot and a caller's progress bar never has to re-read
  // a shrinking or growing total. `step` is advanced by nextStep() immediately
  // before each unit of work begins (each preload name, micropip, each
  // allow_install entry, the prelude); the interpreter is step 1 from the
  // start and `runtime_loaded` deliberately reports it again without
  // advancing, since that message is the SAME step finishing, not a new one.
  let steps = 0;
  let step = 0;

  const describe = (err) => String((err && err.message) || err);

  function nextStep() {
    step += 1;
  }

  function sendProgress(fields) {
    env.send(Object.assign({ type: 'progress', step, steps }, fields));
  }

  function call(name, ...args) {
    const fn = internals.get(name);
    try {
      return fn(...args);
    } finally {
      fn.destroy();
    }
  }

  // The community's entries go INTO Pyodide's own lock rather than beside it, so
  // one loadPackage resolves an overlay package and the distribution packages it
  // depends on, and Pyodide checks every wheel's sha256 as it loads it. An entry
  // may add a package and never replace one: a replaced entry would swap out a
  // compiled package the distribution built for its own ABI.
  function mergeLock(stock, overlay) {
    if (!stock || typeof stock.packages !== 'object' || stock.packages === null) {
      throw new Error("the Pyodide distribution's lock has no packages");
    }
    const packages = Object.assign({}, stock.packages);
    for (const name of Object.keys(overlay)) {
      if (Object.prototype.hasOwnProperty.call(packages, name)) {
        throw new Error(`the community's lock entry ${name} would replace the Pyodide distribution's own`);
      }
      packages[name] = overlay[name];
    }
    return { info: stock.info, packages };
  }

  async function loadPackages(names, phase) {
    // loadPackage throws for an unknown name, but a failure partway through a
    // download is reported through errorCallback and the promise can still
    // resolve. A boot that says ready with a package missing surfaces later as
    // a denied import naming the package, which reads as a policy decision when
    // it was a network failure. So any reported error fails the boot here.
    for (let i = 0; i < names.length; i++) {
      nextStep();
      sendProgress({ phase, package: names[i] });
      const errors = [];
      await pyodide.loadPackage(names[i], {
        messageCallback: () => {},
        errorCallback: (message) => errors.push(String(message)),
        // Pyodide's default, stated because the lock overlay rests on it: each
        // wheel is fetched with its sha256 as fetch integrity. The browser harness
        // fails in CI if this is ever off.
        checkIntegrity: true,
      });
      if (errors.length > 0) {
        throw new Error(`package ${names[i]} did not load: ${errors.join('; ')}`);
      }
    }
  }

  async function boot() {
    const problem = configProblem();
    if (problem !== null) {
      env.send({ type: 'error', kind: 'config', message: `the runtime was built with an invalid configuration: ${problem}` });
      return;
    }
    try {
      // One for the interpreter, one per preload name, and, when
      // allow_install is non-empty, one for micropip plus one per
      // allow_install entry, and one for the prelude when there is one.
      steps =
        1 +
        config.preload.length +
        (config.allowInstall.length > 0 ? 1 + config.allowInstall.length : 0) +
        (config.prelude !== '' ? 1 : 0);
      step = 1; // the interpreter, starting now

      sendProgress({ phase: 'loading_runtime' });
      let loadOptions;
      if (Object.keys(config.lockPackages).length > 0) {
        // Reported apart from a failure of Pyodide itself, since the fault is in
        // the distribution's lock or the community's overlay, and each message
        // names which.
        let stock;
        try {
          stock = await env.stockLock(config.indexURL);
        } catch (err) {
          env.send({ type: 'error', kind: 'lock', message: `the Pyodide distribution's lock could not be read: ${describe(err)}` });
          return;
        }
        let lockFileContents;
        try {
          lockFileContents = mergeLock(stock, config.lockPackages);
        } catch (err) {
          env.send({ type: 'error', kind: 'lock', message: describe(err) });
          return;
        }
        // packageBaseUrl because the stock entries name their wheels relative
        // to the distribution, and Pyodide stops inferring it once it is handed
        // a lock rather than a URL to one.
        loadOptions = { lockFileContents, packageBaseUrl: config.indexURL };
      }
      pyodide = await env.load(config.indexURL, loadOptions);
      // Still step 1: this is the interpreter step finishing, not a new one
      // starting, so the widget's progress bar has something to show for it
      // without appearing to move backward when the next step begins.
      sendProgress({ phase: 'runtime_loaded' });

      await loadPackages(config.preload, 'loading_package');

      // EVERY package this runtime will ever have is installed here, during
      // boot. Installing on demand would mean the wheel index had to stay
      // reachable from executed code, which is the opposite of sealing.
      //
      // deps is false for every entry, so allow_install is a complete, ordered
      // list rather than a resolver seed: a resolver that picks the package set
      // at runtime breaks the byte-identical results the prompt cache depends
      // on. A pinned wheel with its sha256 belongs in the lock overlay instead,
      // which preload resolves above; that is how NEMAR ships zarr, whose
      // numcodecs>=0.14 pin micropip cannot satisfy from PyPI.
      if (config.allowInstall.length > 0) {
        await loadPackages(['micropip'], 'loading_package');
        const micropip = pyodide.pyimport('micropip');
        try {
          for (let i = 0; i < config.allowInstall.length; i++) {
            nextStep();
            sendProgress({ phase: 'installing', package: config.allowInstall[i] });
            const options = { deps: false };
            if (config.indexUrls.length > 0) options.index_urls = config.indexUrls;
            await micropip.install.callKwargs(config.allowInstall[i], options);
          }
        } finally {
          micropip.destroy();
        }
      }

      // The internal namespace: the import gate and the execution harness. Both
      // close over pyodide.code, so both run BEFORE the namespace seal blocks it.
      internals = pyodide.globals.get('dict')();
      pyodide.runPython(config.python.helpers, { globals: internals });
      pyodide.runPython(config.python.outputCapture, { globals: internals });

      userNamespace = pyodide.globals.get('dict')();
      userNamespace.set('__name__', '__main__');

      // _save_file and _validate_before_prefix are the bridges
      // osa.save_script/osa.save_artifact close over (#433): both live in
      // the INTERNAL namespace (defined by outputCapture, beside _pending),
      // so they are handed across the same way `display` is a few lines
      // below, rather than left reachable by name in the namespace executed
      // code runs in.
      const saveFile = internals.get('_record_saved_file');
      userNamespace.set('_save_file', saveFile);
      saveFile.destroy();
      const validateBeforePrefix = internals.get('_validate_relative_and_shape');
      userNamespace.set('_validate_before_prefix', validateBeforePrefix);
      validateBeforePrefix.destroy();

      // The sanctioned network route, installed before the seal because it needs
      // the js bridge the seal removes. Sealing without it leaves executed code
      // with no way to read anything; that was measured, not assumed.
      pyodide.runPython(config.python.dataClient, { globals: userNamespace });

      // display() is the ONE harness function executed code can reach.
      const display = internals.get('display');
      userNamespace.set('display', display);
      display.destroy();

      // Python-side capability removal, then JS-side egress narrowing. Both
      // happen before the first executable statement exists. The egress seal is
      // ONE-SHOT: moving either later widens what executed code can reach and is
      // a decision, not a refactor.
      pyodide.runPython(config.python.namespaceSeal);
      env.seal(config.fetchAllow);

      // The community's own setup, AFTER the seal, so it has exactly the
      // privileges executed code has and no more. A prelude that fails leaves a
      // runtime every later execution would fail in, for a reason naming the
      // wrong cause, so the boot fails instead and says so.
      if (config.prelude !== '') {
        nextStep();
        sendProgress({ phase: 'prelude' });
        try {
          await pyodide.runPythonAsync(config.prelude, { globals: userNamespace });
        } catch (err) {
          env.send({ type: 'error', kind: 'prelude', message: `the community's prelude failed: ${describe(err).slice(-1500)}` });
          return;
        }
      }

      env.send({ type: 'ready', version: pyodide.version });
    } catch (err) {
      env.send({ type: 'error', kind: 'runtime', message: describe(err) });
    }
  }

  async function execute(data) {
    const callId = data.call_id;
    const started = Date.now();
    const reply = (status, fields) => {
      busy = false;
      env.send(
        Object.assign(
          {
            type: 'result',
            call_id: callId,
            status,
            stdout: '',
            stderr: '',
            summary: '',
            images: [],
            artifacts: [],
          },
          fields,
          { elapsed_ms: Date.now() - started }
        )
      );
    };

    if (pyodide === null || internals === null) {
      reply('error', { stderr: '[runtime] the runtime is not booted' });
      return;
    }
    // One execution at a time. Two concurrent runs would share userNamespace
    // and interleave their output, and the second would report the first's.
    if (busy) {
      reply('error', { stderr: '[runtime] another execution is already running' });
      return;
    }
    busy = true;

    const code = typeof data.code === 'string' ? data.code : '';

    // The import gate. loadPackagesFromImports is deliberately NOT used: it
    // fetches from the CDN, which is unreachable once sealed, so it would turn an
    // unavailable package into an egress error naming the wrong cause.
    let missing;
    try {
      const found = call('_unavailable_imports', code);
      missing = found.toJs();
      found.destroy();
    } catch (err) {
      reply('error', { stderr: `[runtime] the import check failed: ${describe(err)}` });
      return;
    }
    if (missing.length > 0) {
      // Phase 1's ResultStatus has no denied_import member, so the status is
      // `denied` and the machine-readable reason travels in stderr. Adding a
      // status would be a server contract change.
      //
      // The names come from the code, which the model wrote and which fetched
      // content can steer, so the list is bounded: at most 20 names of at most
      // 100 characters, then a count. The host bounds the whole field as well.
      const shown = missing.slice(0, 20).map((name) => (name.length > 100 ? `${name.slice(0, 100)}...` : name));
      const listed = shown.join(', ') + (missing.length > 20 ? `, and ${missing.length - 20} more` : '');
      reply('denied', {
        stderr: `denied_import: ${listed}`,
        summary:
          `Not run. This runtime has no ${listed}. Only packages the community ` +
          'installed at startup are available, and nothing is installed on demand.',
      });
      return;
    }

    // The whole run happens in Python, inside the harness: capture, execution,
    // exception classification and the summary. A Python exception therefore
    // never reaches this catch. What does is the instance itself failing, which
    // is what an out-of-memory abort looks like from here.
    let captured;
    try {
      const run = internals.get('_execute');
      try {
        captured = JSON.parse(await run(callId, code, userNamespace));
      } finally {
        run.destroy();
      }
    } catch (err) {
      const text = describe(err);
      const status = OOM_PATTERN.test(text) ? 'oom' : 'error';
      // Bounded here rather than in Python, since Python may be the thing that
      // failed. The tail is kept because that is where an abort names itself.
      reply(status, { stderr: `[runtime] the execution failed outside Python: ${text}`.slice(-4000) });
      return;
    }

    reply(captured.status, {
      stdout: captured.stdout,
      stderr: captured.stderr,
      summary: captured.summary,
      images: captured.images,
      artifacts: captured.artifacts,
      // Kept in the browser for get_full_output and stripped by the host
      // before anything is sent to the server.
      full: captured.full,
      // Explicitly saved files, base64 bytes and all (#433). Never part of
      // ClientToolResult: the host persists these to IndexedDB and strips
      // the field the same way it strips `full`, before anything is sent.
      files: captured.files,
    });
  }

  async function handle(data) {
    if (data.type === 'boot') {
      await boot();
      return;
    }
    if (data.type === 'execute') {
      await execute(data);
      return;
    }
    env.send({ type: 'error', kind: 'protocol', message: `unexpected message: ${data.type}` });
  }

  return { boot, execute, handle };
}
