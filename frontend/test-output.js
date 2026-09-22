// Output capture: the caps, the determinism rules, and the drift guard.
//
// The Python here is EXECUTED by a real interpreter rather than matched with a
// regex. A malformed f-string in an earlier generated module passed every regex
// assertion in this repository and only failed when something ran it.
import { buildOutputCaptureSource, RUNTIME_LIMITS, resolveLimits, SERVER_LIMITS } from './osa-output.js';

let passed = 0;
let failed = 0;

function assert(condition, msg) {
  if (condition) {
    passed++;
    console.log(`  ok ${msg}`);
  } else {
    failed++;
    console.error(`\x1b[31m  x FAIL: ${msg}\x1b[0m`);
  }
}

function assertEqual(actual, expected, msg) {
  assert(
    actual === expected,
    `${msg}${actual === expected ? '' : ` (expected ${JSON.stringify(expected)}, got ${JSON.stringify(actual)})`}`
  );
}

/**
 * Run the generated capture module under a real Python, plus a probe script,
 * and return whatever the probe printed as JSON.
 */
function runPython(limits, probe) {
  const source = `${buildOutputCaptureSource(limits)}\n${probe}\n`;
  const proc = Bun.spawnSync(['python3', '-'], { stdin: new TextEncoder().encode(source) });
  const out = proc.stdout.toString().trim();
  const err = proc.stderr.toString().trim();
  if (proc.exitCode !== 0) {
    throw new Error(`python exited ${proc.exitCode}: ${err.split('\n').slice(-4).join(' | ')}`);
  }
  return { out, err };
}

// A real PNG, built byte by byte, so the IHDR read is tested against a file a
// decoder would accept rather than against bytes shaped to pass.
const MAKE_PNG = `
import struct, zlib
def make_png(w, h):
    def chunk(tag, data):
        body = tag + data
        return struct.pack('>I', len(data)) + body + struct.pack('>I', zlib.crc32(body) & 0xffffffff)
    raw = b''.join(b'\\x00' + b'\\xff\\x00\\x00' * w for _ in range(h))
    return (b'\\x89PNG\\r\\n\\x1a\\n' + chunk(b'IHDR', struct.pack('>IIBBBBB', w, h, 8, 2, 0, 0, 0))
            + chunk(b'IDAT', zlib.compress(raw)) + chunk(b'IEND', b''))
`;

console.log('='.repeat(60));
console.log('Browser runtime output capture');
console.log('='.repeat(60));

console.log('\nthe mirrored caps have not drifted from the server');
{
  // These constants live in Python and are mirrored into JavaScript because the
  // worker cannot import them. A comment saying "keep these in sync" has never
  // kept anything in sync, so the Python is read and compared.
  const python = await Bun.file(new URL('../src/core/limits.py', import.meta.url)).text();
  const readConstant = (name) => {
    const match = python.match(new RegExp(`^${name}\\s*=\\s*([0-9_]+)`, 'm'));
    return match ? Number(match[1].replace(/_/g, '')) : null;
  };

  for (const name of Object.keys(SERVER_LIMITS)) {
    const fromPython = readConstant(name);
    assert(fromPython !== null, `${name} is still declared in src/core/limits.py`);
    assertEqual(SERVER_LIMITS[name], fromPython, `${name} matches the server's value`);
  }
}

console.log('\nthe mirrored defaults and floors have not drifted from RuntimeLimits');
{
  const config = await Bun.file(new URL('../src/core/config/community.py', import.meta.url)).text();
  const limitsPy = await Bun.file(new URL('../src/core/limits.py', import.meta.url)).text();
  const body = config.split('class RuntimeLimits(BaseModel):')[1].split('\nclass ')[0];
  const constant = (name) => {
    const match = limitsPy.match(new RegExp(`^${name}\\s*=\\s*([0-9_]+)`, 'm'));
    return match ? Number(match[1].replace(/_/g, '')) : NaN;
  };
  const declared = {};
  for (const m of body.matchAll(/^ {4}(\w+): int = Field\(default=(\w+), ge=(\d+)/gm)) {
    declared[m[1]] = { default: /^\d+$/.test(m[2]) ? Number(m[2]) : constant(m[2]), min: Number(m[3]) };
  }
  assertEqual(JSON.stringify(Object.keys(declared).sort()), JSON.stringify(Object.keys(RUNTIME_LIMITS).sort()),
    'every RuntimeLimits field is mirrored, and nothing else is');
  for (const [field, bounds] of Object.entries(declared)) {
    assertEqual(JSON.stringify(RUNTIME_LIMITS[field]), JSON.stringify(bounds), `${field}'s default and floor match the Python`);
  }
}

console.log('\na community cannot declare a cap the server will refuse');
{
  const defaults = resolveLimits({});
  assertEqual(defaults.stdout_chars, SERVER_LIMITS.MAX_STDOUT_CHARS, 'an empty config gets the server defaults');
  assertEqual(defaults.image_px, 1024, 'and RuntimeLimits\' own default for image_px, which the server does not cap at 1024');

  // The direction that actually costs something. A config promising more than
  // the server accepts makes every result a 422 refused WHOLE, which reads as
  // the browser misbehaving when it was the config lying.
  const greedy = resolveLimits({ stdout_chars: 1_000_000, images: 99, image_px: 99_999, stderr_chars: 1_000_000 });
  assertEqual(greedy.stdout_chars, SERVER_LIMITS.MAX_STDOUT_CHARS, 'an over-large stdout cap is clamped down');
  assertEqual(greedy.stderr_chars, SERVER_LIMITS.MAX_STDERR_CHARS, 'so is stderr');
  assertEqual(greedy.images, SERVER_LIMITS.MAX_IMAGES, 'so is the image count');
  assertEqual(greedy.image_px, SERVER_LIMITS.MAX_IMAGE_EDGE_PX, 'so is the pixel edge');

  const modest = resolveLimits({ stdout_chars: 512, images: 1 });
  assertEqual(modest.stdout_chars, 512, 'a SMALLER declared cap is honored, not raised to the server maximum');
  assertEqual(modest.images, 1, 'and so is a smaller image count');

  assertEqual(resolveLimits({ images: 0 }).images, 0, 'zero images is a real setting, not a missing one');
  assertEqual(resolveLimits({ stdout_chars: 'lots' }).stdout_chars, SERVER_LIMITS.MAX_STDOUT_CHARS,
    'a non-numeric value falls back rather than producing NaN');

  // exec_seconds and memory_mb are NOT clamped against a server constant,
  // because the server neither measures nor enforces either. exec_seconds is
  // the host's own deadline; memory_mb is carried for reporting only, since
  // wasm32 grows memory from inside the instance and an exhaustion aborts it,
  // so there is no moment at which a byte count could be checked.
  assertEqual(resolveLimits({}).exec_seconds, 120, 'exec_seconds defaults to the RuntimeLimits default');
  assertEqual(resolveLimits({}).memory_mb, 1536, 'and so does memory_mb');
  assertEqual(resolveLimits({ exec_seconds: 900 }).exec_seconds, 900,
    'a LONG deadline is honored, since no server constant bounds it');
  assertEqual(resolveLimits({ exec_seconds: 0 }).exec_seconds, 120,
    'a zero deadline falls back rather than making every run time out instantly');
  assertEqual(resolveLimits({ exec_seconds: -5 }).exec_seconds, 120, 'and so does a negative one');
  assertEqual(resolveLimits({ memory_mb: 8 }).memory_mb, 1536,
    'a memory_mb below RuntimeLimits\' floor is invalid and falls back to the default');

  // The floor on the streams is what keeps clipping honest: below it there is
  // no room for the omission marker, and _clip could not say what it dropped.
  assertEqual(resolveLimits({ stdout_chars: 10 }).stdout_chars, SERVER_LIMITS.MAX_STDOUT_CHARS,
    'a stdout cap below the floor is invalid and falls back to the default');
  assertEqual(resolveLimits({ stderr_chars: 255 }).stderr_chars, SERVER_LIMITS.MAX_STDERR_CHARS,
    'one below the floor is still below it');
  assertEqual(resolveLimits({ stdout_chars: 256 }).stdout_chars, 256, 'the floor itself is valid');
  assertEqual(resolveLimits({ image_px: 4 }).image_px, 1024, 'so is image_px, whose floor is 16');
}

console.log('\nstdout and stderr are captured, and restored afterwards');
{
  const { out } = runPython(resolveLimits({}), `
import json, sys
before = (sys.stdout, sys.stderr)
_begin()
print("on stdout")
print("on stderr", file=sys.stderr)
captured = json.loads(_end())
restored = (sys.stdout, sys.stderr) == before
print(json.dumps({"captured": captured, "restored": restored}))
`);
  const result = JSON.parse(out);
  assertEqual(result.captured.stdout, 'on stdout\n', 'stdout is captured');
  assert(result.captured.stderr.includes('on stderr'), 'stderr is captured');
  assert(result.restored, 'the real streams are restored, so a later run does not print into a dead buffer');
}

console.log('\nan exception still returns the output the run produced before it');
{
  const { out } = runPython(resolveLimits({}), `
import json
_begin()
print("this ran before the failure")
captured = json.loads(_end("ValueError: boom"))
print(json.dumps(captured))
`);
  const result = JSON.parse(out);
  assert(result.stdout.includes('this ran before the failure'),
    'stdout survives a failed run, which is usually what explains the exception');
  assert(result.stderr.includes('ValueError: boom'), 'and the error text is carried alongside it');
}

console.log('\nclipping keeps BOTH ends and says what it dropped');
{
  const { out } = runPython(resolveLimits({ stdout_chars: 600 }), `
import json
_begin()
print("HEAD" + "x" * 5000 + "TAIL")
captured = json.loads(_end())
print(json.dumps(captured))
`);
  const result = JSON.parse(out);
  assert(result.stdout.length <= 600, `the cap is respected (got ${result.stdout.length})`);
  assert(result.stdout.startsWith('HEAD'), 'the head is kept');
  assert(result.stdout.trimEnd().endsWith('TAIL'), 'and so is the tail, which a head-only clip would lose');
  assert(/characters omitted/.test(result.stdout), 'and the gap says something was dropped rather than hiding it');
}

console.log('\n_clip never exceeds its limit, including limits too small for the marker');
{
  // resolveLimits keeps real configs above the floor, but _clip is a general
  // function and the branch for tiny limits had no test at all: deleting it let
  // head and tail go negative and returned text many times over the cap.
  const { out } = runPython(resolveLimits({}), `
worst = 0
for limit in range(1, 90):
    for length in (0, 1, limit - 1, limit, limit + 1, 200, 5000):
        if length < 0:
            continue
        clipped = _clip("y" * length, limit)
        worst = max(worst, len(clipped) - limit)
print(worst)
`);
  assertEqual(out, '0', 'across every limit from 1 to 89 and a spread of lengths, nothing is ever over the cap');
}

console.log('\nthe image BYTE cap is enforced separately from the pixel cap');
{
  // A plot can be well inside the pixel limit and still too heavy, which is the
  // one image refusal a real figure is likely to hit. Noise defeats compression,
  // so the PNG is large while only 32 pixels on a side.
  const limits = { ...resolveLimits({}), image_bytes: 500 };
  const { out } = runPython(limits, `
import json, os, struct, zlib
def noise_png(w, h):
    def chunk(tag, data):
        body = tag + data
        return struct.pack(">I", len(data)) + body + struct.pack(">I", zlib.crc32(body) & 0xffffffff)
    raw = b"".join(b"\\x00" + os.urandom(3 * w) for _ in range(h))
    return (b"\\x89PNG\\r\\n\\x1a\\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", w, h, 8, 2, 0, 0, 0))
            + chunk(b"IDAT", zlib.compress(raw)) + chunk(b"IEND", b""))
_begin()
display(noise_png(32, 32))
print(json.dumps(json.loads(_end())))
`);
  const result = JSON.parse(out);
  assertEqual(result.images.length, 0, 'an image over the byte cap is not returned');
  assert(/over the 500-byte limit/.test(result.stderr) && !/px limit/.test(result.stderr),
    `and the note names the byte limit, not the pixel one (got ${JSON.stringify(result.stderr)})`);
}

console.log('\noutput is deterministic, because the prompt cache is a byte-exact prefix match');
{
  const probe = `
import json
_begin()
class Thing:
    pass
print(Thing())
print(json.dumps(json.loads(_end())))
`;
  const first = JSON.parse(runPython(resolveLimits({}), probe).out);
  const second = JSON.parse(runPython(resolveLimits({}), probe).out);

  // Two separate interpreters, so the heap addresses genuinely differ. Comparing
  // two runs inside ONE process could pass on an address that happened to repeat.
  assert(!/0x[0-9a-f]+/i.test(first.stdout), `no heap address survives into stdout (got ${JSON.stringify(first.stdout)})`);
  assertEqual(first.stdout, second.stdout, 'two runs of the same code produce byte-identical stdout');
}

console.log('\nimage caps are enforced in the browser, and each refusal explains itself');
{
  const { out } = runPython(resolveLimits({ images: 2, image_px: 64 }), `${MAKE_PNG}
import json
_begin()
display(make_png(8, 8))
display(make_png(8, 8))
display(make_png(8, 8))
print(json.dumps(json.loads(_end())))
`);
  const result = JSON.parse(out);
  assertEqual(result.images.length, 2, 'the count cap is applied');
  assertEqual(result.stderr.split('only the first').length - 1, 1,
    'and says so ONCE, rather than repeating the same sentence per dropped image');

  const oversize = JSON.parse(runPython(resolveLimits({ image_px: 64 }), `${MAKE_PNG}
import json
_begin()
display(make_png(200, 4))
print(json.dumps(json.loads(_end())))
`).out);
  assertEqual(oversize.images.length, 0, 'an image over the pixel cap is not returned');
  assert(/200x4/.test(oversize.stderr) && /64px/.test(oversize.stderr),
    'and the note names the actual size and the limit, so the code can be fixed');

  // The case that would otherwise cost the run everything: ToolResultImage
  // requires width and height >= 1 and refuses the WHOLE result, so bytes that
  // are not a readable PNG must be caught here.
  const notPng = JSON.parse(runPython(resolveLimits({}), `
import json
_begin()
display(b"definitely not a png")
print(json.dumps(json.loads(_end())))
`).out);
  assertEqual(notPng.images.length, 0, 'bytes that are not a readable PNG are refused rather than sent as 0x0');
  assert(/not a readable PNG/.test(notPng.stderr), 'and say what to pass instead');

  const disabled = JSON.parse(runPython(resolveLimits({ images: 0 }), `${MAKE_PNG}
import json
_begin()
display(make_png(4, 4))
print(json.dumps(json.loads(_end())))
`).out);
  assertEqual(disabled.images.length, 0, 'a community that disables images gets none');
  assert(/images are disabled/.test(disabled.stderr), 'and the code is told why, rather than seeing its figure vanish');
}

console.log('\nan accepted image carries the size the SERVER will measure');
{
  const { out } = runPython(resolveLimits({}), `${MAKE_PNG}
import json
_begin()
display(make_png(7, 5))
print(json.dumps(json.loads(_end())))
`);
  const image = JSON.parse(out).images[0];
  assertEqual(image.width, 7, 'width is read from the IHDR chunk, not from whatever produced the bytes');
  assertEqual(image.height, 5, 'and so is height');
  assertEqual(image.mime, 'image/png', 'the media type is one the server accepts');
  assert(image.data_base64.length > 0 && /^[A-Za-z0-9+/]+=*$/.test(image.data_base64),
    'and the payload is plain base64, which the server validates strictly');
}

console.log('\nmatplotlib is pinned to a backend that exists in a worker');
{
  const { out } = runPython(resolveLimits({}), `
import os
print(os.environ.get("MPLBACKEND", "UNSET"))
`);
  // Pyodide's default backend draws into the page's DOM. A worker has no DOM, so
  // the import succeeds and the first plot fails somewhere unrelated.
  assertEqual(out, 'Agg', 'MPLBACKEND is set before any code can import matplotlib');
}

console.log('\n' + '='.repeat(60));
console.log(`Total: ${passed + failed}   Passed: ${passed}   Failed: ${failed}`);
console.log('='.repeat(60));
if (failed > 0) process.exit(1);
