# 隱私權政策 / Privacy Policy — FFXIVTCHuntingBot

**最後更新 / Last updated: June 12, 2026**

---

## 1. 概述 / Overview

FFXIVTCHuntingBot（以下簡稱「本 Bot」）是一個服務於最終幻想 XIV 台灣 Crystal 資料中心社群的狩獵工具 Bot，支援伺服器安裝與個人安裝（User Install）。本隱私權政策說明本 Bot 存取哪些資料、如何使用，以及您的相關權利。

FFXIVTCHuntingBot ("the Bot") is a Final Fantasy XIV hunting utility bot for the Taiwan Crystal data center community, supporting both server and personal (User Install) installation. This Privacy Policy explains what data the Bot accesses, how it is used, and your rights as a user.

---

## 2. 我們存取的資料 / Data We Access

### 2.1 訊息內容 / Message Content

本 Bot 透過 Discord 的 **Message Content Intent** 讀取指定頻道內的訊息內容，用途如下：

- 解析玩家回報的討伐時間（支援 Discord timestamp、TST 格式、HH:MM、純數字等格式）
- 識別回報所屬的伺服器名稱
- 偵測討伐結束訊號（刪除線文字或結束關鍵字）
- 自動更新討伐總覽面板

**訊息內容僅供即時解析使用，不會被儲存。**

The Bot reads message content via Discord's **Message Content Intent** in designated channels to parse player-reported hunt kill times, identify world server names, detect end signals, and update the hunt overview dashboard. **Message content is processed in real time and is never stored.**

---

### 2.2 互動資料 / Interaction Data

使用 slash command 或按鈕互動時，本 Bot 會從互動物件取得以下資料：

- 使用者 ID 與顯示名稱（僅用於即時回應，不儲存）
- 互動發生的伺服器與頻道資訊（僅用於功能執行）

When users interact via slash commands or buttons, the Bot accesses user ID, display name, and guild/channel context from the interaction object for the purpose of responding. This data is not stored.

---

### 2.3 我們不收集的資料 / Data We Do NOT Collect

- 不儲存訊息內容或個人訊息歷史
- 不收集密碼、電子郵件或付款資訊
- 不跨伺服器追蹤使用者行為
- 不將任何資料出售或提供給第三方
- 不將資料用於廣告或 AI 模型訓練

We do not store message content, collect passwords, emails, or payment data, track users across servers, sell data to third parties, or use data for advertising or AI training.

---

## 3. 本地儲存資料 / Locally Stored Data

本 Bot 將以下非個人資料儲存於管理員本機，用於功能運作：

| 檔案 / File | 內容 / Content | 說明 / Purpose |
|---|---|---|
| `hunt_state.json` | 各伺服器的討伐擊殺時間戳記 Guild hunt kill timestamps | 計算重生窗口 Spawn window calculation |
| `srank_special_state.json` | S 怪時間窗口資訊 S-Rank spawn window data | 顯示面板與通知 Panel display & notifications |
| `srank_special_notify_state.json` | 通知發送紀錄（訊息 ID、時間戳記）Notification records (message IDs, timestamps) | 避免重複通知 Prevent duplicate notifications |

以上資料**不包含任何個人識別資訊（PII）**，僅為遊戲內功能所需的時間與狀態數值。

The above data contains **no personally identifiable information (PII)**. All stored values are game-related timestamps and state values required for bot functionality.

---

## 4. 資料安全 / Data Security

本 Bot 運行於私人管理的本機環境，儲存資料不上傳至任何雲端服務或第三方。Discord API 的資料傳輸遵循 Discord 官方的安全標準。

The Bot runs on a privately managed local environment. Stored data is not uploaded to any cloud service or third party. Discord API communications follow Discord's official security standards.

---

## 5. 第三方服務 / Third-Party Services

本 Bot 僅使用 **Discord API** 作為外部服務（適用 [Discord 隱私權政策](https://discord.com/privacy)）。不使用其他第三方 API 或服務。

The Bot uses only the **Discord API** as an external service (subject to [Discord's Privacy Policy](https://discord.com/privacy)). No other third-party APIs or services are used.

---

## 6. 兒童隱私 / Children's Privacy

本 Bot 不會有意收集 13 歲以下使用者的資料。若您認為未成年人的資料遭到收集，請立即聯繫我們。

We do not knowingly collect data from users under the age of 13. If you believe a minor's data has been collected, please contact us immediately.

---

## 7. 政策變更 / Changes to This Policy

本政策可能不定期更新。文件頂端的「最後更新」日期將反映任何修訂。繼續使用本 Bot 即代表接受更新後的政策。

We may update this policy from time to time. The "Last updated" date reflects any revisions. Continued use of the Bot constitutes acceptance of changes.

---

## 8. 聯絡我們 / Contact

如對本隱私權政策有任何疑問，請透過 GitHub Issues 或 FFXIV 台灣社群 Discord 聯繫。

For questions or concerns, please open a GitHub Issue or contact us via the FFXIV Taiwan community Discord.

---

*本政策適用於 FFXIVTCHuntingBot 及其所有安裝實例。*

*This Privacy Policy applies to FFXIVTCHuntingBot and all its installation instances.*
