from typing import Dict, Any

from app.chain.media import MediaChain
from app.core.event import eventmanager, Event
from app.core.metainfo import MetaInfo
from app.plugins import _PluginBase
from app.plugins.doubanwatching.DoubanHelper import *
from app.schemas import WebhookEventInfo, MediaInfo
from app.schemas.types import EventType, MediaType
from app.log import logger
import time


class DouBanWatching(_PluginBase):
    # 插件名称
    plugin_name = "豆瓣书影音档案"
    # 插件描述
    plugin_desc = "将剧集电影的在看、看完状态同步到豆瓣书影音档案"
    # 插件图标
    plugin_icon = "douban.png"
    # 插件版本
    plugin_version = "1.8"
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
    _on_played = False

    _user = ""
    _exclude = ""

    _cookie = ""

    def init_plugin(self, config: dict = None):
        self._enable = config.get("enable") if config.get("enable") is not None else False
        self._private = config.get("private") if config.get("private") is not None else True
        self._first = config.get("first") if config.get("first") is not None else True
        self._on_played = config.get("on_played") if config.get("on_played") is not None else False

        self._user = config.get("user") or ""
        self._exclude = config.get("exclude") or ""
        self._cookie = config.get("cookie") or ""
        self._last_call_time = 0

    @eventmanager.register(EventType.WebhookMessage)
    def sync_log(self, event: Event):
        # 最小调用间隔3秒
        last_call_time = self._last_call_time
        elapsed_time = time.time() - last_call_time
        min_interval = 3 
        if elapsed_time < min_interval:
            sleep_time = min_interval - elapsed_time
            self._last_call_time = last_call_time + min_interval  # = now + sleep_time
            logger.info(f"调用过于频繁，等待 {sleep_time:.2f} 秒后继续执行")
            time.sleep(sleep_time)
        else:
            self._last_call_time = time.time()

        event_info: WebhookEventInfo = event.event_data
        logger.debug(f"收到webhook事件: {event_info.event}")
        if not self._on_played:
            play_flag = "playback.start|media.play|PlaybackStart".split('|')
        else:
            play_flag = "item.markplayed|media.scrobble".split('|')  # for emby and plex
        # 根据媒体文件路径判断是否要同步到影音档案
        path = event_info.item_path
        if not DouBanWatching.exclude_keyword(path=path, keywords=self._exclude).get("ret"):
            logger.info(f"关键词排除媒体文件{path}")
            return

        processed_items: Dict = self.get_data("processed") or {}
        if event_info.event in play_flag and \
                event_info.user_name in self._user.split(','):
            """
                event='playback.pause' channel='emby' item_type='TV' item_name='咒术回战 S1E47 关门' item_id='22646' item_path='/media/cartoon/动漫/咒术回战 (2020)/Season 1/咒术回战 - S01E47 - 第 47 集.mkv' season_id=1 episode_id=47 tmdb_id=None overview='渋谷事変の最終局面に呪術師が集うなかで、脹相は夏油の亡骸に寄生する“黒幕”の正体に気付く。そして、絶体絶命の危機に現れた特級術師・九十九由基。九十九と“黒幕”がそれぞれ語る人類の未来（ネクストステージ...' percentage=2.5705228512861966 ip='127.0.0.1' device_name='Chrome Windows' client='Emby Web' user_name='honue' image_url=None item_favorite=None save_reason=None item_isvirtual=None media_type='Episode'
            """
            tmdb_id = event_info.tmdb_id
            logger.info(f"匹配播放事件 {event_info.event}: {event_info.item_name}, tmdb id = {tmdb_id}")
            # 处理电视剧
            if event_info.item_type == "TV":
                # 标题
                index = event_info.item_name.index(" S")
                title = event_info.item_name[:index]
                # 季 集
                season_id, episode_id = map(int, [event_info.season_id, event_info.episode_id])
                if episode_id < 2 and event_info.item_type == "TV" and self._first:
                    logger.info(f"剧集第1集的活动不同步到豆瓣档案，跳过")
                    return

                meta = MetaInfo(title)
                meta.begin_season = season_id
                meta.type = MediaType("电视剧" if event_info.item_type == "TV" else "电影")
                # 识别媒体信息
                mediainfo: MediaInfo = MediaChain().recognize_media(meta=meta, mtype=meta.type,
                                                                    tmdbid=tmdb_id,
                                                                    cache=True)
                if not mediainfo:
                    logger.warn(f'标题：{title}，tmdbid：{tmdb_id}，指定tmdbid未识别到媒体信息，尝试仅使用标题识别')
                    meta.tmdbid = None
                    mediainfo = MediaChain().recognize_media(meta=meta, mtype=meta.type,
                                                             cache=False)
                # 对于电视剧，获取当前季的总集数
                episodes = mediainfo.seasons.get(season_id) or []

                # 带上第x季
                title = DouBanWatching.format_title(title, season_id)

                if len(episodes) == episode_id:
                    status = "collect"
                    logger.info(f"{title} 第{episode_id}集 为最后一集，标记为看过")
                else:
                    status = "do"

                # 同步过在看，且不是最后一集
                if processed_items.get(title) and len(episodes) != episode_id:
                    logger.info(f"{title} 已同步到豆瓣在看，不处理")
                    return
            # 处理电影
            else:
                title = event_info.item_name
                status = "collect"

                if processed_items.get(title):
                    logger.info(f"{title} 已同步到豆瓣在看，不处理")
                    return

            logger.info(f"开始尝试获取 {title} 豆瓣id")

            douban_helper = DoubanHelper(user_cookie=self._cookie)
            subject_name, subject_id = douban_helper.get_subject_id(title=title)
            logger.info(f"查询：{title} => 匹配豆瓣：{subject_name} https://movie.douban.com/subject/{subject_id}")
            if subject_id:
                ret = douban_helper.set_watching_status(subject_id=subject_id, status=status, private=self._private)
                if ret:
                    processed_items[f"{title}"] = subject_id
                    self.save_data("processed", processed_items)
                    logger.info(f"{title} 同步到档案成功")
                else:
                    logger.info(f"{title} 同步到档案失败")
            else:
                logger.warn(f"获取 {title} subject_id 失败，请检查cookie")

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
                                    'md': 3
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
                                    'md': 3
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
                                    'md': 3
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
                            }, {
                                'component': 'VCol',
                                'props': {
                                    'cols': 12,
                                    'md': 3
                                },
                                'content': [
                                    {
                                        'component': 'VSwitch',
                                        'props': {
                                            'model': 'on_played',
                                            'label': '播放完成后同步',
                                            'hint': '此功能仅支持emby和plex',
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
                                },
                                'content': [
                                    {
                                        'component': 'VAlert',
                                        'props': {
                                            'type': 'info',
                                            'variant': 'tonal',
                                            'text': '需要开启媒体服务器的webhook，event要包括开始播放或标记为已播放（开启播放完成后同步）' + '\n' + 
                                                    'http://127.0.0.1:3001/api/v1/webhook?token=<API_TOKEN>，<API_TOKEN>默认为moviepilot' + '\n' +
                                                    '需要浏览器登录豆瓣，将豆瓣的cookie同步到cookiecloud，也可以手动填写cookie，使用cookiecloud的话需要添加保活'
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
            "on_played": False,
            "user": '',
            "exclude": '',
            "cookie": ""
        }

    @staticmethod
    def exclude_keyword(path: str, keywords: str) -> dict:
        keyword_list: list = keywords.split(',') if keywords else []
        for keyword in keyword_list:
            if keyword in path:
                return {'ret': False, 'msg': keyword}
        return {'ret': True, 'msg': ''}

    @staticmethod
    def format_title(title: str, season: int):
        if season < 2:
            return title
        else:
            season_zh = {0: "零", 1: "一", 2: "二", 3: "三", 4: "四", 5: "五", 6: "六", 7: "七", 8: "八",
                         9: "九"}.get(season)
            return f"{title} 第{season_zh}季"

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


