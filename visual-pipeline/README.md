# Visual Storytelling Pipeline — Kurulum Kılavuzu

## Mimari

```
Tarayıcı (Frontend)
    ↓ HTTP POST /run
Railway Backend (FastAPI)
    ├── A1: Makaleyi çekip analiz eder
    ├── A2: Görsel metafor + Gemini prompt üretir
    ├── GEN: Gemini ile 3 görsel paralel üretilir
    ├── A3: Görselleri puanlar, winner seçer, yönlendirir
    └── A0: Tüm pipeline'ı denetler, %90 confidence kontrolü
```

---

## Adım 1 — GitHub reposu oluştur

1. [github.com](https://github.com) → "New repository"
2. İsim ver: `visual-pipeline`
3. Public ya da Private (ikisi de olur)
4. Create repository

---

## Adım 2 — Dosyaları yükle

Reponuza şu dosyaları yükleyin (drag & drop ya da upload):

```
visual-pipeline/
├── main.py
├── requirements.txt
├── Procfile
├── railway.toml
└── static/
    └── index.html
```

---

## Adım 3 — Railway hesabı aç

1. [railway.app](https://railway.app) → "Login with GitHub"
2. "New Project" → "Deploy from GitHub repo"
3. `visual-pipeline` reposunu seç

---

## Adım 4 — Environment Variables ekle

Railway dashboard → proje → "Variables" sekmesi → şunları ekle:

| Key | Value |
|-----|-------|
| `ANTHROPIC_API_KEY` | Anthropic API key'in |
| `GEMINI_API_KEY` | `AIzaSyAAI-jmRIuk01VIIL79IzQGWEBHtvDs970` |

> ⚠️ GEMINI_API_KEY zaten main.py'de hardcoded var ama environment variable
> olarak set etmek daha güvenli (birden fazla yerden değiştirmen gerekmez).

---

## Adım 5 — Deploy

Railway otomatik olarak deploy eder. "Deploy" sekmesinden logları izle.
Deploy tamamlandığında sana bir URL verir:
`https://visual-pipeline-production-xxxx.up.railway.app`

---

## Adım 6 — Kullan

1. Railway URL'ini tarayıcıda aç
2. Herhangi bir makale/rapor URL'i gir
3. "Run Pipeline" tıkla
4. ~60-90 saniyede sonuç:
   - **A1** → makaleyi parse eder
   - **A2** → görsel konsept üretir
   - **GEN** → Gemini ile 3 görsel paralel üretir
   - **A3** → görselleri puanlar, winner seçer
   - **A0** → tüm pipeline'ı denetler, overall confidence verir
   - **%90+ → otomatik publish** / altı → human queue

---

## Anthropic API Key nereden alınır?

1. [console.anthropic.com](https://console.anthropic.com)
2. "API Keys" → "Create Key"
3. Kopyala, Railway'e yapıştır

---

## Lokal test (opsiyonel)

```bash
cd visual-pipeline
pip install -r requirements.txt
export ANTHROPIC_API_KEY="sk-ant-..."
export GEMINI_API_KEY="AIzaSy..."
uvicorn main:app --reload
# → http://localhost:8000 aç
```

---

## Sorun giderme

| Hata | Çözüm |
|------|-------|
| `CORS error` | Railway URL'in production'daki olduğundan emin ol |
| `Gemini 404` | Model adı değişmiş olabilir, main.py'de GEMINI_MODEL güncelle |
| `Claude error` | ANTHROPIC_API_KEY doğru set edilmiş mi kontrol et |
| `502 Bad Gateway` | Railway loglarına bak, timeout artırılabilir |
