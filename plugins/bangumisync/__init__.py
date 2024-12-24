from typing import Tuple, List, Dict, Any
from app.core.event import eventmanager, Event
from app.core.config import settings
from app.core.metainfo import MetaInfo
from app.log import logger
from app.plugins import _PluginBase
from app.schemas import WebhookEventInfo, MediaInfo
from app.schemas.types import EventType, MediaType
from app.utils.http import RequestUtils
from cachetools import cached, TTLCache
import requests
import re
import datetime

class BangumiSync(_PluginBase):
    # 插件名称
    plugin_name = "Bangumi打格子"
    # 插件描述
    plugin_desc = "将在看记录同步到bangumi"
    # 插件图标
    plugin_icon = "https://raw.githubusercontent.com/honue/MoviePilot-Plugins/main/icons/bangumi.jpg"
    # 插件版本
    plugin_version = "1.8.1"
    # 插件作者
    plugin_author = "honue,happyTonakai"
    # 作者主页
    author_url = "https://github.com/happyTonakai"
    # 插件配置项ID前缀
    plugin_config_prefix = "bangumisync_"
    # 加载顺序
    plugin_order = 20
    # 可使用的用户级别
    auth_level = 2

    UA = "honue/MoviePilot-Plugins (https://github.com/honue/MoviePilot-Plugins)"

    _enable = True
    _user = None
    _bgm_uid = None
    _token = None
    _tmdb_key = None
    _request = None

    def init_plugin(self, config: dict = None):
        if config:
            self._enable = config.get('enable')
            self._user = config.get('user') if config.get('user') else None
            self._token = config.get('token') if config.get('token') else None
            self._tmdb_key = settings.TMDB_API_KEY
            headers = {"Authorization": f"Bearer {self._token}",
                    "User-Agent": BangumiSync.UA,
                    "content-type": "application/json"}
            self._request = requests.Session()
            self._request.headers.update(headers)
            if settings.PROXY:
                self._request.proxies.update(settings.PROXY)
            self.__update_config()
            logger.debug("Bangumi在看同步插件初始化成功")

    @eventmanager.register(EventType.WebhookMessage)
    def hook(self, event: Event):
        # 插件未启用
        if not self._enable:
            return
        logger.debug(f"收到webhook事件: {event.event_data}")
        event_info: WebhookEventInfo = event.event_data
        # 不是指定用户, 不处理
        if event_info.user_name not in self._user.split(','):
            return
        play_start = {"playback.start", "media.play", "PlaybackStart"}
        # 不是播放停止事件, 或观看进度不足90% 不处理
        if not (event_info.event in play_start or event_info.percentage and event_info.percentage > 90):
            return
        # 根据路径判断是不是番剧
        path = event_info.item_path
        if not BangumiSync.is_anime(path):
            return

        if event_info.item_type in ["TV"]:
            """
                event='playback.pause' channel='emby' item_type='TV' item_name='咒术回战 S1E47 关门' item_id='22646' item_path='/media/cartoon/动漫/咒术回战 (2020)/Season 1/咒术回战 - S01E47 - 第 47 集.mkv' season_id=1 episode_id=47 tmdb_id=None overview='渋谷事変の最終局面に呪術師が集うなかで、脹相は夏油の亡骸に寄生する“黒幕”の正体に気付く。そして、絶体絶命の危機に現れた特級術師・九十九由基。九十九と“黒幕”がそれぞれ語る人類の未来（ネクストステージ...' percentage=2.5705228512861966 ip='127.0.0.1' device_name='Chrome Windows' client='Emby Web' user_name='honue' image_url=None item_favorite=None save_reason=None item_isvirtual=None media_type='Episode'
            """
            # 标题，mp 的 tmdb 搜索 api 有点问题，带空格的搜不出来，直接使用 emby 事件的标题
            tmdb_id = event_info.tmdb_id
            logger.info(f"匹配播放事件 {event_info.item_name} tmdb id = {tmdb_id}...")
            match = re.match(r"^(.+)\sS\d+E\d+\s.+", event_info.item_name)
            if match:
                title = match.group(1)
            else:
                title = event_info.item_name.split(' ')[0]

            # 季 集
            season_id, episode_id = map(int, [event_info.season_id, event_info.episode_id])
            self._prefix = f"{title} 第{season_id}季 第{episode_id}集"
            # 使用 tmdb airdate 来定位季，提高准确率
            subject_name, subject_id = self.get_subjectid_by_title(title, season_id)
            if subject_id is None:
                return
            logger.info(f"{self._prefix}: {title} => {subject_name} https://bgm.tv/subject/{subject_id}")

            try:
                self.sync_watching_status(subject_id, episode_id)
            except Exception as e:
                logger.warning(f"{self._prefix}: 同步在看状态失败: {e}")

    @cached(TTLCache(maxsize=100, ttl=3600))
    def get_subjectid_by_title(self, title: str, season: int) -> Tuple:
        logger.debug(f"{self._prefix}: 尝试使用 bgm api 来获取 subject id...")
        tmdb_id, original_name = self.get_tmdb_id(title)
        if tmdb_id is not None:
            start_date, end_date = self.get_airdate(tmdb_id, season)
            post_json = {
                "keyword": original_name,
                "sort": "match",
                "filter": {"type": [2], "air_date": [f">={start_date}", f"<={end_date}"]},
            }
        else:
            post_json = {
                "keyword": title,
                "sort": "match",
                "filter": {"type": [2]},
            }
        url = f"https://api.bgm.tv/v0/search/subjects"
        resp = self._request.post(url, json=post_json).json()
        if not resp.get("data"):
            logger.warning(f"{self._prefix}: 未找到{title}的bgm条目")
            return None, None
        data = resp.get("data")[0]
        year = data["date"][:4]
        name_cn = f"{data['name_cn']} ({year})"
        subject_id = data["id"]
        return name_cn, subject_id

    @cached(TTLCache(maxsize=100, ttl=3600))
    def get_tmdb_id(self, title: str):
        logger.debug(f"{self._prefix}: 尝试使用 tmdb api 来获取 subject id...")
        url = f"https://api.tmdb.org/3/search/tv?query={title}&api_key={self._tmdb_key}"
        ret = requests.get(url, proxies=settings.PROXY).json()
        if ret.get("total_results"):
            results = ret.get("results")
        else:
            logger.warning(f"{self._prefix}: 未找到 {title} 的 tmdb 条目")
            return None, None
        for result in results:
            if 16 in result.get("genre_ids"):
                return result.get("id"), result.get("original_name")

    @cached(TTLCache(maxsize=100, ttl=3600))
    def get_airdate(self, tmdbid: int, season: int):
        logger.debug(f"{self._prefix}: 尝试使用 tmdb api 来获取 airdate...")
        url = f"https://api.tmdb.org/3/tv/{tmdbid}/season/{season}?language=zh-CN&api_key={self._tmdb_key}"
        resp = requests.get(url, proxies=settings.PROXY).json()
        air_date = resp.get("air_date")
        air_date = datetime.datetime.strptime(air_date, "%Y-%m-%d").date()
        # 时差原因可能有偏差，且tmdb不计算第0话的首播时间
        start_date = air_date - datetime.timedelta(days=15)
        end_date = air_date + datetime.timedelta(days=15)
        return start_date.strftime("%Y-%m-%d"), end_date.strftime("%Y-%m-%d")

    @cached(TTLCache(maxsize=10, ttl=600))
    def sync_watching_status(self, subject_id, episode):
        # 获取uid
        if not self._bgm_uid:
            resp = self._request.get(url="https://api.bgm.tv/v0/me")
            self._bgm_uid = resp.json().get("id")
            logger.debug(f"{self._prefix}: 获取到 bgm_uid {self._bgm_uid}")
        else:
            logger.debug(f"{self._prefix}: 使用 bgm_uid {self._bgm_uid}")
        
        # 更新合集状态
        self.update_collection_status(subject_id)
        
        # 获取episode id
        ep_info = self.get_episodes_info(subject_id)

        found = False
        for info in ep_info:
            if info["sort"] == episode and info["name"]:
                episode_id = info["id"]
                found = True
                break
        
        if not found:
            for info in ep_info:
                if info["ep"] == episode and info["name"]:
                    episode_id = info["id"]
                    found = True
                    break
        
        last_episode = info == ep_info[-1]
        
        if not found:
            logger.warning(f"{self._prefix}: 未找到episode，可能因为TMDB和BGM的episode映射关系不一致")
            return

        # 点格子
        self.update_episode_status(episode_id)

        # 最后一集，更新状态为看过
        if last_episode:
            self.update_collection_status(subject_id, 2)

    @cached(TTLCache(maxsize=100, ttl=3600))
    def update_collection_status(self, subject_id, new_type=3):
        resp = self._request.get(url=f"https://api.bgm.tv/v0/users/{self._bgm_uid}/collections/{subject_id}")
        resp = resp.json()
        type_dict = {0:"未看", 2:"看过", 3:"在看"}
        old_type = 0 if "type" not in resp else resp["type"]
        if old_type == 2:
            # 已经看过，避免刷屏
            logger.info(f"{self._prefix}: 合集状态 {type_dict[old_type]} => {type_dict[new_type]}，无需更新在看状态")
            return
        if old_type == new_type == 3:
            # 已经在看，避免刷屏
            logger.info(f"{self._prefix}: 合集状态 {type_dict[old_type]} => {type_dict[new_type]}，无需更新在看状态")
            return
        # 更新在看状态
        post_data = {
            "type": new_type,
            "comment": "",
            "private": False,
        }
        resp = self._request.post(url=f"https://api.bgm.tv/v0/users/-/collections/{subject_id}", json=post_data)
        if resp.status_code in [202, 204]:
            logger.info(f"{self._prefix}: 合集状态 {type_dict[old_type]} => {type_dict[new_type]}，在看状态更新成功")
        else:
            logger.warning(resp.text)
            logger.warning(f"{self._prefix}: 合集状态 {type_dict[old_type]} => {type_dict[new_type]}，在看状态更新失败")

    @cached(TTLCache(maxsize=100, ttl=3600))
    def get_episodes_info(self, subject_id):
        resp = self._request.get("https://api.bgm.tv/v0/episodes", params={"subject_id": subject_id})
        if resp.status_code == 200:
            logger.debug(f"{self._prefix}: 获取 episode info 成功")
        else:
            logger.warning(f"{self._prefix}: 获取 episode info 失败, code={resp.status_code}")
        ep_info = resp.json()["data"]
        return ep_info

    @cached(TTLCache(maxsize=100, ttl=3600))
    def update_episode_status(self, episode_id):
        url = f"https://api.bgm.tv/v0/users/-/collections/-/episodes/{episode_id}"
        resp = self._request.get(url)
        if resp.status_code == 200:
            resp = resp.json()
            if resp["type"] == 2:
                logger.info(f"{self._prefix}: 单集已经点过格子了")
                return
        else:
            logger.warning(f"{self._prefix}: 获取单集信息失败, code={resp.status_code}")
            return
        resp = self._request.put(url, json={"type": 2})
        if resp.status_code == 204:
            logger.info(f"{self._prefix}: 单集点格子成功")
        else:
            logger.warning(f"{self._prefix}: 单集点格子失败, code={resp.status_code}")

    @staticmethod
    def is_anime(path):
        """
        通过路径关键词来确定是不是anime媒体库
        """
        path_keyword = "日番,cartoon,动漫,动画,ani,anime,新番,番剧,特摄,bangumi,ova,映画,国漫,日漫"
        path = path.lower()  # Convert path to lowercase to make the check case-insensitive
        for keyword in path_keyword.split(','):
            if path.count(keyword):
                return True
        logger.debug(f"{path} 不是动漫媒体库")
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
    bangumi.sync_watching_status(subject_id, 1)
