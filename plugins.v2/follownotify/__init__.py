import datetime
from typing import List, Tuple, Dict, Any

from app.core.event import eventmanager, Event
from app.core.context import MediaInfo
from app.core.meta import MetaBase
from app.core.metainfo import MetaInfo
from app.log import logger
from app.plugins import _PluginBase
from app.schemas import WebhookEventInfo, TransferInfo, Notification
from app.schemas.types import EventType, MediaType, NotificationType


class FollowNotify(_PluginBase):
    # 插件名称
    plugin_name = "收藏更新通知"
    # 插件描述
    plugin_desc = "收藏剧集，当有新集更新时发送通知。适合针对性的获取更新通知。"
    # 插件图标
    plugin_icon = "like.jpg"
    # 插件版本
    plugin_version = "1.1"
    # 插件作者
    plugin_author = "honue"
    # 作者主页
    author_url = "https://github.com/honue"
    # 插件配置项ID前缀
    plugin_config_prefix = "follownotify_"
    # 加载顺序
    plugin_order = 15
    # 可使用的用户级别
    auth_level = 1

    _enable = False

    def init_plugin(self, config: dict = None):
        self._enable = config.get("enable") or False

    @eventmanager.register(EventType.WebhookMessage)
    def record_favor(self, event: Event):
        """
        记录favor剧集
        event='item.rate' channel='emby' item_type='TV' item_name='幽游白书' item_id=None item_path='/media/series/日韩剧/幽游白书 (2023)' season_id=None episode_id=None tmdb_id='121659' overview='该剧改编自富坚义博的同名漫画。讲述叛逆少年浦饭幽助（北村匠海 饰）为了救小孩不幸车祸身亡，没想到因此获得重生机会并成为灵界侦探，展开一段不可思议的人生。' percentage=None ip=None device_name=None client=None user_name='honue' image_url=None item_favorite=None save_reason=None item_isvirtual=None media_type='Series'
        """
        event_info: WebhookEventInfo = event.event_data
        # 只处理剧集喜爱
        if event_info.event != "item.rate" or event_info.item_type != "TV":
            return
        if event_info.channel != "emby":
            logger.info("目前只支持Emby服务端")
            return
        title = event_info.item_name
        tmdb_id = event_info.tmdb_id
        if title.count(" S"):
            logger.info("只处理喜爱整季，单集喜爱不处理")
            return
        try:
            meta = MetaInfo(title)
            mediainfo: MediaInfo = self.chain.recognize_media(meta=meta, tmdbid=tmdb_id, mtype=MediaType.TV)
            # 存储历史记录
            favor: Dict = self.get_data('favor') or {}
            if favor.get(tmdb_id):
                favor.pop(tmdb_id)
                logger.info(f"{mediainfo.title_year} 取消更新通知")
                self.chain.post_message(Notification(
                    mtype=NotificationType.Plugin,
                    title=f"{mediainfo.title_year} 取消更新通知", text=None, image=mediainfo.get_message_image()))
            else:
                favor[tmdb_id] = {
                    "title": title,
                    "type": mediainfo.type.value,
                    "year": mediainfo.year,
                    "poster": mediainfo.get_poster_image(),
                    "overview": mediainfo.overview,
                    "tmdbid": mediainfo.tmdb_id,
                    "time": datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                }
                logger.info(f"{mediainfo.title_year} 加入更新通知")
                self.chain.post_message(Notification(
                    mtype=NotificationType.Plugin,
                    title=f"{mediainfo.title_year} 加入更新通知", text=None, image=mediainfo.get_message_image()))
            self.save_data('favor', favor)
        except Exception as e:
            logger.error(str(e))

    @eventmanager.register(EventType.TransferComplete)
    def transfer_hook(self, event: Event):
        meta: MetaBase = event.event_data.get("meta")
        mediainfo: MediaInfo = event.event_data.get("mediainfo")
        tmdb_id = str(mediainfo.tmdb_id)
        favor: Dict = self.get_data('favor') or {}

        msg_title = f"{mediainfo.title_year} {meta.episodes} 已入库"

        if favor.get(tmdb_id) and mediainfo.type == MediaType.TV:
            # 发送消息
            self.chain.post_message(Notification(
                mtype=NotificationType.Plugin,
                title=msg_title, text=None, image=mediainfo.get_message_image()))
            logger.info(f"发送通知 {msg_title}")
        else:
            # 没有添加到喜爱
            return

    @staticmethod
    def get_command() -> List[Dict[str, Any]]:
        pass

    def get_api(self) -> List[Dict[str, Any]]:
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
                            }
                        ]
                    }
                ]
            }
        ], {
            "enable": False
        }

    def get_page(self) -> List[dict]:
        """
               拼装插件详情页面，需要返回页面配置，同时附带数据
               """
        # 查询历史记录
        favor: Dict = self.get_data('favor')
        if not favor:
            return [
                {
                    'component': 'div',
                    'text': '暂无数据',
                    'props': {
                        'class': 'text-center',
                    }
                }
            ]
        # 数据按时间降序排序
        favor_list = sorted(favor.values(), key=lambda x: x.get('time'), reverse=True)
        # 拼装页面
        contents = []
        for favor_item in favor_list:
            title = favor_item.get("title")
            poster = favor_item.get("poster")
            mtype = favor_item.get("type")
            time_str = favor_item.get("time")
            contents.append(
                {
                    'component': 'VCard',
                    'content': [
                        {
                            'component': 'div',
                            'props': {
                                'class': 'd-flex justify-space-start flex-nowrap flex-row',
                            },
                            'content': [
                                {
                                    'component': 'div',
                                    'content': [
                                        {
                                            'component': 'VImg',
                                            'props': {
                                                'src': poster,
                                                'height': 120,
                                                'width': 80,
                                                'aspect-ratio': '2/3',
                                                'class': 'object-cover shadow ring-gray-500',
                                                'cover': True
                                            }
                                        }
                                    ]
                                },
                                {
                                    'component': 'div',
                                    'content': [
                                        {
                                            'component': 'VCardText',
                                            'props': {
                                                'class': 'pa-0 px-2'
                                            },
                                            'text': f'剧名：{title}'
                                        },
                                        {
                                            'component': 'VCardText',
                                            'props': {
                                                'class': 'pa-0 px-2'
                                            },
                                            'text': f'类型：{mtype}'
                                        },
                                        {
                                            'component': 'VCardText',
                                            'props': {
                                                'class': 'pa-0 px-2'
                                            },
                                            'text': f'添加时间：{time_str}'
                                        }
                                    ]
                                }
                            ]
                        }
                    ]
                }
            )

        return [
            {
                'component': 'div',
                'props': {
                    'class': 'grid gap-3 grid-info-card',
                },
                'content': contents
            }
        ]

    def get_state(self) -> bool:
        return self._enable

    def stop_service(self):
        pass
