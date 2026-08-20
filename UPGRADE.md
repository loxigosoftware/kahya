# Kâhya 2.0 — UPGRADE REHBERİ (v1 → v2)

> Bu doküman, `REDESIGN.md`'de mutabık kalınan mimariyi **adım adım** mevcut
> koda uygulamak için yazılmıştır. Her adım tamamlandığında aşağıdaki listede
> işaretlenir (☐ → ☑). Dönüşüm bu sırayla yapılır — atlama yok.
> Kullanıcı adımları tek tek onaylayarak ilerler.

---

## ⚙️ PROMPT — dokümanı okuyan AI operatörü için

You are the upgrade operator for **Kahya** (a self-hosted Turkish personal
operations assistant, v1 → v2). `REDESIGN.md` in this repository is the
**single source of truth** for the target architecture — it was agreed with
the owner decision by decision. This `UPGRADE.md` is your execution plan.

**Working rules:**

1. Read `REDESIGN.md` fully before starting. Every step below maps to
   sections of it; if a step conflicts with `REDESIGN.md`, the redesign wins
   — stop and ask the user.
2. Work **strictly step by step** (Step 0 → Step 9). Finish a step
   completely (all subtasks done, tests pass) before starting the next one.
3. When a step is complete: tick its checkbox in the **Adım listesi** below
   (`- [ ]` → `- [x]`) AND all its subtask checkboxes. Commit with a message
   like `v2: step N — <kısa özet>`. Do not batch steps into one commit.
4. Do not "fix" or refactor anything outside the step's scope. If you spot a
   bug that blocks the step, note it in the commit message or ask the user —
   do not silently change unrelated code.
5. Terminology is a hard rule: the word "ajan/agent" is **removed** from the
   whole system (UI, prompts, docs, DB, filenames). Everything is **amele**
   (proper noun — never translated, in any language, including English).
   Kahya's messages use patterns like "amele bakıyorum", "x amelesine
   gönderilsin mi? evet / hayır".
6. Every step must verify its output: run the existing tests, add/update
   tests for new behavior (`tests/`), and prove the acceptance criteria of
   the step before ticking it. "Works" is a claim, not a vibe — check the
   real outcome.
7. Data safety: before any destructive change (migration, table drops,
   renames), take a backup of `data/kahya.db` + `agents/` into a dated
   folder (e.g. `kahya-yedek-YYYYMMDD/`). Never lose user data.
8. If a step cannot be completed (blocker, missing decision, environment
   problem): stop, tick nothing, and report to the user in Turkish with the
   exact problem. Do not improvise a different architecture.
9. After the last step, update `README.md` to describe v2 (amele
   terminology, model strategy incl. Qwen3 27B note, backup instructions).

---

## ✅ Adım listesi (ana tikler)

- [x] **Step 0** — Hazırlık: yedek, git branch, mevcut testlerin doğrulanması
- [x] **Step 1** — DB migration: schema v2 (records, ameleler+model, amele_mcp, pending_actions, scheduled_tasks, conversation_messages+FTS) + veri dönüşümü
- [x] **Step 2** — Amele altyapısı: db_get/db_put yeniden yazımı + JSON doğrulama + amele index + model atama + ajan→amele adlandırma
- [ ] **Step 3** — Orkestratör (Kahya): get_amele_profile / find_ameleler / call_amele / search_history tool'ları + yeni Kahya promptu + 3 paslama limiti
- [ ] **Step 4** — Bot (Telegram): yeni komut seti (`/amele`, otomatik `/<slug>`), yönlendirme, oturum modu, eski komutların kaldırılması, i18n (amele çevrilmez)
- [ ] **Step 5** — Konuşma belleği: conversation_messages kaydı, 40 mesajda bir arşivleme, FTS arama, "geçmişten bak" akışı, gece yedekleme
- [ ] **Step 6** — Onay akışı + zamanlanmış görev: pending_actions yönetimi (başlıkta amele adı, en güncel eşleşme), scheduled_tasks tarayıcı (success/fallback)
- [ ] **Step 7** — Panel (web): Ameleler (CRUD + şema editor + model seçimi), Kayıtlar, Onaylar, Ayarlar, Tasks kaldırma, DB/Geçmiş indir butonları
- [ ] **Step 8** — MCP / Smithery: katalog arama, sunucu ekleme, amele bağlama, `amele mcp login`, sorumluluk beyanı ekranı
- [ ] **Step 9** — Test & yayın: uçtan uca senaryolar, Pi'de canlı kurulum, README güncelleme, v1 kapanış

---

## Step 0 — Hazırlık (güvenlik ağı)

**Amaç:** Dönüşüme başlamadan önce geri dönülebilir durumda olmak.

Alt görevler:

- [x] `data/kahya.db` + `agents/` + `.env` yedeğini `kahya-yedek-<tarih>/` klasörüne al
- [x] Git branch aç: `upgrade-v2`
- [x] Mevcut testlerin tamamını çalıştır (pytest tests/ veya tests/e2e.sh) — hepsi geçmeli
- [x] Ortam kontrolü: Python sürümü, `bin/amele` çalışıyor, SQLite FTS5 destekli mi (yoksa LIKE fallback planı)

**Kabul kriterleri:** Yedek klasörü var; branch açık; mevcut testler geçiyor; FTS5 durumu biliniyor.

**İlgili dosyalar:** `data/kahya.db`, `agents/`, `.env`, `tests/`

---

## Step 1 — DB migration (schema v2)

**Amaç:** REDESIGN §2'deki veri modeline geçiş; mevcut veri korunarak dönüşüm.

Alt görevler:

- [x] `scripts/migrate_v2.py` yaz: mevcut DB'yi oku, yeni şemayı kur, veriyi taşı
- [x] `agents` tablosu → `ameleler` (rename) + yeni alanlar: `model_kind` (local|api, default local), `model_name`, `model_cfg`
- [x] `items` → `records`: title→`ad`, amount/currency→`tutar`, due_date→`due_date`, note→`not`, kind→`tür` (REDESIGN §9)
- [x] `reminders` tablosunu kaldır (geçmiş logs'ta zaten var; veri kaybı yok)
- [x] Yeni tablolar: `mcp_servers`, `amele_mcp`, `pending_actions` (lang alanı dahil), `scheduled_tasks` (status: pending|success|failed), `conversation_messages` (+ index'ler), `conversation_fts` (FTS5; FTS5 yoksa LIKE fallback notu)
- [x] `chat_state`, `logs`, `settings`, `sessions`, `login_attempts` korunur (dokunma)
- [x] `kahya/db.py`'yi yeni şemaya göre güncelle (tüm CRUD yardımcıları)
- [x] Migration'ı yedek DB üzerinde test et, veri kaybını doğrula (satır sayıları karşılaştır)

**Kabul kriterleri:** Migration kopya DB'de sorunsuz çalışıyor; eski kayıtlar records'ta aynen duruyor; yeni tablolar boş ama hazır; mevcut testler (güncellenen) geçiyor.

**İlgili dosyalar:** `kahya/db.py`, `scripts/migrate_v2.py`, `data/kahya.db`

---

## Step 2 — Amele altyapısı

**Amaç:** Amelelerin veri sözleşmesi ve model esnekliği (REDESIGN §2.3, §2.4).

Alt görevler:

- [x] `tools/db_get.py` / `tools/db_put.py`'yi records sözleşmesine göre yeniden yaz (op: get/put/list/search; serbest JSON; şema sütunlarına dokunmaz)
- [x] **JSON doğrulama:** db_put tarafında (a) geçerli JSON, (b) şema varsa şemaya uygunluk kontrolü; hatalıysa hata çıktısı (amele yeniden üretir, max 2 deneme; yine olmazsa kullanıcıya rapor — bozuk JSON DB'ye asla yazılmaz)
- [x] **Amele index üretimi:** `kahya/db.py`'ye fonksiyon — `ameleler` tablosundan `id, slug, tek satır açıklama` listesi üret (amele CRUD'unda ve bot başlangıcında çağrılır)
- [x] **Model atama:** amele YAML'sine `model:` bloğu desteği; `amele_runner` her ameleyi kendi modeliyle çalıştırır (local: Ollama/yerel endpoint; api: dış sağlayıcı; anahtarlar ${VAR} referansı, asla düz metin)
- [x] Sistem ayarlarındaki LLM ayarının **yalnız Kahya** için kullanıldığını doğrula (başka ameleler onu kullanmaz)
- [x] **Ajan → amele adlandırma:** `agents/*.yaml` dosyalarını `-amele` sonekli adlarla yeniden adlandır (`pets-amele`, `fatura-amele`, `reminder-amele`...); içlerindeki "agent/ajan" kelimelerini temizle; amele prompt'larını jenerik görev/olay formatına çevir (REDESIGN §9.3)
- [x] Örnek amele ekle: `mail-amele.yaml` ve `hatirlatıcı-amele.yaml` şablonları (kullanıcının verdiği örnekler)

**Kabul kriterleri:** Bir amele Telegram mesajından kayıt yazabiliyor (doğru JSON + doğrulama); bozuk JSON reddediliyor; her amele farklı modelde çalışıyor; index üretimi çalışıyor.

**İlgili dosyalar:** `tools/db_get.py`, `tools/db_put.py`, `kahya/amele_runner.py`, `agents/*.yaml`, `kahya/db.py`

---

## Step 3 — Orkestratör (Kahya)

**Amaç:** Kahya'yı amele index'li, bağlamı şişirmeyen orkestratöre dönüştür (REDESIGN §3).

Alt görevler:

- [ ] `tools/get_amele_profile.py` — amele_id → tam tanım (açıklama, şema, tool listesi, bağlı MCP sunucuları)
- [ ] `tools/find_ameleler.py` — anahtar kelime araması (index'te eşleşme yoksa güvence)
- [ ] `tools/call_amele.py` — subprocess ile amele_runner'ı çağırır (REDESIGN §11 notu)
- [ ] `tools/search_history.py` — arşivde tam metin arama (Step 5 ile birlikte bağlanır)
- [ ] `agents/kahya.yaml` (Kahya config) yeniden yaz: kimlik + **kompakt amele index** (DB'den, ~600 token) + tool'lar + konuşma kalıpları ("amele bakıyorum", "x amelesine gönderilsin mi? evet / hayır")
- [ ] **Paslama limiti:** 3 paslama derinliği sayacı; aşılırsa zinciri durdur + kullanıcıya rapor (REDESIGN §3.3)
- [ ] Amelenin başka ameleye iş düşünce Kahya'ya dönmesi akışı (jenerik "şunu şu ameleye ilet" sözleşmesi)
- [ ] Sistem mesajları "amele eklendi" formatına geçsin (bot/panel mesajları)

**Kabul kriterleri:** Kahya bir mesajı doğru ameleye yönlendiriyor; index promptta görünüyor; 3 paslama sonrası zincir duruyor ve raporlanıyor.

**İlgili dosyalar:** `agents/kahya.yaml`, `tools/call_amele.py`, `tools/get_amele_profile.py`, `tools/find_ameleler.py`, `kahya/amele_runner.py`

---

## Step 4 — Bot (Telegram)

**Amaç:** Yeni komut seti ve yönlendirme (REDESIGN §4).

Alt görevler:

- [ ] Komut tablosu: `/amele` (listeleme), `/help`, `/iptal` + her etkin amele için otomatik `/<slug>` (Telegram: `-` → `_`; kullanıcı `/mail-amele` yazarsa tire normalize edilip eşleştirilir)
- [ ] `setMyCommands` güncellemesi (amele eklenip silindiğinde otomatik)
- [ ] Mesaj yönlendirme: `/<slug> ...` → doğrudan ameleye; `/` yoksa → Kahya karşılar (kendisi cevaplar / iletir / sorar)
- [ ] Oturum modu: `/<slug>` argümansız → sonraki mesajlar o ameleye; `/iptal` çıkar
- [ ] Eski komutları kaldır: `/add-agent`, `/edit-agent`, `/delete-agent`, `/add-job`, `/jobs`, `/done`
- [ ] Onay cevabı eşleştirme altyapısı (Step 6 ile tamamlanır): "evet/hayır/iptal" bekleyen onaylarla eşleşir, komut değildir
- [ ] `lang/tr.json` + `lang/en.json`: yeni string'ler; **"amele" çevrilmez** (özel isim — her dilde "amele")
- [ ] Bot'un Kahya konuşma kalıplarını kullandığını doğrula (Step 3'teki gibi)

**Kabul kriterleri:** `/amele` listeliyor; `/mail-amele mailleri oku` doğrudan ameleye gidiyor; `/mail-amele` (tireli) de çalışıyor; eski komutlar ölü; i18n'de "amele" korunuyor.

**İlgili dosyalar:** `kahya/bot.py`, `lang/tr.json`, `lang/en.json`, `kahya/i18n.py`

---

## Step 5 — Konuşma belleği

**Amaç:** Sabit boyutlu bağlam + arşiv + arama (REDESIGN §3.5).

Alt görevler:

- [ ] Her mesajda `conversation_messages` kaydı (thread_id: `chat:<id>` veya `amele:<id>:<slug>`; role; content; ts) + FTS index satırı
- [ ] **Arşivleme:** thread 40 mesajı aşınca en eski 20 mesaj `archived=1` (bağlamdan düşer); Kahya çağrılarında bağlam = son 20 ham mesaj
- [ ] FTS5 araması (`conversation_fts`); FTS5 yoksa LIKE fallback
- [ ] `search_history` tool'unu Kahya'ya bağla: "bunu konuşmuştuk / geçmişten bak" → arşivde ara → bulunan mesajlar bağlama eklenir
- [ ] Oturum modu mesajları da aynı kurala tabi (ayrı thread)
- [ ] **Gece yedekleme:** `scripts/backup.sh` (DB + agents + geçmiş dump) + örnek cron notu; yedek proje klasörüne
- [ ] Test: 60 mesajlık sohbet simüle et — bağlam hep ≤20 mesaj, arşivde hepsi duruyor, arama buluyor

**Kabul kriterleri:** Bağlam sabit boyutta; arşiv eksiksiz; "geçmişten bak" çalışıyor; yedek script'i çalışıyor.

**İlgili dosyalar:** `kahya/bot.py`, `tools/search_history.py`, `scripts/backup.sh`, `kahya/db.py`

---

## Step 6 — Onay akışı + zamanlanmış görev

**Amaç:** Komutsuz onay + alarm yeteneği (REDESIGN §7, §8).

Alt görevler:

- [ ] `pending_actions` yönetimi: waiting → approved | cancelled | done; `asked_at`, `resolved_at`, `lang`
- [ ] Onay sorusu **başlığında amele adı**: "📋 **mail-amele:** şunu yapmak istiyor — <özet>. Onaylıyor musun? evet / hayır / iptal" (seçili dilde)
- [ ] **Eşleştirme:** cevap en güncel bekleyenle (asked_at DESC) eşleşir; "mail-amele evet" formatıyla eski onaya da cevap verilebilir
- [ ] Onaylanınca amele `onay_id` ile çağrılır; red/iptal → döngü sonlanır
- [ ] `scheduler.py`'yi yeniden yaz: `scheduled_tasks` tarayıcı (vadesi gelen → `{"olay": "zaman", "record_id": N}` ile ameleyi tetikle)
- [ ] **Durum/fallback:** başarılı → `status='success'` + logs; hata → 3 deneme (1 dk ara) → olmazsa `pending` + kullanıcıya bildirim; görev sessizce kaybolmaz
- [ ] Şemadaki `virtual` zaman alanlarından görev üretimi (REDESIGN §2.2)
- [ ] Panelde Onaylar sekmesinden de karar verilebilmesi (Step 7'de bağlanır)

**Kabul kriterleri:** İki bekleyen onay varken cevap en güncel olana gidiyor; başlıkta amele adı var; zamanlı görev tetikleniyor, başarılıysa success işaretleniyor, hata durumunda bildirim çıkıyor.

**İlgili dosyalar:** `kahya/bot.py`, `kahya/scheduler.py`, `kahya/db.py`, `tools/call_amele.py`

---

## Step 7 — Panel (web)

**Amaç:** Panel v2 (REDESIGN §5).

Alt görevler:

- [ ] **Ameleler** sekmesi: CRUD (ad, slug, açıklama, durum) + **model seçimi** (local/api + model adı + ayarlar) + opsiyonel şema editor + MCP bağlama
- [ ] **Kayıtlar** sekmesi: amele seç → şemalıysa tablo (display alanları) + arama; şemasızsa ham JSON liste/editor; ekle/düzenle/sil
- [ ] **Onaylar** sekmesi: bekleyen onaylar + karar (evet/hayır/iptal)
- [ ] **MCP Sunucuları** sekmesi (Step 8'de işlevsellik; sekme burada)
- [ ] **Ayarlar:** LLM (yalnız Kahya — açıklama ile), Smithery API anahtarı, yedekleme bölümü
- [ ] **Tasks formunu kaldır** (başlık/tutar/para birimi) — tamamen
- [ ] Yedek butonları: **DB indir** (mevcut) + **Geçmiş (konuşma arşivi) indir** (yeni)
- [ ] Server route'ları (`kahya/server.py`) + `web/index.html` güncelle

**Kabul kriterleri:** Panelden amele ekleniyor (model atamayla), şema düzenleniyor, kayıtlar tablo/JSON görünüyor, Tasks yok, iki indirme butonu çalışıyor.

**İlgili dosyalar:** `kahya/server.py`, `web/index.html`, `kahya/db.py`

---

## Step 8 — MCP / Smithery

**Amaç:** Açık uçlu MCP bağlama + sorumluluk beyanı (REDESIGN §6).

Alt görevler:

- [ ] Panel → MCP Sunucuları → **Smithery katalog arama** (API anahtarı Ayarlar'dan)
- [ ] Sunucu ekleme: stdio (örn. `npx @smithery/cli run ...`) / http (kendi endpoint)
- [ ] Auth: statik header (${VAR}) veya OAuth (`amele mcp login` akışı)
- [ ] Amele bağlama: `amele_mcp` kaydı + amele YAML'sine `mcp:` bloğu + `permissions.tools` glob'ları otomatik yazım
- [ ] Tool filtreleri (include/exclude) + `amele explain` ön izlemesi ("bu sunucu ne katıyor")
- [ ] **Sorumluluk beyanı ekranı:** ilk sunucu bağlanmadan önce — üçüncü taraf kodu, sorumluluk kullanıcıda, veri kendi cihazında; kabul zorunlu
- [ ] "required: false" ile sunucu yokken botun açılabilmesi

**Kabul kriterleri:** Katalogdan sunucu bulunup ameleye bağlanıyor; amele ilgili tool'ları görüyor; beyan gösterilip kabul edilmeden bağlama yapılamıyor.

**İlgili dosyalar:** `kahya/server.py`, `web/index.html`, `kahya/db.py`, `agents/*.yaml`, `bin/amele`

---

## Step 9 — Test & yayın

**Amaç:** Uçtan uca doğrulama ve kullanıma geçiş.

Alt görevler:

- [ ] Test senaryoları (tests/ veya canlı): mail→hatırlatma paslaması, arama→mail cevabı, `/mail-amele mailleri oku`, `/pets-amele` kayıt, oturum modu
- [ ] Farklı amelelerde farklı modeller (lokal + api) çalışıyor
- [ ] Paslama limiti (3) + onay eşleştirme (en güncel) + başlıkta amele adı
- [ ] Uzun sohbet: bağlam penceresi + "geçmişten bak"
- [ ] Smithery'den sunucu bağlama + beyan akışı
- [ ] Zamanlı görev: success işareti + fallback bildirimi
- [ ] **Pi'de canlı kurulum:** install.py + deploy servisleri (bot, scheduler, web) — kullanıcının kendi sunucusunda
- [ ] Yedek doğrulaması: DB indir + Geçmiş indir + restore testi
- [ ] README.md güncelle: v2 mimarisi, amele terminolojisi, model stratejisi (Qwen3 27B önerisi), yedekleme talimatı
- [ ] v1 kapanışı: eski komutlar, Tasks formu, ajan adlandırması — sistemde hiçbir "ajan/agent" kalıntısı yok (grep doğrulaması)
- [ ] Git: tüm adımlar commit'li, `upgrade-v2` branch'i ana dala merge kararı kullanıcıda

**Kabul kriterleri:** Tüm senaryolar kullanıcının sunucusunda çalışıyor; README güncel; grep "ajan|agent" temiz (dokümanlardaki açıklama satırları hariç); kullanıcı onayı ile kapanış.

---

## Notlar

- Adım sırası bağımlılıklara göredir: 1→2→3→4→5→6→7→8→9. Ara adımda
  takılınca sonraki adıma geçilmez; sorun kullanıcıya raporlanır.
- REDESIGN.md ile bu rehber çelişirse REDESIGN.md geçerlidir.
- Her adımın sonunda: testler geçiyor + kabul kriterleri sağlanıyor + commit
  atılıyor → ancak o zaman tik.
