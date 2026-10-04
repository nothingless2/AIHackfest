# Image Klipa: ffmpeg 4.4 + Python venv + Node + Remotion + Chromium, identik di laptop mana pun dan VPS.
# Dibangun dengan deploy/bootstrap.sh (satu-satunya cara pemasangan; sudah diuji di Ubuntu 22.04 bersih).
#
#   docker build -t klipa:local .
#   docker run --rm klipa:local                       # menjalankan deploy/verify.sh
#   docker run --rm klipa:local bash deploy/verify.sh --tes
#
# Rahasia TIDAK masuk image: .env dikecualikan oleh .dockerignore. Berikan saat dijalankan:
#   docker run --rm --env-file .env -v "$PWD/workspace:/opt/klipa/workspace" klipa:local ...
FROM ubuntu:22.04

ENV DEBIAN_FRONTEND=noninteractive \
    LANG=C.UTF-8 \
    PYTHONUNBUFFERED=1

WORKDIR /opt/klipa

# Tahap 1 - hanya berkas yang MENENTUKAN dependensi. Layer berat di bawah (apt, Node, venv, npm, Chromium)
# hanya dibangun ulang bila berkas-berkas ini berubah, bukan tiap kali kode diedit (6 menit vs detik).
COPY deploy/ deploy/
COPY requirements.txt requirements.lock* .env.example ./
COPY remotion/package.json remotion/package-lock.json remotion/

# Satu layer: bootstrap lalu bersihkan cache supaya image tidak menyimpan unduhan.
RUN bash deploy/bootstrap.sh \
 && rm -rf /var/lib/apt/lists/* /root/.npm /root/.cache /tmp/*

# Tahap 2 - kode proyek. .env dan workspace/ tidak ikut (lihat .dockerignore).
COPY . /opt/klipa

# remotion/public/fonts adalah symlink di git, tapi COPY dari checkout Windows membawanya sebagai berkas teks
# (Remotion lalu 404). Pulihkan; no-op bila konteks build berasal dari Linux dan symlink-nya utuh.
# /root/AIHackfest: SKILL.md memanggil python3 /root/AIHackfest/scripts/...
RUN { [ -L remotion/public/fonts ] || { rm -rf remotion/public/fonts && ln -s ../../assets/fonts remotion/public/fonts; }; } \
 && ln -sfn /opt/klipa /root/AIHackfest

# python3 di PATH harus venv proyek.
ENV PATH="/opt/klipa-venv/bin:/opt/node/bin:${PATH}"

CMD ["bash", "deploy/verify.sh"]
