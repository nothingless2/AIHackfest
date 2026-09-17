import { Type } from "typebox";
import { defineToolPlugin } from "openclaw/plugin-sdk/tool-plugin";
import { spawn } from "node:child_process";
import { existsSync, copyFileSync, mkdirSync, appendFileSync } from "node:fs";
import { randomUUID } from "node:crypto";
import { basename, extname, resolve } from "node:path";

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

/**
 * Render butuh 2-4 menit, jauh melebihi batas waktu eksekusi tool (~90 detik).
 * Kalau ditunggu, prosesnya dibunuh di tengah jalan dan menyisakan MP4 rusak.
 * Jadi pipeline dilepas sebagai proses detached: ia bertahan setelah tool selesai
 * dan mengirim progress + video hasilnya sendiri ke Telegram.
 */
function startPipelineDetached(
  projectRoot: string,
  extraEnv: Record<string, string> = {},
): number | undefined {
  const child = spawn("python3", ["scripts/run_and_deliver.py"], {
    cwd: projectRoot,
    env: { ...process.env, ...extraEnv },
    detached: true,
    stdio: "ignore",
  });
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
          async execute(toolCallId: string, params: { mediaPaths: string[] }) {
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

            const { mediaPaths } = params;

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
            };

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
