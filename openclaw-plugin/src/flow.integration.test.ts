import { afterAll, beforeAll, describe, expect, it } from "vitest";
import { execFileSync } from "node:child_process";
import {
  cpSync, existsSync, mkdirSync, mkdtempSync, readFileSync, readdirSync, rmSync, utimesSync, writeFileSync,
} from "node:fs";
import { tmpdir } from "node:os";
import { join, resolve } from "node:path";

/**
 * Alur dua-langkah, diuji dari ujung ke ujung TANPA OpenClaw dan TANPA render nyata:
 * tool TypeScript yang asli -> skrip Python yang asli (inspect_media.py) -> status di
 * disk -> gerbang di `run`. Yang diganti hanya pipeline (stub yang mencatat env-nya).
 *
 * Kenapa perlu: e2e lewat CLI OpenClaw tidak mungkin -- giliran CLI tidak punya chat
 * Telegram asal, sehingga kedua tool menolak (gagal-tertutup, benar). Jadi sambungan
 * TS->Python->gerbang dibuktikan di sini.
 */

const ROOT_ASLI = resolve(__dirname, "..", "..");
const CHAT = "1583550141";
const CHAT_LAIN = "531508359";

const STUB = `
import json, os, sys
os.makedirs("workspace/state", exist_ok=True)
env = {k: v for k, v in os.environ.items() if k.startswith(("CONTENT_FACTORY_", "VIDEO_", "FIT_", "SUBTITLE_", "TTS_"))}
json.dump(env, open("workspace/state/stub_run.json", "w"))
`;

let base: string;
let root: string;
let inbound: string;
let segar: string;
let lama: string;
let tools: Record<string, any> = {};
let toolsLain: Record<string, any> = {};
let toolsTanpaChat: Record<string, any> = {};

function bikinVideo(path: string, detik = 2) {
  execFileSync("ffmpeg", ["-y", "-v", "error", "-f", "lavfi", "-i",
    `color=c=gray:size=320x180:rate=10:duration=${detik}`, "-f", "lavfi", "-i",
    `anoisesrc=d=${detik}:c=pink:a=0.3`, "-c:v", "libx264", "-pix_fmt", "yuv420p", "-c:a", "aac",
    "-shortest", path]);
}

function ambilTools(nativeChannelId: string | undefined): Record<string, any> {
  const hasil: Record<string, any> = {};
  const api: any = { pluginConfig: { projectRoot: root }, registerTool: (fn: any) => {
    const r = typeof fn === "function" ? fn({ nativeChannelId, requesterSenderId: nativeChannelId }) : fn;
    for (const t of Array.isArray(r) ? r : [r]) if (t?.name) hasil[t.name] = t;
  } };
  return { api, hasil } as any;
}

function teks(r: any): string {
  return (r.content ?? []).map((c: any) => c.text).join("\n");
}

async function tunggu(kondisi: () => boolean, ms = 8000): Promise<boolean> {
  const batas = Date.now() + ms;
  while (Date.now() < batas) {
    if (kondisi()) return true;
    await new Promise((r) => setTimeout(r, 100));
  }
  return kondisi();
}

beforeAll(async () => {
  base = mkdtempSync(join(tmpdir(), "flow-"));
  root = join(base, "proyek");
  inbound = join(base, "media", "inbound");
  segar = join(inbound, "openclaw-staged-segar");
  lama = join(inbound, "openclaw-staged-lama");

  cpSync(join(ROOT_ASLI, "scripts"), join(root, "scripts"), {
    recursive: true, filter: (n) => !n.includes("__pycache__"),
  });
  writeFileSync(join(root, "scripts", "run_and_deliver.py"), STUB);
  mkdirSync(join(root, "workspace", "state"), { recursive: true });
  for (const d of [segar, lama]) mkdirSync(d, { recursive: true });
  for (const [d, n] of [[segar, 3], [lama, 2]] as const) {
    for (let i = 1; i <= n; i++) bikinVideo(join(d, `input-${i}.mp4`));
  }
  const duaJamLalu = new Date(Date.now() - 2 * 3600_000);
  utimesSync(lama, duaJamLalu, duaJamLalu);

  process.env.OPENCLAW_MEDIA_INBOUND = inbound;     // sebelum modul dimuat
  const { default: entry } = await import("./index.js");
  for (const [chat, sasaran] of [[CHAT, tools], [CHAT_LAIN, toolsLain], [undefined, toolsTanpaChat]] as const) {
    const { api, hasil } = ambilTools(chat) as any;
    (entry as any).register(api);
    Object.assign(sasaran, hasil);
  }
}, 60_000);

afterAll(() => rmSync(base, { recursive: true, force: true }));

const KONTEKS = "edit ya. Ini dokumentasi acara Aksi Merah Laksamana Muda, donor darah";
const berkas = (dir: string, n: number) => Array.from({ length: n }, (_, i) => join(dir, `input-${i + 1}.mp4`));
const logPlugin = () => {
  const p = join(root, "workspace", "state", "plugin_calls.jsonl");
  return existsSync(p) ? readFileSync(p, "utf-8").trim().split("\n").map((l) => JSON.parse(l)) : [];
};

describe("dua tool terdaftar", () => {
  it("inspect dan run sama-sama tersedia", () => {
    expect(Object.keys(tools).sort()).toEqual(["content_factory_inspect", "content_factory_run"]);
  });
});

describe("content_factory_inspect", () => {
  it("mengembalikan fakta, pertanyaan, dan inspectId; status tersimpan di disk", async () => {
    const r = await tools.content_factory_inspect.execute(
      "call_ins_1", { mediaPaths: berkas(segar, 3), userContext: "edit ya" });
    expect(r.details.status).toBe("inspected");
    expect(r.details.inspectId).toMatch(/^[a-f0-9]{12}$/);
    expect(r.details.questions.length).toBeGreaterThan(0);
    expect(teks(r)).toContain(r.details.inspectId);
    expect(teks(r)).toContain("PEMERIKSAAN BAHAN");
    // pesan pertanyaan berpilihan yang dibuat kode, dan larangan ask_user
    expect(teks(r)).toContain("<<<PESAN");
    expect(teks(r)).toMatch(/A\. /);
    expect(teks(r)).toContain("JANGAN memakai ask_user");
    expect(existsSync(join(root, "workspace", "state", "inspect", `${r.details.inspectId}.json`))).toBe(true);
  }, 60_000);

  it("menolak lampiran di luar folder lampiran resmi, dan mencatat penolakannya", async () => {
    const luar = join(base, "rahasia.mp4");
    bikinVideo(luar);
    const r = await tools.content_factory_inspect.execute("call_ins_luar", { mediaPaths: [luar] });
    expect(r.details.status).toBe("error");
    expect(r.details.error).toBe("media_not_from_this_message");
    expect(logPlugin().some((e) => e.toolCallId === "call_ins_luar" && e.ditolak === "media_not_from_this_message")).toBe(true);
  }, 30_000);

  it("menolak folder lampiran lama (kesegaran dinilai saat diperiksa)", async () => {
    const r = await tools.content_factory_inspect.execute("call_ins_lama", { mediaPaths: berkas(lama, 2) });
    expect(r.details.error).toBe("media_not_from_this_message");
    expect(teks(r)).toContain("lama");
  }, 30_000);

  it("gagal-tertutup tanpa chat pemicu, dan penolakannya tercatat", async () => {
    const r = await toolsTanpaChat.content_factory_inspect.execute("call_ins_nochat", { mediaPaths: berkas(segar, 3) });
    expect(r.details.error).toBe("origin_chat_unknown");
    expect(logPlugin().some((e) => e.toolCallId === "call_ins_nochat" && e.ditolak === "origin_chat_unknown")).toBe(true);
  }, 30_000);
});

describe("gerbang di content_factory_run", () => {
  let inspectId: string;
  beforeAll(async () => {
    const r = await tools.content_factory_inspect.execute(
      "call_ins_gerbang", { mediaPaths: berkas(segar, 3), userContext: "edit ya" });
    inspectId = r.details.inspectId;
  }, 60_000);

  const tidakAdaRender = () => !existsSync(join(root, "workspace", "state", "stub_run.json"));
  const rawKosong = () => !existsSync(join(root, "workspace", "raw")) || readdirSync(join(root, "workspace", "raw")).length === 0;

  it("menolak tanpa inspectId -- dan tidak menyalin apa pun", async () => {
    const r = await tools.content_factory_run.execute("call_run_1", { mediaPaths: berkas(segar, 3) });
    expect(r.details.error).toBe("inspect_required");
    expect(r.details.kode).toBe("wajib_inspect");
    expect(teks(r)).toContain("content_factory_inspect");
    expect(tidakAdaRender() && rawKosong()).toBe(true);
  }, 30_000);

  it("menolak kalau pertanyaan belum ditanyakan (userAnswered tidak true)", async () => {
    const r = await tools.content_factory_run.execute("call_run_2", { mediaPaths: berkas(segar, 3), inspectId });
    expect(r.details.kode).toBe("belum_ditanyakan");
    expect(tidakAdaRender() && rawKosong()).toBe(true);
  }, 30_000);

  it("menolak inspectId milik chat lain", async () => {
    const r = await toolsLain.content_factory_run.execute(
      "call_run_3", { mediaPaths: berkas(segar, 3), inspectId, userAnswered: true, userContext: KONTEKS });
    expect(r.details.kode).toBe("milik_chat_lain");
    expect(tidakAdaRender() && rawKosong()).toBe(true);
  }, 30_000);

  it("menolak bahan yang berbeda dari yang diperiksa", async () => {
    const r = await tools.content_factory_run.execute(
      "call_run_4", { mediaPaths: berkas(segar, 2), inspectId, userAnswered: true, userContext: KONTEKS });
    expect(r.details.kode).toBe("bahan_berbeda");
    expect(tidakAdaRender() && rawKosong()).toBe(true);
  }, 30_000);

  it("menolak inspectId yang bukan format heksadesimal 12 karakter (tidak boleh menyusun path)", async () => {
    const r = await tools.content_factory_run.execute(
      "call_run_5", { mediaPaths: berkas(segar, 3), inspectId: "../../etc/passwd", userAnswered: true });
    expect(r.details.error).toBe("inspect_required");
    expect(tidakAdaRender() && rawKosong()).toBe(true);
  }, 30_000);

  it("lolos setelah ditanyakan: pipeline dimulai dengan env yang benar", async () => {
    const konteks = "Edit ya. Jawaban: ini dokumentasi stan kesehatan di food court, brand Prodia.";
    const r = await tools.content_factory_run.execute("call_run_ok", {
      mediaPaths: berkas(segar, 3), inspectId, userAnswered: true, userContext: konteks,
      aspectRatio: "9:16", fitMode: "blur", subtitleStyle: "karaoke", staticText: true,
    });
    expect(r.details.status).toBe("started");
    expect(await tunggu(() => existsSync(join(root, "workspace", "state", "stub_run.json")))).toBe(true);
    const env = JSON.parse(readFileSync(join(root, "workspace", "state", "stub_run.json"), "utf-8"));
    expect(env.CONTENT_FACTORY_CHAT_ID).toBe(CHAT);
    expect(env.CONTENT_FACTORY_USER_CONTEXT).toBe(konteks);
    expect(env.CONTENT_FACTORY_ASSETS.split(",")).toHaveLength(3);
    expect(env.VIDEO_ASPECT).toBe("9:16");
    expect(env.FIT_MODE).toBe("blur");
    expect(env.SUBTITLE_STYLE).toBe("karaoke");
    expect(env.CONTENT_FACTORY_STATIC_TEXT).toBe("1");
  }, 30_000);

  it("REGRESI: jawaban user yang datang BERJAM-JAM kemudian tetap diterima", async () => {
    // Skenario yang nyaris lolos: batas umur lampiran 30 menit akan menolak `run` padahal
    // bahannya sama persis dengan yang sudah diperiksa. Diperiksa saat folder masih segar,
    // lalu folder 'menua' sebelum jawaban datang.
    const seg2 = join(inbound, "openclaw-staged-menua");
    mkdirSync(seg2, { recursive: true });
    bikinVideo(join(seg2, "input-1.mp4"));
    const ins = await tools.content_factory_inspect.execute("call_ins_menua", { mediaPaths: [join(seg2, "input-1.mp4")], userContext: "edit" });
    expect(ins.details.status).toBe("inspected");

    const dua = new Date(Date.now() - 3 * 3600_000);
    utimesSync(seg2, dua, dua);        // 3 jam kemudian

    rmSync(join(root, "workspace", "state", "stub_run.json"), { force: true });
    const r = await tools.content_factory_run.execute("call_run_menua", {
      mediaPaths: [join(seg2, "input-1.mp4")], inspectId: ins.details.inspectId, userAnswered: true,
      userContext: KONTEKS,
    });
    expect(r.details.status, teks(r)).toBe("started");
  }, 60_000);

  it("folder lama TANPA pemeriksaan tetap ditolak (pengecualian umur tidak berlaku tanpa bukti)", async () => {
    const r = await tools.content_factory_run.execute("call_run_lama", { mediaPaths: berkas(lama, 2) });
    expect(r.details.error).toBe("inspect_required");
  }, 30_000);

  it("menolak kalau jawaban user TIDAK masuk userContext (celah nyata 19 Sep 23:36)", async () => {
    // bersihkan jejak tes sebelumnya yang sah, supaya "tidak ada render" bisa diperiksa
    rmSync(join(root, "workspace", "state", "stub_run.json"), { force: true });
    rmSync(join(root, "workspace", "raw"), { recursive: true, force: true });
    const r = await tools.content_factory_run.execute(
      "call_run_tanpa_jawaban", { mediaPaths: berkas(segar, 3), inspectId, userAnswered: true, userContext: "edit ya" });
    expect(r.details.kode).toBe("jawaban_tidak_di_konteks");
    expect(teks(r)).toContain("userContext");
    expect(tidakAdaRender() && rawKosong()).toBe(true);
  }, 30_000);

  it("gagal-tertutup tanpa chat pemicu", async () => {
    const r = await toolsTanpaChat.content_factory_run.execute(
      "call_run_nochat", { mediaPaths: berkas(segar, 3), inspectId, userAnswered: true, userContext: KONTEKS });
    expect(r.details.error).toBe("origin_chat_unknown");
  }, 30_000);
});
