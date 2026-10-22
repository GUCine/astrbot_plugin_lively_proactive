"""插件套件共用的設定讀取。

AstrBot 的插件各自獨立安裝，沒法互相 import，所以每個插件目錄裡都帶一份一模一樣的副本。
修改時請保持各插件裡的副本一致。

一項設定按這個順序找，找到就用：
1. 插件自己的配置（WebUI 插件配置頁，或 data/config/<插件目錄名>_config.json）
2. 門禁插件 astrbot_plugin_lively_gate 的配置（主人、服務的群、機器人 QQ、名字、平台只要在那裡填一次）
3. AstrBot 本身：管理員 admins_id、第一個 aiocqhttp（NapCat）平台、當前預設的對話模型
"""

from __future__ import annotations

import json
import os
import re
import sqlite3
import time
from pathlib import Path

from astrbot.api import logger
from astrbot.core.utils.astrbot_path import get_astrbot_data_path

DATA = Path(get_astrbot_data_path())
PLUGIN_DATA = DATA / "plugin_data"
CONFIG_DIR = DATA / "config"

GATE_PLUGIN = "astrbot_plugin_lively_gate"
DEFAULT_BOT_NAME = ""  # 不預設名字；留空時呼叫方應關閉按名字識別的功能

# 人設用詞。程式碼裡的固定文字寫成「〈我〉」「〈主人〉」「〈卡〉」「〈名字〉」，用 Settings.fill() 換成配置裡填的詞
PERSONA_DEFAULTS = {"self_call": "我", "owner_call": "主人", "card_name": "印象卡"}

# 發給主人的消息轉繁體（台灣字形）。先換這些詞，再交給 OpenCC 逐字轉
TW_PHRASES = [
    ("默认", "预设"),
    ("设置", "设定"),
    ("数据", "资料"),
    ("链接", "连结"),
    ("网络", "网路"),
    ("软件", "软体"),
    ("程序", "程式"),
    ("信息", "讯息"),
    ("账号", "帐号"),
    ("账户", "帐户"),
    ("记账", "记帐"),
    ("视频", "影片"),
    ("保存", "储存"),
    ("支持", "支援"),
    ("运行", "执行"),
    ("登录", "登入"),
    ("服务器", "伺服器"),
    ("缓存", "快取"),
]
_s2tw = None

# 繁體 → 簡體：主人用繁體打指令也認（只收插件裡用到的字，由 scripts 生成）
T2S = str.maketrans(
    "萬與東丟兩個爲為麼義書爭於從倉們價優會傳體餘傾儲兒關內寫衝決淨準減幾則剛刪別劇辦務動勻區單佔歷壓參發變臺號喫後嗎聽啓啟員"
    "嗚響囂圍圖圓場壞聲處備復夠頭誇夾嬌學實寵審對將爾層屬峯帶幫幹並庫應開棄張強歸當錄徑憶態總惡願懶戶執掃擾搶報擔攔擋擠換據撐"
    "攢數斷無舊時顯暫機雜權條來標棧樣檔橋檢檻歡殘氣匯沒測漲溫滿濾點煩愛牽狀獨環現畫盤碼確離種稱筆籤簡類糾約級純納組紹經綁結繪"
    "給絕統緩編縮網羣聯腳脫騰範補見規覽覺觸計認讓議記許設訪證評識訴詞試話語誤說請諾讀誰調謠譜負責敗賬費資蹤轉輪軟軸輕載較輸邊"
    "過運還這進連適選郵採釋裏裡針鍾鑰錢鏈鎖銳錯鍵鏡長門閉問閒間閘閱際險隨隱難靜頁項須預領頻題顏額風飯馬罵驗黃齊",
    "万与东丢两个为为么义书争于从仓们价优会传体余倾储儿关内写冲决净准减几则刚删别剧办务动匀区单占历压参发变台号吃后吗听启启员"
    "呜响嚣围图圆场坏声处备复够头夸夹娇学实宠审对将尔层属峰带帮干并库应开弃张强归当录径忆态总恶愿懒户执扫扰抢报担拦挡挤换据撑"
    "攒数断无旧时显暂机杂权条来标栈样档桥检槛欢残气汇没测涨温满滤点烦爱牵状独环现画盘码确离种称笔签简类纠约级纯纳组绍经绑结绘"
    "给绝统缓编缩网群联脚脱腾范补见规览觉触计认让议记许设访证评识诉词试话语误说请诺读谁调谣谱负责败账费资踪转轮软轴轻载较输边"
    "过运还这进连适选邮采释里里针钟钥钱链锁锐错键镜长门闭问闲间闸阅际险随隐难静页项须预领频题颜额风饭马骂验黄齐",
)


def to_traditional(text: str) -> str:
    """簡體轉繁體；沒裝 opencc 就原樣返回"""
    global _s2tw
    if _s2tw is None:
        try:
            import opencc

            _s2tw = opencc.OpenCC("s2tw").convert
        except Exception as e:
            logger.warning(
                f"[lively_common] 没装 opencc-python-reimplemented，发给主人的消息维持简体: {e}"
            )
            _s2tw = False
    if not _s2tw:
        return text
    for a, b in TW_PHRASES:
        text = text.replace(a, b)
    return _s2tw(text).replace("臺", "台")


def id_list(value) -> list[str]:
    """號碼列表：列表、或用逗號/空格分隔的字串都行，只留純數字，去重保序"""
    if value is None:
        return []
    if isinstance(value, (int, str)):
        value = re.split(r"[\s,，;；]+", str(value))
    out = []
    for v in value:
        v = str(v).strip()
        if v.isdigit() and v not in out:
            out.append(v)
    return out


def plugin_data_dir(name: str) -> Path:
    """別的插件的資料目錄（只拼路徑，不建立）"""
    return PLUGIN_DATA / name


class Settings:
    """包一層插件配置，取不到的項按上面的順序往後找"""

    def __init__(self, context, config=None):
        self.context = context
        self.cfg = config if config is not None else {}
        self._bot_qq = ""
        self._mtime, self._checked = self._file_mtime(), 0.0

    # ---------- 基礎 ----------
    def _file_mtime(self) -> float:
        try:
            return os.path.getmtime(self.cfg.config_path)
        except Exception:
            return 0.0

    def _reload_if_changed(self):
        """配置文件在外面被改了（比如在終端用腳本填密鑰）就重新讀進來，不用重啟"""
        now = time.monotonic()
        if now - self._checked < 5:
            return
        self._checked = now
        mtime = self._file_mtime()
        if not mtime or mtime == self._mtime:
            return
        self._mtime = mtime
        try:
            with open(self.cfg.config_path, encoding="utf-8-sig") as f:
                fresh = json.load(f)
            if isinstance(fresh, dict):
                self.cfg.update(fresh)
                logger.info(
                    f"[lively_common] 重新读取了配置 {os.path.basename(self.cfg.config_path)}"
                )
        except Exception as e:
            logger.warning(f"[lively_common] 重新读取配置失败: {e}")

    def get(self, key: str, default=None):
        self._reload_if_changed()
        try:
            v = self.cfg.get(key)
        except Exception:
            v = None
        return default if v is None or v == "" or v == [] else v

    def _access_cfg(self) -> dict:
        try:
            meta = self.context.get_registered_star(GATE_PLUGIN)
            return (meta.config if meta and meta.config is not None else {}) or {}
        except Exception:
            return {}

    def shared(self, key: str, default=None):
        """自己的配置 → 門禁插件的配置"""
        v = self.get(key)
        if v is None:
            v = self._access_cfg().get(key)
        return default if v is None or v == "" or v == [] else v

    # ---------- 人和群 ----------
    def owners(self) -> list[str]:
        ids = id_list(self.shared("owner_qq"))
        if not ids:
            try:
                ids = id_list(self.context.get_config().get("admins_id", []))
            except Exception:
                ids = []
        return ids

    def owner(self) -> str:
        ids = self.owners()
        return ids[0] if ids else ""

    def is_owner(self, qq) -> bool:
        return str(qq or "").strip() in self.owners()

    def groups(self) -> list[str]:
        """服務的群。為空就是沒有群（只私聊）"""
        return id_list(self.shared("groups"))

    def bot_name(self) -> str:
        return str(self.shared("bot_name", DEFAULT_BOT_NAME)).strip()

    # ---------- 人設用詞 ----------
    def me(self) -> str:
        """機器人的自稱"""
        return str(self.shared("self_call", PERSONA_DEFAULTS["self_call"])).strip()

    def owner_call(self) -> str:
        """機器人對主人的稱呼"""
        return str(self.shared("owner_call", PERSONA_DEFAULTS["owner_call"])).strip()

    def card_name(self) -> str:
        """印象卡的叫法"""
        return str(self.shared("card_name", PERSONA_DEFAULTS["card_name"])).strip()

    def display_name(self) -> str:
        """給模型和頁面看的名字：沒填名字就叫「機器人」"""
        return self.bot_name() or "机器人"

    def fill(self, text: str) -> str:
        """把文字裡的人設佔位符換成配置裡的詞"""
        return (
            str(text)
            .replace("〈我〉", self.me())
            .replace("〈主人〉", self.owner_call())
            .replace("〈卡〉", self.card_name())
            .replace("〈名字〉", self.display_name())
        )

    def owner_text(self, text: str) -> str:
        """發給主人的文字：換人設用詞，按配置轉繁體"""
        text = self.fill(text)
        return to_traditional(text) if self.shared("owner_traditional", True) else text

    def bot_qq(self) -> str:
        """機器人自己的 QQ：配置裡沒填就用收到消息時記下的（remember_self）"""
        ids = id_list(self.shared("bot_qq"))
        return ids[0] if ids else self._bot_qq

    def remember_self(self, event) -> None:
        if self._bot_qq:
            return
        try:
            sid = str(event.get_self_id() or "").strip()
            if sid.isdigit():
                self._bot_qq = sid
        except Exception:
            pass

    async def fetch_bot_qq(self) -> str:
        """還不知道機器人 QQ 時問 NapCat 一次"""
        qq = self.bot_qq()
        if qq:
            return qq
        client = self.client()
        if client is None:
            return ""
        try:
            info = await client.api.call_action("get_login_info")
            qq = str((info or {}).get("user_id") or "")
            if qq.isdigit():
                self._bot_qq = qq
        except Exception as e:
            logger.debug(f"[lively_common] 问不到机器人 QQ: {e}")
        return self._bot_qq

    # ---------- 平台 ----------
    def _platform(self):
        pid = self.shared("platform_id")
        insts = list(getattr(self.context.platform_manager, "platform_insts", []) or [])
        if pid:
            for p in insts:
                if p.meta().id == pid:
                    return p
        for p in insts:
            if p.meta().name == "aiocqhttp":
                return p
        return insts[0] if insts else None

    def platform_id(self) -> str:
        pid = self.shared("platform_id")
        if pid:
            return str(pid)
        p = self._platform()
        return p.meta().id if p else ""

    def client(self):
        """NapCat（OneBot）的客戶端，用來調 call_action"""
        p = self._platform()
        if p is not None and hasattr(p, "get_client"):
            return p.get_client()
        return None

    def private_umo(self, qq) -> str:
        return f"{self.platform_id()}:FriendMessage:{qq}"

    def group_umo(self, gid) -> str:
        return f"{self.platform_id()}:GroupMessage:{gid}"

    # ---------- 人格 ----------
    def persona_id(self) -> str:
        """機器人用的 AstrBot 人格：配置裡填的 → AstrBot 預設人格"""
        pid = self.shared("persona_id")
        if not pid:
            try:
                pid = (
                    self.context.get_config()
                    .get("provider_settings", {})
                    .get("default_personality", "")
                )
            except Exception:
                pid = ""
        return str(pid or "")

    def persona_prompt(self) -> str:
        """人格的系統提示詞（後台自己寫東西時用，讓口吻和聊天時一致）"""
        pid = self.persona_id()
        if not pid:
            return ""
        try:
            con = sqlite3.connect(f"file:{DATA / 'data_v4.db'}?mode=ro", uri=True)
            row = con.execute(
                "select system_prompt from personas where persona_id = ?", (pid,)
            ).fetchone()
            con.close()
            return row[0] if row else ""
        except Exception:
            return ""

    def talked_to_bot(self, column: str = "content") -> tuple[str, list]:
        """SQL 條件：消息是私聊、@了機器人、或喊了它的名字。返回 (條件, 參數)"""
        qq = self.bot_qq() or "-"
        name = self.bot_name()
        # 沒有名字時第二個條件退化成重複的 @ 判斷，避免 LIKE '%%' 匹配所有消息
        name_like = f"%{name}%" if name else f"%At:{qq}%"
        return (
            f"(session_id LIKE '%FriendMessage%' OR {column} LIKE ? OR {column} LIKE ?)",
            [f"%At:{qq}%", name_like],
        )

    # ---------- 模型 ----------
    def provider_id(self, key: str = "provider_id") -> str:
        return str(self.get(key, "") or "")

    def provider(self, key: str = "provider_id"):
        """配置裡選的模型；沒選或找不到就用 AstrBot 當前預設的"""
        pid = self.provider_id(key)
        prov = self.context.get_provider_by_id(pid) if pid else None
        if prov is None:
            if pid:
                logger.warning(f"[lively_common] 找不到模型 {pid}，改用默认模型")
            prov = self.context.get_using_provider()
        return prov
