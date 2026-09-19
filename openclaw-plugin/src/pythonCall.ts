import { spawn } from "node:child_process";

export type PythonResult =
  | { ok: true; json: unknown }
  | { ok: false; error: string };

export type PythonCallOptions = {
  /** Interpreter; bawaan "python3". Tes memakai node supaya tidak bergantung Python. */
  python?: string;
  cwd: string;
  script: string;
  args?: string[];
  /** Objek yang ditulis ke stdin sebagai JSON. */
  stdin?: unknown;
  timeoutMs: number;
  /** Sinyal abort dari OpenClaw untuk panggilan tool ini. */
  signal?: AbortSignal;
};

const MAX_STDOUT = 2 * 1024 * 1024;

/**
 * Jalankan skrip Python SINKRON dan kembalikan objek JSON terakhir di stdout.
 *
 * Proses dijalankan `detached` (process group sendiri) dan pada timeout MAUPUN abort
 * dibunuh dengan `process.kill(-pid, "SIGKILL")` -- tanda minus itu yang membunuh
 * SELURUH group, bukan hanya proses langsungnya. Tanpa ini, tool yang sudah di-abort
 * OpenClaw tetap meninggalkan ffmpeg/python cucu yang berjalan sampai selesai.
 *
 * Tidak ada timer yang hidup melewati panggilan: timer dibersihkan di setiap jalur
 * keluar, dan tidak ada proses yang ditinggalkan berjalan (batas dari user: tanpa
 * penjadwal atau proses latar belakang).
 */
export function runPythonJson(opts: PythonCallOptions): Promise<PythonResult> {
  return new Promise((resolvePromise) => {
    let selesai = false;
    let child: ReturnType<typeof spawn> | undefined;
    let timer: ReturnType<typeof setTimeout> | undefined;

    const bunuh = () => {
      if (child?.pid) {
        try {
          process.kill(-child.pid, "SIGKILL");
        } catch {
          // group sudah tidak ada
        }
      }
    };
    const akhiri = (hasil: PythonResult) => {
      if (selesai) return;
      selesai = true;
      if (timer) clearTimeout(timer);
      opts.signal?.removeEventListener("abort", onAbort);
      resolvePromise(hasil);
    };
    const onAbort = () => {
      bunuh();
      akhiri({ ok: false, error: "dibatalkan (abort)" });
    };

    if (opts.signal?.aborted) {
      akhiri({ ok: false, error: "dibatalkan (abort)" });
      return;
    }

    try {
      child = spawn(opts.python ?? "python3", [opts.script, ...(opts.args ?? [])], {
        cwd: opts.cwd,
        detached: true,
        stdio: ["pipe", "pipe", "pipe"],
      });
    } catch (e) {
      akhiri({ ok: false, error: `gagal menjalankan proses: ${String(e)}` });
      return;
    }

    let stdout = "";
    let stderr = "";
    child.stdout?.on("data", (b: Buffer) => {
      if (stdout.length < MAX_STDOUT) stdout += b.toString("utf-8");
    });
    child.stderr?.on("data", (b: Buffer) => {
      if (stderr.length < 8192) stderr += b.toString("utf-8");
    });
    child.on("error", (e) => akhiri({ ok: false, error: `proses gagal: ${e.message}` }));
    child.on("close", (kode) => {
      // Lewat jalur normal pun group dibersihkan: cucu yang masih hidup tidak boleh tersisa.
      bunuh();
      const baris = stdout.trim().split("\n").filter(Boolean);
      const terakhir = baris[baris.length - 1];
      if (!terakhir) {
        akhiri({ ok: false, error: `tanpa keluaran (kode ${kode}). ${stderr.trim().slice(-300)}` });
        return;
      }
      try {
        akhiri({ ok: true, json: JSON.parse(terakhir) });
      } catch {
        akhiri({ ok: false, error: `keluaran bukan JSON: ${terakhir.slice(0, 200)}` });
      }
    });

    opts.signal?.addEventListener("abort", onAbort, { once: true });
    timer = setTimeout(() => {
      bunuh();
      akhiri({ ok: false, error: `melebihi batas ${Math.round(opts.timeoutMs / 1000)} detik` });
    }, opts.timeoutMs);

    child.stdin?.on("error", () => {
      // proses berhenti sebelum membaca stdin; hasilnya ditangani handler close
    });
    child.stdin?.end(JSON.stringify(opts.stdin ?? {}));
  });
}
