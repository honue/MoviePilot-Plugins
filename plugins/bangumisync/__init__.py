from typing import Tuple, List, Dict, Any
from app.core.event import eventmanager, Event
from app.core.config import settings
from app.log import logger
from app.plugins import _PluginBase
from app.schemas import WebhookEventInfo
from app.schemas.types import EventType
from app.utils.http import RequestUtils
from cachetools import cached, TTLCache


class BangumiSync(_PluginBase):
    # 插件名称
    plugin_name = "Bangumi在看同步"
    # 插件描述
    plugin_desc = "将在看记录同步到bangumi"
    # 插件图标
    plugin_icon = "https://raw.githubusercontent.com/honue/MoviePilot-Plugins/main/icons/bangumi.jpg"
    # 插件版本
    plugin_version = "1.2"
    # 插件作者
    plugin_author = "honue"
    # 作者主页
    author_url = "https://github.com/honue"
    # 插件配置项ID前缀
    plugin_config_prefix = "bangumisync_"
    # 加载顺序
    plugin_order = 30
    # 可使用的用户级别
    auth_level = 2

    UA = "honue/MoviePilot-Plugins (https://github.com/honue/MoviePilot-Plugins)"

    _enable = True
    _user = None
    _token = None

    def init_plugin(self, config: dict = None):
        if config:
            self._enable = config.get('enable')
            self._user = config.get('user') if config.get('user') else None
            self._token = config.get('token') if config.get('token') else None
            self.__update_config()

    @eventmanager.register(EventType.WebhookMessage)
    def hook(self, event: Event):
        event_info: WebhookEventInfo = event.event_data
        # 只判断开始播放的TV剧集是不是anime 调试加入暂停
        play_start = "playback.start|media.play|PlaybackStart".split('|')
        # 根据路径判断是不是番剧
        path = event_info.item_path
        if not self._enable:
            return
        if not BangumiSync.is_anime(path):
            return

        if event_info.item_type in ["TV"] and \
                event_info.event in play_start and \
                event_info.user_name in self._user.split(','):
            """
                event='playback.pause' channel='emby' item_type='TV' item_name='咒术回战 S1E47 关门' item_id='22646' item_path='/media/cartoon/动漫/咒术回战 (2020)/Season 1/咒术回战 - S01E47 - 第 47 集.mkv' season_id=1 episode_id=47 tmdb_id=None overview='渋谷事変の最終局面に呪術師が集うなかで、脹相は夏油の亡骸に寄生する“黒幕”の正体に気付く。そして、絶体絶命の危機に現れた特級術師・九十九由基。九十九と“黒幕”がそれぞれ語る人類の未来（ネクストステージ...' percentage=2.5705228512861966 ip='127.0.0.1' device_name='Chrome Windows' client='Emby Web' user_name='honue' image_url=None item_favorite=None save_reason=None item_isvirtual=None media_type='Episode'
            """
            # 标题
            title = event_info.item_name.split(' ')[0]
            # 季 集
            season_id, episode_id = map(int, [event_info.season_id, event_info.episode_id])
            logger.info(f"开始播放 {title} 第{season_id}季 第{episode_id}集")
            # 好像api限制只能修改收藏，即，在看，看完等，共5种状态 同步要通过这个 subject/set/watched 但这个不能使用access_token
            # 先只同步在看状态吧...
            # if episode_id > 1:
            #     return
            subject_id = self.get_subjectid_by_title(title, season_id)
            self.sync_watching_status(subject_id)

    @staticmethod
    @cached(TTLCache(maxsize=100, ttl=3600))
    def get_subjectid_by_title(title: str, season: int):
        title = BangumiSync.format_title(title, season)
        logger.info(f"获取 {title} subject_id")
        post_json = {
            "keyword": title,
            "sort": "rank",
            "filter": {
                "type": [
                    2
                ]
            }
        }
        url = f"https://api.bgm.tv/v0/search/subjects"
        ret = RequestUtils(proxies=settings.PROXY,
                           ua=BangumiSync.UA,
                           accept_type="application/json"
                           ).post(url=url, json=post_json).json()
        data: dict = ret.get('data')[0]
        logger.info(f"{title} => {data.get('name')} subject_id:{data.get('id')}")
        return data.get('id')

    @cached(TTLCache(maxsize=10, ttl=600))
    def sync_watching_status(self, subject_id):
        post_data = {
            "type": 3,
            "rate": 10,
            "comment": "",
            "private": False,
            "tags": [
                ""
            ]
        }
        headers = {"Authorization": f"Bearer {self._token}",
                   "User-Agent": BangumiSync.UA,
                   "content-type": "application/json"}
        resp = RequestUtils(proxies=settings.PROXY,
                            headers=headers
                            ) \
            .post(url=f"https://api.bgm.tv/v0/users/-/collections/{subject_id}",
                  json=post_data)
        if resp.status_code in [202, 204]:
            logger.info("在看状态更新成功")
        else:
            logger.warning(resp.text)
            logger.warning(f"在看状态更新失败")

    @staticmethod
    def is_anime(path):
        """
        通过路径关键词来确定是不是anime媒体库
        """
        path_keyword = "cartoon,动漫,动画,ani,anime,新番,番剧,特摄"
        for keyword in path_keyword.split(','):
            if path.count(keyword):
                return True
        return False

    @staticmethod
    def format_title(title: str, season: int):
        if season < 2:
            return title
        else:
            season_zh = {0: "零", 1: "一", 2: "二", 3: "三", 4: "四", 5: "五", 6: "六", 7: "七", 8: "八",
                         9: "九"}.get(season)
            return f"{title} 第{season_zh}季"

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
                                            'label': '媒体服务器用户名',
                                            'placeholder': '你的Emby/Plex用户名'
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
                                            'model': 'token',
                                            'label': 'Bangumi Access-token',
                                            'placeholder': 'dY123qxXcdaf234Gj6u3va123Ohh'
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
                                            'text': 'access-token获取：https://next.bgm.tv/demo/access-token' + '\n' +
                                                    'emby添加你mp的webhook（event要包括播放）： http://127.0.0.1:3001/api/v1/webhook?token=moviepilot' + '\n' +
                                                    '感谢@HankunYu的想法'
                                            ,
                                            'style': 'white-space: pre-line;'
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
            "user": "",
            "token": ""
        }

    def get_page(self) -> List[dict]:
        pass

    def __update_config(self):
        """
        列新配置
        """
        self.update_config({
            "enable": self._enable,
            "user": self._user,
            "token": self._token
        })

    def get_state(self) -> bool:
        return self._enable

    def stop_service(self):
        pass


if __name__ == "__main__":
    subject_id = BangumiSync.get_subjectid_by_title("葬送的芙莉莲", 1)
    bangumi = BangumiSync()
    bangumi.sync_watching_status(subject_id)
