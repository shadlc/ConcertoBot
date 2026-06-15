"""图片处理模块"""

import base64
import io
import json
import random
import re
import time
import traceback
from typing import Any, Callable, Tuple
from urllib.parse import quote

import httpx
from PIL import Image

from src.base import MiniCron, Module
from src.utils import Utils


class Picture(Module):
    """图片处理模块"""

    ID = "Picture"
    NAME = "图片处理模块"
    HELP = {
        2: [
            "图片 + 打分 / 回复图片发送打分 | 对图片色气度进行打分",
            "saucenao + 图片 / 回复图片发送saucenao | 使用SauceNAO搜索图片",
            "来张色图 | 调用Lolicon API获取图片",
            "来张梗图 | 调用煎蛋无聊图获取图片",
            "图片 + 清晰术 / 回复图片发送清晰术 | 调用Real-CUGAN增强图片清晰度",
            "图片 + 搜图 / 回复图片发送搜图 | 调用谷歌搜图搜索图片",
            "图片 + 搜番 / 回复图片发送搜番 | 调用TraceMoe搜索番剧",
        ],
    }
    GLOBAL_CONFIG = {
        "real_cugan_url": "",
        "saucenao_key": "",
        "serpapi_key": "",
    }
    CONV_CONFIG = {
        "animate_search": True,
        "image_search": True,
        "saucenao": True,
        "enhance": True,
        "jiandan": {
            "hist": [],
            "cron": "",
            "probability": 0.5,
        },
    }
    AUTO_INIT = True

    def __init__(self, event, auth=0):
        """初始化图片模块并启动煎蛋图定时任务"""
        super().__init__(event, auth)
        if self.is_persisted():
            return
        self.setup_crons()

    def setup_crons(self) -> None:
        """初始化定时任务"""
        # 煎蛋无聊图定时任务
        for owner, chat in self.config.items():
            if not re.match(r"[ug]\d+", owner):
                continue
            config = chat.get("jiandan")
            prob = config["probability"]
            crontab = config["cron"]
            if not crontab or prob == 0:
                continue
            cron = MiniCron(
                crontab,
                lambda o=owner, c=config: self.jiandan_msg_task(o, c),
                loop=self.robot.loop,
                name=f"{owner} 自动煎蛋无聊图(概率{prob:.2%})",
            )
            self.add_cron(cron)

    async def jiandan_msg_task(self, owner: str, config: dict) -> None:
        """自动煎蛋无聊图"""
        ran_int = random.random()
        prob = config["probability"]
        if ran_int > prob:
            return self.printf(f"[煎蛋无聊图][{owner}]因概率未达而未发送({ran_int:.2}>{prob:.2})")
        data_list = await self.get_jiandan()
        if not data_list:
            return self.printf(f"[煎蛋无聊图][{owner}]因无有效数据而未发送")
        data = None
        for item in data_list:
            if item.get("id") not in config["hist"]:
                data = item
                break
        if not data:
            return self.printf(f"[煎蛋无聊图][{owner}]因无新评论而未发送")
        config["hist"].append(data.get("id"))
        config["hist"] = config["hist"][-10:]
        self.save_config()
        msg = data.get("content").strip()
        msg = msg.replace("/mw600/", "/large/").replace("/thumb180/", "/large/")
        msg = re.sub(
            r"""<img\s+src="([^"]+)"\s*/?>""", r"[CQ:image,sub_type=0,file=\1]", msg
        )
        Utils.reply_back(self.robot, owner, msg)
        if notify_maisaka := self.robot.func.get("notify_maisaka"):
            notify_maisaka(msg, owner[1:])

    @Utils.handler(
        lambda self: self.au(2)
        and self.at_or_private()
        and self.match(r"^(来|发)(张|个)(无聊|屌|弔|吊|梗)图$")
    )
    def jiandan_msg(self):
        """获取煎蛋无聊图"""
        if not self.is_private():
            Utils.set_emoji(self.robot, self.event.msg_id, 124)
        config = self.conv_config["jiandan"]
        data_list = self.robot.sync(self.get_jiandan())
        if not data_list:
            return self.reply("未获取到任何有效数据")
        data = None
        for item in data_list:
            if item.get("id") not in config["hist"]:
                data = item
                break
        if not data:
            return self.reply("未获取到新的评论")
        config["hist"].append(data.get("id"))
        config["hist"] = config["hist"][-10:]
        self.save_config()
        msg = data.get("content").strip()
        msg = re.sub(r"""<img\s+src="([^"]+)"\s*/?>""", r"[CQ:image,file=\1]", msg)
        self.reply(msg)

    @Utils.listener(
        lambda self: self.au(2)
        and self.at_or_private()
        and self.match(r"^(\[.*\])?\s*?(打分|评分)(\[.*\])?$")
    )
    def nsfw(self):
        """对图片色气度进行打分"""
        api_url = "https://nsfwtag.azurewebsites.net/api/nsfw?url="
        url = ""
        if match := self.match(r"\[CQ:image,.*url=([^,\]]+?),.*\]"):
            url = match.group(1)
        elif msg := self.get_reply():
            if match := re.search(r"\[CQ:image,.*url=([^,\]]+?),.*\]", msg):
                url = match.group(1)
        if url == "":
            return
        self.handled = True
        try:
            encoded_url = quote(url, safe="")
            response = httpx.get(api_url + encoded_url, timeout=5)
            response.raise_for_status()
            data = response.json()
            if isinstance(data, list) and len(data) > 0:
                result = data[0]
                neutral = result.get("neutral", 0)
                drawings = result.get("drawings", 0)
                hentai = result.get("hentai", 0)
                porn = result.get("porn", 0)
                sexy = result.get("sexy", 0)
                if neutral > 0.3:
                    return "普通哦"
                category = "二次元" if drawings > 0.3 else "三次元"
                if hentai > 0.3:
                    category += f" hentai{hentai:.1%}"
                if porn > 0.3:
                    category += f" porn{porn:.1%}"
                if sexy > 0.3:
                    category += f" hso{sexy:.1%}"
                if " " not in category:
                    category += "正常图片"
                return self.reply(category, reply=True)
            else:
                return self.reply("API返回格式错误", reply=True)
        except httpx.NetworkError:
            return self.reply("网络请求失败", reply=True)
        except (ValueError, KeyError):
            return self.reply("解析API响应失败", reply=True)

    @Utils.handler(
        lambda self: self.au(2)
        and self.at_or_private()
        and self.match(
            r"^我?(要|来|发|看|给|有没有){0,3}?(更|超|超级|很|再|无敌|最强|大){0,3}?(来|发|看|给|瑟|涩|色|se)\S{0,10}(图|瑟|涩|色|se|好看|好康|可爱)的?"
        )
    )
    def lolicon(self):
        """调用Lolicon API获取图片"""
        tags = []
        r18_mode = 0
        if len(self.event.text.split(" ")) > 1:
            tags = self.event.text.split(" ")[1:]
        if len(tags) == 0 and self.match(r"[张个点只](\S+?)[的图瑟涩色]"):
            tags.append(self.match(r"[张个点只](\S+?)[的图瑟涩色]").group(1))
        if self.match(r"(更|超|超级|很|再|无敌|最强)"):
            r18_mode = 1
        try:
            url = ""
            if not self.is_private():
                Utils.set_emoji(self.robot, self.event.msg_id, 124)
            self.printf(f"正在使用Lolicon API获取图片...{tags}")
            data = self.retry(lambda: self.get_lolicon_image(r18_mode, tags))
            self.printf(f"Lolicon API返回结果:\n{data}", level="DEBUG")
            if data:
                author = f"{data['author']}(uid: {data['uid']})"
                title = f"{data['title']}(pid: {data['pid']})"
                tags = ", ".join(data["tags"])
                url = data.get("urls", {}).get("url")
                if data["r18"]:
                    nodes = []
                    msg = f"来自画师{author}的作品: {title}\n{url}"
                    nodes.append(self.node("NSFW"))
                    nodes.append(self.node(msg))
                    nodes.append(self.node(tags))
                    self.reply_forward(nodes, data["title"], "Pixiv")
                else:
                    nodes = []
                    msg = f"来自画师{author}的作品: {title}\n{url}"
                    nodes.append(self.node(msg))
                    nodes.append(self.node(tags))
                    self.reply_forward(nodes, data["title"], "Pixiv")
                    self.reply(f"[CQ:image,file={url}]")
            else:
                return self.reply(f"未找到标签[{tags}]的图片", reply=True)
        except Exception as e:  # pylint: disable=broad-exception-caught
            self.errorf(traceback.format_exc())
            self.reply(f"Lolicon API调用失败! {e}", reply=True)

    @Utils.handler(
        lambda self: self.au(2)
        and self.at_or_private()
        and self.conv_config.get("animate_search")
        and self.match(
            r"^(\[.*\])?\s*?(搜索|搜|查询|查|找)(番|剧|番剧|动画|动漫)\s*?(\[.*\])?$"
        )
    )
    def search_animate(self):
        """搜番"""
        url = ""
        if match := self.match(r"\[CQ:image,.*url=([^,\]]+?),.*\]"):
            url = match.group(1)
        elif msg := self.get_reply():
            if match := re.search(r"\[CQ:image,.*url=([^,\]]+?),.*\]", msg):
                url = match.group(1)
        if url == "":
            return self.reply("请附带番剧截图或回复带截图的消息!")
        try:
            if not self.is_private():
                Utils.set_emoji(self.robot, self.event.msg_id, 124)
            self.printf(f"正在使用TraceMoe搜索图片[{url}]...")
            msg = self.retry(lambda: self.search_animate_tracemoe(url))
            return self.reply(msg, reply=True)
        except Exception as e:  # pylint: disable=broad-exception-caught
            self.errorf(traceback.format_exc())
            self.reply(f"TraceMoe调用失败! {e}", reply=True)

    @Utils.handler(
        lambda self: self.au(2)
        and self.at_or_private()
        and self.conv_config.get("image_search")
        and self.match(r"^(\[.*\])?\s*?(搜索|搜|查询|查|找|识)(图|图片)\s*?(\[.*\])?$")
    )
    def search_image(self):
        """谷歌搜图"""
        url = ""
        if match := self.match(r"\[CQ:image,.*url=([^,\]]+?),.*\]"):
            url = match.group(1)
        elif msg := self.get_reply():
            if match := re.search(r"\[CQ:image,.*url=([^,\]]+?),.*\]", msg):
                url = match.group(1)
        if url == "":
            return self.reply("请附带图片或回复带图片的消息!")
        try:
            if not self.is_private():
                Utils.set_emoji(self.robot, self.event.msg_id, 124)
            self.printf(f"正在使用谷歌搜图搜索图片[{url}]...")
            success, data = self.retry(lambda: self.search_image_google(url))
            if not success:
                return self.reply(data, reply=True)
            nodes = []
            if msg := data[0]:
                nodes.append(self.node(msg))
            for img_msg in data[1]:
                nodes.append(self.node(img_msg))
            if not self.is_private():
                Utils.set_emoji(self.robot, self.event.msg_id, 66)
            self.reply_forward(nodes, source="谷歌搜图结果")
        except Exception as e:  # pylint: disable=broad-exception-caught
            self.errorf(traceback.format_exc())
            self.reply(f"谷歌搜图调用失败! {e}", reply=True)

    @Utils.listener(
        lambda self: self.au(2)
        and self.conv_config.get("saucenao")
        and self.match(r"^(\[.*\])?\s*?(s|S)auce(n|N)(a|A)(o|O)")
    )
    def saucenao(self):
        """SauceNAO搜图"""
        url = ""
        if match := self.match(r"\[CQ:image,.*url=([^,\]]+?),.*\]"):
            url = match.group(1)
        elif msg := self.get_reply():
            if match := re.search(r"\[CQ:image,.*url=([^,\]]+?),.*\]", msg):
                url = match.group(1)
        if url == "":
            return
        self.handled = True
        try:
            if not self.is_private():
                Utils.set_emoji(self.robot, self.event.msg_id, 124)
            self.printf(f"正在使用SauceNAO搜索图片[{url}]...")
            success, data = self.retry(lambda: self.search_image_saucenao(url))
            if not success:
                return self.reply(data, reply=True)
            nodes = []
            for img_msg in data:
                nodes.append(self.node(img_msg))
            if not self.is_private():
                Utils.set_emoji(self.robot, self.event.msg_id, 66)
            self.reply_forward(nodes, source="SauceNAO搜索结果")
        except Exception as e:  # pylint: disable=broad-exception-caught
            self.errorf(traceback.format_exc())
            self.reply(f"SauceNAO调用失败! {e}", reply=True)

    @Utils.listener(
        lambda self: self.au(2)
        and self.conv_config.get("enhance")
        and self.match(r"清晰术")
    )
    def enhance_img(self):
        """清晰术"""
        url = ""
        if match := self.match(r"\[CQ:image,.*url=([^,\]]+?),.*\]"):
            url = match.group(1)
        elif msg := self.get_reply():
            if match := re.search(r"\[CQ:image,.*url=([^,\]]+?),.*\]", msg):
                url = match.group(1)
        if url == "":
            return
        self.handled = True
        if not self.config.get("real_cugan_url"):
            return self.reply("星辰坐标未对齐，法阵无法唤醒!")
        cmd = self.event.text
        try:
            resp = httpx.get(url)
            img = Image.open(io.BytesIO(resp.content))
            img_width, img_height = img.size
            scale = 2
            con = "conservative"
            # 解析放大倍数
            if "双重" in cmd:
                scale = 2
            elif "三重" in cmd and img_width * img_height < 400000:
                scale = 3
            elif "四重" in cmd and img_width * img_height < 400000:
                scale = 4
            # 解析降噪模式
            if "强力术式" in cmd:
                con = "denoise3x"
            elif "中等术式" in cmd:
                con = "no-denoise" if scale != 2 else "denoise2x"
            elif "弱术式" in cmd:
                con = "no-denoise" if scale != 2 else "denoise1x"
            elif "不变式" in cmd:
                con = "no-denoise"
            elif "原式" in cmd:
                con = "conservative"
            if not self.is_private():
                Utils.set_emoji(self.robot, self.event.msg_id, 124)
            self.printf("正在从HuggingFace调用Real-CUGAN模型")
            enhanced_image = self.real_cugan(resp.content, scale, con)
            enhanced_image_url = re.sub(
                r"data:image/.*;base64,", "base64://", enhanced_image
            )
            if not self.is_private():
                Utils.set_emoji(self.robot, self.event.msg_id, 66)
            return self.reply(f"[CQ:image,url={enhanced_image_url}]", reply=True)
        except Exception as e:  # pylint: disable=broad-exception-caught
            self.errorf(traceback.format_exc())
            self.reply(f"{e}", reply=True)

    def real_cugan(self, img: bytes, scale: int, con: str) -> str:
        """Real-CUGAN 增强图片清晰度

        :param img: 输入的图片字节流
        :param scale: 放大倍数（如2、3、4）
        :param con: 增强模型的配置（如"conservative", "no-denoise"等）
        :return: 增强后的图片（Base64编码的字符串）
        """
        try:
            predict_url = self.config.get("real_cugan_url")
            model_name = f"up{scale}x-latest-{con}.pth"
            b64 = base64.b64encode(img).decode("utf-8")
            encoded_image = f"data:image/jpeg;base64,{b64}"
            payload = {"data": [encoded_image, model_name, 2]}
            headers = {"Content-Type": "application/json"}
            response = httpx.post(
                predict_url,
                json=payload,
                headers=headers,
                timeout=300,
                follow_redirects=True,
            )
            response.raise_for_status()
            result = response.json()
            enhanced_image = result.get("data", [None])[0]
            if not enhanced_image:
                raise RuntimeError("Real-CUGAN 未返回增强图片数据")
            return enhanced_image
        except Exception as e:  # pylint: disable=broad-exception-caught
            raise RuntimeError(f"群星之路被遮蔽，星辉无法汇聚: {str(e)}") from e

    def get_lolicon_image(
        self, r18: int = 0, tags: list = None, ai: bool = False
    ) -> dict | None:
        """
        获取LoliconAPI图片
        :param r18: 是否获取R18图片
        :param tags: 需要筛选的标签
        :param ai: 是否包含ai图片
        :return: 图片链接
        """
        url = f"https://api.lolicon.app/setu/v2?r18={r18}&excludeAI={not ai}&proxy=i.pximg.org"
        for tag in tags or []:
            url += f"&tag={quote(tag)}"
        resp = httpx.get(url, timeout=5)
        data = resp.json()
        self.printf(f"调用LoliconAPI({url})返回结果：{data}", level="DEBUG")
        if data.get("data") == []:
            return None
        original_url = data.get("data")[0].get("urls", {}).get("original")
        qq_url = Utils.get_img_url(self.robot, original_url)
        if url == qq_url:
            raise RuntimeError("尝试多次，图片链接均已失效，请重新获取")
        data["data"][0]["urls"]["url"] = qq_url
        img = data["data"][0]
        return img

    def search_image_saucenao(
        self, image_url: str, proxies: str = None
    ) -> Tuple[bool, str | list]:
        """
        SauceNAO搜图
        :param image_url: 图片URL
        :param proxies: 代理配置
        :return: [搜索是否成功, 搜索结果]
        """
        saucenao_key = self.config.get("saucenao_key")
        if not saucenao_key:
            msg = "请先前往[https://saucenao.com/user.php?page=search-api]获取APIKey"
            return False, msg
        saucenao_url = "https://saucenao.com/search.php"
        params = {
            "url": image_url,
            "api_key": saucenao_key,
            "output_type": 2,
            "numres": 3,
        }
        resp = httpx.get(saucenao_url, params=params, timeout=10, proxy=proxies)
        if results := resp.json().get("results"):
            self.printf(
                f"SauceNAO搜图结果:\n{json.dumps(results, ensure_ascii=False)}",
                level="DEBUG",
            )
            msg_list = []
            for _, image in enumerate(results):
                header = image.get("header")
                data = image.get("data")
                similarity = header.get("similarity")
                thumbnail = header.get("thumbnail")
                title = data.get("title", "")
                source = data.get("source")
                creator = data.get("creator", "未知")
                author = data.get("author", data.get("artist", creator))
                if isinstance(creator, list):
                    author = ", ".join(creator)
                if data.get("member_name"):
                    author = f"{data.get('member_name')} (uid: {data.get('member_id')})"
                msg = f"{title}"
                msg += f"\n作者: {author}"
                msg += f"\n相似度: {similarity}%"
                if urls := data.get("ext_urls"):
                    msg += f"\n原图地址: {urls[0]}"
                if source:
                    if "i.pximg.net" in source:
                        source = re.sub(
                            r"i\.pximg\.net.*/(\d{5,})",
                            r"www.pixiv.net/artworks/\1",
                            source,
                        )
                    msg += f"\n来源: {source}"
                msg += f"\n[CQ:image,file={thumbnail}]"
                msg_list.append(msg)
            return True, msg_list
        elif message := resp.json().get("message"):
            message = message.split("<br />")[0].strip()
            message = re.sub(r"<.*?>", "", message)
            return False, message
        else:
            return False, "SauceNAO返回无结果~"

    def search_image_google(
        self, image_url: str, proxies: str = None
    ) -> Tuple[bool, str | list]:
        """
        谷歌搜图
        :param image_url: 图片URL
        :param proxies: 代理配置
        :return: [搜索是否成功, 搜索结果]
        """
        serpapi_key = self.config.get("serpapi_key")
        if not serpapi_key:
            msg = "请先前往[https://serpapi.com/manage-api-key]获取APIKey"
            return False, msg
        api_url = "https://serpapi.com/search"
        params = {
            "engine": "google_lens",
            "hl": "zh-cn",
            "api_key": serpapi_key,
            "url": image_url,
        }
        resp = httpx.get(api_url, params=params, timeout=10, proxy=proxies)
        self.printf(
            f"谷歌搜图结果:\n{json.dumps(resp.text, ensure_ascii=False)}", level="DEBUG"
        )
        success = False
        result = ""
        if matches := resp.json().get("visual_matches"):
            msg_list = []
            for _, data in enumerate(matches[:10]):
                title = data.get("title", "")
                source = data.get("source")
                link = data.get("link")
                thumbnail = data.get("thumbnail")
                date = data.get("date")
                msg = f"{title}"
                msg += f"\n{source}[{link}]"
                if date:
                    msg += f"\n时间: {date}"
                if thumbnail:
                    msg += f"\n[CQ:image,file={thumbnail}]"
                msg_list.append(msg)
            result = ["", msg_list]
            success = True
            if page_token := resp.json().get("ai_overview", {}).get("page_token"):
                flag, overview = self.get_google_ai_overview(page_token)
                if flag:
                    result[0] = overview
        elif message := resp.json().get("error"):
            result = message
        else:
            result = "谷歌搜图返回无结果~"
        return success, result

    def get_google_ai_overview(
        self, page_token: str, proxies: str = None
    ) -> Tuple[bool, str | list]:
        """
        谷歌AI总结
        :param page_token: 页面令牌
        :param proxies: 代理配置
        :return: [搜索是否成功, 搜索结果]
        """

        def extract_snippets(obj):
            """提取snippets"""
            result = []
            if isinstance(obj, dict):
                for k, v in obj.items():
                    if k == "snippet":
                        result.append(v)
                    else:
                        result.extend(extract_snippets(v))
            elif isinstance(obj, list):
                for item in obj:
                    result.extend(extract_snippets(item))
            return result

        serpapi_key = self.config.get("serpapi_key")
        if not serpapi_key:
            msg = "请先前往[https://serpapi.com/manage-api-key]获取APIKey"
            return False, msg
        api_url = "https://serpapi.com/search"
        params = {
            "engine": "google_ai_overview",
            "api_key": serpapi_key,
            "page_token": page_token,
        }
        resp = httpx.get(api_url, params=params, timeout=10, proxy=proxies)
        self.printf(
            f"谷歌AI总结结果:\n{json.dumps(resp.text, ensure_ascii=False)}",
            level="DEBUG",
        )
        success = False
        result = ""
        if ai_overview := resp.json().get("ai_overview"):
            success = True
            snippets = extract_snippets(ai_overview)
            result = "\n".join(snippets)
        return success, result

    def search_animate_tracemoe(self, image_url: str, proxies: str = None) -> str:
        """
        TraceMoe 搜图
        :param image_url: 图片URL
        :param proxies: 代理配置
        :return: 搜索结果
        """
        tracemoe_url = "https://api.trace.moe/search?cutBorders&anilistInfo"
        url = f"{tracemoe_url}&url={quote(image_url)}"
        resp = httpx.post(url, timeout=10, proxy=proxies)
        resp.raise_for_status()
        data = resp.json()
        if results := data.get("result"):
            self.printf(
                f"TraceMoe搜番结果:\n{json.dumps(results, ensure_ascii=False)}",
                level="DEBUG",
            )
            res = results[0]
            ani = res.get("anilist", {})
            similarity = res.get("similarity", 0) * 100
            title_chs = ani.get("title", {}).get("chinese")
            title_native = ani.get("title", {}).get("native", "")
            title_eng = ani.get("title", {}).get("english", "")
            episode = res.get("episode")
            image = res.get("image")
            at = res.get("at")
            msg = "肯定是"
            if similarity < 0.8:
                msg = "大概是"
            msg += f"《{title_chs or title_native or title_eng}》"
            msg += f"第{episode}集"
            msg += f"的{Utils.calc_time(at)}"
            msg += f"\n相似度: {similarity:.2f}%"
            msg += f"\n[CQ:image,file={image}]"
            return msg
        else:
            return "TraceMoe返回无结果~"

    async def get_jiandan(self, page=0, page_num=3, raise_error=False) -> str | None:
        """获取一张煎蛋无聊图"""
        page_str = f"第{page}页" if page else "最新一页"
        try:
            url = f"https://jandan.net/api/comment/post/26402?order=desc?page={page}"
            self.printf(f"获取煎蛋无聊图{page_str}数据")
            resp = await httpx.AsyncClient().get(url, timeout=3)
            resp.raise_for_status()
            data = resp.json().get("data", {}).get("list")
            current_page = resp.json().get("data", {}).get("current_page", 0)
            if not data:
                return []
            if page != 0:
                return data
            if page == 0 and page_num > 0:
                for i in range(1, page_num + 1):
                    data += await self.get_jiandan(current_page - i)
            data = sorted(
                data,
                key=lambda x: x["vote_positive"] - x["vote_negative"],
                reverse=True,
            )
            result = []
            for item in data:
                if (
                    item["vote_positive"] > item["vote_negative"]
                    and item["vote_positive"] > 0
                    and item["vote_negative"] < 30
                    and item["vote_positive"] < 100
                ):
                    # 排除煎蛋本平台相关帖子，宁缺毋滥
                    if "蛋" in item.get("content"):
                        continue
                    result.append(item)
            self.printf(f"共请求到{len(result)}条有效的帖子")
            return result
        except httpx.ConnectTimeout as e:
            self.errorf(f"获取煎蛋无聊图{page_str}时网络请求超时 {e}")
        except Exception as e:  # pylint: disable=broad-exception-caught
            self.errorf(f"获取煎蛋无聊图{page_str}失败 {traceback.format_exc()}")
            if raise_error:
                raise e
            return []

    def retry(
        self, func: Callable[..., Any], name="", max_retries=3, delay=1, failed_ok=False
    ) -> Any:
        """多次尝试执行"""
        for attempt in range(1, max_retries + 1):
            try:
                result = func()
                return result
            except Exception as e:  # pylint: disable=broad-exception-caught
                func_name = name if name else func.__name__
                self.printf(f"第 {attempt} 次执行 {func_name} 失败: {e}")
                if attempt == max_retries:
                    if failed_ok:
                        return None
                    raise
                else:
                    self.printf(f"{delay} 秒后重试...")
                    time.sleep(delay)
