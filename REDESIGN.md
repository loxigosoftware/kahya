# Kâhya 2.0 — Mimari Karar Dokümanı (FİNAL)

> Tüm kararlar kullanıcıyla birlikte verilmiştir (2026-08-20). Bu doküman,
> üzerinde mutabık kalınan mimarinin son halidir. Değişiklik ancak yeni bir
> mutabakatla yapılır.

---

## 0. Karar tablosu

| Konu | Karar |
|---|---|
| **Temel ilke** | Sistemde sabit "iş" kavramı **yoktur**: ne fatura, ne görev, ne reminder. Yalnız ameles + kayıtlar + iletişim |
| **Veri modeli** | `records` — serbest JSON kayıt + **opsiyonel** amele şeması (şema varsa panelde düzenli tablo/arama; yoksa ham JSON görünümü) |
| **Orkestrasyon** | **Kahya tam yetkili orkestratör** — tüm amelesin tanımlarını ve yeteneklerini bilir, görev dağıtımını yapar; anlamadığı mesajda kullanıcıya sorar |
| **Ameles arası iletişim** | Görev paslama **sadece Kahya üzerinden** — ameles birbirini doğrudan çağıramaz; her adım kullanıcıya raporlanır |
| **Zamanlanmış görev** | **Genel yetenek** ("alarm"): amele kendi şemasında zaman alanı tanımlarsa sistem o kayıtları izler ve ameleyi tetikler. Kullanmayan amele hiç etkilenmez |
| **Onay mekanizması** | Komut **yok** (`/onayla` kullanılmaz). Amele onay gerektiren aksiyonda **seçili yazışma dilinde** "evet / hayır / iptal" sorar; kullanıcı düz metin cevap verir |
| **MCP / Smithery** | **Açık uçlu**: kullanıcı Smithery (smithery.ai) kataloğuna kendi hesabıyla girer, **ne isterse** onu dilediği ameleye bağlar. Kahya yalnızca bağlama altyapısını sunar (katalog arama, sunucu ekleme, filtreleme, onay); hangi tool'un bağlanacağına **karar vermez** |
| **Telegram** | `/<slug> ...` → doğrudan o ameleye (örn. `/mail-amele mailleri oku`). `/` yazılmazsa Kahya karşılar, uygun ameleye iletir, anlamazsa sorar. Amele ekleme/düzenleme/silme **paneldedir**, Telegram'da yalnız liste + konuşma |
| **Amele bilgisi (Kahya'nın bağlamı)** | Sistem promptuna tüm amele promptları **gömülmez**. Kahya DB'den üretilen **kompakt amele index'i** (id + slug + tek satır açıklama, ~600 token) taşır; hedef ameleye gideceği anda `get_amele_profile` ile tam tanımı çeker. Token ~%95 azalır, ekstra LLM turu eklenmez |
| **Konuşma belleği** | Eski mesajlar **40 mesajda bir arşive taşınır** (LLM özeti üretilmez — sıfır maliyet); Kahya bağlamı **son 20 ham mesaj**dır. "Bunu konuşmuştuk / geçmişten bak" → `search_history` arşivde tam metin arar, bulduklarını bağlama ekler. Arşiv asla silinmez (gece yedek + DB dump) |
| **Model stratejisi** | **Her amele kendi modelini seçer**: lokal (Ollama/yerel endpoint) veya API. Sistem ayarlarındaki LLM ayarı **yalnız Kahya içindir**; diğer ameles panelden ayrı atanır (örn. görüntü analizi amelesi → lokal qwen3-vl:8b, mail amelesi → API model) |
| **Görev paslama limiti** | Maks **3 paslama** derinliği; aşılırsa Kahya zinciri durdurur, kullanıcıya rapor eder — kullanıcı sorunu anlayıp düzeltir |
| **JSON doğrulama** | `db_put` tarafında JSON + (varsa) şema doğrulaması; hatalı çıktıda amele yeniden üretir (max 2 deneme), olmazsa kullanıcıya rapor. Bozuk JSON DB'ye yazılmaz |
| **Yedekleme** | Veri Pi içinde proje klasöründe; panelde **DB indir** (mevcut) + **Geçmiş indir** (yeni) butonları — manuel yedek veya kullanıcının kendi cron'u |
| **Panel** | "Tasks" formu kaldırılır → **Kayıtlar** sekmesi (amele seç, tablo + arama + JSON) + Ameles (CRUD + opsiyonel şema + MCP bağlama) + MCP Sunucuları (Smithery katalog) |

---

## 1. Tasarım ilkeleri

1. **Sabit iş kavramı yok.** Fatura takibi bu sistemin bir örneğiydi, asla
   çekirdeği değil — belki hiç kimse kullanmayacak. Kod yapısında fatura,
   görev veya hatırlatma diye bir kavram bulunmaz.
2. **Amele = kendi config'i.** Her amele kendi YAML'ine, kendi tool'larına
   (subprocess + MCP), kendi kayıt şekline sahiptir. Amelenin "ne yaptığı"
   yalnız kendi açıklamasında yaşar — sistem bunu yorumlamaz, Kahya yorumlar.
3. **Kahya = orkestratör.** Tüm amelesin manifestini (ad, açıklama, şema,
   bağlı araçlar) bilir; görev dağıtımı ve ameles arası her iletişim onun
   üzerinden geçer.
4. **Her şey isteğe bağlı.** Şema opsiyonel, zamanlama opsiyonel, MCP
   opsiyonel, onay yalnız amelenin kendisinin "onay isterim" dediği aksiyonlarda.
5. **Kullanıcıyı kalıba sokma.** Telegram'da konuşulur, panelde
   yapılandırılır. Sistem kullanıcıya "şunu yapmak ister misin?" diye
   senaryo dayatmaz.
6. **Terminoloji: "amele".** "Ajan" (agent) kelimesi tüm sistemden
   kaldırılmıştır; arayüzde, sistem mesajlarında, Telegram komutlarında ve
   Kahya'nın konuşmalarında yalnız **amele** kullanılır (mail-amele,
   hatırlatıcı-amele...). "Amele" **özel isimdir** — i18n çevirilerinde
   hiçbir dile çevrilmez; İngilizce metinlerde de "amele" olarak kalır.
   Sistem mesajı örnekleri: "✔ mail-amele eklendi", "mail-amele silindi".
   Kahya konuşma kalıpları: "amele bakıyorum...", "x amelesine gönderilsin
   mi? evet / hayır", "mail-amele şunu kaydetti".

---

## 2. Veri modeli (SQLite)

### 2.1 Tablolar

```sql
-- ameles
CREATE TABLE ameles (
  id          INTEGER PRIMARY KEY,
  slug        TEXT UNIQUE NOT NULL,      -- ^[a-z0-9_-]{1,32}$
  name        TEXT NOT NULL,             -- görünen ad
  description TEXT NOT NULL,             -- amelenin ne yaptığı (Kahya bunu okur)
  schema_json TEXT,                      -- OPSİYONEL şema (alan tanımları)
  yaml_path   TEXT NOT NULL,             -- ameles/<slug>.yaml (amele config)
  model_kind  TEXT NOT NULL DEFAULT 'local', -- local | api  (her amele kendi modelini seçer)
  model_name  TEXT NOT NULL,             -- model adı (örn. qwen3:27b, qwen3-vl:8b, gpt-4o-mini)
  model_cfg   TEXT,                      -- model ayarları JSON (endpoint, api_key_ref, sıcaklık...)
  enabled     INTEGER NOT NULL DEFAULT 1,
  created_at  TEXT NOT NULL
);

-- kayıtlar (eski "items"ın yerine — her amele kendi şeklinde saklar)
CREATE TABLE records (
  id          INTEGER PRIMARY KEY,
  amele_id    INTEGER NOT NULL REFERENCES ameles(id) ON DELETE CASCADE,
  data_json   TEXT NOT NULL,             -- kaydın kendisi, serbest JSON
  created_at  TEXT NOT NULL,
  updated_at  TEXT NOT NULL,
  -- amele şemasında "virtual" işaretli alanlardan türetilen sanal sütunlar:
  -- (örnek) due_date TEXT GENERATED ALWAYS AS (json_extract(data_json,'$.due_date')) STORED
);
CREATE INDEX idx_records_amele ON records(amele_id);

-- MCP sunucuları (Smithery'den veya elle eklenir)
CREATE TABLE mcp_servers (
  id          INTEGER PRIMARY KEY,
  name        TEXT UNIQUE NOT NULL,      -- ^[a-z0-9_-]{1,32}$
  kind        TEXT NOT NULL,             -- stdio | http
  command     TEXT,                      -- stdio: argv (JSON dizisi)
  url         TEXT,                      -- http: endpoint
  headers     TEXT,                      -- JSON, ${VAR} referanslı
  env         TEXT,                      -- stdio env allowlist (JSON)
  auth        TEXT,                      -- oauth ayarları (JSON) veya null
  tools_include TEXT,                    -- glob listesi (JSON)
  tools_exclude TEXT,                    -- glob listesi (JSON)
  required    INTEGER NOT NULL DEFAULT 1,
  enabled     INTEGER NOT NULL DEFAULT 1,
  created_at  TEXT NOT NULL
);

-- amele ↔ MCP sunucu (çoktan çoğa — panelden bağlanır)
CREATE TABLE amele_mcp (
  amele_id  INTEGER NOT NULL REFERENCES ameles(id) ON DELETE CASCADE,
  server_id INTEGER NOT NULL REFERENCES mcp_servers(id) ON DELETE CASCADE,
  PRIMARY KEY (amele_id, server_id)
);

-- onay kuyruğu (dile göre evet/hayır/iptal akışı)
CREATE TABLE pending_actions (
  id          INTEGER PRIMARY KEY,
  amele_id    INTEGER NOT NULL REFERENCES ameles(id) ON DELETE CASCADE,
  action_json TEXT NOT NULL,             -- amelenin yapmak istediği aksiyon
  status      TEXT NOT NULL DEFAULT 'waiting', -- waiting | approved | cancelled | done
  lang        TEXT NOT NULL,             -- sorunun sorulduğu dil
  asked_at    TEXT NOT NULL,
  resolved_at TEXT
);

-- zamanlanmış görev ("alarm" yeteneği — isteyen amele kullanır)
CREATE TABLE scheduled_tasks (
  id          INTEGER PRIMARY KEY,
  amele_id    INTEGER NOT NULL REFERENCES ameles(id) ON DELETE CASCADE,
  record_id   INTEGER REFERENCES records(id) ON DELETE CASCADE, -- null = sabit tarife
  run_at      TEXT NOT NULL,             -- tetikleme zamanı
  status      TEXT NOT NULL DEFAULT 'pending', -- pending | success | failed | cancelled
  created_at  TEXT NOT NULL
);

-- konuşma belleği (Telegram bağlamı)
-- thread_id: "chat:<chat_id>" (genel sohbet) veya "amele:<chat_id>:<slug>" (amele oturumu)
CREATE TABLE conversation_messages (
  id          INTEGER PRIMARY KEY,
  thread_id   TEXT NOT NULL,
  role        TEXT NOT NULL,             -- user | assistant | system | summary
  content     TEXT NOT NULL,
  archived    INTEGER NOT NULL DEFAULT 0, -- 1 = bağlamdan düştü, arşivde
  ts          TEXT NOT NULL
);
CREATE INDEX idx_conv_thread ON conversation_messages(thread_id, id);
CREATE INDEX idx_conv_archived ON conversation_messages(thread_id, archived);

-- arşivde tam metin arama (SQLite FTS5; yoksa LIKE fallback)
CREATE VIRTUAL TABLE conversation_fts USING fts5(content, thread_id UNINDEXED);
-- mesaj eklenirken conversation_fts'a da satır yazılır; archive edilirken arama erişimi korunur

-- mevcut tablolar korunur: chat_state, logs, settings, sessions, login_attempts
-- KALDIRILAN: items, reminders (verileri records'a taşınır)
```

### 2.2 Opsiyonel şema örneği

```json
{
  "fields": [
    {"name": "ad",       "type": "string", "searchable": true},
    {"name": "izlendi",  "type": "bool",   "default": false},
    {"name": "not",      "type": "string"},
    {"name": "tarih",    "type": "date",   "virtual": true}
  ],
  "display": ["ad", "izlendi", "not"]
}
```

- `virtual: true` → alan generated column'a yansır; **zamanlanmış görev**
  yeteneği bu alanları izler (amelenin kendi seçimi — sistem dayatmaz).
- `searchable` → panel araması bu alana da bakar.
- Şema **yoksa**: kayıtlar serbest JSON; panelde ham JSON listesi/editor,
  Telegram'da amele zaten doğal dilde çalışır (şema yalnız panele düzen verir).

### 2.3 Kayıt CRUD

- **Telegram:** `/<slug> <doğal dil>` → amele mesajı anlar, kaydı JSON yazar.
- **Panel:** amele seç → şemalıysa form/tablo, değilse JSON editor → arama.
- **Ameles (amele tool'ları):** `db_get` / `db_put` sözleşmesi
  `records`'a göre yeniden yazılır (op: get/put/list/search, serbest JSON
  veri; şema sütunlarına dokunulmaz).
- **JSON doğrulama:** `db_put` tarafında çıktı doğrulanır — (a) geçerli JSON
  olmalı, (b) şema varsa alanlar şemaya uymalı. Doğrulama geçmezse ameleye
  hata döner ve amele çıktıyı yeniden üretir (en fazla 2 deneme); yine
  olmazsa Kahya kullanıcıya rapor eder. Bozuk JSON hiçbir koşulda DB'ye
  yazılmaz.

### 2.4 Amele model atama (lokal / API)

- **Her amele kendi modelini seçer.** `model_kind`: `local` (Ollama veya
  OpenAI-uyumlu yerel endpoint) ya da `api` (dış sağlayıcı). `model_cfg`
  JSON'da endpoint, anahtar referansı (${VAR}, asla düz metin), sıcaklık vb.
- **Sistem ayarlarındaki LLM ayarı YALNIZ Kahya içindir** — orkestratörün
  modeli. Diğer amelesin modeli paneldeki Ameles sekmesinden ayrı atanır.
- Esneklik örnekleri:
  - Kahya (orkestratör) → lokal güçlü model
  - Görüntü analizi amelesi → lokal `qwen3-vl:8b` (görsel model)
  - Mail amelesi → API model (ör. `gpt-4o-mini` veya kullanıcının seçimi)
- Model ataması amele YAML'sine `model:` bloğu olarak yazılır; amele_runner
  her ameleyi kendi modeline bağlar. Model ayarı amele düzenlenirken
  değiştirilebilir — kayıtlar etkilenmez.

---

## 3. Orkestrasyon — Kahya

### 3.1 Katmanlar

```
Kullanıcı (Telegram)
   │  mesaj
   ▼
KAHYA (orkestratör amele config)
   │  sistem promptu: kimlik + KOMPAKT AJAN INDEX'i
   │  (DB'den üretilir: id, slug, tek satır açıklama — 20 amele ≈ 600 token)
   │  tool'ları: db_get/db_put, telegram_send, call_amele,
   │             get_amele_profile, find_ameles, search_history,
   │             schedule, ask_confirm
   ├── kendisi yanıtlar (kayıt soruları, genel sorular)
   ├── amele index'inden hedefi seçer → get_amele_profile ile tam tanımı
   │   çeker → call_amele ile görevi iletir
   ├── index'te eşleşme yoksa → find_ameles ile arar → bulamazsa sorar
   └── "bunu konuşmuştuk" tarzı soruda → search_history ile arşivi tarar
   │
   ▼
Hedef amele (ameles/<slug>.yaml, ayrı amele config)
   ├── kendi kayıtları (records)
   ├── kendi MCP araçları (Smithery'den bağlananlar)
   └── başka ameleye iş düşerse → KAHYA'ya döner ("şunu şu ameleye ilet")
```

### 3.2 Amele bulma (amele discovery) — bağlamı şişirmeyen orkestrasyon

- **Amelesın promptları/tool'ları Kahya'nın sistem promptuna gömülmez.**
  Yerine DB'den otomatik üretilen **kompakt index** konur: her amele için
  `id, slug, tek satır açıklama`. Index bot başlangıcında ve amele
  eklenip/silinip güncellendiğinde yeniden üretilir (el ile bakım yok).
- Kahya mesajı alınca index'ten hedefi seçer; **yalnız o anda**,
  `get_amele_profile(id)` ile tam tanımı çeker (uzun açıklama, şema, tool
  listesi, bağlı MCP sunucuları) ve `call_amele` ile görevi iletir.
- `find_ameles(sorgu)`: index'te eşleşme bulamayan Kahya için anahtar
  kelime araması (güvence); sonuç yoksa kullanıcıya sorar.
- Kazanım: 20 amelelı bir kurulumda ~30K token sabit yük → ~600 token.
  Cevap süresi her mesajda belirgin düşer; ekstra LLM turu eklenmez
  (index her zaman promptta hazırdır).

### 3.3 Görev paslama (handoff)

- **Ameles birbirini doğrudan çağıramaz** — her şey Kahya üzerinden.
- Sözleşme (JSON):
  ```json
  {"görev": "bilet rezervasyonunu hatırlatma kaydı olarak işle",
   "bağlam": {"mail": "...", "tarih": "2026-09-12"},
   "beklenen_çıktı": "kayıt oluşturuldu / hata"}
  ```
- Her paslama kullanıcıya raporlanır: "Arama amelesine gönderdim → sonuç geldi
  → mail amelesi taslağı hazırladı → onay bekliyor."
- **Maks paslama derinliği: 3.** Görev zinciri en fazla 3 paslamayla sınırlıdır
  (örn. Kahya → A → B → C). Limit aşılırsa Kahya zinciri durdurur, kullanıcıya
  rapor eder ("şu görev X amelesinde takıldı, sorun: ...") — kullanıcı sorunu
  anlayıp düzeltir, tekrar denemek isterse yeniden başlatır. Sınırsız paslama
  = sınırsız LLM maliyeti olduğu için bu kural sistem güvencesidir.

### 3.4 Örnek senaryolar (kullanıcının verdiği)

1. **Mail → hatırlatma:** mail amelesi bilet rezervasyon mailini okur →
   "Hatırlatma amelesine kaydettireyim mi? evet / hayır" → evet → Kahya
   hatırlatma amelesine görev iletir → o kendi şemasına göre kaydeder.
2. **Arama → mail cevabı:** kullanıcı "internetten X'i araştır, ona göre
   cevap yaz" der → Kahya arama amelesine görev verir → sonucu mail amelesine
   bağlam olarak iletir → mail amelesi taslağı üretir → onay → gönderir.
3. **Birden çok ameleye paslama:** tek istek birden fazla ameleye bölünebilir;
   Kahya parçaları dağıtır, sonuçları derler, kullanıcıya tek rapor sunar.

### 3.5 Konuşma belleği (bağlam yönetimi)

Telegram'da konuşma penceresi sınırsız büyür — bağlam doldukça halüsinasyon
riski artar. Kâhya her çağrıda tüm geçmişi değil, **sabit boyutlu pencereyi**
görür; geçmiş arşivde kalır ve istenince aranır.

- **Thread kavramı:** genel sohbet (`chat:<id>`) ve her amele oturumu
  (`amele:<id>:<slug>`) ayrı thread'tir; biri büyüyünce diğeri etkilenmez.
- **Bağlam penceresi:** Kahya'ya her çağrıda **son 20 ham mesaj** verilir
  (LLM özeti üretilmez — sıfır maliyet, bilgi kaybı yok; ham metin arşivde).
- **Arşivleme:** thread 40 mesajı aşınca en eski 20 mesaj `archived=1`
  yapılır (bağlamdan düşer, arşivde kalır). FTS index'i güncellenir.
- **Geçmiş sorgusu:** kullanıcı "bunu konuşmuştuk", "geçmişten bak" derse
  Kahya `search_history(sorgu)` tool'unu çağırır → arşivde tam metin arar
  (FTS5; FTS5 yoksa LIKE fallback) → ilgili mesajları bağlama ekleyip
  cevap verir. **Arşiv asla silinmez** — gece yedekleme + periyodik DB dump.
- Oturum modunda (`/<slug>` ile başlayan) amele konuşması da aynı kuralla
  yaşlanır — amelenin eski konuşması genel sohbeti kirletmez.

---

## 4. Telegram

### 4.1 Komutlar

| Komut | İş |
|---|---|
| `/mail-amele ...` | Doğrudan o ameleye mesaj (örn. `/mail-amele mailleri oku`) |
| `/<slug>` (argümansız) | O ameleyle oturum modu — sonraki mesajlar o ameleye gider, `/iptal` çıkar |
| `/amele` | Amelesi listeler (açıklama + kayıt sayısı) — ekleme **yok** |
| `/help` | Komut listesi |
| `/iptal` | Akışı/oturumu iptal |

- `setMyCommands`: `/amele`, `/help`, `/iptal` + her etkin amele için
  otomatik `/<slug>` — Telegram komut adları yalnız a-z0-9_ kabul eder:
  slug `mail-amele` → kayıtlı komut `/mail_amele`. Kullanıcı `/mail-amele`
  yazarsa bot tireyi alt çizgiye çevirip eşleştirir (kullanıcı dostu).
- Amele komutları her amele eklenip silindiğinde otomatik güncellenir.
- **Kaldırılanlar:** `/add-amele`, `/edit-amele`, `/delete-amele`,
  `/add-job`, `/jobs`, `/done` — hepsi panelin işi.

### 4.2 Mesaj yönlendirme

- `/<slug> ...` → doğrudan hedef ameleye (Kahya'yı atlar).
- `/` ile başlamayan mesaj → **Kahya** karşılar: kendisi cevaplar, uygun
  ameleye iletir ya da sorar. Proje adı: **kahya** (orkestratör).
- Onay cevapları ("evet", "hayır", "iptal" — seçili dilde) bot tarafından
  bekleyen onayla eşleştirilir, komut değildir.

---

## 5. Admin panel

| Sekme | İçerik |
|---|---|
| **Genel Bakış** | Amele/kayıt sayıları, bekleyen onaylar, yaklaşan zamanlanmış görevler |
| **Ameles** | Amele CRUD: ad, slug, açıklama (prompt), **opsiyonel şema düzenleyici**, MCP bağlama (hangi sunucular), durum |
| **Kayıtlar** | Amele seç → şemalıysa tablo (display alanları) + arama; şemasızsa ham JSON liste/editor; ekle/düzenle/sil |
| **MCP Sunucuları** | **Smithery katalog arama** (hesap login) + elle sunucu ekleme (stdio/http, auth, filtreler) + bağlantı testi (`amele explain`) |
| **Onaylar** | Bekleyen onayların listesi (kullanıcı buradan da karar verebilir) |
| **Ayarlar** | LLM (sistem ayarı — **yalnız Kahya için**), yedekleme, Smithery API anahtarı |

- "Tasks" formu (başlık/tutar/para birimi) **tamamen kaldırıldı**.
- Amele ekleme/düzenleme yalnız panelde — Telegram'da yalnız konuşma.
- **Yedekleme:** veriler Pi içindeki proje klasöründe tutulur (kullanıcı
  kararı). Panelde **DB indir** (mevcut) + **Geçmiş (konuşma arşivi) indir**
  (yeni) butonları — kullanıcı dilediği an manuel yedek alır; otomatik
  yedekleme dilerse kendi cron'u ile yapar. Yedekler proje klasöründe
  saklanır, kullanıcı dışarı taşımakta özgürdür.

---

## 6. MCP / Smithery entegrasyonu

### 6.1 Model

- **Kahya seçim yapmaz, altyapı sunar.** Kullanıcı Smithery hesabıyla
  panele girer → katalogda arar → "bağla" der → Kahya sunucuyu kaydeder,
  (gerekirse OAuth login akışını başlatır) ve istediği ameleye ekler.
- Her amelenin YAML'sine `mcp:` bloğu + `permissions.tools` glob'ları
  otomatik yazılır (amele 415f781 MCP client — altyapı hazır, Kahya yalnız
  config üretir).
- Amele → sunucu bağlantısı `amele_mcp` tablosunda; bir sunucu birden çok
  ameleye bağlanabilir (örn. gmail → "Kişisel" ve "Fatura" amelesi).

### 6.2 Akış

1. Panel → MCP Sunucuları → Smithery kataloğu → arama ("gmail", "takvim",
   "whatsapp"...) → sunucu seç.
2. Sunucu tipi: **stdio** (genelde `npx @smithery/cli run ...` — Pi'ye bir
   kere Node kurulur) veya **http** (kendi endpoint'in).
3. Auth: statik header (${VAR}) veya OAuth (`amele mcp login`, bir kere).
4. Amele seç → tool filtreleri (include/exclude) → kaydet.
5. `amele explain` ile "bu sunucu ne katıyor" ön izlemesi.

### 6.3 Dürüst risk notları (sisteme değil, kullanıcı tercihlerine dair)

- **Sorumluluk kullanıcıya aittir.** Smithery'den bağlanan sunucular
  üçüncü taraf kodudur; güvenilirliği ve davranışı seçilen sunucuya bağlıdır.
  Kâhya yalnızca bağlama altyapısını sunar, seçilen sunucunun doğruluğunu
  garanti etmez. Tüm veriler **kullanıcının kendi cihazında (Pi) saklanır**;
  bağlanan bir sunucuya yalnız kullanıcının o ameleye verdiği görevler
  çerçevesinde veri gider. Bu beyan, panelde ilk sunucu bağlanmadan önce
  kullanıcıya açıkça gösterilir ve kabulü istenir.
- Smithery'de bakımsız sunucular olabilir — seçim kullanıcının; sistem
  "required: false" ile başlatma ve explain ön izlemesi sunar.
- Hosted (bulutta çalışan) sunucularda veri üçüncü taraf sunucudan geçer —
  gizlilik isteyen self-hosted/stdio kurulum seçer.
- Tehlikeli araçlar (mail gönderme, silme, ödeme) onay akışına tabidir (§7);
  kullanıcı isterse onaysız da kullanabilir — kendi seçimi.

---

## 7. Onay akışı (komutsuz)

1. Amele tehlikeli/onaylı aksiyon yapmak ister → `pending_actions` kaydı +
   Telegram'a soru. Soru **başlığında amelenin adı** yer alır:
   **"📋 mail-amele: şunu yapmak istiyor — <aksiyon özeti>. Onaylıyor
   musun? evet / hayır / iptal"** (seçili yazışma dilinde; i18n dil
   paketinden).
2. Kullanıcı düz metin cevap verir ("evet", "hayır", "iptal" veya seçili
   dildeki karşılıkları).
3. **Eşleştirme kuralı:** aynı anda birden çok onay bekleyebilir; kullanıcının
   cevabı **en güncel bekleyen onayla** (asked_at DESC) eşleşir. Kullanıcı
   eski bir onaya cevap vermek isterse sorunun başlığındaki amele adını
   söyler ("mail-amele evet" gibi) — bot adı arar, bulamazsa en güncel olanı
   kullanır.
4. Onaylanırsa → amele aksiyonu tamamlar (onay_id ile çağrılır); reddedilirse
   iptal edilir; "iptal" → aksiyon iptal, amele döngüsü sonlanır.
5. `/onayla` gibi sabit bir komut **yoktur** — yeni dil eklemek yalnız
   i18n'e kelime eklemektir.

---

## 8. Zamanlanmış görev (alarm yeteneği)

- `scheduled_tasks` + amele şemasındaki `virtual` zaman alanları.
- Tarayıcı (Pi'de cron benzeri): vadesi gelen görevleri toplar → hedef ameleyi
  jenerik tetikleme mesajıyla çağırır:
  `{"event": "time", "record_id": 12}` — amele kendi kaydını okur, ne
  yapacağına kendisi karar verir (bilgi, uyarı, MCP aksiyonu, onay iste...).
- **Durum ve fallback:** görev başarıyla tamamlanırsa DB'de
  `status='success'` olarak işaretlenir (ne zaman yapıldığı logs'a yazılır).
  Amele çalışmazsa veya hata verirse: 3 deneme (1 dk arayla) → hâlâ olmazsa
  görev `pending` kalır ve kullanıcıya bildirilir ("şu görev çalışmadı: ...").
  Görevler asla sessizce kaybolmaz — her sonuç (success/failed) kayıt
  altındadır.
- Bu **sistemin özelliği değil, bir yeteneğidir**: şemasında zaman alanı
  olmayan ameles hiç etkilenmez; "hatırlatma" isteyen kullanıcı kendi
  amelesinde bu alanı tanımlar.
- Eski `reminders` tablosu kalkar; veri kaybı olmaz (kayıtlar records'ta).

---

## 9. Migration (mevcut kurulumdan)

1. Yedek al (mevcut `kahya-yedek-20260819/` üzerine yeni yedek).
2. Yeni şema: `items` → `records` (title→`ad`, amount/currency→`tutar`,
   due_date→`due_date`, note→`not`, kind→`tür`), `reminders` → silinir
   (geçmiş gönderim kayıtları logs'ta zaten var).
3. Mevcut amele YAML'leri: `db_get`/`db_put` sözleşmesi records'a göre
   güncellenir; prompt'lar "REMINDER görevi" yerine jenerik görev/olay
   formatına çevrilir.
4. Panel ve bot yeni akışa geçer (eski komutlar kaldırılır).
5. Pi'de canlı test: eski kayıtlar korunarak yükseltme doğrulanır.

---

## 10. Uygulama sırası (mutabakat sonrası)

1. **DB migration** — schema v2 (records, mcp_servers, amele_mcp,
   pending_actions, scheduled_tasks, conversation_messages + FTS) + eski
   veri dönüşümü.
2. **Amele altyapısı** — db_get/db_put sözleşmesi yeniden yazımı (JSON
   doğrulama dahil); **amele index üretimi** (id, slug, tek satır açıklama —
   amele CRUD'unda ve bot başlangıcında otomatik); **model atama** (local/api
   model_kind + model_name + model_cfg → amele YAML'sine `model:` bloğu).
3. **Orkestratör** — Kahya config'i: get_amele_profile/find_ameles/
   call_amele/schedule/ask_confirm tool'ları (python tool'ları olarak) +
   Kahya promptu (kimlik + amele index).
4. **Bot** — yeni komut seti, `/slug` dinamik komutları, oturum modu, onay
   cevabı eşleştirme; eski komutların kaldırılması.
5. **Konuşma belleği** — conversation_messages kaydı, 40 mesajda bir
   arşivleme (bağlam = son 20), FTS araması, `search_history` tool'u,
   gece yedekleme.
6. **Panel** — Ameles (şema editor) + Kayıtlar (tablo/JSON/arama); Tasks
   formunun kaldırılması.
7. **MCP** — Smithery katalog arama + sunucu yönetimi + amele bağlama +
   amele config üretimi + `amele mcp login` akışı.
8. **Zamanlanmış görev + onay akışı** — scheduled_tasks tarayıcı +
   pending_actions yönetimi.
9. **Test** — kullanıcı kendi sunucusunda test eder: Pi'de canlı kurulum,
   uçtan uca senaryolar (mail→hatırlatma, arama→mail, /personal kayıt,
   Smithery'den sunucu bağlama, "geçmişten bak" sorgusu, uzun sohbette
   bağlam penceresi, farklı amelesde farklı modeller, paslama limiti,
   onay eşleştirmesi).

---

## 11. Açık/ertelenen notlar

- `call_amele` tool'unun gerçekleştirimi: `tools/call_amele.py` subprocess
  tool (amele_runner'ı çağırır) — Kahya python'u zaten amelesi çalıştırıyor.
- Smithery API anahtarı panel ayarlarına eklenir (katalog araması için);
  sunucu login'leri `amele mcp login` ile.
- Harici katalog (mcp.so, glama) ileride eklenebilir — mimari aynıdır.
- Konuşma belleğinde LLM özeti ilk sürümde **yok** (karar: arşivle + ara).
  Thread'ler çok büyürse (binlerce mesaj) ileride opsiyonel özet katmanı
  eklenebilir — `conversation_messages` şeması buna izin verir (role=summary).
- **Önerilen model:** geliştirici testlerinde **Qwen3 27B** (lokal, Ollama)
  kullanılmaktadır — Kahya için güçlü bir yerel seçenektir. Uzman ameles
  kendi işine uygun modeli seçer (örn. görüntü analizi için `qwen3-vl:8b`,
  hızlı/ucuz işler için küçük API modelleri). Model seçimi kullanıcının
  tercihine açıktır; bu yalnız bir başlangıç önerisidir.
