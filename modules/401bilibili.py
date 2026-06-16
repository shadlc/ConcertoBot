"""哔哩哔哩模块"""

import asyncio
import base64
import json
import random
import re
import time
import traceback

from bilibili_api import Credential, NetworkException, search, user, sync
from bilibili_api.exceptions.ResponseCodeException import ResponseCodeException

try:
    from playwright.async_api import async_playwright

    HAS_PLAYWRIGHT = True
except ImportError:
    HAS_PLAYWRIGHT = False

from src.base import MiniCron, Module
from src.utils import Utils


class Bilibili(Module):
    """哔哩哔哩模块"""

    ID = "Bilibili"
    NAME = "哔哩哔哩模块"
    HELP = {
        2: [
            "开启B站通知 / 关闭B站通知 | 开关本会话全部B站通知",
            "关注 [UP主] | 关注一个新的UP主",
            "取关 [UP主] | 取关一个UP主",
            "[UP主] 通知关键词 [匹配规则] | 设置通知过滤关键词(正则匹配)",
            "[UP主] 反向通知关键词 [匹配规则] | 设置通知排除关键词(正则匹配)",
        ],
        3: [
            "关注列表 | 查看当前关注的UP主列表",
            "[UP主] 动态 | 获取UP主的最新动态",
            "[UP主] 直播 | 获取UP主的直播间状态",
            "[UP主] 粉丝数 | 查看该UP粉丝数",
            "开启[UP主]动态 / 关闭[UP主]动态 | 开关UP主动态通知",
            "开启[UP主]直播 / 关闭[UP主]直播 | 开关UP主直播通知",
            "开启[UP主]粉丝数 / 关闭[UP主]粉丝数 | 开关UP主粉丝数通知",
            "[UP主] 通知开启 / [UP主] 通知关闭 | 开关UP主的全部通知",
        ],
    }
    GLOBAL_CONFIG = {
        "env": {
            "sessdata": "",
            "bili_jct": "",
            "buvid3": "",
            "buvid4": "",
            "dedeuserid": "",
            "ac_time_value": "",
            "user_agent": "",
            "proxy": None,
            "cron": "0 8 * * *",
        }
    }
    CONV_CONFIG = {
        "enable": True,
        "sub": {}
    }
    PERSISTENT = True

    def __init__(self, event, auth=0):
        """初始化B站状态缓存并启动后台轮询任务"""
        self.type_msg = {
            "DYNAMIC_TYPE_AV": "投稿了一个视频",
            "DYNAMIC_TYPE_FORWARD": "转发了一条动态",
            "DYNAMIC_TYPE_DRAW": "发布了一条图文动态",
            "DYNAMIC_TYPE_ARTICLE": "发表了一篇新的专栏",
            "DYNAMIC_TYPE_MUSIC": "新出了一首音乐专辑",
            "DYNAMIC_TYPE_LIVE_RCMD": "开播啦",
        }
        super().__init__(event, auth)
        self.live_status = {}
        self.dynamics = {}
        self.browser = None
        self.playwright = None
        if self.is_persisted():
            return
        self.schedule_background_task(self.init_task(), name="延迟10秒初始化任务")

    def premise(self):
        """刷新登录凭据对象并复用持久化模块状态"""
        self.credential = Credential(
            sessdata=self.config["env"]["sessdata"],
            bili_jct=self.config["env"]["bili_jct"],
            buvid3=self.config["env"]["buvid3"],
            buvid4=self.config["env"]["buvid4"],
            dedeuserid=self.config["env"]["dedeuserid"],
            ac_time_value=self.config["env"]["ac_time_value"],
        )
        return super().premise()

    def unload(self) -> None:
        """停止后台轮询并关闭浏览器资源"""
        self.stop_background()
        if self.browser or self.playwright:
            try:
                self.robot.loop.create_task(self._close_browser())
            except RuntimeError:
                pass

    async def _close_browser(self) -> None:
        """关闭浏览器"""
        if self.browser:
            await self.browser.close()
            self.browser = None
        if self.playwright:
            await self.playwright.stop()
            self.playwright = None

    async def init_task(self, interval=20):
        """初始化任务"""
        await asyncio.sleep(10)
        dynamic = len(self.get_uid_list("dynamic"))
        live = len(self.get_uid_list("live"))
        fans = len(self.get_uid_list("fans"))
        self.printf(f"实时检测开启~ 共监控{dynamic}个用户动态, {live}个用户直播, {fans}个用户粉丝数")
        async def dynamic_loop():
            """动态轮询"""
            while True:
                try:
                    await self.dynamic_check(interval)
                except Exception:  # pylint: disable=broad-exception-caught
                    self.warnf(f"动态轮询异常!\n{traceback.format_exc()}")
                await asyncio.sleep(interval)

        self.schedule_background_task(dynamic_loop(), name="动态轮询")

        async def fans_loop():
            """粉丝数轮询"""
            cron = MiniCron(
                self.config["env"]["cron"],
                lambda: sync(self.fans_check()),
                loop=self.robot.loop,
                name="B站粉丝数检查",
            )
            self.track_cron(cron)
            while True:
                try:
                    await cron.run()
                except Exception:  # pylint: disable=broad-exception-caught
                    self.warnf(f"粉丝数轮询异常!\n{traceback.format_exc()}")
                await asyncio.sleep(interval)

        self.schedule_background_task(fans_loop(), name="粉丝数轮询")

        async def live_loop():
            """直播轮询"""
            while True:
                try:
                    await self.live_check()
                except Exception:  # pylint: disable=broad-exception-caught
                    self.warnf(f"直播轮询异常!\n{traceback.format_exc()}")
                await asyncio.sleep(interval)

        self.schedule_background_task(live_loop(), name="直播轮询")

        async def credential_refresh_loop():
            """登录凭据状态轮询"""
            while True:
                if await self.credential.check_refresh():
                    await self.credential.refresh()
                    self.config["env"]["sessdata"] = self.credential.sessdata
                    self.config["env"]["bili_jct"] = self.credential.bili_jct
                    self.config["env"]["buvid3"] = self.credential.buvid3
                    self.config["env"]["buvid4"] = self.credential.buvid4
                    self.config["env"]["dedeuserid"] = self.credential.dedeuserid
                    self.config["env"]["ac_time_value"] = self.credential.ac_time_value
                    self.save_config()
                await asyncio.sleep(interval * 1000)

        self.schedule_background_task(credential_refresh_loop(), name="登录凭据状态轮询")

    @Utils.handler(
        lambda self: self.at_or_private() and self.au(3) and self.match(r"^关注列表$")
    )
    def show_follow_list(self):
        """显示关注列表"""
        title = ""
        nodes = []
        follow_list = self.conv_config["sub"]
        if follow_list:
            if self.event.group_id:
                title = "本群的关注列表"
            else:
                title = "你的关注列表"
            for uid, info in follow_list.items():
                user_info = self.robot.sync(self.robot.get_user_simple_info(uid, fans=True))
                if user_info:
                    info["name"] = user_info["name"]
                    info["fans"] = user_info["fans"]
                    info["avatar"] = user_info["avatar"]
                nodes.append(self.node(self.parse_user_info(uid, info)))
        else:
            msg = "这里还未拥有关注列表，请管理员添加吧~"
            self.reply(msg)
        self.reply_forward(nodes, title, "哔哩哔哩")

    @Utils.handler(
        lambda self: self.at_or_private()
        and self.au(2)
        and self.match(r"^关注\s?(\S+)$")
    )
    def subscribe(self):
        """关注UP主"""
        user_match = self.match(r"^关注\s?(\S+)$").group(1)
        info = self.robot.sync(self.get_info(user_match))
        if info:
            uid = info["uid"]
            name = info["name"]
            fans = info["fans"]
            avatar = info["avatar"]
            if uid in self.conv_config["sub"]:
                msg = f"你已经关注了{name}"
            else:
                self.conv_config["sub"][uid] = {
                    "name": name,
                    "avatar": avatar,
                    "fans": fans,
                    "keyword": "",
                    "anti_keyword": "(抽奖|中奖)",
                    "dynamic_notice": True,
                    "live_notice": True,
                    "fans_notice": False,
                }
                self.save_config()
                msg = f"已将{name}(UID:{uid})添加至关注列表"
            msg += "\n===================="
            msg += self.parse_user_info(uid, self.conv_config["sub"][uid])
        else:
            msg = "查无此人"
        self.reply(msg)

    @Utils.handler(
        lambda self: self.at_or_private()
        and self.au(2)
        and self.match(r"^取关\s?(\S+)$")
    )
    def unsubscribe(self):
        """取关UP主"""
        user_match = self.match(r"^取关\s?(\S+)$").group(1)
        info = self.robot.sync(self.get_info(user_match))
        if info:
            uid = info["uid"]
            name = info["name"]
            if uid not in self.conv_config["sub"]:
                msg = f"你并没有关注{name}"
            else:
                msg = f"已将{name}(UID:{uid})取关"
                del self.conv_config["sub"][uid]
                self.save_config()
        else:
            msg = "查无此人"
        self.reply(msg)

    @Utils.handler(
        lambda self: self.at_or_private()
        and self.au(2)
        and self.match(r"^(\S+)\s?反?向?通知关键词\s+(\S+)?$")
    )
    def set_keywords(self):
        """设置通知关键词"""
        user_match, pattern = self.match(r"^(\S+)\s?反?向?通知关键词\s+(\S+)$").groups()
        info = self.robot.sync(self.get_info(user_match))
        if info:
            uid = info["uid"]
            name = info["name"]
            if uid in self.conv_config["sub"]:
                if self.match("反向通知关键词"):
                    self.conv_config["sub"][uid]["anti_keyword"] = pattern
                    msg = f"已成功为{name}设置反向匹配关键词【{pattern}】"
                else:
                    self.conv_config["sub"][uid]["keyword"] = pattern
                    msg = f"已成功为{name}设置正则匹配关键词【{pattern}】"
                self.save_config()
            else:
                msg = "未关注该UP主，请先关注！"
        else:
            msg = "查无此人"
        self.reply(msg)

    @Utils.handler(
        lambda self: self.at_or_private()
        and self.au(3)
        and self.match(r"^(\S+)\s?通知\s?(开启|关闭)$")
    )
    def notice_control(self):
        """UP主通知总开关"""
        user_match, flag = self.match(r"^(\S+)\s?通知\s?(开启|关闭)$").groups()
        info = self.robot.sync(self.get_info(user_match))
        if not info:
            return self.reply("查无此人")
        uid = info["uid"]
        name = info["name"]
        if uid not in self.conv_config["sub"]:
            return self.reply("请先关注UP主~")

        status = flag == "开启"
        self.conv_config["sub"][uid]["dynamic_notice"] = status
        self.conv_config["sub"][uid]["live_notice"] = status
        self.conv_config["sub"][uid]["fans_notice"] = status
        self.save_config()
        return self.reply(f"已{flag}对{name}的全部通知~")

    @Utils.handler(
        lambda self: self.at_or_private()
        and self.au(3)
        and self.match(r"^(开启|关闭)?\s?(\S+?)\s?最?新?动态(通知)?$")
    )
    def dynamic_control(self):
        """动态控制"""
        flag, user_match, _ = self.match(
            r"^(开启|关闭)?\s?(\S+?)\s?最?新?动态(通知)?$"
        ).groups()
        info = self.robot.sync(self.get_info(user_match))
        if info:
            uid = info["uid"]
            name = info["name"]
            if not flag:
                Utils.set_emoji(self.robot, self.event.msg_id, 124)
                dyn = self.robot.sync(self.get_latest_dynamic(int(uid)))
                if dyn:
                    d_type = dyn["dynamic_type"]
                    if HAS_PLAYWRIGHT:
                        screenshot_base64 = self.robot.sync(self.get_dynamic_screenshot(
                            dyn["dynamic_id"]
                        ))
                        if screenshot_base64:
                            msg += f"\n[CQ:image,file=base64://{screenshot_base64}]"
                            self.reply(msg)
                            return
                    title = f"[哔哩哔哩] {dyn['author']}{self.type_msg.get(d_type, '发布了新动态')}"
                    nodes = []
                    nodes.append(self.node(dyn["url"]))
                    msg = dyn["content"]
                    if d_type == "DYNAMIC_TYPE_AV":
                        video_title = dyn["content"].split("\n")[0]
                        if len(video_title) < 40:
                            msg = title + "\n" + msg
                            title = f"[哔哩哔哩] {video_title}"
                    if len(dyn["imgs"]) == 1:
                        img = dyn["imgs"][0]
                        nodes.append(self.node(f"[CQ:image,file={img}]"))
                        nodes.append(self.node(msg))
                    else:
                        for img in dyn["imgs"]:
                            msg += f"[CQ:image,file={img}]"
                        nodes.append(self.node(msg))
                    if ori := dyn["origin"]:
                        msg = "以下是转发内容:\n\n"
                        msg += f"{ori['author']}:\n"
                        msg += ori["content"]
                        for img in ori["imgs"]:
                            msg += f"[CQ:image,file={img}]"
                        nodes.append(self.node(msg))
                    self.reply_forward(nodes, title, dyn["author"])
                    return
                else:
                    msg = f"{name}没有发过任何动态..."
            elif uid in self.conv_config["sub"]:
                status = flag == "开启"
                self.conv_config["sub"][uid]["dynamic_notice"] = status
                self.save_config()
                msg = f"已{flag}对{name}的动态观测~"
            else:
                msg = "请先关注UP主~"
        else:
            msg = "查无此人"
        self.reply(msg)

    @Utils.handler(
        lambda self: self.at_or_private()
        and self.au(3)
        and self.match(r"^(开启|关闭)?\s?(\S+)\s?直播(通知)?$")
    )
    def live_control(self):
        """直播控制"""
        flag, user_match, _ = self.match(
            r"^(开启|关闭)?\s?(\S+)\s?直播(通知)?$"
        ).groups()
        info = self.robot.sync(self.get_info(user_match))
        if info:
            uid = info["uid"]
            name = info["name"]
            if not flag:
                user_info = self.robot.sync(self.get_user_simple_info(uid))
                if info:
                    status = user_info["live_room"].get("liveStatus")
                    title = user_info["live_room"].get("title")
                    cover = user_info["live_room"].get("cover")
                    room_id = user_info["live_room"].get("roomid")
                    if status:
                        msg = f"{name}正在直播:\n"
                        msg += f"\n标题: {title}"
                        msg += f"\n链接: https://live.bilibili.com/{room_id}"
                        if cover:
                            msg += f"\n[CQ:image,file={cover}]"
                    else:
                        msg = f"{name}还在休息中哦~"
                else:
                    msg = f"{name}从来没有直播过哦~"
            elif uid in self.conv_config["sub"]:
                status = flag == "开启"
                self.conv_config["sub"][uid]["live_notice"] = status
                self.save_config()
                msg = f"已{'开启' if status else '关闭'}对{name}的直播通知~"
            else:
                msg = "请先关注UP主~"
        else:
            msg = "查无此人"
        self.reply(msg)

    @Utils.handler(
        lambda self: self.at_or_private()
        and self.au(3)
        and self.match(r"^(开启|关闭)?(\S+)\s?粉丝数(通知)?$")
    )
    def fans_control(self):
        """粉丝数控制"""
        flag, user_match, _ = self.match(
            r"^(开启|关闭)?(\S+)\s?粉丝数(通知)?$"
        ).groups()
        info = self.robot.sync(self.get_info(user_match))
        if info:
            uid = info["uid"]
            name = info["name"]
            fans = info["fans"]
            avatar = info["avatar"]
            if not flag:
                msg = f"{name}当前的粉丝数为：{fans}"
                msg += f"\n[CQ:image,file={avatar}]"
            elif uid in self.conv_config["sub"]:
                status = flag == "开启"
                self.conv_config["sub"][uid]["fans_notice"] = status
                self.save_config()
                msg = f"已{'开启' if status else '关闭'}对{name}的粉丝数通知~"
            else:
                msg = "请先关注UP主~"
        else:
            msg = "查无此人"
        self.reply(msg)

    @Utils.handler(
        lambda self: self.at_or_private()
        and self.au(2)
        and self.match(r"^(开启|关闭)[b|B|哔]站通知$")
    )
    def toggle(self):
        """通知控制"""
        flag = self.match(r"(开启|关闭)").group(1)
        status = flag == "开启"
        self.conv_config["enable"] = status
        self.save_config()
        msg = f"已{flag}B站通知~"
        self.reply(msg)

    async def init_browser(self):
        """初始化浏览器"""
        if not HAS_PLAYWRIGHT:
            return

        if self.browser:
            return self.browser

        self.playwright = await async_playwright().start()
        browser_config = {}
        if proxy := self.config["proxy"]:
            browser_config["proxy"] = {"server": proxy}
        self.browser = await self.playwright.chromium.launch(**browser_config)
        return self.browser

    async def get_dynamic_screenshot(self, dynamic_id, style="mobile") -> str:
        """获取动态截图并返回base64"""
        if style.lower() == "mobile":
            return await self.get_dynamic_screenshot_mobile(dynamic_id)
        else:
            return await self.get_dynamic_screenshot_pc(dynamic_id)

    async def get_dynamic_screenshot_mobile(self, dynamic_id) -> str:
        """移动端动态截图"""
        url = f"https://m.bilibili.com/dynamic/{dynamic_id}"
        browser = await self.init_browser()
        if not browser:
            return

        context = await browser.new_context(
            device_scale_factor=2,
            user_agent=self.config["user_agent"],
            viewport={"width": 500, "height": 800},
        )

        await context.add_cookies(
            [
                {
                    "name": "SESSDATA",
                    "value": self.config["sessdata"],
                    "domain": ".bilibili.com",
                    "path": "/",
                    "httpOnly": True,
                    "secure": True,
                    "sameSite": "Lax",
                }
            ]
        )

        page = await context.new_page()
        try:
            await page.goto(url, wait_until="networkidle", timeout=10000)
            if page.url == "https://m.bilibili.com/404":
                return

            await page.add_script_tag(content="""
                document.querySelectorAll(".opus-float-btn").forEach(v=>v.remove());
                document.querySelectorAll(".dynamic-float-btn").forEach(v=>v.remove());
                document.querySelectorAll(".dyn-header__following").forEach(v=>v.remove());
                document.querySelectorAll(".dyn-share").forEach(v=>v.remove());

                const contentDiv = document.getElementsByClassName("dyn-card")[0];
                const wrapperDiv = document.createElement("div");
                contentDiv.parentNode.insertBefore(wrapperDiv, contentDiv);
                wrapperDiv.appendChild(contentDiv);

                wrapperDiv.style.padding = "10px";
                wrapperDiv.style.backgroundImage = "linear-gradient(to bottom right, #c8beff, #bef5ff)";
                contentDiv.style.boxShadow = "0px 0px 10px 2px #fff";
                contentDiv.style.border = "2px solid white";
                contentDiv.style.borderRadius = "10px";
                contentDiv.style.background = "rgba(255,255,255,0.7)";
                contentDiv.style.fontFamily = "Noto Sans CJK SC, sans-serif";
                contentDiv.style.overflowWrap = "break-word";

                document.getElementsByClassName("dyn-content__orig")[0].style.backgroundColor = "transparent";
                document.querySelectorAll("img").forEach(v=>{ v.style.border = "2px solid white"; });
                document.getElementsByClassName("dyn-article__card").forEach(v=>{ v.style.border = "2px solid white"; v.style.background = "transparent"; });
                document.querySelectorAll("[class*="pair--"]>*").forEach((e)=>{e.style.width="42.9vmin";e.style.height="42.9vmin";});
                document.querySelectorAll("[class*="well--"]>*").forEach((e)=>{e.style.width="28vmin";e.style.height="28vmin";});
            """)

            card = await page.query_selector(".card-wrap")
            if card:
                clip = await card.bounding_box()
                if clip:
                    screenshot = await page.screenshot(clip=clip, full_page=True)
                    return base64.b64encode(screenshot).decode("utf-8")
        except Exception:  # pylint: disable=broad-exception-caught
            self.errorf(f"截取动态【{url}】时发生错误\n{traceback.format_exc()}")
        finally:
            await page.close()
            await context.close()

    async def get_dynamic_screenshot_pc(self, dynamic_id) -> str:
        """电脑端动态截图"""
        url = f"https://t.bilibili.com/{dynamic_id}"
        browser = await self.init_browser()
        if not browser:
            return

        context = await browser.new_context(
            viewport={"width": 2560, "height": 1080},
            device_scale_factor=2,
        )

        await context.add_cookies(
            [
                {
                    "name": "SESSDATA",
                    "value": self.config["sessdata"],
                    "domain": ".bilibili.com",
                    "path": "/",
                    "httpOnly": True,
                    "secure": True,
                    "sameSite": "Lax",
                }
            ]
        )

        page = await context.new_page()
        try:
            await page.goto(url, wait_until="networkidle", timeout=10000)
            if page.url == "https://www.bilibili.com/404":
                return

            card = await page.query_selector(".card")
            if card:
                clip = await card.bounding_box()
                if clip:
                    action_bar = await page.query_selector(".bili-dyn-action__icon")
                    if action_bar:
                        bar_bound = await action_bar.bounding_box()
                        if bar_bound:
                            clip["height"] = bar_bound["y"] - clip["y"]
                    screenshot = await page.screenshot(clip=clip, full_page=True)
                    return base64.b64encode(screenshot).decode("utf-8")
        except Exception:  # pylint: disable=broad-exception-caught
            self.errorf(f"截取动态【{url}】时发生错误\n{traceback.format_exc()}")
        finally:
            await context.close()

    async def dynamic_check(self, interval=10):
        """动态检查"""
        uid_list = self.get_uid_list("dynamic")
        if len(uid_list) == 0:
            return
        for uid in uid_list:
            delay = interval + random.randint(0, 10)
            name = self.get_local_name(uid)
            if uid not in self.dynamics or len(self.dynamics[uid]) == 0:
                dynamics = await self.get_user_dynamics(uid)
                if len(dynamics) == 0:
                    await asyncio.sleep(delay)
                    continue
                self.printf(f"已初始化{name}({uid})的动态共{len(dynamics)}条")
                self.dynamics[uid] = dynamics
                await asyncio.sleep(3)
                continue
            dynamics = await self.get_new_dynamics(uid)
            if not dynamics:
                await asyncio.sleep(delay)
                continue
            self.printf(f"{name}({uid})发布了新动态{len(dynamics)}条")
            notice_list = self.get_notice_list("dynamic", uid)
            for item in dynamics:
                self.dynamics[uid].append(item)
                dyn = self.parse_dynamic(item)
                d_type = dyn["dynamic_type"]
                if HAS_PLAYWRIGHT:
                    screenshot_base64 = await self.get_dynamic_screenshot(
                        dyn["dynamic_id"]
                    )
                    if screenshot_base64:
                        msg += f"\n[CQ:image,file=base64://{screenshot_base64}]"
                        for owner_id in notice_list:
                            pattern = self.config[owner_id]["sub"][uid].get("keyword")
                            if pattern and re.search(pattern, msg):
                                anti_pattern = self.config[owner_id]["sub"][uid].get(
                                    "anti_keyword"
                                )
                                if anti_pattern and re.search(anti_pattern, msg):
                                    continue
                                self.reply_back(owner_id, msg)
                                await asyncio.sleep(3)
                        continue
                title = f"[哔哩哔哩] {dyn['author']}{self.type_msg.get(d_type, '发布了新动态')}"
                if d_type == "DYNAMIC_TYPE_AV":
                    video_title = dyn["content"].split("\n")[0]
                    if len(video_title) < 40:
                        title = f"[哔哩哔哩] {video_title}"
                nodes = []
                nodes.append(self.node(dyn["url"]))
                msg = dyn["content"]
                if len(dyn["imgs"]) == 1:
                    img = dyn["imgs"][0]
                    nodes.append(self.node(f"[CQ:image,file={img}]"))
                    nodes.append(self.node(msg))
                else:
                    for img in dyn["imgs"]:
                        msg += f"[CQ:image,file={img}]"
                    nodes.append(self.node(msg))
                if ori := dyn["origin"]:
                    msg = "以下是转发内容:\n\n"
                    msg += f"{ori['author']}:\n"
                    msg += ori["content"]
                    for img in ori["imgs"]:
                        msg += f"[CQ:image,file={img}]"
                    nodes.append(self.node(msg))
                for owner_id in notice_list:
                    pattern = self.config[owner_id]["sub"][uid].get("keyword")
                    if pattern == "" or re.search(pattern, msg):
                        anti_pattern = self.config[owner_id]["sub"][uid].get(
                            "anti_keyword"
                        )
                        if anti_pattern and re.search(anti_pattern, msg):
                            continue
                        self.reply_forward_back(owner_id, nodes, title, dyn["author"])
                        await asyncio.sleep(3)
                await asyncio.sleep(3)
            await asyncio.sleep(delay)

    async def live_check(self, interval=5):
        """直播检查"""
        uid_list = self.get_uid_list("live")
        if len(uid_list) == 0:
            return
        for uid in uid_list:
            delay = interval + random.randint(0, 5)
            info = await self.get_user_simple_info(uid)
            if not info:
                await asyncio.sleep(3)
                continue
            status = info["live_room"].get("liveStatus")
            title = info["live_room"].get("title")
            cover = info["live_room"].get("cover")
            room_id = info["live_room"].get("roomid")
            if uid not in self.live_status:
                self.live_status[uid] = status
                await asyncio.sleep(3)
                continue
            if self.live_status[uid] == status:
                await asyncio.sleep(3)
                continue
            self.live_status[uid] = status
            if not status:
                await asyncio.sleep(3)
                continue
            notice_list = self.get_notice_list("live", uid)
            for owner_id in notice_list:
                uname = self.get_local_name(uid)
                msg = f"{uname}开播啦~\n"
                msg += f"\nhttps://live.bilibili.com/{room_id}"
                msg += f"\n{title}"
                if cover:
                    msg += f"\n[CQ:image,file={cover}]"
                self.reply_back(owner_id, msg)
                await asyncio.sleep(3)
            await asyncio.sleep(delay)

    async def fans_check(self, interval=5):
        """粉丝数检查"""
        uid_list = self.get_uid_list("fans")
        if len(uid_list) == 0:
            return
        for uid in uid_list:
            delay = interval + random.randint(0, 5)
            info = await self.get_user_simple_info(uid, fans=True)
            if info:
                name = info["name"]
                fans = info["fans"]
                avatar = info["avatar"]
                past_fans = int(self.get_follow_list_info(uid, "fans"))
                if past_fans == fans or fans == 0:
                    await asyncio.sleep(3)
                    continue
                notice_list = self.get_notice_list("fans", uid)
                for owner_id in notice_list:
                    msg = f"\n[CQ:image,file={avatar}]"
                    msg += f"{name}当前的粉丝数为：{fans}"
                    diff = fans - past_fans
                    if llm_chat := self.robot.func.get("llm_chat"):
                        if diff > 0:
                            msg += f"\n相比上次记录，粉丝数增加了{diff}"
                        else:
                            msg += f"\n相比上次记录，粉丝数减少了了{abs(diff)}"
                        date = time.strftime("%Y年%m月%d日", time.localtime())
                        prompt = (
                            f"今天是{date}，B站账号《{name}》从原来的粉丝数量{past_fans}变化为{fans}个，"
                            "请用简短的两句话，表达自己的看法，请多夹杂日式颜文字和可爱的语气来说明，"
                            "并使用括号描述自己的动作与心情，如果明白，请直接回复内容"
                        )
                        msg += "\n" + llm_chat(prompt)
                    elif diff > 0:
                        msg += f"\n相比上次记录，粉丝数增加了{diff}，"
                        if diff > 10000 or diff > fans:
                            msg += f"{name}的涨粉太浮夸啦！"
                        elif diff > 1000 or (1000 < fans < 10000 and diff > fans / 10):
                            msg += "成为百大指日可待~"
                        elif diff > 100 or diff > fans / 40:
                            msg += "很棒棒啦，继续加把劲~"
                        elif diff > 10 or diff > fans / 100:
                            msg += "继续加油哦~"
                        else:
                            msg += "聊胜于无嘛(oﾟvﾟ)ノ"
                    elif diff < 0:
                        diff = abs(diff)
                        msg += f"\n相比上次记录，粉丝数减少了了{diff}，"
                        if diff > 10000 or diff > fans / 2:
                            msg += "仿佛只在次贷危机看过类似的场景..."
                        elif diff > 1000 or (fans > 10000 and diff > fans / 10):
                            msg += "大危机！(っ °Д °;)っ"
                        elif diff > 200 or (fans > 10000 and diff > fans / 50):
                            msg += "哦吼，不太妙哦~(#｀-_ゝ-)"
                        elif diff > 25 or diff > fans / 100:
                            msg += "一点小失误...(￣﹃￣)"
                        else:
                            msg += "统计学上来说这很正常"
                    else:
                        await asyncio.sleep(3)
                        continue
                    self.update_follow_list_info(uid, {"fans": fans})
                    self.reply_back(owner_id, msg)
                    await asyncio.sleep(3)
            await asyncio.sleep(delay)

    def get_follow_list_info(self, uid: str, key):
        """获取关注列表信息"""
        for owner_id in self.config:
            if owner_id == "env":
                continue

            if uid in self.conv_config["sub"]:
                return self.conv_config["sub"][uid].get(key, "")
        return ""

    def update_follow_list_info(self, uid: str, data: dict, owner_id: str = None):
        """更新关注列表信息"""
        if not owner_id:
            owner_id = self.owner_id
        for key, value in data.items():
            for owner_id in self.config:
                if owner_id == "env":
                    continue
                if uid in self.config[owner_id]["sub"]:

                    self.config[owner_id]["sub"][uid][key] = value
        self.save_config()

    async def get_user_dynamics(self, uid: str, need_top=False) -> list:
        """刷新动态"""
        name = self.get_local_name(uid)
        try:
            u = user.User(int(uid), self.credential)
            # https://api.bilibili.com/x/polymer/web-dynamic/v1/feed/space
            dynamic_list = await u.get_dynamics_new()
            dynamics = dynamic_list.get("items") or []
            if len(dynamics) and not need_top:
                tag = dynamics[0].get("modules", {}).get("module_tag")
                if tag and tag.get("text") == "置顶":
                    dynamics = dynamics[1:]
            return dynamics
        except NetworkException as e:
            self.warnf(f"获取{name}({uid})的动态网络异常 {e.status}")
        except asyncio.TimeoutError:
            self.warnf(f"获取{name}({uid})的动态超时")
        except ResponseCodeException as e:
            self.warnf(f"获取{name}({uid})的动态返回码异常 {e.code} {e.msg}")
        except Exception:  # pylint: disable=broad-exception-caught
            self.warnf(f"用户{name}({uid})的动态获取失败\n{traceback.format_exc()}")
        return []

    async def get_new_dynamics(self, uid: str) -> list:
        """获取新动态"""
        result = []
        dynamics = await self.get_user_dynamics(uid)
        if not dynamics:
            return result
        for i, dynamic in enumerate(reversed(dynamics)):
            dynamic_id = dynamic["id_str"]
            if dynamic_id in [d["id_str"] for d in self.dynamics.get(uid, {})]:
                continue
            if "DYNAMIC_TYPE_LIVE_RCMD" == dynamic["type"]:
                continue
            if len(self.dynamics.get(uid, {})) == 0:
                self.dynamics[uid].insert(i, dynamic)
                result.append(dynamic)
            elif int(dynamic_id) > int(self.dynamics[uid][-1]["id_str"]):
                self.dynamics[uid].insert(i, dynamic)
                result.append(dynamic)
        return result

    async def get_latest_dynamic(self, uid: str) -> dict | None:
        """获取最新动态"""
        dynamics = await self.get_user_dynamics(uid)
        if dynamics:
            return self.parse_dynamic(dynamics[0])

    async def get_user_simple_info(self, uid: str, fans=False) -> dict | None:
        """获取用户信息"""
        name = self.get_local_name(uid)
        try:
            u = user.User(int(uid))
            # https://api.bilibili.com/x/space/wbi/acc/info
            user_info = await u.get_user_info()
            fans_count = 0
            if fans:
                # https://api.bilibili.com/x/relation/stat
                relation_info = await u.get_relation_info()
                fans_count = relation_info["follower"]
            if user_info:
                return {
                    "uid": uid,
                    "name": user_info["name"],
                    "fans": fans_count,
                    "avatar": user_info["face"],
                    "live_room": user_info["live_room"] or {},
                }
        except NetworkException as e:
            self.warnf(f"获取{name}({uid})的用户信息网络异常 {e.status}")
        except asyncio.TimeoutError:
            self.warnf(f"获取{name}({uid})的用户信息超时")
        except ResponseCodeException as e:
            self.warnf(f"获取{name}({uid})的用户信息返回码异常 {e.code} {e.msg}")
        except Exception:  # pylint: disable=broad-exception-caught
            self.warnf(f"查询{name}({uid})的用户信息请求失败\n{traceback.format_exc()}")

    def get_local_uid(self, user_match: str) -> int | None:
        """获取用户UID"""
        for owner_id in self.config:
            if owner_id == "env":
                continue
            for uid, info in self.conv_config["sub"].items():
                if user_match == info["name"]:
                    return uid
        if re.search(r"^[0-9]+$", user_match):
            return user_match

    def get_local_name(self, uid: str) -> str | None:
        """通过UID获取用户名"""
        for owner_id in self.config:
            if owner_id == "env":
                continue
            if uid in self.config[owner_id]["sub"]:
                return self.config[owner_id]["sub"][uid]["name"]

    async def get_info_by_name(self, name: str) -> dict | None:
        """通过名称获取信息"""
        try:
            search_result = await search.search_by_type(
                name, search.SearchObjectType.USER, search.OrderUser.FANS
            )
            if search_result and "result" in search_result:
                for result in search_result["result"]:
                    avatar = result["upic"]
                    if avatar.startswith("//"):
                        avatar = "https:" + avatar
                    return {
                        "uid": result["mid"],
                        "name": result["uname"],
                        "fans": result["fans"],
                        "avatar": avatar,
                    }
            return
        except Exception:  # pylint: disable=broad-exception-caught
            self.warnf(f"查询用户信息请求失败\n{traceback.format_exc()}")
            return

    async def get_info(self, user_match: str) -> dict | None:
        """获取用户信息"""
        uid = self.get_local_uid(user_match)
        info = None
        if uid:
            info = await self.get_user_simple_info(uid, fans=True)
        else:
            info = await self.get_info_by_name(user_match)
        if info:
            self.update_follow_list_info(
                info["uid"],
                {"name": info["name"], "fans": info["fans"], "avatar": info["avatar"]},
            )
        return info

    def get_uid_list(self, get_type: str) -> list:
        """获取订阅的UID列表"""
        uid_list = []
        for owner_id in self.config:
            if owner_id == "env":
                continue
            for uid, info in self.config[owner_id]["sub"].items():
                if not self.config[owner_id]["enable"]:
                    continue
                if get_type == "dynamic" and info["dynamic_notice"]:
                    uid_list.append(uid)
                elif get_type == "live" and info["live_notice"]:
                    uid_list.append(uid)
                elif get_type == "fans" and info["fans_notice"]:
                    uid_list.append(uid)
        return uid_list

    def get_notice_list(self, get_type: str, uid: str) -> list:
        """获取订阅的owner_id列表"""
        oid_list = []
        for owner_id in self.config:
            if owner_id == "env":
                continue
            for sub_uid, info in self.config[owner_id]["sub"].items():
                if uid != sub_uid:
                    continue
                if not self.config[owner_id]["enable"]:
                    continue
                if get_type == "dynamic" and info["dynamic_notice"]:
                    oid_list.append(owner_id)
                elif get_type == "live" and info["live_notice"]:
                    oid_list.append(owner_id)
                elif get_type == "fans" and info["fans_notice"]:
                    oid_list.append(owner_id)
        return oid_list

    def parse_user_info(self, uid: str, info) -> str:
        """打印用户数据"""
        result = f"[CQ:image,file={info['avatar']},subType=1]"
        result += f"\n用户名：{info['name']}"
        result += f"\nUID：{uid}"
        result += f"\n粉丝数：{info['fans']}"
        keyword = info["keyword"] if info["keyword"] != "" else "无(全部通知)"
        result += f"\n通知关键词：{keyword}"
        dynamic = "开启" if info["dynamic_notice"] else "关闭"
        result += f"\n动态通知：{dynamic}"
        live_enable = "开启" if info["live_notice"] else "关闭"
        result += f"\n直播通知：{live_enable}"
        fans = "开启" if info["fans_notice"] else "关闭"
        result += f"\n粉丝数通知：{fans}"
        return result

    def parse_dynamic(self, data: dict) -> dict:
        """将动态解析为可读数据"""
        dynamic_id = data["id_str"]
        dynamic_type = data["type"]
        author = ""
        content = ""
        imgs = []
        url = f"https://t.bilibili.com/{dynamic_id}"
        origin = None
        module_author = data.get("modules", {}).get("module_author", {})
        module_dynamic = data.get("modules", {}).get("module_dynamic", {})
        if name := module_author.get("name", ""):
            author = name
        if "DYNAMIC_TYPE_FORWARD" == dynamic_type:
            content = module_dynamic.get("desc", {}).get("text", "")
            origin = self.parse_dynamic(data.get("orig", {}))
        elif "DYNAMIC_TYPE_AV" == dynamic_type:
            archive = module_dynamic.get("major", {}).get("archive", {})
            content = archive.get("title", "") + "\n"
            content += archive.get("jump_url", "").replace("//", "https://") + "\n"
            if cover := archive.get("cover"):
                imgs.append(cover)
            content += "\n====================\n"
            content += archive.get("desc", "")
        elif "DYNAMIC_TYPE_DRAW" == dynamic_type:
            opus = module_dynamic.get("major", {}).get("opus", {})
            content = opus.get("title", "") + "\n"
            content += opus.get("summary", {}).get("text", "") + "\n"
            if pics := opus.get("pics"):
                for pic in pics:
                    imgs.append(pic.get("url"))
        elif "DYNAMIC_TYPE_ARTICLE" == dynamic_type:
            opus = module_dynamic.get("major", {}).get("opus", {})
            url = opus.get("jump_url", "")
            content = opus.get("jump_url", "") + "\n"
            content += opus.get("title", "") + "\n"
            content += opus.get("summary", {}).get("text", "") + "\n"
            if pics := opus.get("pics"):
                for pic in pics:
                    imgs.append(pic.get("url"))
        elif "DYNAMIC_TYPE_MUSIC" == dynamic_type:
            music = module_dynamic.get("major", {}).get("music", {})
            content = music.get("jump_url", "") + "\n"
            content += music.get("title", "") + "\n"
            content += module_dynamic.get("desc", {}).get("text", {}) + "\n"
            if cover := music.get("cover"):
                imgs.append(cover)
        elif "DYNAMIC_TYPE_LIVE_RCMD" == dynamic_type:
            live_rcmd = module_dynamic.get("major", {}).get("live_rcmd", {})
            data = json.loads(live_rcmd.get("content", ""))
            content = data.get("live_play_info", {}).get("title", "")
            if cover := data.get("live_play_info", {}).get("cover", ""):
                imgs.append(cover)
        return {
            "url": url,
            "dynamic_id": dynamic_id,
            "dynamic_type": dynamic_type,
            "author": author,
            "content": content.strip(),
            "imgs": imgs,
            "origin": origin,
        }

    def reply_back(self, owner_id: str, msg: str) -> dict:
        """回复消息"""
        if owner_id.startswith("g"):
            group_id = int(owner_id[1:])
            return Utils.send_msg(self.robot, "group", group_id, msg)
        else:
            user_id = int(owner_id[1:])
            return Utils.send_msg(self.robot, "private", user_id, msg)

    def reply_forward_back(
        self, owner_id: str, nodes: list, source=None, summary=None
    ) -> dict:
        """回复消息"""
        if owner_id.startswith("g"):
            group_id = int(owner_id[1:])
            return Utils.send_forward_msg(
                self.robot, nodes, group_id=group_id, source=source, summary=summary
            )
        else:
            user_id = int(owner_id[1:])
            return Utils.send_forward_msg(
                self.robot, nodes, user_id=user_id, source=source, summary=summary
            )
