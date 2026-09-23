/**
 * Build a minimal, real, pure-Python wheel as bytes, for a test that needs
 * micropip to install something without reaching PyPI.
 *
 * Hand-assembled rather than shelled out to `zip`: STORED (uncompressed)
 * entries need only a CRC32, which `Bun.hash.crc32` gives directly, so the
 * whole file is built with no external tool and no compression library.
 * What is built is a real ZIP with the wheel's required dist-info files, and
 * `pyodide.loadPackage`/micropip install it the same way they install any
 * other wheel; nothing about the install path is a stand-in.
 *
 * @param {{distribution: string, version: string, modules?: Record<string, string>}} spec
 *   `distribution` and `version` name the wheel; `modules` maps a relative
 *   file path (e.g. "osatestpkg/__init__.py") to its source.
 * @returns {{fileName: string, bytes: Uint8Array}}
 */
export function buildMinimalWheel({ distribution, version, modules = {} }) {
  const distInfo = `${distribution}-${version}.dist-info`;
  const files = {
    ...modules,
    [`${distInfo}/METADATA`]: `Metadata-Version: 2.1\nName: ${distribution}\nVersion: ${version}\n`,
    [`${distInfo}/WHEEL`]: 'Wheel-Version: 1.0\nGenerator: osa-test\nRoot-Is-Purelib: true\nTag: py3-none-any\n',
  };
  // RECORD lists every other file with no hash or size, which the wheel spec
  // allows: a consumer that wants the hash can always recompute it from the
  // file it just unpacked.
  const record = Object.keys(files)
    .map((name) => `${name},,`)
    .concat(`${distInfo}/RECORD,,`)
    .join('\n');
  files[`${distInfo}/RECORD`] = `${record}\n`;

  const bytes = buildStoredZip(files);
  return { fileName: `${distribution}-${version}-py3-none-any.whl`, bytes };
}

/** A ZIP archive of STORED (uncompressed) entries, built by hand. */
function buildStoredZip(files) {
  const encoder = new TextEncoder();
  const localParts = [];
  const centralParts = [];
  let offset = 0;

  for (const [name, content] of Object.entries(files)) {
    const nameBytes = encoder.encode(name);
    const data = typeof content === 'string' ? encoder.encode(content) : content;
    const crc = Bun.hash.crc32(data);

    const local = new DataView(new ArrayBuffer(30));
    local.setUint32(0, 0x04034b50, true);
    local.setUint16(4, 20, true); // version needed
    local.setUint16(6, 0, true); // flags
    local.setUint16(8, 0, true); // method: stored
    local.setUint16(10, 0, true); // mod time
    local.setUint16(12, 0x21, true); // mod date (a valid, arbitrary DOS date)
    local.setUint32(14, crc, true);
    local.setUint32(18, data.length, true);
    local.setUint32(22, data.length, true);
    local.setUint16(26, nameBytes.length, true);
    local.setUint16(28, 0, true); // extra length
    localParts.push(new Uint8Array(local.buffer), nameBytes, data);

    const central = new DataView(new ArrayBuffer(46));
    central.setUint32(0, 0x02014b50, true);
    central.setUint16(4, 20, true); // version made by
    central.setUint16(6, 20, true); // version needed
    central.setUint16(8, 0, true); // flags
    central.setUint16(10, 0, true); // method
    central.setUint16(12, 0, true); // mod time
    central.setUint16(14, 0x21, true); // mod date
    central.setUint32(16, crc, true);
    central.setUint32(20, data.length, true);
    central.setUint32(24, data.length, true);
    central.setUint16(28, nameBytes.length, true);
    central.setUint16(30, 0, true); // extra length
    central.setUint16(32, 0, true); // comment length
    central.setUint16(34, 0, true); // disk number
    central.setUint16(36, 0, true); // internal attrs
    central.setUint32(38, 0, true); // external attrs
    central.setUint32(42, offset, true); // local header offset
    centralParts.push(new Uint8Array(central.buffer), nameBytes);

    offset += 30 + nameBytes.length + data.length;
  }

  const localBytes = concat(localParts);
  const centralBytes = concat(centralParts);

  const end = new DataView(new ArrayBuffer(22));
  end.setUint32(0, 0x06054b50, true);
  end.setUint16(4, 0, true);
  end.setUint16(6, 0, true);
  end.setUint16(8, Object.keys(files).length, true);
  end.setUint16(10, Object.keys(files).length, true);
  end.setUint32(12, centralBytes.length, true);
  end.setUint32(16, localBytes.length, true);
  end.setUint16(20, 0, true);

  return concat([localBytes, centralBytes, new Uint8Array(end.buffer)]);
}

function concat(chunks) {
  const total = chunks.reduce((n, c) => n + c.length, 0);
  const out = new Uint8Array(total);
  let at = 0;
  for (const chunk of chunks) {
    out.set(chunk, at);
    at += chunk.length;
  }
  return out;
}
