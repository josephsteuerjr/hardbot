import { verify } from "node:crypto";
import type { ProductId } from "./product";

export interface SignedRelease { product: ProductId; version: string; file: string; sha512: string; size: number }
export function verifyRelease(envelope: unknown, publicKey: string, product: ProductId): SignedRelease {
  const e = envelope as { payload?: string; signature?: string };
  if (!e || typeof e.payload !== "string" || typeof e.signature !== "string" || e.payload.length > 16000)
    throw new Error("Нет подписанного описания обновления");
  const payload = Buffer.from(e.payload, "base64");
  if (!verify(null, payload, publicKey, Buffer.from(e.signature, "base64"))) throw new Error("Подпись обновления не подтверждена");
  const d = JSON.parse(payload.toString("utf8")) as SignedRelease;
  if (d.product !== product || !/^\d+\.\d+\.\d+$/.test(d.version)
    || d.file !== `${product}-${d.version}-setup.exe` || !/^[A-Za-z0-9+/]{86}==$/.test(d.sha512)
    || !Number.isSafeInteger(d.size) || d.size <= 0 || d.size > 2_000_000_000)
    throw new Error("Описание обновления не соответствует этой программе");
  return d;
}
export function matchUpdate(info: { version: string; files: { url: string; sha512: string; size?: number }[]; packages?: unknown }, signed: SignedRelease): void {
  if (info.packages !== undefined || info.version !== signed.version || info.files.length !== 1 || info.files[0].url !== signed.file
    || info.files[0].sha512 !== signed.sha512 || info.files[0].size !== signed.size)
    throw new Error("Файл обновления не совпадает с подписанным релизом");
}
