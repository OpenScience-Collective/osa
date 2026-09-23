/**
 * One small STORED-zip writer (epic #429, phase #433).
 *
 * Two callers need a real zip with no external tool and no compression
 * library: `test-support/minimal-wheel.js` (a wheel is a zip) and
 * `osa-workspace.js` (a workspace export). Both used to hand-roll the same
 * byte layout; this is the one implementation, so a fix to one no longer
 * risks leaving the other's copy wrong.
 *
 * STORED (uncompressed) entries need only a CRC32 and the byte lengths, so
 * the whole archive is built from `DataView` writes with nothing beyond what
 * the ZIP local-file-header, central-directory and end-of-central-directory
 * records require. `Bun.hash.crc32` gives the checksum directly under Bun;
 * `crc32` below is a portable fallback for the one caller (the pytest that
 * spawns a Bun script) that still runs inside the same process but wants a
 * value it can also compute without Bun, and for the day this module runs
 * somewhere Bun is not `globalThis`.
 */

// The standard CRC-32 table (IEEE 802.3), computed once. Written out as a
// loop rather than a literal 256-entry array so the polynomial is legible.
const CRC_TABLE = (() => {
  const table = new Uint32Array(256);
  for (let n = 0; n < 256; n++) {
    let c = n;
    for (let k = 0; k < 8; k++) {
      c = c & 1 ? 0xedb88320 ^ (c >>> 1) : c >>> 1;
    }
    table[n] = c >>> 0;
  }
  return table;
})();

/**
 * CRC-32 of a byte array, portable (no Bun dependency).
 *
 * @param {Uint8Array} data
 * @returns {number} Unsigned 32-bit checksum.
 */
export function crc32(data) {
  let crc = 0xffffffff;
  for (let i = 0; i < data.length; i++) {
    crc = CRC_TABLE[(crc ^ data[i]) & 0xff] ^ (crc >>> 8);
  }
  return (crc ^ 0xffffffff) >>> 0;
}

function crc32Of(data) {
  // Bun's native CRC32 when it is available (both current callers run under
  // Bun); the portable table above otherwise, so this module works wherever
  // it is imported, not only under Bun.
  if (typeof Bun !== 'undefined' && Bun.hash && typeof Bun.hash.crc32 === 'function') {
    return Bun.hash.crc32(data) >>> 0;
  }
  return crc32(data);
}

/**
 * A DOS date+time pair (the only timestamp format a ZIP local/central header
 * carries), from a real `Date`. Clamped to 1980, DOS's own epoch: a `Date`
 * earlier than that has no representation and would otherwise wrap to a
 * nonsense year.
 *
 * @param {Date} date
 * @returns {{time: number, date: number}}
 */
export function dosDateTime(date) {
  const year = Math.max(1980, date.getFullYear());
  const time =
    (date.getHours() << 11) | (date.getMinutes() << 5) | (Math.floor(date.getSeconds() / 2) & 0x1f);
  const dosDate = ((year - 1980) << 9) | ((date.getMonth() + 1) << 5) | date.getDate();
  return { time: time & 0xffff, date: dosDate & 0xffff };
}

function encodeEntry(name, content) {
  const encoder = new TextEncoder();
  const nameBytes = encoder.encode(name);
  const data = typeof content === 'string' ? encoder.encode(content) : new Uint8Array(content);
  return { nameBytes, data };
}

/**
 * Build a ZIP archive of STORED (uncompressed) entries.
 *
 * @param {Record<string, string|Uint8Array|{data: string|Uint8Array, mtime?: Date}>} files -
 *   Map of archive path to its content, either directly or with a `mtime`
 *   (default: now). Iteration order is insertion order, which is also the
 *   archive's entry order, so a deterministic caller (e.g. a sorted object)
 *   gets deterministic bytes.
 * @returns {Uint8Array}
 */
export function buildStoredZip(files) {
  const entries = Object.entries(files).map(([name, value]) => {
    const hasMeta = value && typeof value === 'object' && !(value instanceof Uint8Array) && 'data' in value;
    const content = hasMeta ? value.data : value;
    const mtime = hasMeta && value.mtime instanceof Date ? value.mtime : new Date();
    const { nameBytes, data } = encodeEntry(name, content);
    return { nameBytes, data, crc: crc32Of(data), ...dosDateTime(mtime) };
  });

  const localParts = [];
  const centralParts = [];
  let offset = 0;

  for (const entry of entries) {
    const { nameBytes, data, crc, time, date } = entry;

    const local = new DataView(new ArrayBuffer(30));
    local.setUint32(0, 0x04034b50, true);
    local.setUint16(4, 20, true); // version needed
    local.setUint16(6, 0, true); // flags
    local.setUint16(8, 0, true); // method: stored
    local.setUint16(10, time, true);
    local.setUint16(12, date, true);
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
    central.setUint16(12, time, true);
    central.setUint16(14, date, true);
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
  end.setUint16(8, entries.length, true);
  end.setUint16(10, entries.length, true);
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
