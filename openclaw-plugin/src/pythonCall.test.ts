import { afterEach, describe, expect, it } from "vitest";
import { mkdtempSync, writeFileSync, readFileSync, existsSync, rmSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { runPythonJson } from "./pythonCall.js";

const dirs: string[] = [];
function skrip(isi: string): { dir: string; path: string } {
  const dir = mkdtempSync(join(tmpdir(), "pycall-"));
  dirs.push(dir);
  const path = join(dir, "s.js");
  writeFileSync(path, isi);
  return { dir, path };
}
afterEach(() => {
  while (dirs.length) rmSync(dirs.pop()!, { recursive: true, force: true });
});

// Node dipakai sebagai interpreter supaya tes tidak bergantung pada Python.
const base = { python: process.execPath, timeoutMs: 5000 };

function hidup(pid: number): boolean {
  try {
    process.kill(pid, 0);
    return true;
  } catch {
    return false;
  }
}
async function tunggu(kondisi: () => boolean, ms = 3000): Promise<boolean> {
  const batas = Date.now() + ms;
  while (Date.now() < batas) {
    if (kondisi()) return true;
    await new Promise((r) => setTimeout(r, 50));
  }
  return kondisi();
}

describe("runPythonJson", () => {
  it("mengembalikan objek JSON dan meneruskan stdin", async () => {
    const { dir, path } = skrip(`
      let s=""; process.stdin.on("data",d=>s+=d).on("end",()=>{
        console.log(JSON.stringify({ok:true, gema: JSON.parse(s), argumen: process.argv.slice(2)}));
      });`);
    const r = await runPythonJson({ ...base, cwd: dir, script: path, args: ["inspect"], stdin: { a: 1 } });
    expect(r).toEqual({ ok: true, json: { ok: true, gema: { a: 1 }, argumen: ["inspect"] } });
  });

  it("memakai objek JSON TERAKHIR kalau ada keluaran lain sebelumnya", async () => {
    const { dir, path } = skrip(`console.log("peringatan biasa"); console.log(JSON.stringify({x:2}));`);
    expect(await runPythonJson({ ...base, cwd: dir, script: path })).toEqual({ ok: true, json: { x: 2 } });
  });

  it("keluaran yang bukan JSON dilaporkan sebagai galat", async () => {
    const { dir, path } = skrip(`console.log("bukan json");`);
    const r = await runPythonJson({ ...base, cwd: dir, script: path });
    expect(r.ok).toBe(false);
    if (!r.ok) expect(r.error).toContain("bukan JSON");
  });

  it("proses tanpa keluaran dilaporkan dengan stderr-nya", async () => {
    const { dir, path } = skrip(`console.error("kabur"); process.exit(3);`);
    const r = await runPythonJson({ ...base, cwd: dir, script: path });
    expect(r.ok).toBe(false);
    if (!r.ok) expect(r.error).toContain("kabur");
  });

  it("JSON dari proses yang keluar dengan kode nonzero tetap dikembalikan (pemanggil yang menilai)", async () => {
    const { dir, path } = skrip(`console.log(JSON.stringify({ok:false,kode:"x"})); process.exit(2);`);
    expect(await runPythonJson({ ...base, cwd: dir, script: path })).toEqual({ ok: true, json: { ok: false, kode: "x" } });
  });

  it("timeout membunuh SELURUH process group, termasuk proses cucu", async () => {
    // Cucu (sleep) menulis pid-nya lalu tidur lama. Kalau hanya proses langsung yang
    // dibunuh, cucu ini tetap hidup -- persis kebocoran yang dicegah oleh kill(-pid).
    const { dir, path } = skrip(`
      const {spawn}=require("node:child_process"), fs=require("node:fs");
      const c=spawn("sleep",["30"],{stdio:"ignore"});
      fs.writeFileSync(process.argv[2], String(c.pid));
      setInterval(()=>{},1000);`);
    const pidFile = join(dir, "cucu.pid");
    const r = await runPythonJson({ ...base, cwd: dir, script: path, args: [pidFile], timeoutMs: 700 });
    expect(r.ok).toBe(false);
    if (!r.ok) expect(r.error).toContain("melebihi batas");
    expect(existsSync(pidFile)).toBe(true);
    const pid = Number(readFileSync(pidFile, "utf-8"));
    expect(await tunggu(() => !hidup(pid))).toBe(true);
  });

  it("abort membunuh seluruh process group dan mengembalikan galat", async () => {
    const { dir, path } = skrip(`
      const {spawn}=require("node:child_process"), fs=require("node:fs");
      const c=spawn("sleep",["30"],{stdio:"ignore"});
      fs.writeFileSync(process.argv[2], String(c.pid));
      setInterval(()=>{},1000);`);
    const pidFile = join(dir, "cucu.pid");
    const ac = new AbortController();
    const p = runPythonJson({ ...base, cwd: dir, script: path, args: [pidFile], timeoutMs: 20000, signal: ac.signal });
    expect(await tunggu(() => existsSync(pidFile))).toBe(true);
    const pid = Number(readFileSync(pidFile, "utf-8"));
    expect(hidup(pid)).toBe(true);
    ac.abort();
    const r = await p;
    expect(r).toEqual({ ok: false, error: "dibatalkan (abort)" });
    expect(await tunggu(() => !hidup(pid))).toBe(true);
  });

  it("sinyal yang sudah abort sebelum mulai tidak menjalankan proses apa pun", async () => {
    const { dir, path } = skrip(`require("fs").writeFileSync(process.argv[2],"jalan");`);
    const tanda = join(dir, "tanda");
    const ac = new AbortController();
    ac.abort();
    const r = await runPythonJson({ ...base, cwd: dir, script: path, args: [tanda], signal: ac.signal });
    expect(r.ok).toBe(false);
    expect(existsSync(tanda)).toBe(false);
  });

  it("cucu yang tersisa setelah selesai normal ikut dibersihkan", async () => {
    const { dir, path } = skrip(`
      const {spawn}=require("node:child_process"), fs=require("node:fs");
      const c=spawn("sleep",["30"],{stdio:"ignore"});
      c.unref();   // tanpa ini induk tidak pernah keluar, dan yatim tidak tercipta
      fs.writeFileSync(process.argv[2], String(c.pid));
      console.log(JSON.stringify({ok:true}));`);
    const pidFile = join(dir, "cucu.pid");
    const r = await runPythonJson({ ...base, cwd: dir, script: path, args: [pidFile] });
    expect(r).toEqual({ ok: true, json: { ok: true } });
    const pid = Number(readFileSync(pidFile, "utf-8"));
    expect(await tunggu(() => !hidup(pid))).toBe(true);
  });

  it("interpreter yang tidak ada dilaporkan sebagai galat, bukan melempar", async () => {
    const r = await runPythonJson({ python: "/tidak/ada/python", cwd: tmpdir(), script: "x.py", timeoutMs: 2000 });
    expect(r.ok).toBe(false);
  });
});
