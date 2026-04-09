import re
from typing import List, Tuple
from urllib.parse import unquote

import requests
from bs4 import BeautifulSoup
from http.cookies import SimpleCookie
from app.core.config import settings
from app.core.meta import MetaBase
from app.helper.cookiecloud import CookieCloudHelper
from app.log import logger
from app.utils.http import RequestUtils


class DoubanHelper:

    def __init__(self, user_cookie: str = None):
        """
        初始化豆瓣助手：
        1. 从插件配置或 CookieCloud 获取 cookie
        2. 组装请求头
        3. 优先使用已有 ck；缺失时再尝试刷新
        """
        if not user_cookie:
            self.cookiecloud = CookieCloudHelper()
            cookie_dict, msg = self.cookiecloud.download()
            if cookie_dict is None:
                logger.error(f"获取cookiecloud数据错误 {msg}")
                self.cookies = {}
            else:
                self.cookies = cookie_dict.get("douban.com")
        else:
            self.cookies = user_cookie
        self.cookies = {k: v.value for k, v in SimpleCookie(self.cookies).items()} if self.cookies else {}

        self.headers = {
            'User-Agent': settings.USER_AGENT,
            'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,image/webp,*/*;q=0.8',
            'Accept-Encoding': 'gzip, deflate, sdch',
            'Accept-Language': 'zh-CN,zh;q=0.8,en-US;q=0.6,en;q=0.4,en-GB;q=0.2,zh-TW;q=0.2',
            'Connection': 'keep-alive',
            'DNT': '1',
            'HOST': 'www.douban.com'
        }

        self.cookies.pop("__utmz", None)

        self.ck = self.cookies.get('ck')
        if not self.ck:
            # 仅在未提供ck时尝试刷新，避免覆盖用户配置中的有效ck
            self.set_ck()
            self.ck = self.cookies.get('ck')
        logger.debug(f"ck:{self.ck} cookie:{self.cookies}")

        if not self.cookies:
            logger.error(f"cookie获取为空，请检查插件配置或cookie cloud")
        if not self.ck:
            logger.error(f"请求ck失败，请检查传入的cookie登录状态")

    def set_ck(self):
        """
        刷新 ck：
        - 优先保留旧 ck，避免刷新失败导致登录态不可用
        - 仅解析 Set-Cookie 中的 ck 字段，不依赖字段顺序
        """
        old_ck = self.cookies.get("ck")
        self.headers["Cookie"] = ";".join([f"{key}={value}" for key, value in self.cookies.items()])

        try:
            response = requests.get("https://www.douban.com/", headers=self.headers, timeout=10)
        except Exception as e:
            logger.error(f"请求豆瓣首页获取ck失败: {e}")
            if old_ck:
                self.cookies["ck"] = old_ck
            return

        ck_str = response.headers.get('Set-Cookie', '')
        logger.debug(ck_str)

        cookie = SimpleCookie()
        cookie.load(ck_str)
        ck_cookie = cookie.get("ck")
        if ck_cookie and ck_cookie.value and ck_cookie.value != '"deleted"':
            self.cookies['ck'] = ck_cookie.value
            logger.debug(self.cookies['ck'])
        elif old_ck:
            self.cookies['ck'] = old_ck
        else:
            self.cookies['ck'] = ''

    def get_subject_id(self, title: str = None, meta: MetaBase = None) -> Tuple | None:
        """
        根据标题查询豆瓣条目并返回 (匹配标题, subject_id)。
        当前实现按搜索结果顺序返回首个命中项。
        """
        if not title:
            title = meta.title
            year = meta.year
        url = f"https://www.douban.com/search?cat=1002&q={title}"
        response = RequestUtils(headers=self.headers).get_res(url)
        if not response.status_code == 200:
            logger.error(f"搜索 {title} 失败 状态码：{response.status_code}")
            return None
        # self.headers["Cookie"] = response.cookies
        soup = BeautifulSoup(response.text.encode('utf-8'), 'lxml')
        title_divs = soup.find_all("div", class_="title")
        subject_items: List = []
        # 遍历所有找到的div标签
        for div in title_divs:
            item = {}

            # title
            a_tag = div.find_all("a")[0]
            item["title"] = a_tag.string
            item["title"] = item["title"].strip()

            # year 原名:피라미드 게임 / 朴昭妍 / 金知妍 / 2024
            # span_tag = div.find_all(class_="subject-cast")[0]
            # year: str = span_tag.string[-4:]
            # if year.isdigit():
            #     item["year"] = year

            # subject_id
            link = unquote(a_tag["href"])
            if link.count("subject/"):
                pattern = r"subject/(\d+)/"
                match = re.search(pattern, link)
                if match:
                    subject_id = match.group(1)
                    item["subject_id"] = subject_id
            subject_items.append(item)

        if not subject_items:
            logger.error(f"找不到 {title} 相关条目 搜索结果html:{response.text.encode('utf-8')}")
        for subject_item in subject_items:
            logger.debug(f"{subject_item['title']} {subject_item['subject_id']}")
            return subject_item["title"], subject_item["subject_id"]
        return None, None

    def set_watching_status(self, subject_id: str, status: str = "do", private: bool = True) -> bool:
        """
        同步豆瓣观影状态：
        - status: do(想看)/doing(在看)/done(看过)
        - 首次返回 403 时刷新 ck 后重试一次
        - 仅当响应 r == 0 视为成功
        """
        self.headers["Referer"] = f"https://movie.douban.com/subject/{subject_id}/"
        self.headers["Origin"] = "https://movie.douban.com"
        self.headers["Host"] = "movie.douban.com"
        self.headers["Cookie"] = ";".join([f"{key}={value}" for key, value in self.cookies.items()])
        data_json = {
            "ck": self.ck,
            "interest": "do",
            "rating": "",
            "foldcollect": "U",
            "tags": "",
            "comment": ""
        }
        if private:
            data_json["private"] = "on"
        data_json["interest"] = status

        try:
            response = requests.post(
                url=f"https://movie.douban.com/j/subject/{subject_id}/interest",
                headers=self.headers,
                data=data_json,
                timeout=10)
        except Exception as e:
            logger.error(f"同步豆瓣状态失败: {e}")
            return False

        if response.status_code == 403:
            # 豆瓣常见场景：ck 过期或风控导致拒绝，刷新后再重试一次
            logger.error(f"豆瓣返回403，尝试刷新ck后重试: {response.text}")
            self.set_ck()
            self.ck = self.cookies.get("ck")
            self.headers["Cookie"] = ";".join([f"{key}={value}" for key, value in self.cookies.items()])
            data_json["ck"] = self.ck
            try:
                response = requests.post(
                    url=f"https://movie.douban.com/j/subject/{subject_id}/interest",
                    headers=self.headers,
                    data=data_json,
                    timeout=10)
            except Exception as e:
                logger.error(f"刷新ck后重试失败: {e}")
                return False

        if response.status_code == 200:
            try:
                ret = response.json().get("r")
            except Exception:
                logger.error(f"豆瓣响应解析失败: {response.text}")
                return False

            # 正常情况 {"r":0}
            if ret == 0:
                return True
            # 未开播 {"r": false}
            if isinstance(ret, bool) and ret is False:
                logger.error(f"douban_id: {subject_id} 未开播")
                return False

            logger.error(response.text)
            return False
        logger.error(response.text)
        return False


if __name__ == "__main__":
    doubanHelper = DoubanHelper()
    subject_title, subject_id = doubanHelper.get_subject_id("太阳的后裔")
    # doubanHelper.set_watching_status(subject_id=subject_id, status="do", private=True)
