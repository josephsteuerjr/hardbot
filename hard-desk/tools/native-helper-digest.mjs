import { createHash } from 'node:crypto';

// The legacy C# compiler stamps the PE creation time and a random module ID.
// Ignore only those two build values; every other byte remains audited.
// COFF TimeDateStamp: https://learn.microsoft.com/en-us/windows/win32/debug/pe-format#coff-file-header-object-and-image
export function nativeHelperDigest(input) {
  const bytes = Buffer.from(input);
  const pe = bytes.readUInt32LE(0x3c);
  if (bytes.toString('ascii', pe, pe + 4) !== 'PE\0\0') throw new Error('Invalid helper PE');
  bytes.fill(0, pe + 8, pe + 12);
  const sections = bytes.readUInt16LE(pe + 6), optional = pe + 24;
  const magic = bytes.readUInt16LE(optional);
  if (magic !== 0x10b && magic !== 0x20b) throw new Error('Invalid helper optional header');
  const directory = optional + (magic === 0x10b ? 96 : 112);
  const sectionTable = optional + bytes.readUInt16LE(pe + 20);
  const offsetOf = rva => {
    for (let n = 0; n < sections; n++) {
      const at = sectionTable + n * 40, address = bytes.readUInt32LE(at + 12);
      const size = Math.max(bytes.readUInt32LE(at + 8), bytes.readUInt32LE(at + 16));
      if (rva >= address && rva < address + size) return bytes.readUInt32LE(at + 20) + rva - address;
    }
    throw new Error('Invalid helper metadata address');
  };
  const cli = offsetOf(bytes.readUInt32LE(directory + 14 * 8));
  const metadata = offsetOf(bytes.readUInt32LE(cli + 8));
  if (bytes.toString('ascii', metadata, metadata + 4) !== 'BSJB') throw new Error('Invalid helper metadata');
  const flags = metadata + 16 + bytes.readUInt32LE(metadata + 12);
  const streams = bytes.readUInt16LE(flags + 2);
  let at = flags + 4, guidFound = false;
  for (let n = 0; n < streams; n++) {
    const offset = bytes.readUInt32LE(at), size = bytes.readUInt32LE(at + 4);
    const end = bytes.indexOf(0, at + 8);
    if (end < 0 || end - at > 40) throw new Error('Invalid helper stream');
    const name = bytes.toString('ascii', at + 8, end);
    if (name === '#GUID') {
      if (guidFound || size !== 16 || metadata + offset + size > bytes.length)
        throw new Error('Unexpected helper GUID metadata');
      bytes.fill(0, metadata + offset, metadata + offset + size);
      guidFound = true;
    }
    at = (end + 4) & ~3;
  }
  if (!guidFound) throw new Error('Helper module ID missing');
  return createHash('sha256').update(bytes).digest('hex');
}
