import datetime
from typing import List, Tuple, Dict, Any

from app.core.event import eventmanager, Event
from app.plugins import _PluginBase
from app.schemas import WebhookEventInfo
from app.schemas.types import EventType
from .EmbyHelper import *
from app.log import logger


class AdaptiveIntroSkip(_PluginBase):
    # 插件名称
    plugin_name = "AdaptiveIntroSkip"
    # 插件描述
    plugin_desc = "自适应生成IntroSkip片头标记，Emby跳片头、片尾"
    # 插件图标
    plugin_icon = "https://raw.githubusercontent.com/honue/MoviePilot-Plugins/main/icons/chapter.png"
    # 插件版本
    plugin_version = "0.2"
    # 插件作者
    plugin_author = "honue"
    # 作者主页
    author_url = "https://github.com/honue"
    # 插件配置项ID前缀
    plugin_config_prefix = "AdaptiveIntroSkip_"
    # 加载顺序
    plugin_order = 10
    # 可使用的用户级别
    auth_level = 2

    _enable: bool = False
    _begin_percentage: float = 10
    _end_percentage: float = 15

    def init_plugin(self, config: dict = None):
        if config:
            self._enable = config.get("enable") if config.get("enable") else False
            self._begin_percentage = float(config.get("begin_percentage") if config.get("begin_percentage") else 10)
            self._end_percentage = float(config.get("end_percentage") if config.get("end_percentage") else 15)
            self._end_percentage = 100 - self._end_percentage

    @eventmanager.register(EventType.WebhookMessage)
    def hook(self, event: Event):
        event_info: WebhookEventInfo = event.event_data
        if event_info.channel != 'emby' and event_info.media_type != 'Episode':
            return
        if event_info.event not in ['playback.unpause', 'playback.stop']:
            # 'playback.pause' 'playback.start'
            return
        if self._begin_percentage < event_info.percentage < self._end_percentage:
            logger.info("不在设置的时间段内，不标记片头片尾")
            return
        # 剧集在某集之后的所有剧集的item_id
        next_episode_ids = get_next_episode_ids(item_id=event_info.item_id, playing_idx=event_info.episode_id)
        # 当前正在播放集的信息
        current_percentage = event_info.percentage
        current_video_item_id = get_current_video_item_id(item_id=event_info.item_id, playing_idx=event_info.episode_id)

        # logger.info(event_info)

        if next_episode_ids:
            # 进度在[0,begin]之间，且是暂停播放后，恢复播放动作，标记片头
            if event_info.percentage < self._begin_percentage and event_info.event == 'playback.unpause':
                intro_end = current_percentage / 100 * get_total_time(current_video_item_id)
                # 批量标记之后的所有剧集，不影响已经看过的标记
                for next_episode_id in next_episode_ids:
                    update_intro(next_episode_id, intro_end)
                logger.info(f"{event_info.item_name} 后续剧集片头在{int(intro_end / 60)}分{int(intro_end % 60)}秒结束")
            # 进度在[end,100]之间，且是退出播放动作，标记片尾
            if event_info.percentage > self._end_percentage and event_info.event == 'playback.stop':
                credits_start = current_percentage / 100 * get_total_time(current_video_item_id)
                for next_episode_id in next_episode_ids:
                    update_credits(next_episode_id, credits_start)
                logger.info(f"{event_info.item_name} 后续剧集片尾在{int(credits_start / 60)}分{int(credits_start % 60)}秒开始")

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
                                        'component': 'VTextField',
                                        'props': {
                                            'model': 'begin_percentage',
                                            'label': '最大片头百分比',
                                            'placeholder': '10',
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
                                            'model': 'end_percentage',
                                            'label': '最大片尾百分比',
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
                                            'text': 'Supported by ChapterAPI, 只支持Emby,Emby需要安装ChapterAPI插件，需要在emby通知中添加mp的回调webhook'
                                        }
                                    }
                                ]
                            },                            {
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
                                            'text': '在片头百分比前，暂停一下恢复播放能够将后续剧集的片头全标记在这个点，片尾退出播放能够将后续剧集的片尾开始全标记在这个点，如有问题欢迎交流。'
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
            "begin_percentage": 10,
            "end_percentage": 15
        }

    def get_state(self) -> bool:
        return self._enable

    def get_page(self) -> List[dict]:
        pass

    def stop_service(self):
        pass

    def get_api(self):
        pass

    def get_command(self):
        pass
