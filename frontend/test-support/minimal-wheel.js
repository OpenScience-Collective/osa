import { buildStoredZip } from '../osa-zip.js';

/**
 * Build a minimal, real, pure-Python wheel as bytes, for a test that needs
 * micropip to install something without reaching PyPI.
 *
 * What is built is a real ZIP (via `osa-zip.js`, the one STORED-zip writer
 * this repository has) with the wheel's required dist-info files, and
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
