"""插件套件共用的設定讀取。

AstrBot 的插件各自獨立安裝，沒法互相 import，所以每個插件目錄裡都帶一份一模一樣的副本。
修改時請保持各插件裡的副本一致。

一項設定按這個順序找，找到就用：
1. 插件自己的配置（WebUI 插件配置頁，或 data/config/<插件目錄名>_config.json）
2. 門禁插件 astrbot_plugin_lively_gate 的配置（主人、服務的群、機器人 QQ、名字、平台只要在那裡填一次）
3. AstrBot 本身：管理員 admins_id、第一個 aiocqhttp（NapCat）平台、當前預設的對話模型
"""

from __future__ import annotations

import asyncio
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


def tail_lines(path, n: int, block: int = 64 * 1024) -> list[str]:
    """檔案最後 n 行：從檔尾往前一塊塊讀，不把整個檔讀進來（jsonl 日誌只會越來越長）。檔案不在就回空列表"""
    try:
        f = open(path, "rb")
    except OSError:
        return []
    with f:
        f.seek(0, os.SEEK_END)
        pos, buf = f.tell(), b""
        while pos > 0 and buf.count(b"\n") <= n:
            step = min(block, pos)
            pos -= step
            f.seek(pos)
            buf = f.read(step) + buf
    # 沒讀到檔頭時第一行是半截的，多讀了一行，切掉
    return buf.decode("utf-8", "replace").splitlines()[-n:] if n > 0 else []


# ---------- 聊天流程外發出去的話 ----------
# 各插件直接 context.send_message 發的通知（好友申請、進了新群、日報、審核、打招呼、群漫畫……）
# 不走聊天流程，不在對話歷史裡：對方接著問「這個群怎麼進的」時她會一臉茫然。發出去時記一筆，
# 門禁插件在那個聊天下一輪問模型時把還沒看過的帶上（存進那輪的對話歷史），她就知道自己剛發過什麼
OUTBOX = PLUGIN_DATA / GATE_PLUGIN / "outbox.jsonl"
OUTBOX_TEXT_MAX = 300
OUTBOX_KEEP = 400  # 檔案超過 OUTBOX_KEEP*2 行時只留最後 OUTBOX_KEEP 行


def note_outbox(umo: str, text: str, kind: str = "") -> None:
    """記下一條不走聊天流程發出去的消息（給模型看，用簡體原文；失敗不影響發送）"""
    text = re.sub(r"\s*\n\s*", " / ", str(text or "")).strip()
    if not umo or not text:
        return
    if len(text) > OUTBOX_TEXT_MAX:
        text = text[:OUTBOX_TEXT_MAX] + "…"
    try:
        OUTBOX.parent.mkdir(parents=True, exist_ok=True)
        with OUTBOX.open("a", encoding="utf-8") as f:
            # 時間記到毫秒：門禁插件的游標是看過的最後一條的時間，只到 0.1 秒時同一刻接著記的那條會被當成看過
            f.write(json.dumps({"ts": round(time.time(), 3), "umo": umo, "kind": kind, "text": text},
                               ensure_ascii=False) + "\n")
        if OUTBOX.stat().st_size > OUTBOX_KEEP * 2 * 400:
            lines = tail_lines(OUTBOX, OUTBOX_KEEP)
            tmp = OUTBOX.with_suffix(".tmp")
            tmp.write_text("\n".join(lines) + "\n", "utf-8")
            tmp.replace(OUTBOX)
    except Exception as e:
        logger.warning(f"[lively_common] 记下发出去的消息失败: {e}")


def read_outbox(umo: str, since: float, n: int = 200) -> list[dict]:
    """這個聊天 since 之後記下的（舊→新）"""
    out = []
    for line in tail_lines(OUTBOX, n):
        try:
            d = json.loads(line)
        except Exception:
            continue
        if d.get("umo") == umo and float(d.get("ts") or 0) > since:
            out.append(d)
    return out


# ---------- 後台的事交給〈我〉說（10-26 用戶定：插件不寫冒充她口吻的句子） ----------
# 插件主動要告訴〈主人〉的事（好友申請、說說待審、飯錢快沒了、被踢……）：把事實交給〈我〉（人設＋WORDS_RULE），她寫一句。
# must：要〈主人〉拍板、或〈主人〉一定得知道的，她的話之後另發一張【后台】卡片放號碼、原文這些（人設不准她說號碼，
# 模型寫話也會漏細節），她沒寫出來就只發卡片；不是 must 的她可以不說，不說就只記一筆讓她知道（門禁插件放在「后台的事」那段）。
# 主人指令的回覆也用 BACKSTAGE 開頭的白話，不用她的口吻
BACKSTAGE = "【后台】"
BACKSTAGE_KIND = "后台"  # outbox 的 kind 前綴：沒發給誰、只是讓〈我〉知道的後台事
WORDS_RULE = """
[后台的事：{what}]
下面是后台刚发生的事（事实，不是谁对〈我〉说的话）。{how}
不用说号码，不要编下面没写的事。
只输出 JSON：{{"say": "私信〈主人〉的话{empty}"}}"""
WORDS_MUST = ("这件事会私信告诉〈主人〉，号码、原文这些会另外附在一张【后台】卡片上一起发过去，不用抄。"
              "〈我〉想跟〈主人〉说什么、怎么说由〈我〉定，用〈我〉平时跟〈主人〉说话的样子，可以带上〈我〉自己的看法")
WORDS_MAY = "要不要私信〈主人〉说一声、怎么说由〈我〉定，用〈我〉平时跟〈主人〉说话的样子；不想说就空着"
WORDS_TIMEOUT = 90
# 硬規定（防刷屏、防風控、花錢的上限，〈主人〉定的開關）擋下時接在回傳後面（10-30 用戶定）：她改不了，要改找〈主人〉；只講事實，不叫她一定去說
ASK_OWNER = "（这一道〈我〉自己改不了，是〈主人〉那边定的；觉得该松，可以私信〈主人〉说）"
_LONG_NUM_RE = re.compile(r"\d{5,}")
_owner_lock = None  # 同一個插件發給主人的話和卡片挨著發（各插件各有一份這個模組）


# 轉發的聊天記錄（10-27）：AstrBot 只給一個 id，群聊背景裡那條是空的，〈我〉看不到裡面是什麼。
# NapCat 的 get_forward_msg（或拉歷史時帶的 content）給的是一串節點，這裡寫成一行給〈我〉看。
# 節點的名字常常是「QQ用户」（NapCat 拿不到轉發裡的人是誰），照實寫看不出是誰
# 轉發裡再轉發（10-31）：往裡展開 FORWARD_NEST_DEPTH 層，每段只帶前 FORWARD_NEST_LINES 條（短一點），其餘寫「還有幾條」
FORWARD_LINES = 15
FORWARD_LINE_MAX = 80
FORWARD_MAX = 900
FORWARD_NEST_DEPTH = 3
FORWARD_NEST_LINES = 6
FORWARD_NEST_LINE_MAX = 40
FORWARD_NEST_BLOCK_MAX = 360  # 裡面套了轉發的那一條，整條最多多少字
FORWARD_NEST_MAX = 2400  # 套了轉發的整段最多多少字
NO_NAME = ("", "QQ用户")


def node_segs(n) -> list:
    """一個節點裡的消息段（get_forward_msg 有時包一層 node，拉歷史時是 message／content）"""
    if not isinstance(n, dict):
        return []
    if n.get("type") == "node":
        d = n.get("data") or {}
        msg = d.get("content") or d.get("message")
    else:
        msg = n.get("message") if isinstance(n.get("message"), list) else n.get("content")
    return [x for x in msg if isinstance(x, dict)] if isinstance(msg, list) else []


def _forward_seg(seg: dict, depth: int) -> str:
    """depth：這個消息段所在的節點在第幾層（最外面那段轉發的節點是 0）"""
    t, d = seg.get("type"), seg.get("data") or {}
    if t == "text":
        return str(d.get("text") or "")
    if t in ("image", "mface"):
        s = str(d.get("summary") or "")
        return s if s and s != "[图片]" else ("[动画表情]" if str(d.get("sub_type", "0")) == "1" else "[图片]")
    if t == "at":
        return f"@{d.get('name') or d.get('qq') or '某人'}"
    if t == "forward":
        inner = d.get("content") or []
        lines = forward_lines(inner, depth + 1) if depth + 1 < FORWARD_NEST_DEPTH else []
        if not lines:
            return f"[又转了一段聊天记录，{len(inner)} 条" + ("，里面的内容 QQ 没给出来" if not inner else "") + "]"
        more = len(lines) - FORWARD_NEST_LINES
        return (f"[又转的聊天记录 {len(lines)} 条：" + "／".join(lines[:FORWARD_NEST_LINES])
                + (f"／…还有 {more} 条" if more > 0 else "") + "]")
    return {"face": "[表情]", "reply": "（回复）", "record": "[语音]", "video": "[视频]", "file": "[文件]",
            "json": "[卡片]"}.get(t, "")


def forward_lines(nodes, depth: int = 0) -> list[str]:
    """節點 → 「名字：內容」一行一條（最多 FORWARD_LINES 條，每條最多 FORWARD_LINE_MAX 字；
    套在裡面的轉發每條短一點，裡面再套轉發的那一條放寬到 FORWARD_NEST_BLOCK_MAX）"""
    out = []
    for n in nodes if isinstance(nodes, list) else []:
        if not isinstance(n, dict):
            continue
        if n.get("type") == "node":
            sender = {"nickname": (n.get("data") or {}).get("nickname")}
        else:
            sender = n.get("sender") or {}
        name = str(sender.get("card") or sender.get("nickname") or "").strip()
        raw = n.get("message") if isinstance(n.get("message"), list) else n.get("content")
        if isinstance(raw, str):
            text = raw
        else:
            segs = node_segs(n)
            text = "".join(_forward_seg(x, depth) for x in segs)
            nested = any(x.get("type") == "forward" for x in segs)
        text = re.sub(r"\s+", " ", text).strip()
        if not text:
            continue
        limit = FORWARD_NEST_LINE_MAX if depth else FORWARD_LINE_MAX
        if not isinstance(raw, str) and nested:
            limit = FORWARD_NEST_BLOCK_MAX
        if len(text) > limit:
            text = text[:limit] + "…"
        out.append(f"{'？' if name in NO_NAME else name}：{text}")
    return out


def forward_text(nodes) -> str:
    """一段轉發的聊天記錄寫成給〈我〉看的一行；空的回「里面的内容 QQ 没给出来」"""
    lines = forward_lines(nodes)
    if not lines:
        return "[转发的聊天记录：里面的内容 QQ 没给出来，〈我〉看不到]"
    more = len(lines) - FORWARD_LINES
    body = "｜".join(lines[:FORWARD_LINES])
    cap = FORWARD_NEST_MAX if "又转的聊天记录" in body else FORWARD_MAX
    if len(body) > cap:
        body = body[:cap] + "…"
    note = "，名字写「？」的是 QQ 没给出是谁" if any(x.startswith("？：") for x in lines) else ""
    return (f"[转发的聊天记录，{len(lines)} 条（旧→新{note}）：{body}"
            + (f"｜…后面还有 {more} 条" if more > 0 else "") + "]")


# 「记住」那一行（11-02）：她把要記的事單獨寫成一行漏進正文。行首（可帶「|」）是「记住」，後面緊接冒號（: ：）或左括號（（ ( 【 [ 「）才算；
# 括號寫法要括號收在行尾（或根本沒收）才算，括號裡可以再套括號、引號（「记住（敲黑板）明天要早起」是正常說話，不動）；「记住了」「记住啦」「记住，」「记住你…」「记住 xx」都不算。不在行首的也不算
REMEMBER_LINE_RE = re.compile(
    r"^[ \t　]*[|｜]?[ \t　]*记住(?:[:：][ \t　]*(?P<c>[^\n]*?)"
    r"|[（(【\[「](?:(?P<b>[^）)】\]」\n]*)|(?P<b2>[^\n]*)[）)】\]」][ \t　。.!！~～]*))[ \t　]*$", re.M)


def take_remember_lines(text: str) -> tuple[str, list[str]]:
    """拿掉所有「记住」那一行（見 REMEMBER_LINE_RE），回 (剩下的, [每行的內容])；內容已去掉冒號、括號，可能是空字串。沒有就原樣"""
    if "记住" not in (text or ""):
        return text, []
    got, keep = [], []
    for line in text.split("\n"):
        m = REMEMBER_LINE_RE.match(line)
        if m:
            got.append(next((m.group(k) for k in ("c", "b", "b2") if m.group(k) is not None), "").strip())
        else:
            keep.append(line)
    if not got:
        return text, []
    return re.sub(r"\n{3,}", "\n\n", "\n".join(keep)).strip(), got


def backstage(text: str) -> str:
    """後台白話（指令回覆、資料卡）：加上【后台】，看得出不是她說的"""
    text = str(text or "").strip()
    return text if text.startswith(BACKSTAGE) else BACKSTAGE + text


def _words_out(out: str) -> str:
    """她回的 JSON 裡的 say；看不懂就拋"""
    m = re.search(r"\{.*\}", out, re.S) or re.search(r"\{.*", out, re.S)
    if not m:
        raise RuntimeError(f"回答看不懂：{out[:80]}")
    raw = m.group(0)
    try:
        d = json.loads(raw if raw.rstrip().endswith("}") else raw + "}")
        say = str(d.get("say") or "")
    except Exception:
        mm = re.search(r'"say"\s*:\s*"((?:[^"\\]|\\.)*)"', raw)
        if not mm:
            raise RuntimeError(f"回答看不懂：{out[:80]}")
        say = json.loads(f'"{mm.group(1)}"')
    say = re.sub(r"&&[^&]*&&", "", say)
    say = re.sub(r"(?:<<|《《|＜＜|【bond)[^\n]*", "", say).strip()
    say = re.sub(r"\n\s*\n+", "\n", say)  # 一條私信裡的空行併掉
    return (pick_final(say) or "").strip()[:300] if say else ""


async def her_words(s, facts: str, what: str, must: bool = False, timeout: float = WORDS_TIMEOUT) -> str:
    """把後台的事交給〈我〉，回她想私信〈主人〉的話（空字串＝不說）。模型用門禁插件的 words_provider_id，
    留空用插件自己的模型。帶了號碼（5 位以上數字）當沒寫成：號碼在卡片上，人設不准她說。出錯照拋"""
    import asyncio
    pid = str(s.shared("words_provider_id", "") or "")
    prov = s.context.get_provider_by_id(pid) if pid else None
    if prov is None:
        prov = s.provider()
    if prov is None:
        raise RuntimeError("没有能用的模型")
    rule = WORDS_RULE.format(what=what, how=WORDS_MUST if must else WORDS_MAY, empty="" if must else "，不说就空字符串")
    resp = await asyncio.wait_for(prov.text_chat(prompt=s.fill(facts),
                                                 system_prompt=(s.persona_prompt() + "\n" + s.fill(rule)).strip()), timeout)
    say = _words_out((getattr(resp, "completion_text", "") or "").strip())
    if say and _LONG_NUM_RE.search(say):
        logger.info(f"[lively_common] 她写的「{what}」带了号码，当没写成：{say[:80]}")
        return ""
    return say


async def tell_owner(s, facts: str, what: str, *, must: bool = False, card: str | list = "",
                     image: str | None = None, kind: str = "") -> str:
    """後台的事告訴〈主人〉：先問〈我〉要說什麼，發她的話，must 的再發【后台】卡片（card 可以是好幾張，image 跟第一張）。
    不是 must、她也不說：什麼都不發，事實記一筆讓她知道。發出去的都記進主人私聊的 outbox。回她說的話"""
    import asyncio
    global _owner_lock
    from astrbot.core.message.message_event_result import MessageChain
    kind = kind or what
    facts = s.fill(facts)
    say = ""
    try:
        say = await her_words(s, facts, what, must)
    except Exception as e:
        logger.warning(f"[lively_common] 问她要不要跟主人说「{what}」没问成：{e}")
    owner = s.owner()
    if not owner:
        return say
    umo = s.private_umo(owner)
    cards = [c for c in ([card] if isinstance(card, str) else list(card or [])) if c] if must else []
    if must and not cards and not image:
        cards = [facts]
    if not say and not cards:
        note_outbox(umo, s.fill(f"{facts}（〈我〉没私信〈主人〉）"), f"{BACKSTAGE_KIND}:{kind}")
        return ""
    if _owner_lock is None:
        _owner_lock = asyncio.Lock()
    async with _owner_lock:
        if say:
            try:
                await s.context.send_message(umo, MessageChain().message(s.owner_text(say)))
                note_outbox(umo, say, kind)
            except Exception as e:
                logger.error(f"[lively_common] 私信主人失败（{what}）: {e}")
        for i, c in enumerate(cards or [""]):
            text = backstage(s.fill(c)) if c else ""
            chain = MessageChain()
            if text:
                chain = chain.message(s.owner_text(text))
            if image and i == 0:
                chain = chain.file_image(image)
            if not text and not (image and i == 0):
                continue
            if i:
                await asyncio.sleep(1.5)
            try:
                await s.context.send_message(umo, chain)
                note_outbox(umo, (text or BACKSTAGE) + ("（附图）" if image and i == 0 else ""), kind)
            except Exception as e:
                logger.error(f"[lively_common] 私信主人失败（{what}卡片）: {e}")
    return say


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
        """發給主人的文字：換人設用詞；開了 owner_traditional 才轉繁體（預設不轉）"""
        text = self.fill(text)
        return to_traditional(text) if self.shared("owner_traditional", False) else text

    def admin_text(self, text: str) -> str:
        """主人指令的回覆：【后台】白話，不是她說的話，所以〈我〉寫成「她」、〈主人〉寫成「你」"""
        return self.owner_text(backstage(str(text or "").replace("〈我〉", "她").replace("〈主人〉", "你")))

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
            try:
                row = con.execute(
                    "select system_prompt from personas where persona_id = ?", (pid,)
                ).fetchone()
            finally:
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
        """配置裡選的模型；沒選或找不到就用 AstrBot 當前預設的。
        設定裡有 fallback_<key>（例如 fallback_provider_id）時，主模型出錯就改用它（10-31：背景活走降智版，出錯退回 DeepSeek）"""
        pid = self.provider_id(key)
        prov = self.context.get_provider_by_id(pid) if pid else None
        if prov is None:
            if pid:
                logger.warning(f"[lively_common] 找不到模型 {pid}，改用默认模型")
            prov = self.context.get_using_provider()
        fid = self.provider_id("fallback_" + key)
        fb = self.context.get_provider_by_id(fid) if fid and fid != pid else None
        return _Fallback(prov, fb, pid, fid) if prov is not None and fb is not None else prov


class _Fallback:
    """主模型 text_chat 出錯（斷線、限額、超時）就用備用再問一次；別的屬性照主模型的"""

    def __init__(self, main, backup, main_id: str, backup_id: str):
        self._main, self._backup, self._ids = main, backup, (main_id, backup_id)

    def __getattr__(self, name):
        return getattr(self._main, name)

    async def text_chat(self, *args, **kwargs):
        try:
            return await self._main.text_chat(*args, **kwargs)
        except asyncio.CancelledError:
            raise
        except Exception as e:
            logger.warning(f"[lively_common] 模型 {self._ids[0]} 出错（{type(e).__name__}: {str(e)[:120]}），改用备用 {self._ids[1]}")
            return await self._backup.text_chat(*args, **kwargs)


# ---------- 起草外洩 ----------
# 模型偶爾把「怎麼回」的思考草稿當成正文寫出來（10-23 20:59 主群：情景摘要 → 「认，但要害羞」→「短」→「表情」→
# 「写：…」→ 結尾規則 →「好感 0，害羞」→ 終稿）。聊天回覆由 gate 攔；不走聊天流程、直接 send_message 的
# （主動私聊、例行說話、說說、心跳說話）各自在送出前用 pick_final() 過一遍。
# 「標籤：」開頭的行：寫、終稿這類是「下面就是要發的話」
FINAL_LABELS = ("终稿", "定稿", "最终回复", "最终", "成品", "正文", "回复", "输出", "写", "发", "回一句", "回他", "回她", "回TA")
# 其他規劃用的標籤
PLAN_LABELS = (
    "草稿", "思路", "分析", "情景", "场景", "情况", "背景", "人设", "设定", "语气", "口吻", "风格",
    "长度", "字数", "表情", "结尾", "句末", "规则", "要求", "注意", "检查", "好感", "心情", "分寸",
    "策略", "计划", "目标", "方向", "态度", "定位", "反应", "核心", "重点", "要点", "格式",
    "标记", "好感标记", "配图", "配表情", "配表情包",
)
_BULLET = r"(?:[-*•·]|\d{1,2}\s*[.、)）]|[（(]\d{1,2}[)）]|[①-⑩])?\s*"
LABEL_RE = re.compile(
    r"^\s*" + _BULLET + r"(" + "|".join(sorted(FINAL_LABELS + PLAN_LABELS, key=len, reverse=True)) + r")\s*[:：]\s*(.*)$"
)
# 「标记 排队就排队吧…」：好感標記、配圖的標籤後面跟空格不跟冒號（10-28 11:46 私聊）
SPACE_LABEL_RE = re.compile(r"^\s*" + _BULLET + r"(?:好感标记|标记|配表情包|配表情|配图)\s+\S")
# 「表情包。」「标记」：整行只剩一個規劃標籤（10-29 15:09 群：答案 →「表情包。」→「标记：<<…>>答案」又抄一遍）
BARE_LABEL_RE = re.compile(r"^\s*" + _BULLET + r"(?:表情包|表情|标记|好感标记|配图|配表情包|配表情|好感|心情)\s*[。.…:：]*\s*$")
# 「好感 0，害羞」：把好感標記寫成了正文（真標記 <<好感0|害羞>> bond 已經先取走了）
FAVOR_ECHO_RE = re.compile(r"^\s*[<《【\[(（]*\s*好感\s*[+\-－]?\s*\d+\s*[|｜,，、/ ]")
ARROW_RE = re.compile(r"→|->|=>")
CHECK_RE = re.compile(r"^\s*(?:[☐☑✅✔✓□■]|\[[ xX✓]\])")
# 規劃口吻的詞：一行裡出現兩個以上算規劃
META_WORDS = (
    "用户", "对方", "群友", "这条消息", "这一轮", "这轮", "人设", "设定", "好感", "分寸", "嘴硬", "害羞",
    "要短", "短一点", "简短", "一句话", "一两句", "的一句", "短句", "口语", "加表情", "带表情", "带个表情", "表情包", "句末", "结尾", "语气", "口吻",
    "不要提", "别提", "应该", "需要", "可以写", "回复", "回应", "草稿", "终稿", "checklist", "符合",
    "要求", "规则", "风格", "承认", "不承认", "认，", "认但", "先认", "身份", "梗", "接梗", "OOC",
    # 10-24 主群：一邊回一邊糾結好感標記的心情詞（「系统给的正文情绪是…」「规矩说心情要选列表里的」「改成"平常"」）
    "系统", "规矩", "列表", "情绪", "心情", "正文", "标记", "改成", "改为", "我写",
    # 10-28 11:46 私聊：「图片是红眼动漫脸，当语气」「回一句：…」「配表情包 ？或 心情好笑」
    "当语气", "配表情", "配个表情", "配图", "回一句",
)
# 一行裡列了一串心情詞：是在對著好感標記的規矩自言自語
MOOD_LIST_WORDS = ("开心", "得意", "感动", "害羞", "吃醋", "委屈", "生气", "担心", "嫌弃", "平常")
QUOTES = "「」『』“”\"'‘’"


def _sentences(line: str) -> list[str]:
    """一行裡好幾句時按句號再切（思考常常和終稿擠在同一行）；引號裡的句號不切"""
    out, cur, depth, ascii_q = [], "", 0, False
    for ch in line:
        cur += ch
        if ch in "「『“‘":
            depth += 1
        elif ch in "」』”’":
            depth = max(0, depth - 1)
        elif ch == '"':
            ascii_q = not ascii_q
        elif ch == "。" and depth == 0 and not ascii_q:
            out.append(cur); cur = ""
    out.append(cur)
    return [x.strip() for x in out if x.strip()]


def _lines(text: str) -> list[str]:
    return [x for l in (text or "").splitlines() for x in _sentences(l.strip())]


def _meta_words(line: str) -> int:
    return sum(1 for w in META_WORDS if w in line)


def classify(line: str) -> str:
    """'final'：帶「写：/终稿：」這類標籤；'plan'：其他規劃行；'text'：像正常說話"""
    m = LABEL_RE.match(line)
    if m:
        return "final" if m.group(1) in FINAL_LABELS else "plan"
    if SPACE_LABEL_RE.match(line) or BARE_LABEL_RE.match(line):
        return "plan"
    if FAVOR_ECHO_RE.match(line) or CHECK_RE.match(line) or _mood_list(line):
        return "plan"
    if ARROW_RE.search(line) and _meta_words(line) >= 1:
        return "plan"
    if _meta_words(line) >= 2:
        return "plan"
    return "text"


def _mood_list(line: str) -> bool:
    return sum(1 for w in MOOD_LIST_WORDS if w in line) >= 4


def _echoed_tail(lines: list[str], kinds: list[str]) -> bool:
    """「終稿 → 規劃 → 同一段終稿再來一遍」：最後一條規劃行後面的話（「标记：」後面接著寫的也算），在第一條規劃行前面原樣出現過"""
    meta_at = [i for i, k in enumerate(kinds) if k != "text"]
    if not meta_at:
        return False
    head, tail = lines[:meta_at[0]], lines[meta_at[-1] + 1:]
    m = LABEL_RE.match(lines[meta_at[-1]])
    if m and m.group(2).strip() and m.group(1) not in FINAL_LABELS:
        tail = [m.group(2).strip(), *tail]
    return bool(tail) and len(head) >= len(tail) and head[-len(tail):] == tail


def looks_like_draft(text: str) -> bool:
    lines = _lines(text)
    if len(lines) < 2:
        return False
    kinds = [classify(l) for l in lines]
    meta = sum(1 for k in kinds if k != "text")
    strong = any(
        k == "final"
        or FAVOR_ECHO_RE.match(l)
        or CHECK_RE.match(l)
        or _mood_list(l)
        or (k == "plan" and (LABEL_RE.match(l) or SPACE_LABEL_RE.match(l)))
        for l, k in zip(lines, kinds)
    ) or _echoed_tail(lines, kinds)
    return strong and meta >= 2


def _unquote(s: str) -> str:
    s = s.strip()
    while len(s) >= 2 and s[0] in QUOTES and s[-1] in QUOTES:
        s = s[1:-1].strip()
    return s


def extract_final(text: str) -> str | None:
    """先看最後一條規劃行後面有沒有正常說話（終稿常常就放在最後）；沒有就取最後一個「写：/终稿：」後面的話。"""
    lines = _lines(text)
    kinds = [classify(l) for l in lines]
    # 規劃後面又抄了一遍前面的話：前面那段就是完整的終稿（後面那遍常常只抄了半截）
    if _echoed_tail(lines, kinds):
        first = next(i for i, k in enumerate(kinds) if k != "text")
        out = _unquote("\n".join(lines[:first]))
        if out:
            return out
    last_meta = max((i for i, k in enumerate(kinds) if k != "text"), default=-1)
    tail = lines[last_meta + 1:]
    if tail:
        out = _unquote("\n".join(tail))
        if out:
            return out
    for i in range(len(lines) - 1, -1, -1):
        if kinds[i] != "final":
            continue
        first = _unquote(LABEL_RE.match(lines[i]).group(2))
        more = []
        for l, k in zip(lines[i + 1:], kinds[i + 1:]):
            if k != "text":
                break
            more.append(l)
        out = _unquote("\n".join([x for x in [first, *more] if x]))
        # 「写：短一点，带个表情」這種寫的還是要求，不是話
        if out and classify(out.splitlines()[0]) == "text":
            return out
    return None


def pick_final(text: str) -> str | None:
    """不像草稿：原樣返回；像草稿：返回挑出的終稿，挑不出來返回 None（這次別發）"""
    if not looks_like_draft(text):
        return text
    return extract_final(text)
