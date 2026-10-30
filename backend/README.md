# SELA Backend

FastAPI + SQLite. Jalankan di laptop dulu, deploy ke server paling akhir.

## 1. Setup di laptop

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements-dev.txt
cp .env.example .env
```

Buka `.env`, isi `SELA_API_KEY` dengan string acak panjang. Cara membuat:

```bash
python3 -c "import secrets; print(secrets.token_urlsafe(32))"
```

## 2. Jalankan tes

```bash
python -m pytest -q
```

Semua harus hijau sebelum lanjut.

## 3. Jalankan server lokal

```bash
set -a; source .env; set +a
uvicorn app.main:app --reload
```

Coba dari terminal lain (ganti KUNCI dengan isi `.env`):

```bash
curl -s localhost:8000/health

curl -s -X POST localhost:8000/telemetry -H "X-API-Key: $SELA_API_KEY" -H "Content-Type: application/json" -d '{"device_id":"sela-node-a","boot_id":"b1","seq":0,"ts":1000,"source":"synthetic","relay_on":true,"sensor_ok":true,"power":10}'

curl -s localhost:8000/state -H "X-API-Key: $SELA_API_KEY"
```

Dokumentasi interaktif otomatis ada di http://localhost:8000/docs

## 4. Docker Compose (masih di laptop)

```bash
docker compose up --build
```

Database tersimpan di folder `data/` sehingga tetap ada setelah container restart.

## 5. Deploy ke server

1. Salin folder ini ke server (git clone dari repo, tanpa `.env`).
2. Di server, buat `.env` sendiri dengan API key baru.
3. `docker compose up -d --build`
4. Di Security Group, buka port 8000 hanya untuk IP lu.
5. Tes dari laptop: `curl http://IP_SERVER:8000/health`
