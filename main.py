"""機器人偶爾主動私聊熟人。

只找：QQ 好友 + 和機器人直接聊過幾次 + 好感度到「親近」、態度正常、沒說過嫌〈我〉煩（問 bond 的關係讀口 can_reach）+ 最近兩天沒聊過的人
不煩人：只在白天和晚上的幾個時段；每天最多 DAILY_MAX 次；同一個人隔幾天才找一次（心跳私聊開新話題、加好友打招呼也算找過，
        都記在 bond 的 reach，看 last_reach）；
        上次主動找 TA 沒回就隔更久，連著兩次沒回就不再主動找，等 TA 自己來
開口前先讓模型判斷有沒有合適的由頭，沒有就不發
發出去的話寫進 TA 的私聊記錄，TA 回覆時機器人記得自己說過什麼

主人私信指令：主動聊天 狀態 / 開 / 關 / 預覽 / 自動 / 試一下（回覆是【后台】白話，不用她的口吻）
預覽模式下她寫的開場白連同【后台】說明私信主人，記成後台的事
不主動找某個人：面板〈卡〉的規矩頁「不要主动找 TA 聊天」（10-26 拿掉了「別主動找 QQ／可以找 QQ」指令）
"""
import asyncio
import json
import random
import re
import sqlite3
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

from astrbot.api import logger
from astrbot.api.event import AstrMessageEvent, filter
from astrbot.api import AstrBotConfig
from astrbot.api.star import Context, Star, StarTools, register
from astrbot.core.message.message_event_result import MessageChain

from .lively_common import Settings, T2S, backstage, note_outbox, pick_final, plugin_data_dir

TZ = timezone(timedelta(hours=8))
WINDOWS = [("10:30", "12:00"), ("15:00", "17:30"), ("19:30", "22:00")]
CHECK_EVERY = 600  # 每 10 分鐘看一次
CHANCE = 0.2  # 時段內每次檢查有這麼大概率去找人（每天大約觸發幾次，受 DAILY_MAX 限制）
DAILY_MAX = 2
MIN_TALKS = 2
OLD_MIN_FAVOR = 5  # 舊版 bond 沒有關係讀口時，照舊看舊好感
QUIET_DAYS = 2  # 最近這麼多天聊過的不找
STALE_DAYS = 30  # 太久沒聯繫的也不找（關係淡了）
GAP_DAYS = 5  # 同一個人至少隔這麼多天
GAP_DAYS_UNANSWERED = 10
STOP_AFTER_UNANSWERED = 2
HISTORY_USER_TEXT = "（这是〈我〉主动发起的私聊，对方还没说话）"
TUNE_FILE = plugin_data_dir("astrbot_plugin_lively_evolve") / "tunables.json"  # 自我成長插件調的參數


def _tune(key: str, default):
    try:
        v = json.loads(TUNE_FILE.read_text("utf-8"))["values"][key]
        return type(default)(v)
    except Exception:
        return default


OPENER_RULE = """
现在是你主动私聊 TA（对方没有先找你）。像真人朋友那样随口找个由头：接着你们上次聊过的事、TA 的喜好或者 TA 最近说过的话，问一句或者分享一件小事
- 一两句话就够，像发微信那样短，不要自我介绍，不要说「好久不见」这种套话开头，不要提这是系统安排的
- 不要编你们没聊过的事，下面给你的资料里没有的就别说
- 挑轻松友好的由头；别拿 TA 说过的粗话、黄段子、吵架当话题
- 群聊摘要里有很多人，只挑和 TA 本人有关的事说，别把别人做的事安到 TA 头上
- 现在是 {now}，别在不合时宜的时间说早安晚安
- 如果实在找不到自然的由头，只回复「跳过」两个字
- 只输出要发给 TA 的话本身，不要先写分析、情景、清单或「写：」这类草稿
"""


def _ro(path: Path):
    return sqlite3.connect(f"file:{path}?mode=ro", uri=True)


def _in_window(now: datetime) -> bool:
    hm = now.strftime("%H:%M")
    return any(a <= hm < b for a, b in WINDOWS)


@register(
    "astrbot_plugin_lively_proactive",
    "GUCine",
    "偶爾主動私聊有過交互、好感夠的熟人，沒回就不再打擾",
    "2.0.0",
)
class LivelyProactive(Star):
    def __init__(self, context: Context, config: AstrBotConfig = None):
        super().__init__(context)
        self.s = Settings(context, config)
        self.dir = Path(StarTools.get_data_dir("astrbot_plugin_lively_proactive"))
        self.data = self.dir.parent
        self.root = self.data.parent
        self.state_file = self.dir / "state.json"
        self.events_file = self.dir / "events.jsonl"
        self.state = self._load()
        self._lock = asyncio.Lock()
        self._task = asyncio.create_task(self._loop())

    async def terminate(self):
        self._task.cancel()

    # ---------- 狀態 ----------
    def _load(self) -> dict:
        try:
            s = json.loads(self.state_file.read_text("utf-8"))
        except Exception:
            s = {}
        s.setdefault("enabled", True)
        s.setdefault("mode", "auto")  # auto：直接發；preview：先發給主人看
        s.setdefault("day", "")
        s.setdefault("count", 0)
        s.setdefault("people", {})  # qq -> {last: ts, replied: bool, unanswered: n}
        s.setdefault("blocked", [])
        return s

    def _save(self):
        try:
            tmp = self.state_file.with_suffix(".tmp")
            tmp.write_text(json.dumps(self.state, ensure_ascii=False, indent=1), "utf-8")
            tmp.replace(self.state_file)
        except Exception as e:
            logger.error(f"[lively_proactive] 保存状态失败: {e}")

    def _log(self, kind: str, **data):
        try:
            with self.events_file.open("a", encoding="utf-8") as f:
                f.write(json.dumps({"ts": int(time.time()), "kind": kind, **data}, ensure_ascii=False) + "\n")
        except Exception:
            pass

    def _today_count(self) -> int:
        day = datetime.now(TZ).strftime("%Y-%m-%d")
        if self.state["day"] != day:
            self.state["day"], self.state["count"] = day, 0
        return self.state["count"]

    # ---------- 候選人 ----------
    def _client(self):
        return self.s.client()

    async def _friends(self) -> set[str]:
        client = self._client()
        if client is None:
            return set()
        try:
            return {str(f["user_id"]) for f in await client.get_friend_list()}
        except Exception as e:
            logger.warning(f"[lively_proactive] 拿好友列表失败: {e}")
            return set()

    def _eligible(self, p: dict, now: float, quiet: set[str] = frozenset()) -> tuple[bool, str]:
        qq = p["qq"]
        if self.s.is_owner(qq):
            return False, "主人"
        if qq in self.state["blocked"]:
            return False, "主人说别找"
        if (p["talks"] or 0) < MIN_TALKS:
            return False, "聊得太少"
        if not p.get("can_reach", True):
            return False, p.get("reach_why") or "好感度不够"
        if qq in quiet or p.get("quiet"):  # 程式替她抽人時不抽嫌煩的（她自己找不攔，10-30）
            return False, "TA 嫌〈我〉烦"
        last_talk = p["last_talk"] or 0
        if now - last_talk < QUIET_DAYS * 86400:
            return False, "最近刚聊过"
        if now - last_talk > STALE_DAYS * 86400:
            return False, "太久没联系"
        h = self.state["people"].get(qq) or {}
        # 上次主動找完，TA 在私聊回了或者在群裡和機器人說過話，都算回應了
        answered = h.get("replied", True) or last_talk > h.get("last", 0)
        unanswered = 0 if answered else h.get("unanswered", 0) + 1
        if unanswered >= STOP_AFTER_UNANSWERED:
            return False, "主动找了两次都没回"
        gap = GAP_DAYS_UNANSWERED if unanswered else GAP_DAYS
        if now - max(h.get("last", 0), p.get("last_reach") or 0) < gap * 86400:
            return False, "前几天刚主动找过"
        return True, ""

    def _star(self, short: str):
        meta = self.context.get_registered_star(f"astrbot_plugin_{short}")
        return getattr(meta, "star_cls", None) if meta and getattr(meta, "activated", True) else None

    def _people(self) -> list[dict]:
        """bond 的關係讀口；舊版 bond 才自己讀 bond.db、看舊好感"""
        b = self._star("lively_bond")
        if b and hasattr(b, "relations"):
            return [r for r in b.relations() if r["known"]]
        con = _ro(self.data / "astrbot_plugin_lively_bond" / "bond.db")
        con.row_factory = sqlite3.Row
        rows = [dict(r) for r in con.execute(
            "select qq, name, familiarity, favor, impression, last_talk, talks from people")]
        con.close()
        for r in rows:
            r["score"] = r["favor"] or 0
            r["can_reach"], r["reach_why"] = r["score"] >= OLD_MIN_FAVOR, "好感不够"
        return rows

    def _quiet(self) -> set[str]:
        m = self._star("lively_mind")
        try:
            return set(m.quiet_people()) if m and hasattr(m, "quiet_people") else set()
        except Exception:
            return set()

    async def candidates(self) -> tuple[list[dict], dict]:
        friends = await self._friends()
        rows = self._people()
        quiet = self._quiet()
        now = time.time()
        ok, why = [], {}
        for p in rows:
            if p["qq"] not in friends:
                why["不是好友"] = why.get("不是好友", 0) + 1
                continue
            good, reason = self._eligible(p, now, quiet)
            if good:
                ok.append(p)
            else:
                why[reason] = why.get(reason, 0) + 1
        return ok, why

    # ---------- 素材 ----------
    def _material(self, p: dict) -> str:
        qq, name = p["qq"], p["name"] or p["qq"]
        lines = [f"对方：{name}"]
        if p.get("impression"):
            lines.append(f"你对 TA 的印象：{p['impression']}")
        days = int((time.time() - (p["last_talk"] or 0)) // 86400)
        lines.append(f"你们上次说话是 {days} 天前，一共直接聊过 {p['talks']} 次")
        try:
            con = _ro(self.data / "astrbot_plugin_livingmemory" / "conversations.db")
            # 只要 TA 對機器人說的話：私聊，或者群裡 @機器人、叫機器人（群裡的閒聊太雜）
            cond, args = self.s.talked_to_bot()
            said = con.execute(
                "select content, timestamp from messages where sender_id = ? and role = 'user' "
                f"and {cond} order by timestamp desc limit 6", (qq, *args)).fetchall()
            con.close()
            if said:
                lines.append("TA 最近对你说过的话（新的在前）：")
                for c, ts in said:
                    when = datetime.fromtimestamp(ts, TZ).strftime("%m-%d")
                    lines.append(f"- [{when}] {str(c)[:80]}")
        except Exception:
            pass
        try:
            con = _ro(self.data / "astrbot_plugin_livingmemory" / "livingmemory.db")
            mem = []
            for text, meta in con.execute("select text, metadata from documents order by id desc limit 200"):
                m = json.loads(meta or "{}")
                if m.get("status") != "active":
                    continue
                # 號碼、名字都要完全相等：子字串會把「12345」配上「123456789」、一個字的名字配上一堆人
                ids = {str(i.get("sender_id") or "") for i in m.get("participant_identities") or [] if isinstance(i, dict)}
                if qq in ids or (not ids and name in (m.get("participants") or [])):
                    mem.append((m.get("canonical_summary") or text)[:150])
                if len(mem) >= 3:
                    break
            con.close()
            if mem:
                lines.append("你记得的和 TA 有关的事：")
                lines += [f"- {x}" for x in mem]
        except Exception:
            pass
        return "\n".join(lines)

    def _persona(self) -> str:
        return self.s.persona_prompt()

    async def _write(self, p: dict) -> str | None:
        prov = self.s.provider()
        if prov is None:
            return None
        now = datetime.now(TZ).strftime("%m月%d日 %H:%M")
        system = self._persona() + "\n" + OPENER_RULE.format(now=now)
        resp = await asyncio.wait_for(
            prov.text_chat(prompt=self._material(p) + "\n\n现在给 TA 发第一句：", system_prompt=system),
            timeout=60,
        )
        text = (getattr(resp, "completion_text", "") or "").strip()
        text = re.sub(r"(?:<<|《《|＜＜)[^\n]*", "", text)  # 萬一帶了好感標記
        final = pick_final(text)  # 把起草稿寫成正文了：挑出終稿，挑不出來這次不找
        if final != text:
            logger.warning(f"[lively_proactive] 开场白像起草稿，{'挑出了终稿' if final else '这次不发'}:\n{text[:400]}")
        text = re.sub(r"&&[^&]*&&", "", final or "").replace("。", "").strip()
        if not text or "跳过" in text[:4]:
            return None
        return text

    # ---------- 發送 ----------
    async def reach_out(self, force: bool = False) -> str:
        async with self._lock:
            if not force and self._today_count() >= _tune("proactive.daily_max", DAILY_MAX):
                return "今天已经主动找够人了"
            cands, why = await self.candidates()
            if not cands:
                return "现在没有能找的人：" + "，".join(f"{k} {v}" for k, v in why.items())
            # 熟一點、好感高一點、久一點沒聊的更容易被選中
            weights = [
                (p["familiarity"] or 1) * max(p["score"] or 1, 1) ** 0.5
                * min((time.time() - (p["last_talk"] or 0)) / 86400, 10)
                for p in cands
            ]
            p = random.choices(cands, weights=weights, k=1)[0]
            try:
                text = await self._write(p)
            except Exception as e:
                logger.warning(f"[lively_proactive] 写开场白失败: {e}")
                return "写开场白时出错了"
            name = p["name"] or p["qq"]
            if not text:
                self._log("skip", qq=p["qq"], name=name)
                return f"挑了 {name}，她没写开场白（觉得没有合适的由头），没发"
            if self.state["mode"] == "preview":
                await self._tell_owner(backstage(f"预览模式，没发出去：她想主动找 {name}（QQ {p['qq']}）说的话是\n{text}"))
                self._log("preview", qq=p["qq"], name=name, text=text)
                return f"给 {name} 的开场白发给主人看了，没发出去"
            await self._send(p["qq"], text)
            if (b := self._star("lively_bond")) and hasattr(b, "note_reach"):
                try:
                    b.note_reach(p["qq"], "proactive")
                except Exception as e:
                    logger.warning(f"[lively_proactive] 记主动找人失败: {e}")
            self.state["count"] = self._today_count() + 1
            h = self.state["people"].setdefault(p["qq"], {})
            answered = h.get("replied", True) or (p["last_talk"] or 0) > h.get("last", 0)
            h["unanswered"] = 0 if answered else h.get("unanswered", 0) + 1
            h.update(last=int(time.time()), replied=False)
            self._save()
            self._log("sent", qq=p["qq"], name=name, text=text)
            logger.info(f"[lively_proactive] 主动找了 {name}({p['qq']}): {text}")
            return f"找了 {name}：{text}"

    async def _send(self, qq: str, text: str):
        umo = self.s.private_umo(qq)
        parts = [x.strip() for x in text.split("\n") if x.strip()]
        for i, part in enumerate(parts):
            if i:
                await asyncio.sleep(random.uniform(1.0, 2.5))
            await self.context.send_message(umo, MessageChain().message(part))
        # 寫進 TA 的私聊記錄
        try:
            cm = self.context.conversation_manager
            cid = await cm.get_curr_conversation_id(umo)
            if not cid:
                cid = await cm.new_conversation(umo, platform_id=self.s.platform_id())
            await cm.add_message_pair(
                cid,
                {"role": "user", "content": self.s.fill(HISTORY_USER_TEXT)},
                {"role": "assistant", "content": "\n".join(parts)},
            )
        except Exception as e:
            logger.warning(f"[lively_proactive] 写入聊天记录失败: {e}")

    async def _tell_owner(self, text: str):
        try:
            await self.context.send_message(self.s.private_umo(self.s.owner()), MessageChain().message(self.s.owner_text(text)))
        except Exception as e:
            logger.error(f"[lively_proactive] 私信主人失败: {e}")
            return
        note_outbox(self.s.private_umo(self.s.owner()), self.s.fill(text), "后台:主动私聊")

    async def _loop(self):
        await asyncio.sleep(120)
        while True:
            try:
                now = datetime.now(TZ)
                m = self._star("lively_mind")
                if m and hasattr(m, "reach_in_heartbeat") and m.reach_in_heartbeat():
                    pass  # 10-26 起交給心跳：她看手機時自己看要不要找誰，這裡不再隨機抽人
                elif (
                    self.state["enabled"]
                    and _in_window(now)
                    and self._today_count() < _tune("proactive.daily_max", DAILY_MAX)
                    and random.random() < _tune("proactive.chance", CHANCE)
                ):
                    result = await self.reach_out()
                    logger.info(f"[lively_proactive] {result}")
            except asyncio.CancelledError:
                raise
            except Exception as e:
                logger.error(f"[lively_proactive] 定时任务出错: {e}")
            await asyncio.sleep(CHECK_EVERY + random.uniform(-120, 120))

    # ---------- 回覆跟蹤 + 主人指令 ----------
    @filter.event_message_type(filter.EventMessageType.PRIVATE_MESSAGE, priority=1400)
    async def on_dm(self, event: AstrMessageEvent):
        if event.get_extra("lively_beat"):  # 〈我〉看手機時想私聊 TA 的內部消息：不是 TA 回了
            return
        qq = str(event.get_sender_id() or "")
        h = self.state["people"].get(qq)
        if h and not h.get("replied", True):
            h.update(replied=True, unanswered=0)
            self._save()
            self._log("replied", qq=qq)
        if not self.s.is_owner(qq):
            return
        text = (event.message_str or "").strip().translate(T2S)
        reply = None
        if text.startswith("主动聊天"):
            arg = text[4:].strip()
            if arg in ("开", "开启"):
                self.state["enabled"] = True
                reply = "主动聊天：开了"
            elif arg in ("关", "关闭"):
                self.state["enabled"] = False
                reply = "主动聊天：关了，不再定时找人"
            elif arg == "预览":
                self.state["mode"] = "preview"
                reply = "主动聊天：预览模式，她写的开场白先发给你看，不发出去"
            elif arg == "自动":
                self.state["mode"] = "auto"
                reply = "主动聊天：自动模式，她写的开场白直接发"
            elif arg in ("试一下", "试试"):
                reply = await self.reach_out(force=True)
            elif arg in ("", "状态"):
                cands, why = await self.candidates()
                names = "、".join((p["name"] or p["qq"]) for p in cands[:10]) or "暂时没有"
                m = self._star("lively_mind")
                hb = bool(m and hasattr(m, "reach_in_heartbeat") and m.reach_in_heartbeat())
                reply = (
                    ("现在交给心跳了：她看 QQ 时自己看要不要找谁、找几个，这里不再随机抽人；「试一下」还能用\n" if hb else "")
                    + f"主动聊天：{'开' if self.state['enabled'] else '关'}，"
                    f"{'预览' if self.state['mode'] == 'preview' else '自动'}模式，"
                    f"今天找了 {self._today_count()}/{_tune('proactive.daily_max', DAILY_MAX)} 次\n"
                    f"现在可以找的：{names}\n"
                    f"不能找的原因：" + "，".join(f"{k} {v}" for k, v in why.items())
                )
            if reply is not None:
                self._save()
        if reply is not None:
            event.stop_event()
            await event.send(MessageChain().message(self.s.admin_text(reply)))
