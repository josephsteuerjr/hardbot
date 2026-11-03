import { existsSync } from "node:fs";
import { execFileSync } from "node:child_process";
import { join } from "node:path";

const HEADER = Buffer.from("CRM-DPAPI-1\n");
function transform(mode: "seal" | "unseal", bytes: Buffer): Buffer {
  const packaged = join(process.resourcesPath, "process-check.exe");
  const helper = existsSync(packaged)
    ? packaged
    : join(__dirname, "..", "installer", "bin", "process-check.exe");
  try {
    return execFileSync(helper, [mode], {
      input: bytes,
      windowsHide: true,
      timeout: 15000,
      maxBuffer: 17 * 1024 * 1024,
    });
  } catch {
    throw new Error(
      "Windows не подтвердил шифрование данных. Исходный файл сохранён.",
    );
  }
}
export function directSeal(text: string): Buffer {
  return Buffer.concat([HEADER, transform("seal", Buffer.from(text, "utf8"))]);
}
export function isDirect(bytes: Buffer): boolean {
  return bytes.subarray(0, HEADER.length).equals(HEADER);
}
export function directOpen(bytes: Buffer): string {
  if (!isDirect(bytes))
    throw new Error("Неизвестный формат защищённых данных.");
  return transform("unseal", bytes.subarray(HEADER.length)).toString("utf8");
}
