from typing import Dict, Any

from app.core.config import settings
from app.core.event import eventmanager, Event
from app.core.metainfo import MetaInfo
from app.helper.cookiecloud import CookieCloudHelper
from app.log import logger
from app.plugins import _PluginBase
from app.schemas import WebhookEventInfo, MediaInfo
from app.schemas.types import EventType, MediaType
from app.plugins.doubanwatching.DoubanHelper import *


class DouBanWatching(_PluginBase):
    # 插件名称
    plugin_name = "豆瓣书影音档案"
    # 插件描述
    plugin_desc = "将在看的剧集自动同步到豆瓣书影音档案"
    # 插件图标
    plugin_icon = "douban.png"
    # 插件版本
    plugin_version = "1.1"
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

    _user = ""
    _exclude = ""

    def init_plugin(self, config: dict = None):
        self._enable = config.get("enable") if config.get("enable") is not None else False
        self._private = config.get("private") if config.get("private") is not None else True
        self._user = config.get("user") or ""
        self._exclude = config.get("exclude") or ""

    @eventmanager.register(EventType.WebhookMessage)
    def hook(self, event: Event):
        event_info: WebhookEventInfo = event.event_data
        # 只判断开始播放的TV剧集是不是anime 调试加入暂停
        play_start = "playback.start|media.play|PlaybackStart".split('|')
        # 根据媒体文件路径判断是否要同步到影音档案
        path = event_info.item_path
        if not DouBanWatching.exclude_keyword(path=path, keywords=self._exclude).get("ret"):
            logger.info(f"关键词排除媒体文件{path}")
            return

        processed_items: Dict = self.get_data("processed") or {}
        if event_info.event in play_start and \
                event_info.user_name in self._user.split(','):
            """
                event='playback.pause' channel='emby' item_type='TV' item_name='咒术回战 S1E47 关门' item_id='22646' item_path='/media/cartoon/动漫/咒术回战 (2020)/Season 1/咒术回战 - S01E47 - 第 47 集.mkv' season_id=1 episode_id=47 tmdb_id=None overview='渋谷事変の最終局面に呪術師が集うなかで、脹相は夏油の亡骸に寄生する“黒幕”の正体に気付く。そして、絶体絶命の危機に現れた特級術師・九十九由基。九十九と“黒幕”がそれぞれ語る人類の未来（ネクストステージ...' percentage=2.5705228512861966 ip='127.0.0.1' device_name='Chrome Windows' client='Emby Web' user_name='honue' image_url=None item_favorite=None save_reason=None item_isvirtual=None media_type='Episode'
            """
            # 标题
            title = event_info.item_name.split(' ')[0]
            tmdb_id = event_info.tmdb_id
            # 季 集
            season_id, episode_id = map(int, [event_info.season_id, event_info.episode_id])
            logger.info(f"开始播放 {title} 第{season_id}季 第{episode_id}集")
            if episode_id < 2 and event_info.item_type == "TV":
                logger.info(f"剧集第一集的活动不同步到豆瓣档案，跳过")
                return
            # 带上第x季
            title = DouBanWatching.format_title(title, season_id)
            if processed_items.get(title):
                logger.info(f"{title} 已同步到在看，不处理")
                return

            logger.info(f"开始尝试获取 {title} 豆瓣subject_id")

            doubanHelper = DoubanHelper()
            subject_name, subject_id = doubanHelper.get_subject_id(title=title)
            logger.info(f"查询：{title} => 匹配豆瓣：{subject_name} https://movie.douban.com/subject/{subject_id}")
            if subject_id:
                ret = doubanHelper.set_watching_status(subject_id=subject_id, private=self._private)
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
                                    'md': 6
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
                                    'md': 6
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
                                },
                                'content': [
                                    {
                                        'component': 'VAlert',
                                        'props': {
                                            'type': 'info',
                                            'variant': 'tonal',
                                            'text': '需要开启媒体服务器的webhook，另需要浏览器登录豆瓣，将豆瓣的cookie同步到cookiecloud，剧集第一集的活动不会同步到档案'
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
            "user": '',
            "exclude": ''
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
