// 財務規劃台的加密資料檔工具（跟網頁用同一種加密：PBKDF2-SHA256 → AES-GCM）
//
//   加密：FIN_PASS=密碼 node tools/finance_vault.mjs encrypt 明文.json docs/finance/vault.json
//   解密：FIN_PASS=密碼 node tools/finance_vault.mjs decrypt docs/finance/vault.json 明文.json
//
// 明文 JSON 不要放進這個 repo。
import { readFileSync, writeFileSync } from "node:fs";
import { webcrypto as crypto } from "node:crypto";

const ITER = 310000;
const b64 = (u8) => Buffer.from(u8).toString("base64");
const unb64 = (s) => new Uint8Array(Buffer.from(s, "base64"));

async function deriveKey(pass, salt, iter) {
  const base = await crypto.subtle.importKey("raw", new TextEncoder().encode(pass), "PBKDF2", false, ["deriveKey"]);
  return crypto.subtle.deriveKey({ name: "PBKDF2", hash: "SHA-256", salt, iterations: iter }, base,
    { name: "AES-GCM", length: 256 }, false, ["encrypt", "decrypt"]);
}

const [, , mode, src, dst] = process.argv;
const pass = process.env.FIN_PASS;
if (!pass || !["encrypt", "decrypt"].includes(mode) || !src || !dst) {
  console.error("用法：FIN_PASS=密碼 node tools/finance_vault.mjs encrypt|decrypt 來源 目的");
  process.exit(1);
}
if (mode === "encrypt") {
  const plain = JSON.stringify(JSON.parse(readFileSync(src, "utf8")));
  const salt = crypto.getRandomValues(new Uint8Array(16));
  const iv = crypto.getRandomValues(new Uint8Array(12));
  const key = await deriveKey(pass, salt, ITER);
  const ct = new Uint8Array(await crypto.subtle.encrypt({ name: "AES-GCM", iv }, key, new TextEncoder().encode(plain)));
  writeFileSync(dst, JSON.stringify({ v: 1, kdf: "PBKDF2-SHA256", iter: ITER, salt: b64(salt), iv: b64(iv), ct: b64(ct) }) + "\n");
  console.log(`已加密 → ${dst}`);
} else {
  const v = JSON.parse(readFileSync(src, "utf8"));
  const key = await deriveKey(pass, unb64(v.salt), v.iter);
  const pt = await crypto.subtle.decrypt({ name: "AES-GCM", iv: unb64(v.iv) }, key, unb64(v.ct));
  writeFileSync(dst, JSON.stringify(JSON.parse(new TextDecoder().decode(pt)), null, 1) + "\n");
  console.log(`已解密 → ${dst}`);
}
