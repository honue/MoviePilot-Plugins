import threading
from datetime import datetime
from typing import Dict, Any, Optional, Tuple, List

from app.chain.media import MediaChain
from app.core.event import eventmanager, Event
from app.core.metainfo import MetaInfo
from app.plugins import _PluginBase
from app.plugins.doubanwatching.DoubanHelper import DoubanHelper
from app.schemas import WebhookEventInfo, MediaInfo
from app.schemas.types import EventType, MediaType
import re
from app.log import logger

lock = threading.Lock()


class DouBanWatching(_PluginBase):
    # 插件名称
    plugin_name = "豆瓣书影音档案"
    # 插件描述
    plugin_desc = "将剧集电影的在看、看完状态同步到豆瓣书影音档案。"
    # 插件图标
    plugin_icon = "douban.png"
    # 插件版本
    plugin_version = "1.9.3"
    # 插件作者
    plugin_author = "honue"
    # 作者主页
    author_url = "https://github.com/honue"
    # 插件配置项ID前缀
    plugin_config_prefix = "doubanwatching_"
    # 加载顺序
    plugin_order = 15
    # 可使用的用户级别
    auth_level = 1

    _enable = False
    _private = True
    _first = True
    _user = ""
    _exclude = ""
    _cookie = ""

    _pc_month = None
    _pc_num = None
    _mobile_month = None
    _mobile_num = None

    def init_plugin(self, config: dict = None):
        config = config or {}
        self._enable = config.get("enable", False)
        self._private = config.get("private", True)
        self._first = config.get("first", True)
        self._user = config.get("user", "")
        self._exclude = config.get("exclude", "")
        self._cookie = config.get("cookie", "")

        self._pc_month = int(config.get("pc_month", 3))
        self._pc_num = int(config.get("pc_num", 50))
        self._mobile_month = int(config.get("mobile_month", 2))
        self._mobile_num = int(config.get("mobile_num", 15))

        if self.get_data("processed"):
            from app.db.plugindata_oper import PluginDataOper
            PluginDataOper().del_data(plugin_id="DouBanWatching")
            logger.warn("检测到本插件旧版本数据，删除旧版本数据，避免报错...")

    @eventmanager.register(EventType.WebhookMessage)
    def sync_log(self, event: Event, played: bool = False):
        event_info: WebhookEventInfo = event.event_data
        play_start = {"playback.start", "media.play", "PlaybackStart"}
        path = event_info.item_path
        processed_items: Dict = self.get_data('data') or {}

        if (event_info.event in play_start and event_info.user_name in self._user.split(',')) or played:
            logger.info(" ")
            if played:
                logger.info(f"标记播放完成 {event_info.item_name}")

            if not self.exclude_keyword(path=path, keywords=self._exclude).get("ret", False):
                logger.info(self.exclude_keyword(path=path, keywords=self._exclude).get("message", ""))
                return

            if event_info.item_type == "TV":
                self._process_tv_show(event_info, processed_items, played=played)
            else:
                self._process_movie(event_info, processed_items, played=played)

    @eventmanager.register(EventType.WebhookMessage)
    def sync_played(self, event: Event):
        event_info: WebhookEventInfo = event.event_data
        played = {'item.markplayed', 'media.scrobble'}

        if event_info.event in played and event_info.user_name in self._user.split(','):
            with lock:
                self.sync_log(event=event, played=True)

    def _process_tv_show(self, event_info: WebhookEventInfo, processed_items: Dict, played: bool = False):
        index = event_info.item_name.index(" S")
        title = event_info.item_name[:index]
        season_id, episode_id = map(int, [event_info.season_id, event_info.episode_id])
        tmdb_id = event_info.tmdb_id

        if not played:
            logger.info(f"开始播放 {title} 第{season_id}季 第{episode_id}集")

        if episode_id < 2 and self._first:
            logger.info(f"剧集第1集的活动不同步到豆瓣档案，跳过")
            return

        meta = MetaInfo(title)
        meta.begin_season = season_id
        meta.type = MediaType("电视剧")
        mediainfo = self._recognize_media(meta, tmdb_id)

        if not mediainfo:
            logger.warn(f'标题：{title}，tmdbid：{tmdb_id}，指定tmdbid未识别到媒体信息，尝试仅使用标题识别')
            meta.tmdbid = None
            mediainfo = self._recognize_media(meta, None)
            if not mediainfo:
                logger.error(f'仍然未识别到媒体信息，请检查TMDB网络连接...')
                return

        episodes = mediainfo.seasons.get(season_id, [])

        title = self.format_title(title, season_id)
        status = "collect" if len(episodes) == episode_id else "do"

        if processed_items.get(title) and len(episodes) != episode_id:
            logger.info(f"{title} 已同步到豆瓣在看，不处理")
            return

        self._sync_to_douban(title, status, event_info, processed_items, mediainfo)

    def _process_movie(self, event_info: WebhookEventInfo, processed_items: Dict, played: bool = False):
        title = event_info.item_name

        if not played:
            logger.info(f"开始播放 {title}")

        meta = MetaInfo(title)
        meta.type = MediaType("电影")
        mediainfo = self._recognize_media(meta, event_info.tmdb_id)

        if not mediainfo:
            logger.warn(f'标题：{title}，tmdbid：{event_info.tmdb_id}，指定tmdbid未识别到媒体信息，尝试仅使用标题识别')
            meta.tmdbid = None
            mediainfo = self._recognize_media(meta, None)
            if not mediainfo:
                logger.error(f'仍然未识别到媒体信息，请检查TMDB网络连接...')
                return

        if processed_items.get(title):
            logger.info(f"{title} 已同步到豆瓣在看，不处理")
            return

        self._sync_to_douban(title, "collect", event_info, processed_items, mediainfo)

    def _recognize_media(self, meta: MetaInfo, tmdb_id: Optional[int]) -> Optional[MediaInfo]:
        return MediaChain().recognize_media(meta=meta, mtype=meta.type, tmdbid=tmdb_id, cache=True)

    def _sync_to_douban(self, title: str, status: str, event_info: WebhookEventInfo, processed_items: Dict,
                        mediainfo: MediaInfo):
        logger.info(f"开始尝试获取 {title} 豆瓣id")
        douban_helper = DoubanHelper(user_cookie=self._cookie)
        subject_name, subject_id = douban_helper.get_subject_id(title=title)

        if subject_id:
            logger.info(f"查询：{title} => 匹配豆瓣：{subject_name} https://movie.douban.com/subject/{subject_id}/")
            ret = douban_helper.set_watching_status(subject_id=subject_id, status=status, private=self._private)
            if ret:
                processed_items[title] = {
                    "subject_id": subject_id,
                    "subject_name": subject_name,
                    "timestamp": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                    "poster_path": mediainfo.poster_path,
                    "type": "电视剧" if event_info.item_type == "TV" else "电影"
                }
                self.save_data('data', processed_items)
                logger.info(f"{title} 同步到档案成功")
            else:
                logger.info(f"{title} 同步到档案失败")
        else:
            logger.warn(f"获取 {title} subject_id 失败，本条目不存在于豆瓣，或请检查cookie")

    def get_form(self) -> Tuple[List[dict], Dict[str, Any]]:
        """
        拼装插件配置页面，需要返回两块数据：1、页面配置；2、数据结构
        """
        return [
            {
                'component': 'VForm',
                'content': [
                    {
                        'component': 'VRow',
                        'content': [
                            {
                                'component': 'VCol',
                                'props': {
                                    'cols': 12,
                                    'md': 4
                                },
                                'content': [
                                    {
                                        'component': 'VSwitch',
                                        'props': {
                                            'model': 'enable',
                                            'label': '启用插件',
                                        }
                                    }
                                ]
                            }, {
                                'component': 'VCol',
                                'props': {
                                    'cols': 12,
                                    'md': 4
                                },
                                'content': [
                                    {
                                        'component': 'VSwitch',
                                        'props': {
                                            'model': 'private',
                                            'label': '仅自己可见',
                                        }
                                    }
                                ]
                            }, {
                                'component': 'VCol',
                                'props': {
                                    'cols': 12,
                                    'md': 4
                                },
                                'content': [
                                    {
                                        'component': 'VSwitch',
                                        'props': {
                                            'model': 'first',
                                            'label': '不标记第一集',
                                        }
                                    }
                                ]
                            }
                        ]
                    },
                    {
                        'component': 'VRow',
                        'content': [
                            {
                                'component': 'VCol',
                                'props': {
                                    'cols': 12,
                                    'md': 6
                                },
                                'content': [
                                    {
                                        'component': 'VTextField',
                                        'props': {
                                            'model': 'user',
                                            'label': '媒体库用户名',
                                            'placeholder': '多个关键词以,分隔',
                                        }
                                    }
                                ]
                            },
                            {
                                'component': 'VCol',
                                'props': {
                                    'cols': 12,
                                    'md': 6
                                },
                                'content': [
                                    {
                                        'component': 'VTextField',
                                        'props': {
                                            'model': 'exclude',
                                            'label': '媒体路径排除关键词',
                                            'placeholder': '多个关键词以,分隔',
                                        }
                                    }
                                ]
                            }
                        ]
                    },
                    {
                        'component': 'VRow',
                        'content': [
                            {
                                'component': 'VCol',
                                'props': {
                                    'cols': 12,
                                    'md': 12
                                },
                                'content': [
                                    {
                                        'component': 'VTextField',
                                        'props': {
                                            'model': 'cookie',
                                            'label': '豆瓣cookie',
                                            'placeholder': '留空则每次从cookiecloud获取',
                                        }
                                    }
                                ]
                            }
                        ]
                    },
                    {
                        'component': 'VRow',
                        'content': [
                            {
                                'component': 'VCol',
                                'props': {
                                    'cols': 12,
                                    'md': 3
                                },
                                'content': [
                                    {
                                        'component': 'VTextField',
                                        'props': {
                                            'model': 'pc_month',
                                            'label': '大屏幕显示月份数',
                                            'placeholder': '3',
                                        }
                                    }
                                ]
                            }, {
                                'component': 'VCol',
                                'props': {
                                    'cols': 12,
                                    'md': 3
                                },
                                'content': [
                                    {
                                        'component': 'VTextField',
                                        'props': {
                                            'model': 'pc_num',
                                            'label': '大屏幕每月最多显示数',
                                            'placeholder': '50',
                                        }
                                    }
                                ]
                            }, {
                                'component': 'VCol',
                                'props': {
                                    'cols': 12,
                                    'md': 3
                                },
                                'content': [
                                    {
                                        'component': 'VTextField',
                                        'props': {
                                            'model': 'mobile_month',
                                            'label': '小屏幕屏幕显示月份数',
                                            'placeholder': '2',
                                        }
                                    }
                                ]
                            }, {
                                'component': 'VCol',
                                'props': {
                                    'cols': 12,
                                    'md': 3
                                },
                                'content': [
                                    {
                                        'component': 'VTextField',
                                        'props': {
                                            'model': 'mobile_num',
                                            'label': '小屏幕每月最多显示数',
                                            'placeholder': '15',
                                        }
                                    }
                                ]
                            }
                        ]
                    },
                    {
                        'component': 'VRow',
                        'content': [
                            {
                                'component': 'VCol',
                                'props': {
                                    'cols': 12,
                                },
                                'content': [
                                    {
                                        'component': 'VAlert',
                                        'props': {
                                            'type': 'info',
                                            'variant': 'tonal',
                                            'text': '需要开启媒体服务器的webhook，需要浏览器登录豆瓣，将豆瓣的cookie同步到cookiecloud，也可以手动将cookie填写到此处，不异地登陆有效期很久。'
                                        }
                                    }
                                ]
                            },
                            {
                                'component': 'VCol',
                                'props': {
                                    'cols': 12,
                                },
                                'content': [
                                    {
                                        'component': 'VAlert',
                                        'props': {
                                            'type': 'info',
                                            'variant': 'tonal',
                                            'text': 'v1.8+ 解决了容易提示cookie失效，导致同步失败的问题，现在用cookiecloud应该不用填保活了,建议使用cookiecloud。'
                                        }
                                    }
                                ]
                            }, {
                                'component': 'VCol',
                                'props': {
                                    'cols': 12,
                                },
                                'content': [
                                    {
                                        'component': 'VAlert',
                                        'props': {
                                            'type': 'info',
                                            'variant': 'tonal',
                                            'text': 'v1.9.0 支持标记已观看同步，播放自动同步。'
                                        }
                                    }
                                ]
                            }
                        ]
                    }
                ]
            }
        ], {
            "enable": False,
            "private": True,
            "first": True,
            "user": '',
            "exclude": '',
            "cookie": "",
            "pc_month": 3,
            "pc_num": 50,
            "mobile_month": 2,
            "mobile_num": 15,
        }

    def get_dashboard(self, **kwargs) -> Optional[Tuple[Dict[str, Any], Dict[str, Any], List[dict]]]:
        cols = {
            "cols": 12, "md": 12
        }
        mobile = self.is_mobile(kwargs.get('user_agent'))
        attrs = {"refresh": 600, "border": False}
        elements = [
            {
                'component': 'VRow',
                'props': {
                },
                'content': [
                    {
                        'component': 'VTimeline',
                        'props': {
                            'dot-color': '#AF85FD',
                            'direction': "vertical",
                            'style': 'padding: 1rem 1rem 1rem 1rem',
                            'hide-opposite': True,
                            'side': 'end',
                            'align': 'start'
                        },
                        "content": self.get_line_item(mobile=mobile)
                    }
                ]
            }
        ]

        return cols, attrs, elements

    def get_line_item(self, mobile: bool = False):
        """
        processed_items[f"{title}"] = {
                        "subject_id": subject_id,
                        "subject_name": subject_name,
                        "timestamp": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                    }
        """
        data: Dict = self.get_data('data') or {}
        content = []

        # 按月分组
        last_month = None
        current_month_item = None
        # 限制显示月数
        limit_month = self._mobile_month if mobile else self._pc_month
        limit_month -= 1
        # 限制每月最多显示数
        limit_num = self._mobile_num if mobile else self._pc_num

        # 将字典按照 timestamp 排序
        sorted_data = sorted(data.items(),
                             key=lambda item: datetime.strptime(item[1]['timestamp'], "%Y-%m-%d %H:%M:%S"))

        for key, val in sorted_data[::-1]:
            if not isinstance(val, dict):
                continue
            if not val.get('poster_path', ''):
                meta = MetaInfo(val.get("subject_name"))
                meta.type = MediaType("电视剧" if not val.get("type", '') else val.get("type"))
                # 识别媒体信息
                mediainfo: MediaInfo = MediaChain().recognize_media(meta=meta, mtype=meta.type,
                                                                    cache=True)
                if mediainfo:
                    poster_path = mediainfo.poster_path
                else:
                    continue
            else:
                poster_path = val.get('poster_path')

            time_object = datetime.strptime(val.get('timestamp'), "%Y-%m-%d %H:%M:%S")

            if time_object.month != last_month or last_month is None:
                if limit_month < 1:
                    break
                if last_month:
                    num_movies = len(current_month_item["content"][0]["content"][1]["content"])
                    current_month_item["content"][0]["content"][0][
                        "html"] += f"<span class='text-sm font-normal'>看过{num_movies}部</span>"
                    # 截取limit_num
                    current_month_item["content"][0]["content"][1]["content"] = \
                        current_month_item["content"][0]["content"][1]["content"][:limit_num]
                    content.append(current_month_item)
                    limit_month -= 1

                # 新的一月
                # 初始化 current_month_item 模板
                current_month_item = {
                    "component": "VTimelineItem",
                    "props": {
                        "size": "x-small",
                    },
                    "content": [
                        {
                            "component": "VCol",
                            'props': {
                                'style': 'padding: 0rem 0rem 0rem 0rem'
                            },
                            'content': [
                                {
                                    'component': 'h1',
                                    'props': {
                                        'style': 'padding:0rem 0rem 1rem 0rem;font-weight: bold;',
                                        'class': 'text-base'
                                    },
                                    'html': f"{time_object.month}月 ",
                                },
                                {
                                    'component': 'VRow',
                                    'props': {
                                        'style': 'padding: 0rem 0rem 0rem 0rem'
                                    },
                                    'content': []
                                }
                            ]
                        }
                    ]
                }
                last_month = time_object.month

            current_month_item["content"][0]["content"][1]["content"].append({
                "component": "a",
                'props': {
                    'href': 'https://www.douban.com/doubanapp/dispatch?uri=/movie/' + val.get(
                        'subject_id') + '?from=mdouban&open=app',
                    'target': '_blank',
                    # 图片卡片间的间距 上 右 下 左
                    # 'style': 'padding: 1rem 0.5rem 1rem 0.5rem'
                    'style': 'padding: 0.2rem'
                },
                "content": [
                    {
                        "component": "VCard",
                        "props": {
                            "class": "elevation-4"
                        },
                        "content": [
                            {
                                "component": "VImg",
                                "props": {
                                    "src": poster_path.replace("/original/", "/w200/"),
                                    "style": "width:44px; height: 66px;" if mobile else "width:66px; height: 99px;",
                                    "aspect-ratio": "2/3"
                                }
                            }
                        ]
                    }
                ]
            })

        if current_month_item:
            num_movies = len(current_month_item["content"][0]["content"][1]["content"])
            current_month_item["content"][0]["content"][0][
                "html"] += f"<span class='text-sm font-normal'>看过{num_movies}部</span>"
            current_month_item["content"][0]["content"][1]["content"] = \
                current_month_item["content"][0]["content"][1]["content"][:limit_num]
            content.append(current_month_item)
        return content

    @staticmethod
    def is_mobile(user_agent):
        mobile_keywords = [
            'Mobile', 'Android', 'Silk/', 'Kindle', 'BlackBerry', 'Opera Mini', 'Opera Mobi', 'iPhone', 'iPad'
        ]
        for keyword in mobile_keywords:
            if re.search(keyword, user_agent, re.IGNORECASE):
                return True
        return False

    def get_page(self) -> List[dict]:
        pass

    def get_state(self) -> bool:
        return self._enable

    def stop_service(self):
        pass

    @staticmethod
    def get_command() -> List[Dict[str, Any]]:
        pass

    def get_api(self) -> List[Dict[str, Any]]:
        pass

    @staticmethod
    def exclude_keyword(path: str, keywords: str) -> Dict[str, Any]:
        if not keywords:
            return {"ret": True, "message": "空关键词"}

        keywords_list = re.split(r'[，,]', keywords)
        if any(k in path for k in keywords_list):
            return {"ret": False, "message": f"路径 {path} 包含 {keywords}"}

        return {"ret": True, "message": f"路径 {path} 不包含任何关键词 {keywords}"}

    @staticmethod
    def format_title(title: str, season_id: int) -> str:
        if season_id > 1:
            return f"{title} 第{season_id}季"
        else:
            return title
