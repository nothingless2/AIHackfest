import { Type } from "typebox";
import { defineToolPlugin } from "openclaw/plugin-sdk/tool-plugin";
import { spawn } from "node:child_process";
import { existsSync, copyFileSync, mkdirSync, appendFileSync, realpathSync, statSync } from "node:fs";
import { randomUUID } from "node:crypto";
import { basename, extname, resolve, relative, isAbsolute, dirname } from "node:path";
import { homedir } from "node:os";
import { runPythonJson } from "./pythonCall.js";

const SUPPORTED_EXTENSIONS = new Set([
  ".jpg",
  ".jpeg",
  ".png",
  ".webp",
  ".bmp",
  ".mp4",
  ".mov",
  ".avi",
  ".mkv",
]);

/**
 * Folder tempat OpenClaw menaruh lampiran masuk. Path dari model adalah input
 * TIDAK TEPERCAYA -- ia cuma string yang ditulis LLM.
 *
 * Idealnya path diambil dari metadata lampiran milik runtime, bukan dari model.
 * Sudah diperiksa: `OpenClawPluginToolContext` di SDK TIDAK menyediakannya (tipe
 * `attachments` ada di SDK, tapi untuk hook dan ACP runtime, tidak diekspos ke
 * tool plugin). Jadi path diterima dari model LALU divalidasi ketat.
 *
 * Layout nyata (diverifikasi dari plugin_calls.jsonl, bukan diasumsikan):
 *   ~/.openclaw/workspace/media/inbound/openclaw-staged-<uuid-per-turn>/input-<uuid>.mp4
 *
 * Tiap turn mendapat folder staging SENDIRI. Itu primitif isolasi yang kuat --
 * tapi folder turn lama tetap tersimpan di disk, jadi sekadar mengizinkan seluruh
 * root masih memungkinkan satu user memakai lampiran milik user lain dari turn
 * sebelumnya. Karena itu ada tiga syarat, bukan satu.
 */
const INBOUND_ROOTS = [
  process.env.OPENCLAW_MEDIA_INBOUND?.trim(),
  resolve(homedir(), ".openclaw", "workspace", "media", "inbound"),
  resolve(homedir(), ".openclaw", "media", "inbound"),
]
  .filter((p): p is string => Boolean(p))
  .map((p) => resolve(p));

/** Umur maksimal folder staging yang boleh dipakai. Lampiran turn ini baru dibuat
 * beberapa detik lalu; folder lama HANYA bisa milik turn/user lain. */
const INBOUND_MAX_AGE_MS =
  Number(process.env.OPENCLAW_MEDIA_MAX_AGE_MINUTES || 30) * 60_000;

function realOrNull(p: string): string | null {
  try {
    return realpathSync(p);
  } catch {
    return null;
  }
}

function isInsideRoots(real: string, roots: string[]): boolean {
  return roots.some((root) => {
    const rel = relative(root, real);
    return rel !== "" && !rel.startsWith("..") && !isAbsolute(rel);
  });
}

/**
 * Lampiran untuk `content_factory_run`, dengan kesegaran yang BERSYARAT.
 *
 * Struktur (di dalam folder lampiran resmi, satu folder saja, folder terbaca) SELALU
 * diperiksa. Batas umur 30 menit hanya berlaku kalau tidak ada pemeriksaan bahan yang
 * sah: `content_factory_inspect` sudah menilai kesegaran SAAT DIPERIKSA, dan gerbangnya
 * (scripts/inspect_media.py) mengikat hasilnya ke chat dan himpunan berkas yang sama
 * selama 24 jam. Tanpa pengecualian ini, user yang menjawab pertanyaan agent 40 menit
 * kemudian akan ditolak dengan "bukan dari pesan ini" padahal bahannya sama persis.
 *
 * `izinKode`: "ok" = pemeriksaan sah; selain itu (mis. "dinonaktifkan") = tanpa bukti.
 */
export function verifyForRun(
  paths: string[],
  izinKode: string | undefined,
  opts: { roots?: string[]; maxAgeMs?: number } = {},
): InboundVerdict {
  const struktur = verifyInbound(paths, { ...opts, maxAgeMs: Number.POSITIVE_INFINITY });
  if (!struktur.ok) return struktur;
  if (izinKode === "ok") return struktur;
  return verifyInbound(paths, opts);
}

export type InboundVerdict =
  | { ok: true }
  | { ok: false; reason: string; offending: string[] };

/**
 * Tiga syarat yang harus dipenuhi bersama:
 *  1. realpath berada di dalam salah satu root inbound (symlink keluar ditolak);
 *  2. SEMUA berkas berada di folder staging yang SAMA -- mencampur folder berarti
 *     mencampur turn, dan turn lain bisa milik user lain;
 *  3. folder itu masih baru -- folder lama hanya bisa milik turn/user lain.
 */
export function verifyInbound(
  paths: string[],
  opts: { roots?: string[]; maxAgeMs?: number } = {},
): InboundVerdict {
  const roots = opts.roots ?? INBOUND_ROOTS;
  const maxAgeMs = opts.maxAgeMs ?? INBOUND_MAX_AGE_MS;

  if (paths.length === 0) {
    return { ok: false, reason: "tidak ada lampiran yang disebutkan", offending: [] };
  }

  const reals = paths.map((p) => ({ asli: p, real: realOrNull(p) }));

  const tidakSah = reals.filter((r) => !r.real || !isInsideRoots(r.real, roots));
  if (tidakSah.length > 0) {
    return {
      ok: false,
      reason: "berada di luar folder lampiran OpenClaw",
      offending: tidakSah.map((r) => r.asli),
    };
  }

  const folders = new Set(reals.map((r) => dirname(r.real as string)));
  if (folders.size > 1) {
    return {
      ok: false,
      reason:
        "berasal dari lebih dari satu folder lampiran (tiap pesan punya folder " +
        "sendiri, jadi ini mencampur kiriman yang berbeda)",
      offending: [...folders],
    };
  }

  const folder = [...folders][0];
  try {
    const umur = Date.now() - statSync(folder).mtimeMs;
    if (umur > maxAgeMs) {
      return {
        ok: false,
        reason: `berasal dari folder lampiran lama (${Math.round(umur / 60_000)} menit), ` +
          "bukan dari pesan ini",
        offending: [folder],
      };
    }
  } catch {
    return { ok: false, reason: "folder lampiran tidak terbaca", offending: [folder] };
  }

  return { ok: true };
}

const PERSONA = {
  contentInsight: "📊 ContentInsight",
  trendAnalysts: "🔍 TrendAnalysts",
  brainIdea: "💡 BrainIdea",
  contentMakers: "✍️ ContentMakers",
  approvalPost: "✅ ApprovalPost",
} as const;

const configSchema = Type.Object({
  projectRoot: Type.Optional(
    Type.String({
      description:
        "Path absolut root proyek AIHackfest (berisi folder scripts/ dan workspace/). " +
        "Kalau kosong, dibaca dari env CONTENT_FACTORY_ROOT.",
    }),
  ),
});

const mediaPathsSchema = Type.Array(
  Type.String({
    description: "Path lokal satu file gambar atau video yang diupload user di chat ini.",
  }),
  {
    minItems: 1,
    description:
      "Daftar path lokal semua gambar/video mentah yang diupload user pada pesan ini, " +
      "yang harus disusun jadi satu konten.",
  },
);

const inspectParams = Type.Object({
  mediaPaths: mediaPathsSchema,
  userContext: Type.Optional(
    Type.String({
      description:
        "Kalimat permintaan user APA ADANYA (topik, platform, durasi, gaya yang sudah ia " +
        "sebut). Dipakai untuk menentukan pertanyaan mana yang MASIH kurang.",
    }),
  ),
});

const runParams = Type.Object({
  mediaPaths: mediaPathsSchema,
  inspectId: Type.Optional(
    Type.String({
      pattern: "^[a-f0-9]{12}$",
      description:
        "ID dari hasil content_factory_inspect untuk bahan yang SAMA. WAJIB: tanpa ini " +
        "pemanggilan ditolak.",
    }),
  ),
  userAnswered: Type.Optional(
    Type.Boolean({
      description:
        "true HANYA kalau kamu sudah menanyakan pertanyaan dari hasil inspect ke user dan user " +
        "sudah menjawab (atau menjawab 'terserah'/'langsung saja'). Jangan mengisi true untuk " +
        "melewati pertanyaan.",
    }),
  ),
  audioMode: Type.Optional(
    Type.Union([Type.Literal("ai"), Type.Literal("original")], {
      description:
        "Sumber suara video. 'original' = pakai audio asli dari video user, TANPA " +
        "voice-over AI — isi HANYA kalau user memang memintanya (mis. \"jangan pakai " +
        "suara AI\", \"pakai suara asli saya\"). 'ai' = voice-over AI. " +
        "Kosongkan kalau user tidak menyebut soal suara sama sekali. " +
        "Catatan: kalau video ternyata tidak ada ucapannya, sistem otomatis kembali " +
        "ke voice-over AI supaya videonya tidak sunyi.",
    }),
  ),
  subtitleStyle: Type.Optional(
    Type.Union(
      [
        Type.Literal("karaoke"), Type.Literal("karaoke-tebal"), Type.Literal("karaoke-kapital"),
        Type.Literal("putih-kotak"), Type.Literal("kuning-kotak"),
        Type.Literal("putih-tebal"), Type.Literal("kuning"),
      ],
      {
        description:
          "Gaya subtitle. 'karaoke' (default): seluruh frasa diam di tempat dan kata " +
          "yang sedang diucapkan menyala kuning, dengan kotak gelap. 'karaoke-tebal': " +
          "sama tapi tanpa kotak (outline). 'karaoke-kapital': huruf besar semua. " +
          "Gaya lama tanpa sorotan kata: 'putih-kotak', 'kuning-kotak', 'putih-tebal', " +
          "'kuning' (teks menumpuk kata demi kata). Isi HANYA kalau user meminta " +
          "gaya subtitle tertentu.",
      },
    ),
  ),
  staticText: Type.Optional(
    Type.Boolean({
      description:
        "true kalau user meminta SATU teks yang sama tampil statis sepanjang video (bukan teks " +
        "yang berganti per klip). Berlaku untuk bahan tanpa ucapan; bahan berucapan tetap " +
        "memakai subtitle.",
    }),
  ),
  editMode: Type.Optional(
    Type.Union(
      [Type.Literal("auto"), Type.Literal("full")],
      {
        description:
          "Seleksi konten. 'auto' (default): editor AI memilih dan mengurutkan " +
          "potongan ucapan terbaik, membuang take ulang dan bagian tidak jelas. " +
          "'full': pakai SEMUA bahan apa adanya, hanya jeda diam yang dipotong. " +
          "Isi 'full' hanya kalau user minta jangan ada yang dibuang atau bilang " +
          "semua videonya harus masuk.",
      },
    ),
  ),
  music: Type.Optional(
    Type.Union(
      [Type.Literal("on"), Type.Literal("off")],
      {
        description:
          "Musik latar. 'off' kalau user minta tanpa musik. Musik hanya bisa " +
          "dipakai kalau pemilik sistem sudah menaruh berkasnya di assets/music/ " +
          "— kalau kosong, video tetap dibuat tanpa musik dan itu dilaporkan.",
      },
    ),
  ),
  musicMood: Type.Optional(
    Type.String({
      maxLength: 40,
      description:
        "Nuansa musik yang diminta user, mis. 'lofi', 'akustik', 'upbeat'. " +
        "Dicocokkan dengan NAMA BERKAS di pustaka musik. Isi hanya kalau user " +
        "menyebutkannya; kalau tidak ada yang cocok, permintaan DITOLAK dengan " +
        "daftar yang tersedia, bukan diganti diam-diam dengan lagu lain.",
    }),
  ),
  durationSeconds: Type.Optional(
    Type.Integer({
      minimum: 10,
      maximum: 60,
      description:
        "Durasi video yang diminta user, dalam DETIK. Isi HANYA kalau user " +
        "menyebut panjang videonya (mis. 'bikin 15 detik saja', 'sekitar setengah " +
        "menit'). Jangan menebak kalau user tidak menyebutkannya — tanpa angka, " +
        "sistem memakai panjang bawaan 20-35 detik. Di luar 10-60 akan dijepit ke " +
        "batas terdekat dan user diberi tahu.",
    }),
  ),
  aspectRatio: Type.Optional(
    Type.Union(
      [Type.Literal("9:16"), Type.Literal("1:1"), Type.Literal("16:9")],
      {
        description:
          "Rasio video. '9:16' vertikal untuk Reels/TikTok/Shorts (default), " +
          "'1:1' persegi untuk feed Instagram, '16:9' lebar untuk YouTube. " +
          "Isi HANYA kalau user menyebut rasio atau platform tujuannya.",
      },
    ),
  ),
  fitMode: Type.Optional(
    Type.Union(
      [Type.Literal("crop"), Type.Literal("blur"), Type.Literal("letterbox")],
      {
        description:
          "Cara memuat bahan yang rasionya berbeda dari target. 'crop' isi penuh " +
          "tapi tepi terpotong (default), 'blur' seluruh frame dipertahankan " +
          "dengan latar blur, 'letterbox' dengan latar hitam. Isi hanya kalau " +
          "user memintanya, mis. 'jangan dipotong'.",
      },
    ),
  ),
  voicePersona: Type.Optional(
    Type.Union(
      [Type.Literal("ramah"), Type.Literal("profesional"), Type.Literal("energik"),
       Type.Literal("tenang"), Type.Literal("bercerita")],
      {
        description:
          "Gaya suara voice-over AI, kalau user memintanya. 'ramah' akrab, " +
          "'profesional' tenang berwibawa, 'energik' bersemangat untuk promosi, " +
          "'tenang' pelan dan lembut, 'bercerita' dengan jeda dramatis. " +
          "Kosongkan kalau user tidak menyebut soal gaya suara. " +
          "Hanya berpengaruh saat voice-over AI dipakai — secara default sistem " +
          "memakai suara ASLI dari video user.",
      },
    ),
  ),
  userContext: Type.Optional(
    Type.String({
      maxLength: 2000,
      description:
        "Permintaan user apa adanya untuk konten ini: topik, produk, target audiens, " +
        "gaya, atau pesan yang ingin disampaikan. Salin maksud user dari pesan chat " +
        "ini; JANGAN mengarang atau menambah detail yang tidak disebut user. " +
        "Kosongkan kalau user benar-benar tidak menyebutkan apa pun selain mengirim file.",
    }),
  ),
});

/**
 * Root proyek HARUS absolut dan eksplisit.
 * Plugin di-copy ke ~/.openclaw/extensions/ saat install, jadi path relatif terhadap
 * lokasi plugin akan salah arah — itu bug versi sebelumnya.
 */
function resolveProjectRoot(configuredRoot?: string): string {
  const root = configuredRoot?.trim() || process.env.CONTENT_FACTORY_ROOT?.trim();

  if (!root) {
    throw new Error(
      "projectRoot belum dikonfigurasi. Set plugins.entries.content-factory.config.projectRoot " +
        "ke path absolut proyek (mis. /root/AIHackfest), atau set env CONTENT_FACTORY_ROOT.",
    );
  }

  const pipelineEntry = resolve(root, "scripts", "pipeline.py");
  if (!existsSync(pipelineEntry)) {
    throw new Error(
      `projectRoot "${root}" tidak valid: ${pipelineEntry} tidak ditemukan. ` +
        "Pastikan path menunjuk ke root proyek yang berisi folder scripts/.",
    );
  }

  return root;
}

const SYSTEMD_RUN = ["/usr/bin/systemd-run", "/bin/systemd-run"].find((p) =>
  existsSync(p),
);

/**
 * Render butuh 2-4 menit, jauh melebihi batas waktu eksekusi tool
 * (TOOL_TIMEOUT_MS = 120 detik). Kalau ditunggu, prosesnya dibunuh di tengah
 * jalan dan menyisakan MP4 rusak. Jadi pipeline dilepas sebagai proses terpisah.
 *
 * `detached: true` + `child.unref()` SAJA TIDAK CUKUP, dan ini terbukti merusak
 * data: gateway berjalan sebagai service systemd dengan KillMode=mixed, jadi saat
 * service di-restart systemd mengirim SIGKILL ke SELURUH cgroup-nya. Proses render
 * yang "detached" tetap berada di cgroup itu dan ikut mati — tanpa run_finished,
 * tanpa pesan ke user. Satu render nyata hilang persis begitu, satu langkah
 * sebelum selesai.
 *
 * `systemd-run --user --scope` menaruh pipeline di scope unit TRANSIEN miliknya
 * sendiri, di luar cgroup service, sehingga restart gateway tidak menyentuhnya.
 * Mode --scope (bukan --service) dipilih supaya environment ikut terwariskan
 * apa adanya: CONTENT_FACTORY_CHAT_ID, _ASSETS, dan kawan-kawan dikirim lewat env.
 */
function startPipelineDetached(
  projectRoot: string,
  extraEnv: Record<string, string> = {},
): number | undefined {
  const env = { ...process.env, ...extraEnv };
  const opts = {
    cwd: projectRoot,
    env,
    detached: true,
    stdio: "ignore" as const,
  };

  if (SYSTEMD_RUN) {
    try {
      const child = spawn(
        SYSTEMD_RUN,
        ["--user", "--scope", "--collect", "--quiet", "python3", "scripts/run_and_deliver.py"],
        opts,
      );
      // Kalau systemd-run gagal setelah spawn (mis. tidak ada session bus),
      // jangan biarkan run hilang diam-diam — jatuh ke cara lama + peringatan.
      child.on("error", (e) => {
        console.warn(
          `[content-factory] systemd-run gagal (${e.message}); ` +
            "jatuh ke spawn biasa. Render TIDAK akan selamat dari restart gateway.",
        );
        spawn("python3", ["scripts/run_and_deliver.py"], opts).unref();
      });
      child.unref();
      return child.pid;
    } catch (e) {
      console.warn(
        `[content-factory] systemd-run tidak bisa dijalankan (${String(e)}); ` +
          "jatuh ke spawn biasa. Render TIDAK akan selamat dari restart gateway.",
      );
    }
  } else {
    console.warn(
      "[content-factory] systemd-run tidak ditemukan; memakai spawn biasa. " +
        "Render TIDAK akan selamat dari restart gateway.",
    );
  }

  const child = spawn("python3", ["scripts/run_and_deliver.py"], opts);
  child.unref();
  return child.pid;
}

/**
 * DIAGNOSTIK ROUTING (sementara, untuk melacak bug "output pindah ke chat lain").
 *
 * Dua hipotesis yang sedang diuji:
 *  (a) di chat pribadi `nativeChannelId` undefined -> jatuh ke TELEGRAM_CHAT_ID di .env,
 *      sehingga output selalu ke satu chat tetap;
 *  (b) `toolContext` yang ditangkap closure factory tidak diperbarui tiap turn, sehingga
 *      chat tujuan basi (milik turn sebelumnya).
 *
 * Untuk membedakannya kita catat `factoryInstanceId`: kalau dua panggilan dari DUA chat
 * berbeda memakai factoryInstanceId yang SAMA dan channel id yang sama, hipotesis (b)
 * terbukti. Menulis log TIDAK BOLEH menggagalkan tool -- semua dibungkus try/catch.
 */
function logInvocation(projectRoot: string, entry: Record<string, unknown>): void {
  try {
    const dir = resolve(projectRoot, "workspace", "state");
    mkdirSync(dir, { recursive: true });
    appendFileSync(
      resolve(dir, "plugin_calls.jsonl"),
      JSON.stringify({ ts: new Date().toISOString(), ...entry }) + "\n",
      "utf8",
    );
  } catch {
    // sengaja diabaikan: diagnostik tidak boleh merusak jalur utama
  }
}

export default defineToolPlugin({
  id: "content-factory",
  name: "Content Factory",
  description:
    "Susun gambar/video mentah yang diupload user jadi satu konten vertikal 9:16 " +
    "lewat pipeline 5 agent (TrendAnalysts, BrainIdea, ContentMakers, ApprovalPost, ContentInsight).",
  configSchema,
  tools: (tool) => [
    tool({
      name: "content_factory_run",
      label: "Content Factory Run",
      description:
        "Edit gambar dan/atau video mentah yang diupload user pada pesan chat ini menjadi SATU " +
        "draft konten sosial media (9:16 bawaan; juga 1:1 dan 16:9), lalu kirim draft itu balik " +
        "ke chat untuk approval. Pakai ketika user minta bahan mereka diedit atau disusun jadi " +
        "konten Reels/TikTok/Shorts. Ini BEDA dari video_generate yang membuat video sintesis AI — " +
        "tool ini mengolah bahan milik user.\n\n" +
        "YANG BENAR-BENAR DIKERJAKAN pipeline ini:\n" +
        "- Suara ASLI video dipertahankan (bawaan). Voice-over AI hanya kalau user memintanya " +
        "(audioMode='ai').\n" +
        "- Ucapan ditranskrip di mesin ini lalu jadi subtitle karaoke (frasa diam, kata yang " +
        "sedang diucapkan menyala kuning). Bahan tanpa ucapan tidak dapat subtitle.\n" +
        "- Jeda diam dipotong. Untuk video berucapan, editor AI memilih dan mengurutkan potongan " +
        "ucapan terbaik dan membuang take ulang serta bagian tidak jelas (editMode='full' memakai " +
        "semua bahan).\n" +
        "- Transisi: hard cut di dalam satu klip, fade hanya di pergantian topik.\n" +
        "- Musik latar hanya kalau pemilik sistem sudah menaruh berkas musik; levelnya mengikuti " +
        "kenyaringan video dan otomatis mengecil saat ada yang bicara.\n" +
        "- Rasio, durasi 10-60 detik, gaya subtitle, dan cover JPG.\n\n" +
        "YANG TIDAK ADA — JANGAN dijanjikan ke user: koreksi warna/color grading, stabilisasi, " +
        "efek atau filter, stiker, pemilihan thumbnail 'frame paling tajam', musik tanpa " +
        "ducking, memilih lagu tren, dubbing bahasa lain, upload otomatis ke platform.\n\n" +
        "Pipeline sendiri mengirim laporan hasilnya (video, cover, dan catatan) ke chat. Cukup " +
        "konfirmasi singkat apa yang diminta user dan bahwa hasil dikirim otomatis; jangan " +
        "merinci pengaturan yang tidak ada di daftar di atas dan jangan menebak hasilnya " +
        "sebelum terkirim.\n\n" +
        "ALUR WAJIB: (1) panggil content_factory_inspect PALING PERTAMA dengan bahan yang sama " +
        "(sebelum bertanya apa pun ke user); (2) kirim pesan pertanyaan dari hasilnya sebagai " +
        "pesan biasa lalu akhiri giliran (bukan ask_user); (3) setelah user membalas, panggil " +
        "tool ini dengan inspectId dan userAnswered=true. Pemanggilan tanpa inspectId yang sah " +
        "akan ditolak.",
      parameters: runParams,
      optional: true,
      factory({ api, toolContext }) {
        // Snapshot SAAT FACTORY DIPANGGIL. Dibandingkan dengan nilai saat execute()
        // untuk membuktikan apakah toolContext diperbarui per turn atau basi.
        const factoryInstanceId = randomUUID().slice(0, 8);
        const factoryChannelId = toolContext.nativeChannelId ?? null;
        const factorySenderId = toolContext.requesterSenderId ?? null;

        return {
          name: "content_factory_run",
          label: "Content Factory Run",
          description: "Jalankan pipeline content factory atas bahan yang diupload user.",
          parameters: runParams,
          async execute(
            toolCallId: string,
            params: {
              mediaPaths: string[];
              inspectId?: string;
              userAnswered?: boolean;
              userContext?: string;
              audioMode?: "ai" | "original";
              voicePersona?: string;
              aspectRatio?: string;
              fitMode?: string;
              durationSeconds?: number;
              music?: string;
              editMode?: string;
              staticText?: boolean;
              subtitleStyle?: string;
              musicMood?: string;
            },
            signal?: AbortSignal,
          ) {
            const fail = (text: string, details: Record<string, unknown>) => ({
              content: [{ type: "text" as const, text }],
              details: { status: "error", ...details },
            });

            let projectRoot: string;
            try {
              projectRoot = resolveProjectRoot(
                (api.pluginConfig as { projectRoot?: string } | undefined)?.projectRoot,
              );
            } catch (e) {
              return fail(String(e instanceof Error ? e.message : e), {
                error: "project_root_invalid",
              });
            }

            const {
              mediaPaths, userContext, audioMode, voicePersona, aspectRatio, fitMode,
              durationSeconds, music, musicMood, editMode, subtitleStyle,
              inspectId, userAnswered, staticText,
            } =
              params;

            // Dicatat SEBELUM validasi apa pun, supaya tetap ada bukti walau
            // permintaan nanti ditolak (file hilang / format tidak didukung).
            logInvocation(projectRoot, {
              toolCallId,
              toolCallIdLength: toolCallId.length,
              factoryInstanceId,
              factoryChannelId,
              factorySenderId,
              executeChannelId: toolContext.nativeChannelId ?? null,
              executeSenderId: toolContext.requesterSenderId ?? null,
              senderIsOwner: toolContext.senderIsOwner ?? null,
              messageChannel: toolContext.messageChannel ?? null,
              sessionKey: toolContext.sessionKey ?? null,
              mediaCount: mediaPaths.length,
              hasUserContext: Boolean((userContext ?? "").trim()),
              audioMode: audioMode ?? null,
              voicePersona: voicePersona ?? null,
              aspectRatio: aspectRatio ?? null,
              fitMode: fitMode ?? null,
              durationSeconds: durationSeconds ?? null,
              music: music ?? null,
              editMode: editMode ?? null,
              subtitleStyle: subtitleStyle ?? null,
              musicMood: musicMood ?? null,
              // Path yang BENAR-BENAR dikirim model, plus hasil validasinya.
              // Tanpa ini, penolakan "file tidak ditemukan" tidak bisa didiagnosis
              // dari log sama sekali -- yang tercatat cuma jumlahnya.
              mediaPaths,
              mediaMissing: mediaPaths.filter((p) => !existsSync(p)),
              mediaUnsupported: mediaPaths.filter(
                (p) => !SUPPORTED_EXTENSIONS.has(extname(p).toLowerCase()),
              ),
              mediaVerdict: verifyInbound(mediaPaths),
            });

            const missing = mediaPaths.filter((p) => !existsSync(p));
            if (missing.length > 0) {
              return fail(`File berikut tidak ditemukan: ${missing.join(", ")}`, {
                error: "media_not_found",
                missing,
              });
            }

            const unsupported = mediaPaths.filter(
              (p) => !SUPPORTED_EXTENSIONS.has(extname(p).toLowerCase()),
            );
            if (unsupported.length > 0) {
              return fail(
                `Format tidak didukung: ${unsupported.join(", ")}. ` +
                  "Gunakan gambar (jpg/png/webp/bmp) atau video (mp4/mov/avi/mkv).",
                { error: "unsupported_format", unsupported },
              );
            }

            // Path dari model wajib berada di dalam folder lampiran OpenClaw.
            // Tanpa ini, satu string dari LLM bisa menyuruh plugin menyalin file
            // mana pun yang terbaca proses gateway ke workspace user.
            // Struktur diperiksa SEKARANG (tanpa batas umur); kesegaran dinilai setelah
            // gerbang, karena pemeriksaan bahan yang sah sudah membuktikannya (lihat
            // verifyForRun). Urutan ini juga menjaga gerbang tidak pernah menyentuh path
            // yang jelas bukan lampiran.
            const struktur = verifyInbound(mediaPaths, { maxAgeMs: Number.POSITIVE_INFINITY });
            if (!struktur.ok) {
              return fail(
                `Lampiran ditolak: ${struktur.reason}. Hanya file yang diupload pada ` +
                  `pesan ini yang boleh dipakai. Detail: ${struktur.offending.join(", ")}`,
                { error: "media_not_from_this_message", reason: struktur.reason,
                  offending: struktur.offending },
              );
            }

            // Tujuan pengiriman HARUS chat pemicu (lihat catatan panjang di bawah). Dicek
            // SEBELUM menyalin file, supaya penolakan tidak meninggalkan salinan.
            const originChatId = toolContext.nativeChannelId;
            if (!originChatId) {
              return fail(
                "Chat pemicu tidak dapat ditentukan, jadi pipeline tidak dijalankan — " +
                  "hasilnya tidak punya tujuan yang aman. Lihat workspace/state/plugin_calls.jsonl " +
                  "untuk konteks yang diterima plugin.",
                { error: "origin_chat_unknown" },
              );
            }

            // GERBANG pemeriksaan bahan: pipeline hanya jalan setelah bahan diperiksa dan --
            // kalau ada yang perlu ditanyakan -- user sudah ditanya. Ditegakkan di Python
            // (scripts/inspect_media.py cek_izin) supaya aturannya satu dan diuji satu kali.
            // GAGAL-TERTUTUP: kalau pemeriksaannya sendiri tidak bisa dijalankan, ditolak.
            const gate = await runPythonJson({
              cwd: projectRoot,
              script: "scripts/inspect_media.py",
              args: ["check"],
              stdin: {
                inspect_id: inspectId ?? "",
                chat_id: String(originChatId),
                paths: mediaPaths,
                user_answered: userAnswered === true,
                konteks: (userContext ?? "").trim(),
              },
              timeoutMs: 15_000,
              signal,
            });
            if (!gate.ok) {
              return fail(
                `Pemeriksaan bahan tidak bisa diverifikasi (${gate.error}), jadi pipeline tidak ` +
                  "dijalankan. Coba panggil content_factory_inspect lagi.",
                { error: "inspect_check_failed", detail: gate.error },
              );
            }
            const izin = gate.json as { ok?: boolean; kode?: string; teks?: string };
            if (!izin.ok) {
              logInvocation(projectRoot, { tool: "run", toolCallId, ditolak: izin.kode ?? "?" });
              return fail(izin.teks || "Pemeriksaan bahan diperlukan sebelum memproses.", {
                error: "inspect_required",
                kode: izin.kode ?? "unknown",
              });
            }

            const verdict = verifyForRun(mediaPaths, izin.kode);
            if (!verdict.ok) {
              return fail(
                `Lampiran ditolak: ${verdict.reason}. Hanya file yang diupload pada ` +
                  `pesan ini yang boleh dipakai. Detail: ${verdict.offending.join(", ")}`,
                { error: "media_not_from_this_message", reason: verdict.reason,
                  offending: verdict.offending },
              );
            }

            const rawDir = resolve(projectRoot, "workspace", "raw");
            mkdirSync(rawDir, { recursive: true });

            const runPrefix = toolCallId.slice(0, 8);
            const copiedNames = mediaPaths.map((srcPath) => {
              const destName = `${runPrefix}_${basename(srcPath)}`;
              copyFileSync(srcPath, resolve(rawDir, destName));
              return destName;
            });

            // Catatan: kalau chat pemicu tidak diketahui, pipeline ditolak (dicek di atas)
            // -- BUKAN dilanjutkan dengan chat cadangan dari .env. Fallback itulah yang
            // dulu membuat hasil run siapa pun terkirim ke satu chat tetap, sehingga
            // materi milik user A bisa sampai ke user B.
            const pipelineEnv: Record<string, string> = {
              CONTENT_FACTORY_ASSETS: copiedNames.join(","),
              // Diteruskan apa adanya (BUKAN runPrefix yang sudah dipotong 8 char) --
              // Python-side run_lock.sanitize_run_id() yang menangani sanitasi/
              // pemendekan aman (prefix+hash) kalau toolCallId ternyata panjang.
              CONTENT_FACTORY_RUN_ID: toolCallId,
              CONTENT_FACTORY_CHAT_ID: String(originChatId),
              // Prefix kepemilikan file, dikirim EKSPLISIT supaya sisi Python tidak
              // perlu menebak ulang `slice(0, 8)` di sini. Dipakai untuk memastikan
              // run ini hanya memakai bahan miliknya sendiri, bukan seluruh isi
              // workspace/raw/ yang kini menampung materi lebih dari satu user.
              CONTENT_FACTORY_RUN_PREFIX: runPrefix,
            };

            // Kalimat user adalah sinyal paling langsung tentang MAKSUD konten --
            // topik, audiens, gaya. Sebelumnya dibuang sama sekali, sehingga
            // pipeline harus menebak semuanya dari piksel.
            if (audioMode) {
              pipelineEnv.CONTENT_FACTORY_AUDIO_MODE = audioMode;
            }
            if (voicePersona) {
              pipelineEnv.TTS_PERSONA = voicePersona;
            }
            if (aspectRatio) {
              pipelineEnv.VIDEO_ASPECT = aspectRatio;
            }
            if (editMode) {
              pipelineEnv.CONTENT_FACTORY_EDIT = editMode;
            }
            if (subtitleStyle) {
              pipelineEnv.SUBTITLE_STYLE = subtitleStyle;
            }
            if (staticText === true) {
              pipelineEnv.CONTENT_FACTORY_STATIC_TEXT = "1";
            }
            if (music) {
              pipelineEnv.CONTENT_FACTORY_MUSIC = music;
            }
            if (musicMood) {
              pipelineEnv.CONTENT_FACTORY_MUSIC_MOOD = musicMood;
            }
            if (typeof durationSeconds === "number" && Number.isFinite(durationSeconds)) {
              // Dikirim apa adanya; penjepitan ke 10-60 dilakukan Python di titik
              // masuk supaya jalur CLI dan jalur plugin memakai aturan yang sama.
              pipelineEnv.CONTENT_FACTORY_DURATION = String(Math.round(durationSeconds));
            }
            if (fitMode) {
              pipelineEnv.FIT_MODE = fitMode;
            }

            const konteks = (userContext ?? "").trim();
            if (konteks) {
              pipelineEnv.CONTENT_FACTORY_USER_CONTEXT = konteks;
            }

            const pid = startPipelineDetached(projectRoot, pipelineEnv);

            if (!pid) {
              return fail("Gagal memulai proses pipeline.", { error: "spawn_failed" });
            }

            const draftPath = resolve(projectRoot, "workspace", "drafts", "video_output.mp4");

            return {
              content: [
                {
                  type: "text" as const,
                  text:
                    `Pipeline content factory dimulai untuk ${copiedNames.length} bahan ` +
                    "(pid " +
                    pid +
                    "). Rendering berjalan di latar belakang sekitar 2-4 menit; " +
                    "pesan progres tiap agent dan video hasilnya akan dikirim OTOMATIS ke chat " +
                    "ini setelah selesai. JANGAN memanggil tool ini lagi untuk permintaan yang " +
                    "sama, dan jangan mengklaim video sudah jadi sampai filenya benar-benar " +
                    "terkirim. Cukup beri tahu user bahwa prosesnya sedang berjalan.",
                },
              ],
              details: {
                status: "started",
                pid,
                draftPath,
                assets: copiedNames,
                async: true,
              },
            };
          },
        };
      },
    }),
    tool({
      name: "content_factory_inspect",
      label: "Content Factory Inspect",
      description:
        "LANGKAH PERTAMA sebelum content_factory_run. Memeriksa gambar/video yang diupload user " +
        "(durasi, orientasi, ada tidaknya suara dan ucapan) dan mengembalikan (a) fakta terukur, " +
        "(b) DAFTAR PERTANYAAN yang benar-benar masih kurang untuk hasil terbaik, dan (c) inspectId.\n\n" +
        "Cepat (beberapa detik), tidak memakai LLM, tidak merender apa pun, tidak mengubah apa pun.\n\n" +
        "CARA MEMAKAINYA: panggil tool ini PALING PERTAMA begitu user mengirim bahan dan meminta " +
        "diedit — SEBELUM menanyakan apa pun ke user. Pertanyaan dan PILIHAN JAWABANNYA dibuat " +
        "tool ini dari fakta terukur; kamu tidak menulis pertanyaan sendiri. Hasilnya berisi " +
        "satu pesan siap kirim: kirim APA ADANYA sebagai pesan biasa lalu AKHIRI giliranmu. " +
        "JANGAN memakai ask_user (menahan giliran lalu kedaluwarsa dalam 15 menit dan " +
        "menggagalkan giliran; user boleh membalas kapan saja dalam 24 jam). Saat user " +
        "membalas, hasil tool ini juga memuat pemetaan jawaban ke parameter content_factory_run. " +
        "Kalau tidak ada pertanyaan, langsung lanjut ke content_factory_run.",
      parameters: inspectParams,
      optional: true,
      factory({ api, toolContext }) {
        return {
          name: "content_factory_inspect",
          label: "Content Factory Inspect",
          description: "Periksa bahan user dan tentukan pertanyaan yang masih kurang.",
          parameters: inspectParams,
          async execute(
            toolCallId: string,
            params: { mediaPaths: string[]; userContext?: string },
            signal?: AbortSignal,
          ) {
            let projectRootDiketahui: string | undefined;
            // Setiap penolakan DICATAT. Versi pertama hanya mencatat yang sukses, sehingga
            // empat panggilan yang semuanya gagal tidak meninggalkan jejak apa pun dan
            // penyebabnya harus ditebak dari ringkasan tool di keluaran agent.
            const fail = (text: string, details: Record<string, unknown>) => {
              if (projectRootDiketahui) {
                logInvocation(projectRootDiketahui, {
                  tool: "inspect", toolCallId, ditolak: details.error ?? "unknown",
                  alasan: details.reason ?? details.detail ?? null,
                });
              }
              return {
                content: [{ type: "text" as const, text }],
                details: { status: "error", ...details },
              };
            };

            let projectRoot: string;
            try {
              projectRoot = resolveProjectRoot(
                (api.pluginConfig as { projectRoot?: string } | undefined)?.projectRoot,
              );
              projectRootDiketahui = projectRoot;
            } catch (e) {
              return fail(String(e instanceof Error ? e.message : e), {
                error: "project_root_invalid",
              });
            }

            // Sama seperti run: tanpa chat pemicu yang jelas, pemeriksaan tidak punya
            // pemilik, dan inspectId-nya tidak bisa dicocokkan nanti.
            const originChatId = toolContext.nativeChannelId;
            if (!originChatId) {
              return fail("Chat pemicu tidak dapat ditentukan, pemeriksaan dibatalkan.", {
                error: "origin_chat_unknown",
              });
            }

            // Path dari model tetap TIDAK TEPERCAYA: hanya file lampiran pesan ini.
            const verdict = verifyInbound(params.mediaPaths);
            if (!verdict.ok) {
              return fail(
                `Lampiran ditolak: ${verdict.reason}. Hanya file yang diupload pada pesan ini ` +
                  `yang boleh diperiksa. Detail: ${verdict.offending.join(", ")}`,
                { error: "media_not_from_this_message", reason: verdict.reason },
              );
            }

            const hasil = await runPythonJson({
              cwd: projectRoot,
              script: "scripts/inspect_media.py",
              args: ["inspect"],
              stdin: {
                paths: params.mediaPaths,
                konteks: (params.userContext ?? "").trim(),
                chat_id: String(originChatId),
              },
              timeoutMs: 60_000,
              signal,
            });
            if (!hasil.ok) {
              return fail(
                `Pemeriksaan bahan gagal (${hasil.error}). Tanyakan ke user secara singkat: ` +
                  "topiknya apa, untuk platform mana, dan berapa lama — lalu beri tahu bahwa " +
                  "pemeriksaan otomatis sedang bermasalah.",
                { error: "inspect_failed", detail: hasil.error },
              );
            }
            const r = hasil.json as {
              ok?: boolean; teks?: string; inspect_id?: string;
              pertanyaan?: Array<{ kode: string }>; degraded?: string | null;
            };
            if (!r.ok) {
              return fail(r.teks || "Pemeriksaan bahan gagal.", { error: "inspect_rejected" });
            }

            logInvocation(projectRoot, {
              tool: "inspect",
              toolCallId,
              chatId: String(originChatId),
              mediaCount: params.mediaPaths.length,
              inspectId: r.inspect_id,
              pertanyaan: (r.pertanyaan ?? []).map((p) => p.kode),
              degraded: r.degraded ?? null,
            });

            return {
              content: [{ type: "text" as const, text: r.teks ?? "" }],
              details: {
                status: "inspected",
                inspectId: r.inspect_id,
                questions: (r.pertanyaan ?? []).map((p) => p.kode),
                degraded: r.degraded ?? null,
              },
            };
          },
        };
      },
    }),
  ],
});
