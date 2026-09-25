"""Rantai model LLM (common.chat_json + LLM_FALLBACK): 25 Sep model gratis utama dicabut dari
OpenRouter dan model lain bergantian 429. Klien OpenAI dipalsukan -- tanpa jaringan."""

import httpx
import openai
import pytest

import common


def _galat(status, pesan="x"):
    req = httpx.Request("POST", "https://openrouter.ai/api/v1/chat/completions")
    resp = httpx.Response(status, request=req, json={"error": {"message": pesan}})
    kelas = {404: openai.NotFoundError, 429: openai.RateLimitError, 401: openai.AuthenticationError,
             503: openai.InternalServerError}[status]
    return kelas(pesan, response=resp, body={"error": {"message": pesan}})


class _Klien:
    def __init__(self, perilaku, log):
        self.chat = self
        self.completions = self
        self.perilaku, self.log = perilaku, log

    def create(self, model, messages, response_format):
        gambar = any(isinstance(m["content"], list) and any(x.get("type") == "image_url" for x in m["content"])
                     for m in messages)
        self.log.append((model, gambar))
        aksi = self.perilaku.get((model, gambar), self.perilaku.get(model))
        if isinstance(aksi, Exception):
            raise aksi

        class R:
            usage = None
            choices = [type("C", (), {"message": type("M", (), {"content": '{"ok": "%s"}' % model})()})()]
        return R()


@pytest.fixture
def klien(monkeypatch):
    log = []

    def pasang(perilaku, cadangan="b,c"):
        monkeypatch.setenv("LLM_FALLBACK", cadangan)
        monkeypatch.setattr(common, "make_openai_client", lambda timeout=None: _Klien(perilaku, log))
        monkeypatch.setattr(common, "_catat_pemakaian_llm", lambda *a: None)
        monkeypatch.setattr("time.sleep", lambda s: None)
    return pasang, log


PESAN = [{"role": "user", "content": "halo"}]


def test_model_dicabut_pindah_ke_cadangan(klien):
    pasang, log = klien
    pasang({"a": _galat(404, "No endpoints found for a"), "b": None})
    assert common.chat_json(PESAN, model="a") == {"ok": "b"}
    assert [m for m, _ in log] == ["a", "b"]


def test_semua_antre_penuh_putaran_kedua_berhasil(klien):
    pasang, log = klien
    hasil = {"n": 0}

    class Sekali(dict):
        def get(self, k, d=None):
            if isinstance(k, tuple):          # hanya kunci model yang dihitung (sekali per panggilan)
                return d
            hasil["n"] += 1
            return _galat(429, "rate-limited upstream") if hasil["n"] <= 3 else None
    pasang(Sekali())
    assert common.chat_json(PESAN, model="a") == {"ok": "a"}
    assert [m for m, _ in log] == ["a", "b", "c", "a"]


def test_kunci_salah_tidak_mencoba_cadangan(klien):
    pasang, log = klien
    pasang({"a": _galat(401, "invalid key")})
    with pytest.raises(openai.AuthenticationError):
        common.chat_json(PESAN, model="a")
    assert [m for m, _ in log] == ["a"]


PESAN_GAMBAR = [{"role": "user", "content": [{"type": "text", "text": "x"},
                                            {"type": "image_url", "image_url": {"url": "data:x"}}]}]


def test_model_teks_saja_dipakai_paling_akhir_dengan_catatan(klien, monkeypatch):
    """25 Sep: model teks-saja dipakai di putaran pertama lalu mengarang isi visual."""
    pasang, log = klien
    dikirim = []
    pasang({"a": _galat(429), ("b", True): _galat(404, "No endpoints found that support image input"),
            ("b", False): None}, cadangan="b")
    asli = common.make_openai_client

    class Pencatat:
        def __init__(self, k):
            self.chat = self
            self.completions = self
            self.k = k

        def create(self, model, messages, response_format):
            dikirim.append(messages)
            return self.k.create(model, messages, response_format)
    monkeypatch.setattr(common, "make_openai_client", lambda timeout=None: Pencatat(asli()))
    assert common.chat_json(PESAN_GAMBAR, model="a") == {"ok": "b"}
    assert log == [("a", True), ("b", True), ("a", True), ("b", False)], "bergambar dulu, semua putaran"
    assert "TIDAK melihat" in dikirim[-1][0]["content"][0]["text"]
    assert common.PANGGILAN_TERAKHIR == {"model": "b", "tanpa_gambar": True}


def test_model_bergambar_pulih_lebih_diutamakan_daripada_teks_saja(klien):
    pasang, log = klien
    hitung = {"a": 0}

    class P(dict):
        def get(self, k, d=None):
            if k == ("b", True):
                return _galat(404, "No endpoints found that support image input")
            if k == "a":
                hitung["a"] += 1
                return _galat(429) if hitung["a"] == 1 else None
            return d
    pasang(P(), cadangan="b")
    assert common.chat_json(PESAN_GAMBAR, model="a") == {"ok": "a"}
    assert common.PANGGILAN_TERAKHIR == {"model": "a", "tanpa_gambar": False}


def test_tanpa_cadangan_perilaku_lama(klien, monkeypatch):
    pasang, log = klien
    pasang({"a": _galat(404, "gone")}, cadangan="")
    with pytest.raises(openai.NotFoundError):
        common.chat_json(PESAN, model="a", max_attempts=1)
