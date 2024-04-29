from typing import List, Tuple, Dict, Any

from app.core.event import eventmanager, Event
from app.plugins import _PluginBase
from app.schemas import WebhookEventInfo
from app.schemas.types import EventType
from .SkipHelper import *
from app.log import logger
from app.core.meta import MetaBase


class AdaptiveIntroSkip(_PluginBase):
    # 插件名称
    plugin_name = "自适应IntroSkip"
    # 插件描述
    plugin_desc = "自适应生成IntroSkip片头片尾标记，Emby跳片头、片尾"
    # 插件图标
    plugin_icon = "https://raw.githubusercontent.com/honue/MoviePilot-Plugins/main/icons/chapter.png"
    # 插件版本
    plugin_version = "1.4"
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
    _user: str = ''
    _begin_min: float = 4
    _end_min: float = 6
    _include: str = ''
    _exclude: str = ''
    _spec = ''

    def init_plugin(self, config: dict = None):
        if config:
            self._enable = config.get("enable") or False
            self._user = config.get("user") or ""
            self._begin_min = float(config.get("begin_min") or 4)
            self._end_min = float(config.get("end_min") or 6)
            # 关键词
            self._include = config.get("include") or ''
            self._exclude = config.get("exclude") or ''
            # 特别指定开始 结束时间
            self._spec = config.get("spec") or ''

    @eventmanager.register(EventType.WebhookMessage)
    def hook(self, event: Event):
        event_info: WebhookEventInfo = event.event_data
        if event_info.channel != 'emby' and event_info.media_type != 'Episode':
            logger.info("只支持Emby的剧集 目前其他服务端、其他影片不支持")
            return
        if event_info.event not in ['playback.unpause', 'playback.stop']:
            # 'playback.pause' 'playback.start'
            return

        if self._user and event_info.user_name not in self._user.split(','):
            logger.info(f"{event_info.user_name} 不在用户列表 {self._user} 里")
            return

        include_ret = include_keyword(event_info.item_path, self._include)
        exclude_ret = exclude_keyword(event_info.item_path, self._exclude)
        if not include_ret.get('ret') or not exclude_ret.get('ret'):
            if not include_ret.get('ret'):
                logger.info(f"{event_info.item_path} 不包含任何关键词 {self._include} 不标记片头片尾")
            else:
                logger.info(f"{event_info.item_path} 包含关键词 {exclude_ret.get('msg')} 不标记片头片尾")
            return

        logger.debug(event_info)

        # 特别指定时间
        spec_conf = self._spec.split('\n') if self._spec else []
        for spec in spec_conf:
            word, spec_begin, spec_end = spec.split('#')
            if word in event_info.item_path:
                self._begin_min = float(spec_begin)
                self._end_min = float(spec_end)
                logger.info(
                    f"受关键词 {word} 限定，片头最晚结束于{self._begin_min}分钟，片尾最早开始于末尾{self._end_min}分钟")
                break

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
            # 存储最新片头位置，新集入库使用本数据
            space_idx = event_info.item_name.index(' ')
            series_name = event_info.item_name[:space_idx]
            chapter_info = self.get_data(series_name) or {"item_id": event_info.item_id,
                                                          "intro_end": 0,
                                                          "credits_start": 0}
            # 当前播放时间（s）在[开始,begin_min]之间，且是暂停播放后，恢复播放的动作，标记片头
            if current_sec < (self._begin_min * 60) and event_info.event == 'playback.unpause':
                intro_end = current_sec
                # 批量标记之后的所有剧集，不影响已经看过的标记
                for next_episode_id in next_episode_ids:
                    update_intro(next_episode_id, intro_end)
                chapter_info['intro_end'] = intro_end
                logger.info(
                    f"{event_info.item_name} 后续剧集片头设置在 {int(intro_end / 60)}分{int(intro_end % 60)}秒 结束")
            # 当前播放时间（s）在[end_min,结束]之间，且是退出播放动作，标记片尾
            if current_sec > (total_sec - self._end_min * 60) and event_info.event == 'playback.stop':
                credits_start = current_sec
                for next_episode_id in next_episode_ids:
                    update_credits(next_episode_id, credits_start)
                chapter_info['credits_start'] = credits_start
                logger.info(
                    f"{event_info.item_name} 后续剧集片尾设置在 {int(credits_start / 60)}分{int(credits_start % 60)}秒 开始")

            self.save_data(series_name, chapter_info)

    @eventmanager.register(EventType.TransferComplete)
    def episodes_hook(self, event: Event):
        event_info: MetaBase = event.event_data.get("meta")
        if event_info.total_episode > 5:
            logger.debug(f"本事件只处理追更订阅")
            return
        series_name = event.event_data.get("mediainfo").title
        if not series_name:
            return
        chapter_info: dict = self.get_data(series_name)
        if not chapter_info:
            logger.debug(f"{series_name} 没有设置过片头片尾信息，跳过")
            return

        # 新入库剧集的item_id
        next_episode_ids = get_next_episode_ids(item_id=chapter_info.get("item_id"),
                                                playing_idx=event_info.begin_episode)

        if next_episode_ids:
            # 批量标记新入库的剧集
            intro_end = chapter_info.get("intro_end")
            for next_episode_id in next_episode_ids:
                update_intro(next_episode_id, intro_end)
            logger.info(
                f"{series_name} {event_info.season_episode} 新入库剧集，片头设置在 {int(intro_end / 60)}分{int(intro_end % 60)}秒 结束")

            credits_start = chapter_info.get("credits_start")
            for next_episode_id in next_episode_ids:
                update_credits(next_episode_id, credits_start)
            logger.info(
                f"{series_name} {event_info.season_episode} 新入库剧集，片尾设置在 {int(credits_start / 60)}分{int(intro_end % 60)}秒 开始")

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
                                    'md': 4
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
                                    'md': 4
                                },
                                'content': [
                                    {
                                        'component': 'VTextField',
                                        'props': {
                                            'model': 'user',
                                            'label': '媒体库用户名',
                                            'placeholder': '多个以,分隔 留空默认全部用户',
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
                                            'model': 'exclude',
                                            'label': '媒体路径排除关键词',
                                            'placeholder': '多个关键词以,分隔',
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
                                    'md': 12
                                },
                                'content': [
                                    {
                                        'component': 'VTextarea',
                                        'props': {
                                            'model': 'spec',
                                            'rows': 6,
                                            'label': '特别指定开始结束时间段',
                                            'placeholder': '用关键词特别指定开始结束时间段，格式：关键词#片头最大分钟#片尾最大分钟',
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
                                    }, {
                                        'component': 'VAlert',
                                        'props': {
                                            'type': 'success',
                                            'variant': 'tonal',
                                            'text': '目前回报暂停信息的只有emby官方的客户端(包括小秘)、网页端，所以只推荐这几个客户端的用户使用。'
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
            "exclude": '',
            "spec": '',
            "user": ''
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
