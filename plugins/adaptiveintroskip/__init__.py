from typing import List, Tuple, Dict, Any

from app.core.event import eventmanager, Event
from app.plugins import _PluginBase
from app.schemas import WebhookEventInfo
from app.schemas.types import EventType
from .SkipHelper import *
from app.log import logger


class AdaptiveIntroSkip(_PluginBase):
    # 插件名称
    plugin_name = "自适应IntroSkip"
    # 插件描述
    plugin_desc = "自适应生成IntroSkip片头片尾标记，Emby跳片头、片尾"
    # 插件图标
    plugin_icon = "https://raw.githubusercontent.com/honue/MoviePilot-Plugins/main/icons/chapter.png"
    # 插件版本
    plugin_version = "1.0"
    # 插件作者
    plugin_author = "honue"
    # 作者主页
    author_url = "https://github.com/honue"
    # 插件配置项ID前缀
    plugin_config_prefix = "AdaptiveIntroSkip_"
    # 加载顺序
    plugin_order = 10
    # 可使用的用户级别
    auth_level = 1

    _enable: bool = False
    _begin_min: float = 4
    _end_min: float = 6
    _include: str = ''
    _exclude: str = ''

    def init_plugin(self, config: dict = None):
        if config:
            self._enable = config.get("enable") if config.get("enable") else False
            self._begin_min = float(config.get("begin_min") if config.get("begin_min") else 4)
            self._end_min = float(config.get("end_min") if config.get("end_min") else 6)
            # 关键词
            self._include = config.get("include") if config.get("include") else ''
            self._exclude = config.get("exclude") if config.get("exclude") else ''

    @eventmanager.register(EventType.WebhookMessage)
    def hook(self, event: Event):
        event_info: WebhookEventInfo = event.event_data
        if event_info.channel != 'emby' and event_info.media_type != 'Episode':
            logger.info("只支持Emby的Episode 目前其他服务端、其他影片不支持")
            return
        if event_info.event not in ['playback.unpause', 'playback.stop']:
            # 'playback.pause' 'playback.start'
            return

        include_ret = include_keyword(event_info.item_path, self._include)
        exclude_ret = exclude_keyword(event_info.item_path, self._exclude)
        if not include_ret.get('ret') \
                or not exclude_ret.get('ret'):
            logger.info(
                f"受关键词{include_ret.get('msg')} {exclude_ret.get('msg')} 限制，{event_info.item_path} 不标记片头片尾")
            return

        logger.debug(event_info)

        # 当前正在播放集的信息
        current_percentage = event_info.percentage
        current_video_item_id = get_current_video_item_id(item_id=event_info.item_id, playing_idx=event_info.episode_id)
        total_sec = get_total_time(current_video_item_id)
        current_sec = current_percentage / 100 * total_sec

        if (self._begin_min * 60) < current_sec < (total_sec - self._end_min * 60):
            logger.info("不在设置的时间段内，不标记片头片尾")
            return

        # 剧集在某集之后的所有剧集的item_id
        next_episode_ids = get_next_episode_ids(item_id=event_info.item_id, playing_idx=event_info.episode_id)

        if next_episode_ids:
            # 当前播放时间（s）在[开始,begin_min]之间，且是暂停播放后，恢复播放的动作，标记片头
            if current_sec < (self._begin_min * 60) and event_info.event == 'playback.unpause':
                intro_end = current_sec
                # 批量标记之后的所有剧集，不影响已经看过的标记
                for next_episode_id in next_episode_ids:
                    update_intro(next_episode_id, intro_end)
                logger.info(f"{event_info.item_name} 后续剧集片头设置在 {int(intro_end / 60)}分{int(intro_end % 60)}秒 结束")
            # 当前播放时间（s）在[end_min,结束]之间，且是退出播放动作，标记片尾
            if current_sec > (total_sec - self._end_min * 60) and event_info.event == 'playback.stop':
                credits_start = current_sec
                for next_episode_id in next_episode_ids:
                    update_credits(next_episode_id, credits_start)
                logger.info(
                    f"{event_info.item_name} 后续剧集片尾设置在 {int(credits_start / 60)}分{int(credits_start % 60)}秒 开始")

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
                                    'md': 3
                                },
                                'content': [
                                    {
                                        'component': 'VTextField',
                                        'props': {
                                            'model': 'begin_min',
                                            'label': '片头最晚结束于（分钟）',
                                            'placeholder': '4',
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
                                            'model': 'end_min',
                                            'label': '片尾最早开始于最后（分钟）',
                                            'placeholder': '6',
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
                                            'model': 'exclude',
                                            'label': '媒体路径排除关键词',
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
                                            'model': 'include',
                                            'label': '媒体路径包含关键词',
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
                                            'type': 'warning',
                                            'variant': 'tonal',
                                            'text': 'Supported by ChapterAPI, 目前只支持Emby, Emby需要安装ChapterAPI插件，需要在emby通知中添加mp的回调webhook'
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
                                            'type': 'success',
                                            'variant': 'tonal',
                                            'text': '在片头限定时间内，暂停一下恢复播放能够将后续剧集的片头跳转全标记在这个点，在片尾限定时间内，片尾正常退出播放能够将后续剧集的片尾开始全标记在这个点，如有问题欢迎交流。'
                                        }
                                    },{
                                        'component': 'VAlert',
                                        'props': {
                                            'type': 'success',
                                            'variant': 'tonal',
                                            'text': '目前支持使用emby自带片头片尾信息的应该只有emby官方的客户端(包括小秘)，第三方播放器需要跟作者反馈请求支持这个功能。获取章节信息的API是存在的 /emby/Shows/${item_id}/Episodes'
                                        }
                                    }
                                ]
                            },{
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
                                            "text": "具体安装使用说明见README https://github.com/honue/MoviePilot-Plugins"
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
            "begin_min": 4,
            "end_min": 6,
            "include": '',
            "exclude": ''
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
