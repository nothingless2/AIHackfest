// Eksekutor render lapisan teks TRANSPARAN. RENCANA (frame mana yang dirender) dibuat Python
// (scripts/overlay_remotion.py); di sini hanya dijalankan, dengan SATU browser untuk semua.
//   node render.mjs <masukan.json>   masukan = {props, komposisi?, pekerjaan:[{jenis, dari, sampai|frame, out}]}
// komposisi: 'TextOverlay' (bawaan, teks tulisan) atau 'MotionOverlay' (motion graphic).
// Klip -> ProRes 4444 (.mov, alpha). VP8/WebM+alpha DICOBA lebih dulu dan ditolak: dekoder
// libvpx di ffmpeg 4.4 mesin ini gagal ("Bitstream not supported"); dekoder bawaan membuang alpha.
import {bundle} from '@remotion/bundler';
import {openBrowser, renderFrames, renderMedia, renderStill, selectComposition} from '@remotion/renderer';
import fs from 'node:fs';
import path from 'node:path';
import {fileURLToPath} from 'node:url';

const DIR = path.dirname(fileURLToPath(import.meta.url));
const masukan = process.argv[2];
if (!masukan) {
  console.error('pakai: node render.mjs <masukan.json>');
  process.exit(2);
}
const {props, pekerjaan, komposisi = 'TextOverlay'} = JSON.parse(fs.readFileSync(masukan, 'utf8'));
const chromium = process.env.REMOTION_CHROMIUM
  || '/root/.cache/ms-playwright/chromium_headless_shell-1243/chrome-headless-shell-linux64/chrome-headless-shell';

// Bundel di-cache per isi src/ (bundle ~4 dtk; tidak perlu diulang tiap render).
const cacheDir = path.join(DIR, '.bundle-cache');
const sidik = fs.readdirSync(path.join(DIR, 'src')).sort()
  .map((f) => f + fs.statSync(path.join(DIR, 'src', f)).mtimeMs).join('|');
const penanda = path.join(cacheDir, 'SIDIK');
if (!fs.existsSync(penanda) || fs.readFileSync(penanda, 'utf8') !== sidik) {
  fs.rmSync(cacheDir, {recursive: true, force: true});
  await bundle({entryPoint: path.join(DIR, 'src/index.jsx'), outDir: cacheDir,
    publicDir: path.join(DIR, 'public')});
  fs.writeFileSync(penanda, sidik);
}

const t0 = Date.now();
const browser = await openBrowser('chrome', {browserExecutable: chromium,
  chromiumOptions: {gl: 'swangle'}});
try {
  const composition = await selectComposition({serveUrl: cacheDir, id: komposisi,
    inputProps: props, puppeteerInstance: browser});
  const umum = {composition, serveUrl: cacheDir, inputProps: props, puppeteerInstance: browser,
    logLevel: 'error'};
  for (const p of pekerjaan) {
    if (p.jenis === 'diam') {
      await renderStill({...umum, frame: p.frame, imageFormat: 'png', output: p.out});
    } else if (p.jenis === 'urutan') {
      // Urutan PNG (caption dinamis): digabung ffmpeg lewat daftar concat jadi SATU input komposit.
      fs.mkdirSync(p.out, {recursive: true});
      await renderFrames({...umum, imageFormat: 'png', outputDir: p.out, frameRange: [p.dari, p.sampai],
        onStart: () => {}, onFrameUpdate: () => {},
        concurrency: Number(process.env.REMOTION_CONCURRENCY || 4)});
    } else {
      await renderMedia({...umum, codec: 'prores', proResProfile: '4444', imageFormat: 'png',
        pixelFormat: 'yuva444p10le', muted: true, frameRange: [p.dari, p.sampai],
        outputLocation: p.out, concurrency: Number(process.env.REMOTION_CONCURRENCY || 4)});
    }
  }
} finally {
  await browser.close({silent: true});
}
console.log(JSON.stringify({ok: true, detik: (Date.now() - t0) / 1000, pekerjaan: pekerjaan.length}));
