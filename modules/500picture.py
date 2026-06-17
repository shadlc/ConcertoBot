"""图片处理模块"""

import base64
import io
import json
import re
import time
import traceback
from typing import Any, Callable, Tuple
from urllib.parse import quote

import httpx
from PIL import Image

from src.base import Module
from src.utils import Utils


class Picture(Module):
    """图片处理模块"""

    ID = "Picture"
    NAME = "图片处理模块"
    HELP = {
        2: [
            "图片 + 打分 / 回复图片发送打分 | 对图片色气度进行打分",
            "来张色图 | 调用Lolicon API获取图片",
            "图片 + 清晰术 / 回复图片发送清晰术 | 调用Real-CUGAN增强图片清晰度",
            "图片 + 搜图 / 回复图片发送搜图 | 调用谷歌搜图搜索图片",
            "图片 + 搜番 / 回复图片发送搜番 | 调用TraceMoe识别作品来源",
            "图片 + 搜人 / 回复图片发送搜人 | 调用AnimeTrace识别角色",
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
    }
    PERSISTENT = True

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
        and self.match(r"^来张(瑟|涩|色|se)图")
    )
    def lolicon(self):
        """调用Lolicon API获取图片"""
        tags = []
        r18_mode = 0
        if len(self.event.text.split(" ")) > 1:
            tags = self.event.text.split(" ")[1:]
        if self.match(r"[rR]18"):
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
        and self.match(r"^(\[.*\])?\s*?(搜索|搜|查询|查|找|识)(角色|人物|人)\s*?(\[.*\])?$")
    )
    def search_animate_person(self):
        """搜番"""
        url = ""
        if match := self.match(r"\[CQ:image,.*url=([^,\]]+?),.*\]"):
            url = match.group(1)
        elif msg := self.get_reply():
            if match := re.search(r"\[CQ:image,.*url=([^,\]]+?),.*\]", msg):
                url = match.group(1)
        if url == "":
            return self.reply("请附带搜索图片或回复带图片的消息!")
        try:
            if not self.is_private():
                Utils.set_emoji(self.robot, self.event.msg_id, 124)
            self.printf(f"正在使用AnimeTrace搜索图片[{url}]...")
            result = self.retry(lambda: self.search_animate_animetrace(url))
            if isinstance(result, str):            
                return self.reply(result, reply=True)
            nodes = []
            for character_url, candidates in result:
                text_lines = [f"作品《{candidates[0][0]}》\n人物: {candidates[0][1]}"]
                if character_url:
                    text_lines.append(f"[CQ:image,file={character_url}]")
                if len(candidates) > 1:
                    text_lines.append("其他相似结果:")
                    for item in candidates[1:]:
                        text_lines.append(f"作品《{item[0]}》人物{item[1]}")
                nodes.append(self.node("\n".join(text_lines)))
            return self.reply_forward(nodes, source="AnimeTrace识别结果")
        except Exception as e:  # pylint: disable=broad-exception-caught
            self.errorf(traceback.format_exc())
            self.reply(f"AnimeTrace调用失败! {e}", reply=True)

    @Utils.handler(
        lambda self: self.au(2)
        and self.at_or_private()
        and self.conv_config.get("animate_search")
        and self.match(r"^(\[.*\])?\s*?(搜索|搜|查询|查|找|识)(番|剧|番剧|动画|动漫)\s*?(\[.*\])?$")
    )
    def search_animate(self):
        """显式使用 TraceMoe 搜番"""
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
        except Exception as e:
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

    def crop_animetrace_box(
        self, image_bytes: bytes, box: list[float] | tuple[float, ...]
    ) -> str | None:
        """按 AnimeTrace 的 box 裁剪人物图并返回 CQ base64 图片"""
        if not isinstance(box, (list, tuple)) or len(box) < 4:
            return None
        try:
            x1, y1, x2, y2 = (float(box[i]) for i in range(4))
        except (TypeError, ValueError):
            return None
        try:
            with Image.open(io.BytesIO(image_bytes)) as source:
                source = source.convert("RGB")
                width, height = source.size
                left = max(0, min(width - 1, int(width * x1)))
                top = max(0, min(height - 1, int(height * y1)))
                right = max(left + 1, min(width, int(width * x2)))
                bottom = max(top + 1, min(height, int(height * y2)))
                if right <= left or bottom <= top:
                    return None
                crop = source.crop((left, top, right, bottom))
                buf = io.BytesIO()
                crop.save(buf, format="PNG")
                return f"base64://{base64.b64encode(buf.getvalue()).decode('utf-8')}"
        except Exception:  # pylint: disable=broad-exception-caught
            return None

    def search_animate_animetrace(
        self, image_url: str, proxies: str = None
    ) -> str | tuple[str, list[dict[str, Any]]]:
        """
        AnimeTrace 搜番
        :param image_url: 图片URL
        :param proxies: 代理配置
        :return: 搜索结果
        """
        img_resp = httpx.get(image_url, timeout=15, proxy=proxies)
        img_resp.raise_for_status()
        content_type = img_resp.headers.get("content-type", "image/jpeg").split(";")[0]
        ext = content_type.split("/")[-1] if "/" in content_type else "jpg"
        files = {
            "file": (
                f"animetrace.{ext or 'jpg'}",
                img_resp.content,
                content_type or "image/jpeg",
            )
        }
        data = {
            "model": "animetrace_high_beta",
            "is_multi": "true",
            "ai_detect": "false",
        }
        resp = httpx.post(
            "https://api.animetrace.com/v1/search",
            data=data,
            files=files,
            timeout=30,
            proxy=proxies,
        )
        resp.raise_for_status()
        payload = resp.json()
        self.printf(
            f"AnimeTrace识别结果:\n{json.dumps(payload, ensure_ascii=False)}",
            level="DEBUG",
        )
        if payload.get("code") != 0:
            return (
                payload.get("zh_message")
                or payload.get("message")
                or payload.get("msg")
                or "AnimeTrace返回异常~"
            )
        results = payload.get("data") or []
        if not results:
            return "AnimeTrace返回无结果~"
        characters = []
        for _, item in enumerate(results, start=1):
            candidates = []
            for char in item.get("character", [])[:4]:
                work = str(char.get("work") or "").strip()
                name = str(char.get("character") or "").strip()
                candidate = [work, name]
                if candidate and candidate not in candidates:
                    candidates.append(candidate)
            crop_url = self.crop_animetrace_box(img_resp.content, item.get("box"))
            characters.append([crop_url, candidates])
        if not characters:
            return "AnimeTrace未识别到明确角色来源~"
        return characters

    def retry(
        self, func: Callable[..., object], name="", max_retries=3, delay=1, failed_ok=False
    ) -> object:
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
