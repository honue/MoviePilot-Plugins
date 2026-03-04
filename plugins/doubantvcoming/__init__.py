import datetime
import re
import xml.dom.minidom
from threading import Event
from typing import Tuple, List, Dict, Any, Optional
from urllib.parse import urlparse

import pytz
from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.cron import CronTrigger

from app.chain.download import DownloadChain
from app.chain.subscribe import SubscribeChain
from app.core.config import settings
from app.core.context import MediaInfo
from app.core.metainfo import MetaInfo
from app.log import logger
from app.plugins import _PluginBase
from app.schemas.types import MediaType, NotificationType
from app.utils.dom import DomUtils
from app.utils.http import RequestUtils


class DoubanTvComing(_PluginBase):
    plugin_name = "豆瓣即将播出订阅"
    plugin_desc = "豆瓣即将播出剧集，想看人数，超过阈值后自动添加订阅。"
    plugin_icon = "douban.png"
    plugin_version = "1.1"
    plugin_author = "honue"
    author_url = "https://github.com/honue"
    plugin_config_prefix = "doubantvcoming_"
    plugin_order = 14
    auth_level = 1

    _event = Event()
    downloadchain: DownloadChain = None
    subscribechain: SubscribeChain = None
    _scheduler = None

    _enabled = False
    _cron = "5 18 * * *"
    _onlyonce = False
    _clear = False
    _clearflag = False
    _proxy = False
    _rss_domain = "https://rsshub.ddsrem.com/"
    _rss_path = "/douban/tv/coming"
    _rss_url = "https://rsshub.ddsrem.com/douban/tv/coming"
    _air_date_within_days = 3
    _min_wish = 5000
    _region_filters: List[str] = []
    _genre_filters: List[str] = []

    _region_options = [
        "中国大陆", "中国香港", "中国台湾", "美国", "日本", "韩国", "英国", "泰国", "印度", "法国",
        "德国", "西班牙", "加拿大", "澳大利亚", "俄罗斯", "瑞典", "丹麦", "爱尔兰", "意大利", "巴西"
    ]
    _genre_options = [
        "爱情", "喜剧", "剧情", "悬疑", "古装", "动作", "犯罪", "科幻", "家庭", "奇幻", "武侠",
        "历史", "动画", "惊悚", "战争", "冒险", "恐怖", "灾难", "传记", "音乐", "歌舞"
    ]

    def init_plugin(self, config: dict = None):
        self.downloadchain = DownloadChain()
        self.subscribechain = SubscribeChain()

        config = config or {}
        self._enabled = config.get("enabled", False)
        self._cron = config.get("cron") or "5 18 * * *"
        self._proxy = config.get("proxy", False)
        self._onlyonce = config.get("onlyonce", False)
        self._clear = config.get("clear", False)
        self._rss_domain = self.__normalize_rss_domain(config.get("rss_domain") or "https://rsshub.app")
        self._rss_url = self.__build_rss_url(self._rss_domain)
        self._min_wish = int(config.get("min_wish", 5000) or 5000)
        self._air_date_within_days = int(config.get("air_date_within_days", 3) or 3)
        self._region_filters = config.get("region_filters") or []
        self._genre_filters = config.get("genre_filters") or []

        # 清理历史不依赖任务执行，保存配置后立即生效
        if self._clear:
            self.save_data("history", [])
            self._clear = False
            self._clearflag = False
            self.__update_config()

        self.stop_service()

        if self._enabled or self._onlyonce:
            self._scheduler = BackgroundScheduler(timezone=settings.TZ)
            if self._enabled and self._cron:
                try:
                    self._scheduler.add_job(
                        func=self.__refresh_rss,
                        trigger=CronTrigger.from_crontab(self._cron),
                        name="豆瓣剧集即将播出订阅"
                    )
                    # logger.info(f"豆瓣剧集即将播出订阅服务启动，周期：{self._cron}")
                except Exception as err:
                    logger.error(f"豆瓣剧集即将播出订阅服务启动失败：{err}")
                    self.systemmessage.put(f"豆瓣剧集即将播出订阅服务启动失败：{err}")
            else:
                self._enabled = False

            if self._onlyonce:
                self._scheduler.add_job(
                    func=self.__refresh_rss,
                    trigger="date",
                    run_date=datetime.datetime.now(
                        tz=pytz.timezone(settings.TZ)
                    ) + datetime.timedelta(seconds=3)
                )
                logger.info("豆瓣剧集即将播出订阅服务启动，立即运行一次")

            if self._onlyonce:
                self._onlyonce = False
                self.__update_config()

            if self._scheduler.get_jobs():
                self._scheduler.print_jobs()
                self._scheduler.start()

    def get_state(self) -> bool:
        return self._enabled

    @staticmethod
    def get_command() -> List[Dict[str, Any]]:
        pass

    def get_api(self) -> List[Dict[str, Any]]:
        pass

    def get_form(self) -> Tuple[List[dict], Dict[str, Any]]:
        return [
            {
                "component": "VForm",
                "content": [
                    {
                        "component": "VRow",
                        "content": [
                            {
                                "component": "VCol",
                                "props": {"cols": 12, "md": 3},
                                "content": [
                                    {
                                        "component": "VSwitch",
                                        "props": {
                                            "model": "enabled",
                                            "label": "启用插件"
                                        }
                                    }
                                ]
                            },
                            {
                                "component": "VCol",
                                "props": {"cols": 12, "md": 3},
                                "content": [
                                    {
                                        "component": "VSwitch",
                                        "props": {
                                            "model": "proxy",
                                            "label": "是否使用代理"
                                        }
                                    }
                                ]
                            },
                            {
                                "component": "VCol",
                                "props": {"cols": 12, "md": 3},
                                "content": [
                                    {
                                        "component": "VSwitch",
                                        "props": {
                                            "model": "onlyonce",
                                            "label": "立即运行一次"
                                        }
                                    }
                                ]
                            },
                            {
                                "component": "VCol",
                                "props": {"cols": 12, "md": 3},
                                "content": [
                                    {
                                        "component": "VSwitch",
                                        "props": {
                                            "model": "clear",
                                            "label": "清理历史记录"
                                        }
                                    }
                                ]
                            }
                        ]
                    },
                    {
                        "component": "VRow",
                        "content": [
                            {
                                "component": "VCol",
                                "props": {"cols": 12, "md": 6},
                                "content": [
                                    {
                                        "component": "VTextField",
                                        "props": {
                                            "model": "cron",
                                            "label": "执行周期",
                                            "placeholder": "5位cron表达式"
                                        }
                                    }
                                ]
                            },
                            {
                                "component": "VCol",
                                "props": {"cols": 12, "md": 6},
                                "content": [
                                    {
                                        "component": "VTextField",
                                        "props": {
                                            "model": "rss_domain",
                                            "label": "RSSHub域名",
                                            "placeholder": "https://rsshub.app"
                                        }
                                    }
                                ]
                            }
                        ]
                    },
                    {
                        "component": "VRow",
                        "content": [
                            {
                                "component": "VCol",
                                "props": {"cols": 12, "md": 6},
                                "content": [
                                    {
                                        "component": "VTextField",
                                        "props": {
                                            "model": "min_wish",
                                            "label": "最小想看人数",
                                            "placeholder": "5000"
                                        }
                                    }
                                ]
                            },
                            {
                                "component": "VCol",
                                "props": {"cols": 12, "md": 6},
                                "content": [
                                    {
                                        "component": "VTextField",
                                        "props": {
                                            "model": "air_date_within_days",
                                            "label": "提取订阅（天）",
                                            "placeholder": "7"
                                        }
                                    }
                                ]
                            }
                        ]
                    },
                    {
                        "component": "VRow",
                        "content": [
                            {
                                "component": "VCol",
                                "props": {"cols": 12, "md": 6},
                                "content": [
                                    {
                                        "component": "VSelect",
                                        "props": {
                                            "model": "region_filters",
                                            "label": "地区筛选",
                                            "items": self._region_options,
                                            "multiple": True,
                                            "chips": True,
                                            "clearable": True
                                        }
                                    }
                                ]
                            },
                            {
                                "component": "VCol",
                                "props": {"cols": 12, "md": 6},
                                "content": [
                                    {
                                        "component": "VSelect",
                                        "props": {
                                            "model": "genre_filters",
                                            "label": "类型筛选",
                                            "items": self._genre_options,
                                            "multiple": True,
                                            "chips": True,
                                            "clearable": True
                                        }
                                    }
                                ]
                            }
                        ]
                    },
                    {
                        "component": "VRow",
                        "content": [
                            {
                                "component": "VCol",
                                "props": {"cols": 12},
                                "content": [
                                    {
                                        "component": "VAlert",
                                        "props": {
                                            "type": "info",
                                            "variant": "tonal",
                                            "text": (
                                                "先按想看人数阈值过滤；地区/类型筛选为“命中任一即通过”，未选择则不筛选；"
                                                "随后通过TMDB获取首播日期，仅订阅窗口期内将播出的剧集。"
                                            )
                                        }
                                    }
                                ]
                            }
                        ]
                    }
                ]
            }
        ], {
            "enabled": False,
            "proxy": False,
            "onlyonce": False,
            "clear": False,
            "cron": "0 */6 * * *",
            "rss_domain": "https://rsshub.ddsrem.com",
            "min_wish": 5000,
            "air_date_within_days": 7,
            "region_filters": [],
            "genre_filters": []
        }

    def get_page(self) -> List[dict]:
        history: List[dict] = self.get_data("history") or []
        if not history:
            return [{
                "component": "div",
                "text": "暂无数据",
                "props": {"class": "text-center"}
            }]

        history = sorted(history, key=lambda x: x.get("time", ""), reverse=True)
        contents = []
        for item in history:
            title = item.get("title")
            poster = item.get("poster")
            link = item.get("link")
            wish_count = item.get("wish_count", 0)
            air_date = item.get("air_date", "")
            time_str = item.get("time", "")
            contents.append({
                "component": "VCard",
                "content": [
                    {
                        "component": "div",
                        "props": {
                            "class": "d-flex justify-space-start flex-nowrap flex-row",
                        },
                        "content": [
                            {
                                "component": "div",
                                "content": [
                                    {
                                        "component": "VImg",
                                        "props": {
                                            "src": poster,
                                            "height": 120,
                                            "width": 80,
                                            "aspect-ratio": "2/3",
                                            "class": "object-cover shadow ring-gray-500",
                                            "cover": True
                                        }
                                    }
                                ]
                            },
                            {
                                "component": "div",
                                "content": [
                                    {
                                        "component": "VCardSubtitle",
                                        "props": {
                                            "class": "pa-2 font-bold break-words whitespace-break-spaces"
                                        },
                                        "content": [
                                            {
                                                "component": "a",
                                                "props": {
                                                    "href": link,
                                                    "target": "_blank"
                                                },
                                                "text": title
                                            }
                                        ]
                                    },
                                    {
                                        "component": "VCardText",
                                        "props": {
                                            "class": "pa-0 px-2"
                                        },
                                        "text": f"想看人数：{wish_count}"
                                    },
                                    {
                                        "component": "VCardText",
                                        "props": {
                                            "class": "pa-0 px-2"
                                        },
                                        "text": f"首播日期：{air_date}"
                                    },
                                    {
                                        "component": "VCardText",
                                        "props": {
                                            "class": "pa-0 px-2"
                                        },
                                        "text": f"时间：{time_str}"
                                    }
                                ]
                            }
                        ]
                    }
                ]
            })
        return [{
            "component": "div",
            "props": {"class": "grid gap-3 grid-info-card"},
            "content": contents
        }]

    def stop_service(self):
        try:
            if self._scheduler:
                self._scheduler.remove_all_jobs()
                if self._scheduler.running:
                    self._event.set()
                    self._scheduler.shutdown()
                    self._event.clear()
                self._scheduler = None
        except Exception as err:
            logger.error(str(err))

    def __update_config(self):
        self.update_config({
            "enabled": self._enabled,
            "cron": self._cron,
            "proxy": self._proxy,
            "onlyonce": self._onlyonce,
            "clear": self._clear,
            "rss_domain": self._rss_domain,
            "min_wish": self._min_wish,
            "air_date_within_days": self._air_date_within_days,
            "region_filters": self._region_filters,
            "genre_filters": self._genre_filters
        })

    def __refresh_rss(self):
        if not self._rss_url:
            logger.info("未设置RSS地址，结束任务")
            return

        if self._clearflag:
            history = []
            # 先落库清空，避免本次任务异常/提前退出时残留旧历史
            self.save_data("history", history)
        else:
            history: List[dict] = self.get_data("history") or []
        unique_history = {item.get("unique") for item in history}

        logger.info(f"开始刷新豆瓣即将播出RSS：{self._rss_url}")
        rss_infos = self.__get_rss_info(self._rss_url)
        if not rss_infos:
            logger.error(f"RSS地址：{self._rss_url}，未查询到数据")
            return

        logger.info(f"RSS地址：{self._rss_url}，获取 {len(rss_infos)} 条数据")
        for rss_info in rss_infos:
            if self._event.is_set():
                logger.info("订阅服务停止")
                return

            title = rss_info.get("title") or ""
            link = rss_info.get("link") or ""
            wish_count = rss_info.get("wish_count", 0)
            rss_description = rss_info.get("description") or ""
            year = rss_info.get("year") or ""
            regions = rss_info.get("regions") or []
            genres = rss_info.get("genres") or []
            unique_flag = f"doubantvcoming:{link or title}"
            logger.info(f"\n")

            logger.info(f"标题：{title}，想看人数：{wish_count}，地区：{regions}，类型：{genres}")
            if unique_flag in unique_history:
                logger.info(f"{title} 已处理过")
                continue
            if wish_count < self._min_wish:
                logger.info(f"{title} 想看人数 {wish_count} 未达到阈值 {self._min_wish}")
                continue
            if not self.__match_any_filter(regions, self._region_filters):
                logger.info(f"{title} 地区 {regions} 未命中已选筛选 {self._region_filters}")
                continue
            if not self.__match_any_filter(genres, self._genre_filters):
                logger.info(f"{title} 类型 {genres} 未命中已选筛选 {self._genre_filters}")
                continue

            meta = MetaInfo(title)
            if year:
                meta.year = str(year)
            meta.type = MediaType.TV

            mediainfo: MediaInfo = self.chain.recognize_media(meta=meta, mtype=MediaType.TV)
            if not mediainfo:
                logger.warn(f"未识别到媒体信息，标题：{title}，链接：{link}")
                continue

            tmdb_air_date = self.__get_tmdb_air_date(mediainfo.tmdb_id, season=meta.begin_season)
            if not tmdb_air_date:
                logger.info(f"{title} 未获取到TMDB播出日期，跳过")
                continue
            if not self.__is_within_days(tmdb_air_date, self._air_date_within_days):
                logger.info(
                    f"{title} TMDB播出日期 {tmdb_air_date} 不在{self._air_date_within_days}天内，跳过"
                )
                continue

            exist_flag, _ = self.downloadchain.get_no_exists_info(meta=meta, mediainfo=mediainfo)
            if exist_flag:
                logger.info(f"{mediainfo.title_year} 媒体库中已存在")
                continue
            if self.subscribechain.exists(mediainfo=mediainfo, meta=meta):
                logger.info(f"{mediainfo.title_year} 订阅已存在")
                continue

            sid, msg = self.subscribechain.add(
                title=mediainfo.title,
                year=mediainfo.year or year or "",
                mtype=MediaType.TV,
                tmdbid=mediainfo.tmdb_id,
                season=meta.begin_season,
                exist_ok=True,
                username="豆瓣即将播出剧集",
                message=False
            )
            if not sid:
                logger.error(f"{title} 订阅失败：{msg}")
                continue
            
            self.post_message(
                mtype=NotificationType.Subscribe,
                title=f"{mediainfo.title_year} Season {meta.begin_season if meta.begin_season else '1'} 已添加订阅",
                text=(
                    f"播出时间：{tmdb_air_date}，{rss_description or mediainfo.overview or '暂无简介'}\n"
                    f"豆瓣链接：{self.__build_douban_dispatch_link(link)}\n\n"
                    f"[{self.plugin_name}]\n"
                ),
                image=mediainfo.get_message_image(),
                link=settings.MP_DOMAIN("#/subscribe/tv?tab=mysub")
            )

            logger.info(f"{title} 想看人数 {wish_count}，已添加订阅")
            history.append({
                "title": title,
                "year": year,
                "wish_count": wish_count,
                "air_date": tmdb_air_date,
                "regions": regions,
                "genres": genres,
                "link": link,
                "tmdbid": mediainfo.tmdb_id,
                "poster": mediainfo.get_poster_image(),
                "overview": mediainfo.overview,
                "time": datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                "unique": unique_flag
            })
            unique_history.add(unique_flag)

        self.save_data("history", history)
        self._clearflag = False
        logger.info("豆瓣即将播出RSS刷新完成\n")

    def __get_rss_info(self, addr: str) -> List[dict]:
        try:
            if self._proxy:
                ret = RequestUtils(proxies=settings.PROXY).get_res(addr)
            else:
                ret = RequestUtils().get_res(addr)
            if not ret:
                return []

            dom_tree = xml.dom.minidom.parseString(ret.text)
            root_node = dom_tree.documentElement
            items = root_node.getElementsByTagName("item")

            ret_array = []
            for item in items:
                title = DomUtils.tag_value(item, "title", default="")
                link = DomUtils.tag_value(item, "link", default="")
                description = DomUtils.tag_value(item, "description", default="")
                category = DomUtils.tag_value(item, "category", default="")

                if not title and not link:
                    continue

                wish_count = self.__parse_wish_count(description)
                year = self.__parse_year(category)
                regions, genres = self.__parse_regions_and_genres(category)
                ret_array.append({
                    "title": title,
                    "link": link,
                    "description": description,
                    "wish_count": wish_count,
                    "year": year,
                    "regions": regions,
                    "genres": genres
                })
            return ret_array
        except Exception as err:
            logger.error(f"获取RSS失败：{err}")
            return []

    @staticmethod
    def __parse_wish_count(description: str) -> int:
        if not description:
            return 0
        match = re.search(r"想看人数[：:]\s*([0-9,]+)", description)
        if not match:
            return 0
        try:
            return int(match.group(1).replace(",", ""))
        except ValueError:
            return 0

    @staticmethod
    def __parse_year(category: str) -> str:
        if not category:
            return ""
        match = re.search(r"\b(19|20)\d{2}\b", category)
        if not match:
            return ""
        return match.group(0)

    @staticmethod
    def __parse_regions_and_genres(category: str) -> Tuple[List[str], List[str]]:
        if not category:
            return [], []
        parts = [p.strip() for p in category.split("/") if p.strip()]
        region_text = parts[1] if len(parts) > 1 else ""
        genre_text = parts[2] if len(parts) > 2 else ""
        regions = [x.strip() for x in re.split(r"[\s、,，]+", region_text) if x.strip()]
        genres = [x.strip() for x in re.split(r"[\s、,，]+", genre_text) if x.strip()]
        return regions, genres

    @staticmethod
    def __match_any_filter(item_values: List[str], selected_values: List[str]) -> bool:
        if not selected_values:
            return True
        return bool(set(item_values) & set(selected_values))

    @staticmethod
    def __normalize_rss_domain(raw_domain: str) -> str:
        domain = (raw_domain or "").strip()
        if not domain:
            return "https://rsshub.app"
        if "://" not in domain:
            domain = f"https://{domain}"
        parsed = urlparse(domain)
        netloc = parsed.netloc or parsed.path
        scheme = parsed.scheme or "https"
        return f"{scheme}://{netloc}".rstrip("/")

    def __build_rss_url(self, domain: str) -> str:
        return f"{domain.rstrip('/')}{self._rss_path}"

    def __get_tmdb_air_date(self, tmdb_id: Optional[int], season: Optional[int] = None) -> Optional[str]:
        if not tmdb_id:
            return None
        try:
            if season:
                season_info = self.chain.tmdb_info(tmdbid=tmdb_id, mtype=MediaType.TV, season=season)
                if season_info:
                    season_air_date = season_info.get("air_date") or season_info.get("first_air_date")
                    if season_air_date:
                        return season_air_date

            tmdb_info = self.chain.tmdb_info(tmdbid=tmdb_id, mtype=MediaType.TV)
            if not tmdb_info:
                return None

            if season:
                for season_item in tmdb_info.get("seasons", []) or []:
                    if season_item.get("season_number") == season and season_item.get("air_date"):
                        return season_item.get("air_date")

            return tmdb_info.get("first_air_date") or tmdb_info.get("release_date")
        except Exception as err:
            logger.error(f"获取TMDB播出日期失败：{err}")
            return None

    def __is_within_days(self, date_str: str, days: int) -> bool:
        try:
            target_date = datetime.datetime.strptime(date_str, "%Y-%m-%d").date()
            today = datetime.datetime.now(pytz.timezone(settings.TZ)).date()
            diff_days = (target_date - today).days
            return 0 <= diff_days <= days
        except Exception:
            return False

    @staticmethod
    def __build_douban_dispatch_link(link: str) -> str:
        if not link:
            return ""
        match = re.search(r"/subject/(\d+)/?", link)
        if not match:
            return link
        subject_id = match.group(1)
        return f"https://www.douban.com/doubanapp/dispatch?uri=/movie/{subject_id}?from=mdouban&open=app"