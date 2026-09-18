import { Type } from "typebox";
import { defineToolPlugin } from "openclaw/plugin-sdk/tool-plugin";
import { spawn } from "node:child_process";
import { existsSync, copyFileSync, mkdirSync, appendFileSync, realpathSync } from "node:fs";
import { randomUUID } from "node:crypto";
import { basename, extname, resolve, relative, isAbsolute } from "node:path";
import { homedir } from "node:os";

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
 * Folder tempat OpenClaw menyimpan lampiran masuk. Path dari model adalah input
 * TIDAK TEPERCAYA -- ia cuma string yang ditulis LLM.
 *
 * Idealnya path diambil dari metadata lampiran milik runtime, bukan dari model.
 * Sudah diperiksa: `OpenClawPluginToolContext` di SDK TIDAK menyediakannya (tipe
 * `attachments` ada di SDK, tapi untuk hook dan ACP runtime, tidak diekspos ke
 * tool plugin). Jadi satu-satunya jalan adalah menerima path dari model LALU
 * memvalidasinya dengan ketat.
 *
 * Yang SENGAJA TIDAK dilakukan: memindai folder ini atau memakai "file terbaru"
 * sebagai fallback. Folder ini dipakai BERSAMA oleh semua user, jadi menebak file
 * berarti satu user bisa memakai lampiran milik user lain.
 */
const INBOUND_DIR = resolve(
  process.env.OPENCLAW_MEDIA_INBOUND?.trim() ||
    resolve(homedir(), ".openclaw", "media", "inbound"),
);

/**
 * True kalau `p` benar-benar berada DI DALAM folder inbound setelah symlink
 * diselesaikan. realpathSync penting: tanpa itu, symlink di dalam inbound yang
 * menunjuk ke /etc/passwd akan lolos pemeriksaan prefix string biasa.
 */
function isInsideInbound(p: string): boolean {
  let real: string;
  try {
    real = realpathSync(p);
  } catch {
    return false; // tidak ada / tidak terbaca -> perlakukan sebagai tidak sah
  }
  const rel = relative(INBOUND_DIR, real);
  return rel !== "" && !rel.startsWith("..") && !isAbsolute(rel);
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

const runParams = Type.Object({
  mediaPaths: Type.Array(
    Type.String({
      description: "Path lokal satu file gambar atau video yang diupload user di chat ini.",
    }),
    {
      minItems: 1,
      description:
        "Daftar path lokal semua gambar/video mentah yang diupload user pada pesan ini, " +
        "yang harus disusun jadi satu konten.",
    },
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
        "Gabungkan/susun beberapa gambar dan/atau video mentah (foto produk, klip, slideshow) " +
        "yang diupload user pada pesan chat ini menjadi SATU draft konten sosial media 9:16 " +
        "lengkap dengan voice-over AI, lalu kirim draft itu balik ke chat untuk approval. " +
        "Pakai tool ini ketika user minta bahan-bahan mereka disusun jadi konten. Ini BEDA dari " +
        "video_generate yang membuat video baru hasil sintesis AI — tool ini menyusun ulang " +
        "bahan mentah milik user apa adanya.",
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
            params: { mediaPaths: string[]; userContext?: string },
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

            const { mediaPaths, userContext } = params;

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
              // Path yang BENAR-BENAR dikirim model, plus hasil validasinya.
              // Tanpa ini, penolakan "file tidak ditemukan" tidak bisa didiagnosis
              // dari log sama sekali -- yang tercatat cuma jumlahnya.
              mediaPaths,
              mediaMissing: mediaPaths.filter((p) => !existsSync(p)),
              mediaUnsupported: mediaPaths.filter(
                (p) => !SUPPORTED_EXTENSIONS.has(extname(p).toLowerCase()),
              ),
              mediaOutsideInbound: mediaPaths.filter((p) => !isInsideInbound(p)),
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
            const diLuar = mediaPaths.filter((p) => !isInsideInbound(p));
            if (diLuar.length > 0) {
              return fail(
                `Path berikut berada di luar folder lampiran OpenClaw dan ditolak: ` +
                  `${diLuar.join(", ")}. Hanya file yang benar-benar diupload di chat ini ` +
                  `yang boleh dipakai.`,
                { error: "media_outside_inbound", outside: diLuar },
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

            // Tujuan pengiriman HARUS chat pemicu. Kalau tidak diketahui, pipeline
            // ditolak di sini -- BUKAN dilanjutkan dengan chat cadangan dari .env.
            // Fallback itulah yang dulu membuat hasil run siapa pun terkirim ke satu
            // chat tetap, sehingga materi milik user A bisa sampai ke user B.
            const originChatId = toolContext.nativeChannelId;
            if (!originChatId) {
              return fail(
                "Chat pemicu tidak dapat ditentukan, jadi pipeline tidak dijalankan — " +
                  "hasilnya tidak punya tujuan yang aman. Lihat workspace/state/plugin_calls.jsonl " +
                  "untuk konteks yang diterima plugin.",
                { error: "origin_chat_unknown" },
              );
            }

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
  ],
});
