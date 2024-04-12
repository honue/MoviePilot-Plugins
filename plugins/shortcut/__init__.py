from typing import List, Tuple, Dict, Any

from app.chain.download import DownloadChain
from app.chain.media import MediaChain
from app.chain.subscribe import SubscribeChain
from app.core.metainfo import MetaInfo
from app.plugins import _PluginBase
from app.core.config import settings
from app.log import logger
from app.schemas import MediaInfo, MediaType


class ShortCut(_PluginBase):
    # 插件名称
    plugin_name = "快捷指令"
    # 插件描述
    plugin_desc = "IOS快捷指令，快速选片添加订阅"
    # 插件图标
    plugin_icon = "https://raw.githubusercontent.com/honue/MoviePilot-Plugins/main/icons/shortcut.jpg"
    # 插件版本
    plugin_version = "1.2"
    # 插件作者
    plugin_author = "honue"
    # 作者主页
    author_url = "https://github.com/honue"
    # 插件配置项ID前缀
    plugin_config_prefix = "ShortCut_"
    # 加载顺序
    plugin_order = 15
    # 可使用的用户级别
    auth_level = 1

    _enable: bool = False
    _plugin_key: str = ""
    _num: int = 3

    downloadchain: DownloadChain = None
    subscribechain: SubscribeChain = None
    mediachain: MediaChain = None

    def init_plugin(self, config: dict = None):
        self._enable = config.get("enable") if config.get("enable") else False
        self._plugin_key = config.get("plugin_key") if config.get("plugin_key") else settings.API_TOKEN
        self._num = int(config.get("num")) if config.get("num") else 3

        self.downloadchain = DownloadChain()
        self.subscribechain = SubscribeChain()
        self.mediachain = MediaChain()

    def search(self, title: str, plugin_key: str) -> Any:
        """
        模糊搜索媒体信息列表
        """
        if self._plugin_key != plugin_key:
            logger.error(f"plugin_key错误：{plugin_key}")
            return []
        _, medias = self.mediachain.search(title=title)
        if medias:
            ret = []
            for media in medias[:self._num]:
                # 降低图片质量
                media.poster_path.replace("/original/", "/w200/")
                ret.append(media)
            return ret
        logger.info(f"{title} 没有找到结果")
        return []

    def subscribe(self, title: str, tmdbid: str, type: str = "电视剧", plugin_key: str = "") -> Any:
        """
        添加订阅订阅
        """
        if self._plugin_key != plugin_key:
            msg = f"plugin_key错误：{plugin_key}"
            logger.error(msg)
            return msg
        # 元数据
        meta = MetaInfo(title=title)
        meta.tmdbid = tmdbid
        logger.info(type)
        mediainfo: MediaInfo = self.chain.recognize_media(meta=meta, tmdbid=tmdbid,
                                                          mtype=MediaType.TV if type == "电视剧" else MediaType.MOVIE)
        if not mediainfo:
            msg = f'未识别到媒体信息，标题：{title}，tmdb_id：{tmdbid}'
            logger.warn(msg)
            return msg

        # 查询缺失的媒体信息
        exist_flag, _ = self.downloadchain.get_no_exists_info(meta=meta, mediainfo=mediainfo)
        if exist_flag:
            msg = f'{mediainfo.title_year} 媒体库中已存在'
            logger.info(msg)
            return msg
        # 判断用户是否已经添加订阅
        if self.subscribechain.exists(mediainfo=mediainfo, meta=meta):
            msg = f'{mediainfo.title_year} 订阅已存在'
            logger.info(msg)
            return msg
        # 添加订阅
        sid, msg = self.subscribechain.add(title=mediainfo.title,
                                           year=mediainfo.year,
                                           mtype=mediainfo.type,
                                           tmdbid=mediainfo.tmdb_id,
                                           season=meta.begin_season,
                                           exist_ok=True,
                                           username="快捷指令")
        if not msg:
            return f"{mediainfo.title_year} 订阅成功"
        else:
            return msg

    def get_api(self) -> List[Dict[str, Any]]:
        return [
            {
                "path": "/search",
                "endpoint": self.search,
                "methods": ["GET"],
                "summary": "模糊搜索",
                "description": "模糊搜索",
            }, {
                "path": "/subscribe",
                "endpoint": self.subscribe,
                "methods": ["GET", "POST"],
                "summary": "添加订阅",
                "description": "添加订阅",
            }
        ]

    @staticmethod
    def get_command() -> List[Dict[str, Any]]:
        pass

    def get_form(self) -> Tuple[List[dict], Dict[str, Any]]:
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
                                    'md': 2
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
                                        'component': 'VTextField',
                                        'props': {
                                            'model': 'num',
                                            'label': '快捷指令列表展示数量',
                                            'placeholder': '数量过多会影响快捷指令速度',
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
                                        'component': 'VTextField',
                                        'props': {
                                            'model': 'plugin_key',
                                            'label': '插件plugin_key',
                                            'placeholder': '留空默认是mp的api_key',
                                        }
                                    }
                                ]
                            }
                        ]
                    }, {
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
                                            'text': '安装完插件需要重启mp，2024/4/12 快捷指令：https://www.icloud.com/shortcuts/fdfff20c25284d19bb8976f9f2f8db65'
                                        }
                                    }
                                ]
                            }
                        ]
                    }
                ]
            }
        ], {
            "enable": self._enable,
            "num": self._num,
            "plugin_key": self._plugin_key,
        }

    def get_page(self) -> List[dict]:
        pass

    def get_state(self) -> bool:
        return self._enable

    def stop_service(self):
        pass
