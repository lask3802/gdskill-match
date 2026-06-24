# 部署與雲端平台選型分析

針對本專案（每天觸發一次 skill 掃描 + 重建分析檔、流量很低、希望 cloud-native on-demand）的成本與適配比較。
比較對象：**GCP**（Cloud Run / Cloud Functions）、**Cloudflare**、**is\*hosting VPS**，以及免費網域選項。

> 資料為 2025–2026 公開定價，僅供決策參考；實際以各平台帳單為準。

## 先看本專案的技術特性（決定適配性）

| 面向 | 現況 | 對選型的影響 |
|---|---|---|
| 執行環境 | Python + **numpy + scikit-learn** | 需要原生科學套件 → 容器化最穩；純 JS/邊緣執行需改寫 |
| 記憶體 | 載入 ~30MB 密集矩陣，行程約 **200–300MB** | 排除最小的 128MB 邊緣 isolate（除非改寫/精簡） |
| 每次請求 | cosine／加權統計，~數十 ms | 計算很輕，純 serverless 綽綽有餘 |
| 每日工作 | 抓 ~1970 位玩家（GraphQL，**~65 秒、I/O 密集**）+ 重建 npz/json（~3.6MB）+ SVD | 1970 次外部請求 → **不適合 Cloudflare Worker 的 subrequest 上限**；適合 Cloud Run Job／VM cron |
| 儲存 | 檔案（npz/json，~15MB）讀多寫少 | **不需要關聯式 DB**；物件儲存（GCS/R2）即可，或 Firestore/D1 免費額度 |
| 流量 | 很低 | 幾乎落在各家免費額度內 |

**結論先講**：掃描（scrape）是重點負擔、適合 Cloud Run Job；服務（serving）很輕、放哪都行。

---

## 三平台比較（低流量、每日批次）

| | **GCP Cloud Run** | **Cloudflare**（需改寫） | **is\*hosting VPS** |
|---|---|---|---|
| 運算模式 | on-demand、**scale-to-zero** | on-demand（邊緣） | **always-on** |
| 跑現有 Python+sklearn？ | ✅ 直接容器化照跑 | ⚠️ 需把引擎改寫成 JS（或用 Pyodide，較 fiddly） | ✅ 直接跑 |
| 每日觸發 | Cloud Scheduler（**3 jobs 免費**）→ Cloud Run Job | Cron Triggers（免費） | crontab |
| 1970 次抓取的工作 | ✅ Cloud Run Job 無 subrequest 限制、timeout 上限 60 分 | ⚠️ Worker 免費 subrequest 上限 50/次（付費 1000）→ 需分批/Queues/Workflows | ✅ 一支腳本搞定 |
| 儲存／DB | GCS 物件儲存 / Firestore（免費額度足） | R2（10GB 免費）/ D1（SQLite 5GB 免費） | 本機磁碟 |
| 靜態前端 | Cloud Run 直接服務 / 或放 GCS | **Pages 免費、全球 CDN** | nginx |
| 每月成本（低流量） | **~$0**（免費額度內，頂多幾分錢儲存/egress） | **$0**（免費額度充裕） | **~$5.94 固定**（Lite 方案） |
| 冷啟動 | ~3–8 秒（Python+sklearn import + 載矩陣） | 純 JS 很快；Pyodide 較重 | 無 |
| 維運複雜度 | 中（GCP 專案／IAM／Artifact Registry／Cloud Build） | 低–中（但要先改寫一次引擎） | 低（但要自己顧系統更新與安全） |
| 適合誰 | **保留現有技術棧、又想幾乎不花錢** | **全面 serverless、且要最好的 DNS/網域** | **最簡單、成本可預測** |

### 各家免費額度重點（佐證）
- **Cloud Run**：每月 2,000,000 requests、180,000 vCPU-秒、360,000 GiB-秒免費；min-instances=0 時閒置不計費。CPU $0.000024/vCPU-秒、requests $0.40/百萬（超出免費後）。
- **Cloud Scheduler**：每帳號 3 個 job 免費（每日掃描只需 1 個）。**Firestore** 免費：5 萬讀/天、2 萬寫/天、1GB（本案用不到，GCS 更省）。
- **Cloudflare 免費**：Workers/Pages 10 萬 requests/天；**D1** 5GB＋500 萬列讀/天；**R2** 10GB＋無 egress 費；**Cron Triggers** 免費。Python Workers（Pyodide）現已支援 numpy 等套件且冷啟動加速，但 **128MB 記憶體 + 免費 10ms CPU/次** 對 sklearn+30MB 矩陣偏緊，較適合改寫成 JS。
- **is\*hosting VPS**：Lite **$5.94/月**（1 vCPU / 1GB / 20GB SSD / 2TB），Start $10.19（2GB）。1GB 足以跑本 app。有試用期。

---

## 建議（依你的取捨）

1. **想保留現有 Python 技術棧 + 幾乎零成本 + 真正 on-demand → 首選 GCP Cloud Run**
   - 服務：Cloud Run（容器照跑、scale-to-zero）。
   - 每日掃描：Cloud Scheduler → Cloud Run **Job** 跑 `fetch_data.py + build_dataset.py`，把 artifacts 寫到 **GCS bucket**；服務端啟動時從 GCS 載入。
   - 預估 **~$0/月**（低流量落在免費額度；僅少量儲存/registry 費）。代價是冷啟動 3–8 秒（可接受；若在意可設 min-instances=1，每月數美元）。
   - 這正是你說的「Cloud Run / Cloud Functions」on-demand 路線，且**不必改任何程式**。

2. **願意把引擎改寫成 JS → Cloudflare 最省又最快、且網域/CDN 最強**
   - 演算法很單純（cosine、加權 z、IDF Jaccard），可移植到 Workers（TS）；矩陣放 D1/R2，前端用 Pages，每日用 Cron Worker 重建。
   - 全部落在免費額度 → **$0/月**、全球邊緣、冷啟動快。
   - 注意：1970 次抓取會撞 Worker subrequest 上限，掃描那段建議用 **Workflows/Queues 分批**，或乾脆把「每日掃描」放 GCP Cloud Run Job、Cloudflare 只負責服務（混合架構）。

3. **想最簡單、成本可預測、不想碰 serverless → is\*hosting Lite $5.94/月**
   - systemd 跑 app + crontab 跑每日掃描，無冷啟動、單一機器全掌控；但閒置也要付費、需自行維運。

> 關於「資料庫」：本案讀多寫少、每日整批重建，**用物件儲存放 artifacts（GCS / R2）最省最簡**，不需要關聯式 DB。若日後要存歷史趨勢，再上 Firestore（GCP）或 D1（Cloudflare），兩者免費額度都夠這個規模。

---

## 免費／便宜網域

| 選項 | 費用 | 說明 |
|---|---|---|
| 平台預設子網域 | 免費、即時 | `*.run.app`（Cloud Run）、`*.pages.dev` / `*.workers.dev`（Cloudflare）。最快上線。 |
| **is-a.dev** | 免費 | 透過 GitHub PR 申請 `xxx.is-a.dev`，適合個人專案，門檻低。 |
| **eu.org** | 免費 | `xxx.eu.org`，約 14 天審核，老牌（1996 起）。 |
| js.org | 免費 | 只收 **JavaScript 函式庫/工具**；本專案性質不符，**大概不符資格**。 |
| **Cloudflare Registrar** | 成本價（無加價，.com 約 $10/年） | 搭 Cloudflare 免費 DNS，最划算的「真實網域」。 |
| Porkbun / Namecheap | 首年促銷 $1–10/年 | 便宜入手，之後續約較貴。 |

**建議**：先用平台免費子網域（`*.pages.dev` 或 `*.run.app`）上線；要正式門面再用 **Cloudflare Registrar 成本價網域 + Cloudflare 免費 DNS**。想完全免費又比子網域好看，用 **is-a.dev** 或 **eu.org**。
（提醒：Freenom 的免費 .tk/.ml 已名存實亡，別用。）

---

## 一句話總結

> 低流量 + 每日批次 + 想 on-demand 又不想改程式 → **GCP Cloud Run（服務）+ Cloud Scheduler/Cloud Run Job（每日掃描）+ GCS（artifacts）**，月費約 **$0**；網域先用免費子網域，正式上線再買 Cloudflare 成本價網域。願意改寫成 JS 的話，**Cloudflare 全家桶**是長期最省、最快、DNS 最強的選擇。
