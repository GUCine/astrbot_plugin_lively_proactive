# 主動私聊

> 偶爾主動私聊有過交互、好感夠的熟人，沒回就不再打擾

AstrBot 插件，屬於「擬真群友」（lively）插件套件。適用 AstrBot >=4.29.0 + NapCat（aiocqhttp）。

主人、群、機器人名字、人設用詞這些共用設定在 [astrbot_plugin_lively_gate](https://github.com/GUCine/astrbot_plugin_lively_gate) 裡填，建議一起裝。

## 功能

```text
機器人偶爾主動私聊熟人。

只找：QQ 好友 + 和機器人直接聊過幾次 + 好感不低 + 最近兩天沒聊過的人
不煩人：只在白天和晚上的幾個時段；每天最多 DAILY_MAX 次；同一個人隔幾天才找一次；
        上次主動找 TA 沒回就隔更久，連著兩次沒回就不再主動找，等 TA 自己來
開口前先讓模型判斷有沒有合適的由頭，沒有就不發
發出去的話寫進 TA 的私聊記錄，TA 回覆時機器人記得自己說過什麼

主人私信指令：主動聊天 狀態 / 開 / 關 / 預覽 / 自動 / 試一下，別主動找 QQ，可以找 QQ
```

## 依賴

astrbot_plugin_lively_bond（用好感度挑人）；可選 LivingMemory（找聊天由頭）。

## 配置

在 AstrBot WebUI 的插件配置裡填，或者直接編輯 `data/config/astrbot_plugin_lively_proactive_config.json`。留空的公共項（主人、群、機器人名字、平台）會用 astrbot_plugin_lively_gate 裡填的。

| 配置項 | 說明 | 預設 |
| --- | --- | --- |
| `provider_id` | 寫開場白用的模型：留空用 AstrBot 預設模型 | （空） |
| `persona_id` | 機器人用的人格 ID：寫東西時套用這個人格的提示詞，口吻和聊天一致。留空用 AstrBot 預設人格 | （空） |
| `owner_qq` | 主人 QQ（留空用門禁插件裡填的） | （空） |

## 資料

執行資料在 `data/plugin_data/astrbot_plugin_lively_proactive/`。

## 許可

MIT，見 [LICENSE](LICENSE)。
