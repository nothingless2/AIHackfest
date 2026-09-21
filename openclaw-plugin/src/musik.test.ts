import { describe, expect, it } from "vitest";
import { AUDIO_EXTENSIONS, pisahMusik } from "./index.js";

describe("pisahMusik", () => {
  it("memisahkan audio dari bahan visual berdasarkan ekstensi", () => {
    const r = pisahMusik(["/i/a.mp4", "/i/lagu.mp3", "/i/b.jpg", "/i/c.MOV"]);
    expect(r.bahan).toEqual(["/i/a.mp4", "/i/b.jpg", "/i/c.MOV"]);
    expect(r.musik).toEqual(["/i/lagu.mp3"]);
  });

  it("tidak peka huruf besar-kecil pada ekstensi", () => {
    expect(pisahMusik(["/i/LAGU.MP3", "/i/x.M4A"]).musik).toHaveLength(2);
  });

  it("mengenali semua format audio umum", () => {
    for (const e of [".mp3", ".m4a", ".aac", ".wav", ".ogg", ".opus", ".flac"]) {
      expect(AUDIO_EXTENSIONS.has(e), e).toBe(true);
      expect(pisahMusik([`/i/x${e}`]).musik).toHaveLength(1);
    }
  });

  it("video tidak pernah dikira musik (mp4 dengan audio tetap bahan)", () => {
    expect(pisahMusik(["/i/klip.mp4"]).musik).toEqual([]);
  });

  it("daftar tanpa musik menghasilkan musik kosong", () => {
    expect(pisahMusik(["/i/a.mp4"])).toEqual({ bahan: ["/i/a.mp4"], musik: [] });
  });
});
