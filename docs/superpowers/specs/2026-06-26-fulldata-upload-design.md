# 設計規格：官方完整遊玩資料上傳與融合（Full-Data Overlay）

- 狀態：草案，待實作
- 日期：2026-06-26
- 設計者：使用者 + Claude（Opus 4.8）+ Codex GPT-5.5（xhigh）對抗式 review
- 相關程式：`pipeline/`、`server/engine.py`、`server/app.py`、`server/cloudstore.py`、`web/`

---

## 1. 目標與動機

現況：gdskill-match 的每位玩家只由 **gsv.fun 的技能帳（HOT 25 + OTHER 25）** 描述 ——
即一條跨 ~3920 譜面、僅 50 個非零的稀疏向量，帶有嚴重**倖存者偏差**（只看得到夠強、
留在帳上的曲）。

目標：讓**自願**的玩家，用書籤工具（bookmarklet，gsv 同款模式）從 **KONAMI e-amusement
官方頁** 抓取自己的**完整遊玩資料**（數百～上千張譜面，含達成率），上傳到 gdskill-match，
把自己的向量**稠密化**，藉此提高自己的 profile / 相似玩家 / 對手 / 選曲推薦的精準度；
並讓有上傳的玩家成為（可選擇公開的）更豐富的比較節點。

非目標（本期不做）：
- 不做伺服器端 KONAMI 登入/驗證（資料一律由玩家在自己已登入的瀏覽器抓取）。
- 不立刻用少量上傳資料去改寫全域 per-chart 統計（見 §7 的選擇偏差討論）。
- 暫不延伸到 GuitarFreaks（引擎已參數化，未來可加）。

---

## 2. 官方頁面結構（實測結論）

base 路徑：`/game/gfdm/gitadora_galaxywave_delta/p/playdata/`

| 來源 | URL | 提供 | 成本 |
|---|---|---|---|
| 曲別成績（列表） | `music.html?gtype=dm&cat=<0..36>` | **37 個類別頁，無類別內分頁**；每列一首歌、4 個難度欄（BASIC/ADVANCED/EXTREME/MASTER），每格只有一個 best-rank 獎牌（CSS class `score_data_rank <RANK>`，列表頁為 `icon<RANK>`，`RANK ∈ {SS,S,A,B,C,D,E,-}`，`-`＝未玩） | 37 GET（便宜） |
| 曲別成績（單曲詳細） | `music_detail.html?gtype=dm&sid=<songid>&index=&cat=&page=` | 每首 4 個難度區塊（`diff_BASIC/ADVANCED/EXTREME/MASTER`），各含：level、プレー回数、クリア回数、最高ランク、**達成率（exact %，例：MASTER 65.15%）**、ハイスコア、MAX COMBO | 1 GET / 首（規模大時昂貴） |
| プロフィール | `profile.html` | **ギタドラ ID**（例 `HG12B7108F`）、カードナンバー（**敏感，禁止存取/傳輸**）、玩家名、DM Skill（top-50 總分）、全曲 Skill（全部已玩加總，可作驗證）、クリア/フルコンボ/エクセレント 曲數與最高難度 | 1 GET |
| スキル対象曲 | gsv GraphQL（既有） | top-50 exact（HOT25+OTHER25） | 已有 |

關鍵洞察：
- `sid` 為穩定曲 id（例 `sid=2`＝「10,000,000,000」）；一次 detail GET 拿到該曲 4 譜面的 exact 達成率。
- 列表頁的 rank 獎牌＝**全庫粗略達成率帶**，只要 37 個請求即可覆蓋整個曲庫的 presence + rank。
- `達成率` 是計算 skill 的關鍵：`skill = level × 20 × achievement`。

privacy guard 註記：開發/觀察時，MCP 的 `javascript_tool` 會把含 query string／cookie 的回傳值
整段遮蔽（回 `{}` 或 `[BLOCKED]`）。書籤本身在玩家瀏覽器執行，不受此限；此註記僅供未來再觀察時參考。

---

## 3. 端到端架構

```
1. 玩家在 gdskill-match 開自己的玩家頁 → 點「上傳官方完整資料」
2. App 回一份「上傳規格(upload spec)」：gsvPlayerId、version、challenge 曲、
   目前推薦/kasegi 候選曲、detail 抓取上限、上傳端點與 token
3. 玩家點書籤（bookmarklet）—— 只在「已登入的 eagate 分頁」內執行
4. 書籤：rank 全庫掃描 → 針對性 detail 抓取 → 瀏覽器端快取 → 序列化節流 → POST（或下載 JSON 後備）
5. 獨立 Upload Function 驗證 + 連結身分/隔離 + 寫入 GCS userdata/*
6. Cloud Run 載入 base artifacts + 該玩家 overlay（lazy、即時生效，不等每日重建）
7. 每日 updater 重建 base(gsv) artifacts + 編譯 overlay 索引；【絕不】把上傳觀測併入 base 統計
```

**核心不變式**：上傳的稠密資料走 **overlay 疊加層**，**不**寫進 `matrix.npz`、**不**重算
`chart_mean / chart_std / idf / SVD`。理由：base 矩陣 presence 的語意是「進過 gsv top-50」，
上傳 presence 的語意是「曾經玩過」；兩者混用會污染既有統計與相似度。

元件邊界（各自單一職責、可獨立測試）：

| 元件 | 職責 | 輸入 | 輸出 |
|---|---|---|---|
| `web/bookmarklet/` | 在 eagate 抓資料、節流、快取、組 payload、上傳 | upload spec、玩家 eagate session | 正規化 JSON payload |
| `upload_fn/`（Cloud Function） | 驗證、身分連結/隔離、寫 GCS | POST payload | `userdata/*` 物件、回 share token |
| `server/overlay.py` | 從 GCS lazy 載入單一玩家 overlay、快取 | gsvPlayerId、version | overlay 物件（obs 陣列） |
| `server/engine.py`（擴充） | overlay-aware profile/similar/rivals/song_recs | base artifacts + overlay | API JSON |
| `pipeline/build_official_stats.py`（新） | 跨上傳者聚合 `official_play_stats`（階段式，§7） | `userdata/latest/*` | `official_stats.json` |
| `web/`（前端） | 上傳 UI、書籤安裝、狀態、enhanced 推薦顯示、公開切換 | API | 畫面 |

---

## 4. 抓取層（書籤）

採「**rank 掃描 + 針對性 detail**」（使用者決策）。

### 4.1 模式與請求數

| 模式 | KONAMI 請求數 | 說明 |
|---|---:|---|
| rank 掃描 | **37** | 全庫 presence + rank 帶 |
| **標準（預設）** | 37 + 約 80–150 detail ≈ **120–190** | 足以實質改變推薦/profile；約 5–10 分鐘 |
| 完整（進階/手動） | 37 + ~1000 detail | 不預設；約 35–60 分鐘 |

### 4.2 節流與快取（降伺服器負擔，硬性要求）

- **序列、無並行**：類別頁間隔 1–1.5s；detail 間隔 2–3s，加隨機 jitter。
- 遇 `429 / 5xx` → 指數退避並在連續失敗時**停止**，回報已抓到的部分。
- **瀏覽器端快取**（localStorage）key＝`{version, sid}`：存 rank 快照、detail 欄位、`fetchedAt`。
- **增量再同步**：每次一定先做便宜的 37 頁 rank 掃描；detail 只在「rank 改變 / 逼近 pool 門檻 /
  過期（如 >14 天）/ 新進推薦集」時才重抓。gsv top-50 視為已知 exact，不重抓（除少量身分驗證抽樣）。

### 4.3 detail 抓取優先序（曲級；一次 GET ＝ 4 譜面）

對某首歌，若其任一譜面滿足：
```
rank != "-"  AND  exact 快取缺/過期/rank 改變  AND (
    P(level*20*achievement > 該 pool 目前 cutoff + 2) >= 0.10
    OR 該曲在目前 engine 推薦 / kasegi / 相似玩家曲集
    OR 高 rank × 可及高難度
    OR 身分驗證 challenge 曲
)
```
→ 納入 detail 佇列。佇列依「預估對 skill 的影響」排序，截到模式上限。

### 4.4 跨源與後備

- 書籤在 eagate 抓 eagate 頁＝**同源**，無 CORS 問題。
- 僅「最後 POST 到 gdskill-match 後端」是跨源 → 見 §5.3。
- **後備路徑**：書籤可改為「下載正規化 JSON」，玩家再到 app 自己的上傳頁（同源）送出，完全避開 CORS。

---

## 5. 後端：上傳與儲存

### 5.1 獨立 Upload Function

- 新的 **HTTP Cloud Function**（`upload_fn/`），**不**動現有 GET-only `server/app.py`（維持唯讀低風險）。
- 專屬最小權限 SA：只能寫 `gs://$GCS_BUCKET/userdata/*`；Cloud Run 對 `userdata/` 唯讀。

### 5.2 Payload schema（v1）

```json
{
  "schema": 1,
  "version": "galaxywave_delta",
  "game": "gitadora", "gtype": "dm",
  "gsvPlayerId": 12345,
  "uploadToken": "<app 發的一次性 token>",
  "scrapedAt": "2026-06-26T12:00:00Z",
  "profile": { "gitadoraId": "HG12B7108F", "playerName": "LASK",
               "drumSkillPoint": 7530.40, "allSongSkill": 24020.46 },
  "charts": [
    { "sid": "2", "name": "10,000,000,000", "diff": "MAS",
      "rank": "B", "achievement": 0.6515, "exact": true,
      "level": 8.45, "playCount": 3, "clearCount": 1,
      "hiScore": 1234567, "maxCombo": 456 }
  ]
}
```

### 5.3 驗證、限制、CORS

- 限制：`schema==1`、≤ 5–8 MiB（完整模式上限可 15 MiB）、≤ 5000 列、`rank ∈ enum`、
  `0 ≤ achievement ≤ 1`、`0 ≤ level ≤ 10`、**拒收任何原始 HTML / cookie / 驗證資料 / カードナンバー**。
- CORS：實作 `OPTIONS` 預檢；`Access-Control-Allow-Origin` 只允許 `https://p.eagate.573.jp` 與
  Cloud Run origin；`credentials: omit`；`Vary: Origin`。並保留 §4.4 的下載後備。
- 速率限制：以 `gsvPlayerId` + IP 粗略限流（如每帳號每小時數次），擋濫用。

### 5.4 GCS 佈局與持久化

```
userdata/raw/v1/<version>/<gsvPlayerId>/<uploadId>.json   # 不可變原始上傳
userdata/latest/v1/<version>/<gsvPlayerId>.json           # 最新指標（含 visibility 旗標）
userdata/unlinked/v1/<uploadId>.json                       # 身分未連結/可疑 → 隔離
```

- 每日 base 重建（`cloudstore.rebuild_and_upload`）**絕不**刪除/覆蓋 `userdata/*`。
- source of truth 永遠是 `userdata/*`；overlay/官方統計都是「衍生編譯產物」。

---

## 6. 引擎融合（overlay-aware）

新增 `server/overlay.py`：依 `gsvPlayerId` 從 GCS lazy 載入 `userdata/latest/...`，
解析為觀測陣列並快取（含 TTL，讓上傳後很快生效）。`engine.py` 在處理該玩家時注入 overlay。

每個觀測：
```
obs_kind: unknown | gsv_exact | upload_exact | upload_rank
played_mask, eval_mask, obs_ach_mean, obs_ach_var, obs_weight
```

### 6.1 rank → 達成率（貝氏區間，不把中點當 exact）

```
rank 先驗（區間 [L,U]）:  mu_rank=(L+U)/2,  var_rank=(U-L)²/12
脈絡先驗 mu_ctx:          engine._expected_ach() 既有混合（同段持有者均值 + kasegi + chart_mean）
後驗（精度加權, clip 回 [L,U]）:
  mu_post = clip((τ_rank·mu_rank + τ_ctx·mu_ctx)/(τ_rank+τ_ctx), L, U)
  var_post = 1/(τ_rank+τ_ctx)
  skill_mean = level·20·mu_post,   skill_sd = level·20·√var_post
z（含不確定度）:  z = (skill_mean − bracket_mean)/√(bracket_std² + skill_sd²)
```
權重：`upload_exact` / `gsv_exact` = 1.0；`upload_rank` = 0.15–0.65（依帶寬與脈絡支撐）。
`unknown` 不視為 0（不貢獻、也不當成 0 表現）。
（rank→[L,U] 官方對照表於實作期由觀測校正；已知錨點：65.15% ⇒ B。）

### 6.2 相似度（必須非對稱）

- 現行對稱加權 Jaccard 會**懲罰**稠密玩家（union 太大）→ 改為：
  「稀疏對手的 top-50 口味，有多少落在上傳者的加權已玩/高 skill 集合裡」（非對稱涵蓋率）。
- **style**：以「共同曲」overlap 正規化，乘 `obs_weight`；不直接用既有全列 cosine 套在稠密玩家上。
- **latent（SVD）**：emb 是用稀疏 gsv 列訓練的 → 對 overlay 玩家**降權**，或只投影 top-50-like/高 skill 子集；
  在 builder 另存 SVD components 並驗證投影行為前，latent 對 overlay 玩家僅作次要參考或關閉。

### 6.3 profile 與選曲

- **profile**：用上傳觀測，但濾掉低訊號曲（只顯示有支撐、可及、近段位的強弱），避免被海量低難度曲洗版。
- **選曲推薦** 拆三類：
  - 未玩發掘（official rank `-`）
  - **已玩練習目標**（玩過但未進 top-50；標出「要打到多少達成率才會進帳」）— 這是稠密資料的新價值
  - 提升總分（近門檻的 exact 或高信心 coarse）

---

## 7. 全域改善：階段式獨立官方統計層（使用者決策）

- 上傳資料聚合進**獨立** artifact `official_play_stats.json`（由 `pipeline/build_official_stats.py`
  讀 `userdata/latest/*` 產生），**不**併入 `matrix.npz`、**不**改 base 的 `chart_mean/std/idf/SVD`。
- 用途：以「全部已玩」母體（非倖存者）提供更貼近真實的 per-chart 達成率分布／kasegi 校正，
  作為推薦的**額外先驗**。
- **階段式啟用**：每 chart 需達最低上傳者數門檻（如 ≥ N 位）才採用其官方統計；未達門檻沿用既有 base。
  上傳量足夠後，再考慮顯式建模選擇偏差。1 位上傳者時，此層幾乎不影響全域（符合預期）。

---

## 8. 身分連結

- 主鍵：玩家在 app 中**選定的 gsv 數字 playerId**（eagate 不暴露 gsv id）。
- eagate 端可得：ギタドラ ID（存為 eagate-side key）、玩家名、DM skillpoint、全曲 skill。
- **機率式驗證**：正規化名稱相符、skillpoint 約略相符、rank 帶涵蓋 gsv top-50 的達成率、
  5–10 首伺服器指定 challenge 曲 exact 比對。
- **明確聲明**：無伺服器端 KONAMI 驗證 ⇒ 此為**自陳資料**，非密碼學身分。
  驗證失敗/模糊 → 存 `userdata/unlinked/`，**不覆蓋**既有資料。

---

## 9. 隱私與可見度（使用者決策）

- **預設私有**：上傳後預設只有上傳者本人（憑回傳的 share token）看得到 enhanced 結果。
- **一鍵公開**：玩家可手動把 overlay 設為公開 → 成為他人可比較的豐富節點。
- visibility 旗標存於 `userdata/latest/...`；公開狀態才會被別的玩家的 similar/rivals 計算採用。
- 文案明確標示：上傳即同意該資料用於本工具分析；公開即他人可見。

---

## 10. 錯誤處理

- 書籤：分頁/網路失敗 → 重試上限 + 退避；部分成功也能上傳（標 partial）。
- 上傳：schema/限制違反 → 4xx + 明確訊息；身分模糊 → 202 + 隔離 + 提示。
- overlay 載入失敗 → 靜默退回 base（該玩家就只是稀疏，不致全站故障；比照 `cloudstore` 的容錯風格）。

---

## 11. 測試策略

- **parser 單元測試**：用實際抓下的 eagate HTML 樣本（list + detail，含各 rank、未玩、partial）做 fixture，
  斷言 rank/level/達成率解析正確。
- **rank→ach 數學測試**：合成輸入驗證後驗 clip 在 [L,U]、z 的不確定度傳播。
- **engine overlay 測試**：稠密玩家 vs 稀疏母體的非對稱相似度、profile 濾波、三類選曲。
- **upload function 測試**：schema 驗證、限制、CORS 標頭、身分連結/隔離分支、GCS 佈局。
- **官方統計層測試**：低上傳量時不影響全域；達門檻後才採用。
- **end-to-end smoke**：bookmarklet → 下載 JSON 後備 → 上傳 → overlay 生效 → API 反映。

---

## 12. 分期（建議）

1. **MVP**：bookmarklet（rank 掃描 + 針對性 detail + 下載 JSON 後備）、upload function（同源上傳優先）、
   `userdata/*` 儲存、overlay lazy 載入、engine overlay（profile + 三類選曲 + 非對稱相似度）、
   rank→ach 貝氏、身分連結、預設私有 + 一鍵公開。
2. **強化**：直接跨源 POST（CORS）、增量再同步、latent 投影修正、speed/UX。
3. **全域**：`official_play_stats` 階段式統計層（達上傳量門檻後啟用）。

---

## 13. 風險

- KONAMI ToS / 伺服器負擔（已用序列節流 + 快取 + 增量 + 上限緩解）。
- 偽造/自陳資料（機率式驗證 + 隔離；公開節點以驗證強度標示信心）。
- CORS 脆弱（下載後備保底）。
- 曲名/sid 對映誤差（以 sid 為主鍵 + 名稱比對校驗）。
- 隱私（預設私有 + 明確同意文案 + 禁存敏感卡號）。
