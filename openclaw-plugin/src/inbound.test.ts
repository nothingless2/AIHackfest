import { describe, expect, it, beforeAll, afterAll } from "vitest";
import { mkdtempSync, mkdirSync, writeFileSync, symlinkSync, rmSync, utimesSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";

import { verifyForRun, verifyInbound } from "./index.js";

/**
 * Lampiran datang sebagai path yang ditulis MODEL — input tidak tepercaya.
 * Tiap pesan mendapat folder staging sendiri, tapi folder pesan lama tetap ada
 * di disk, jadi folder milik user lain bisa ditebak/dipakai kalau tidak dijaga.
 */
describe("verifyInbound", () => {
  let base: string;
  let inbound: string;   // root yang diizinkan
  let turnA: string;
  let turnB: string;
  let luar: string;      // DI LUAR inbound -- bukan sekadar folder lain di dalamnya
  let roots: string[];

  beforeAll(() => {
    base = mkdtempSync(join(tmpdir(), "inbound-"));
    inbound = join(base, "media", "inbound");
    turnA = join(inbound, "openclaw-staged-aaa");
    turnB = join(inbound, "openclaw-staged-bbb");
    luar = join(base, "bukan-inbound");
    for (const d of [turnA, turnB, luar]) mkdirSync(d, { recursive: true });
    for (const d of [turnA, turnB]) {
      writeFileSync(join(d, "input-1.mp4"), "x");
      writeFileSync(join(d, "input-2.mp4"), "x");
    }
    writeFileSync(join(luar, "rahasia.mp4"), "x");
    roots = [inbound];
  });

  afterAll(() => rmSync(base, { recursive: true, force: true }));

  it("menerima berkas dari satu folder staging yang baru", () => {
    const v = verifyInbound(
      [join(turnA, "input-1.mp4"), join(turnA, "input-2.mp4")],
      { roots },
    );
    expect(v.ok).toBe(true);
  });

  it("menolak path di luar folder inbound", () => {
    const v = verifyInbound([join(luar, "rahasia.mp4")], { roots });
    expect(v.ok).toBe(false);
    if (!v.ok) expect(v.reason).toContain("di luar folder lampiran");
  });

  it("menolak path absolut sembarang di sistem", () => {
    const v = verifyInbound(["/etc/passwd"], { roots });
    expect(v.ok).toBe(false);
  });

  it("menolak path dengan .. yang keluar dari inbound", () => {
    const v = verifyInbound(
      [join(turnA, "..", "bukan-inbound", "rahasia.mp4")],
      { roots },
    );
    expect(v.ok).toBe(false);
  });

  it("menolak symlink di dalam inbound yang menunjuk keluar", () => {
    // Pemeriksaan prefix string biasa akan LOLOS di sini; realpath yang menangkapnya.
    const tautan = join(turnA, "tampak-normal.mp4");
    symlinkSync(join(luar, "rahasia.mp4"), tautan);
    const v = verifyInbound([tautan], { roots });
    expect(v.ok).toBe(false);
    rmSync(tautan);
  });

  it("menolak campuran dua folder staging (dua user tidak bisa saling memakai)", () => {
    const v = verifyInbound(
      [join(turnA, "input-1.mp4"), join(turnB, "input-1.mp4")],
      { roots },
    );
    expect(v.ok).toBe(false);
    if (!v.ok) expect(v.reason).toContain("lebih dari satu folder");
  });

  it("menolak folder staging lama milik pesan/user lain", () => {
    const lama = new Date(Date.now() - 60 * 60 * 1000);
    utimesSync(turnB, lama, lama);
    const v = verifyInbound([join(turnB, "input-1.mp4")], { roots, maxAgeMs: 60_000 });
    expect(v.ok).toBe(false);
    if (!v.ok) expect(v.reason).toContain("folder lampiran lama");
  });

  it("menolak daftar kosong alih-alih tersandung", () => {
    // minItems:1 di skema seharusnya mencegah ini, tapi fungsi validasi tidak
    // boleh bergantung pada pemanggilnya — daftar kosong sempat membuatnya
    // melempar dengan pesan yang menyesatkan.
    const v = verifyInbound([], { roots });
    expect(v.ok).toBe(false);
    if (!v.ok) expect(v.reason).toContain("tidak ada lampiran");
  });

  it("menolak berkas yang tidak ada", () => {
    const v = verifyInbound([join(turnA, "hantu.mp4")], { roots });
    expect(v.ok).toBe(false);
  });
});

/**
 * Kesegaran lampiran bersyarat untuk `content_factory_run`.
 *
 * Latar belakang: agent kini bertanya dulu ke user. Jawaban user bisa datang jauh
 * setelah 30 menit, dan tanpa pengecualian ini pemanggilan `run` ditolak sebagai
 * "bukan dari pesan ini" padahal bahannya sama persis dengan yang sudah diperiksa.
 */
describe("verifyForRun", () => {
  let base: string;
  let inbound: string;
  let turnBaru: string;
  let turnLama: string;
  let turnLain: string;
  let luar: string;
  let roots: string[];

  beforeAll(() => {
    base = mkdtempSync(join(tmpdir(), "forrun-"));
    inbound = join(base, "media", "inbound");
    turnBaru = join(inbound, "openclaw-staged-baru");
    turnLama = join(inbound, "openclaw-staged-lama");
    turnLain = join(inbound, "openclaw-staged-lain");
    luar = join(base, "bukan-inbound");
    for (const d of [turnBaru, turnLama, turnLain, luar]) mkdirSync(d, { recursive: true });
    for (const d of [turnBaru, turnLama, turnLain, luar]) writeFileSync(join(d, "input-1.mp4"), "x");
    writeFileSync(join(turnLama, "input-2.mp4"), "x");
    const duaJamLalu = new Date(Date.now() - 2 * 3600_000);
    utimesSync(turnLama, duaJamLalu, duaJamLalu);
    roots = [inbound];
  });
  afterAll(() => rmSync(base, { recursive: true, force: true }));

  it("folder lama DITERIMA kalau pemeriksaan bahan sah (jawaban user datang belakangan)", () => {
    const v = verifyForRun([join(turnLama, "input-1.mp4")], "ok", { roots });
    expect(v.ok).toBe(true);
  });

  it("folder lama DITOLAK kalau tidak ada pemeriksaan yang sah", () => {
    for (const kode of [undefined, "dinonaktifkan", "wajib_inspect"]) {
      const v = verifyForRun([join(turnLama, "input-1.mp4")], kode, { roots });
      expect(v.ok, `kode=${String(kode)}`).toBe(false);
      if (!v.ok) expect(v.reason).toContain("lama");
    }
  });

  it("kontrol positif: verifyInbound biasa memang menolak folder yang sama", () => {
    expect(verifyInbound([join(turnLama, "input-1.mp4")], { roots }).ok).toBe(false);
  });

  it("folder baru diterima dengan atau tanpa pemeriksaan", () => {
    expect(verifyForRun([join(turnBaru, "input-1.mp4")], "ok", { roots }).ok).toBe(true);
    expect(verifyForRun([join(turnBaru, "input-1.mp4")], "dinonaktifkan", { roots }).ok).toBe(true);
  });

  it("pemeriksaan sah TIDAK melonggarkan struktur: di luar folder lampiran tetap ditolak", () => {
    const v = verifyForRun([join(luar, "input-1.mp4")], "ok", { roots });
    expect(v.ok).toBe(false);
    if (!v.ok) expect(v.reason).toContain("di luar folder lampiran");
  });

  it("pemeriksaan sah TIDAK melonggarkan struktur: campuran dua folder tetap ditolak", () => {
    const v = verifyForRun(
      [join(turnBaru, "input-1.mp4"), join(turnLain, "input-1.mp4")],
      "ok",
      { roots },
    );
    expect(v.ok).toBe(false);
    if (!v.ok) expect(v.reason).toContain("lebih dari satu folder");
  });

  it("path yang tidak ada ditolak", () => {
    expect(verifyForRun([join(turnBaru, "tidak-ada.mp4")], "ok", { roots }).ok).toBe(false);
  });

  it("daftar kosong ditolak", () => {
    expect(verifyForRun([], "ok", { roots }).ok).toBe(false);
  });
});
