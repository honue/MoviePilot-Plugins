import contextvars
import re
from contextlib import contextmanager
from datetime import datetime, timedelta
from pathlib import Path
from typing import Callable, Optional, Tuple, List, Dict, Any

from requests import Response, Session

from app.chain.mediaserver import MediaServerChain
from app.core.cache import cached
from app.core.config import settings
from app.core.context import MediaInfo
from app.core.event import eventmanager, Event
from app.core.meta.metabase import MetaBase
from app.core.metainfo import MetaInfoPath
from app.db.models.mediaserver import MediaServerItem
from app.log import logger
from app.plugins import _PluginBase
from app.schemas import WebhookEventInfo
from app.schemas.exception import ImmediateException
from app.schemas.types import EventType, MediaType, NotificationType
from app.utils.common import retry
from app.utils.http import RequestUtils


# 为每个上下文维护独立的状态
_temp_attrs_state = contextvars.ContextVar('temp_attrs_state', default={})


class BangumiAPIClient:
    """
    https://bangumi.github.io/api/
    """

    _urls = {
        "myself": "v0/me",
        "discover": "v0/subjects",
        "search": "v0/search/subjects",
        "detail": "v0/subjects/%s",
        "subjects": "v0/subjects/%s/subjects",
        "episodes": "v0/episodes?subject_id=%s",
        "episodecollection": "v0/users/-/collections/-/episodes/%s",
        "collection": "v0/users/%s/collections/%s",
    }
    _base_url = "https://api.bgm.tv/"

    def __init__(self, token: str, ua: str = None):
        if not token:
            logger.critical("Bangumi API Token未配置！")
            return
        _req = RequestUtils(
            headers={
                "Authorization": f"Bearer {token}",
                "User-Agent": ua or settings.USER_AGENT,
                "content-type": "application/json",
            },
            proxies=settings.PROXY,
            session=Session(),
        )
        self.req_method: dict[str, Callable[..., Optional[Response]]] = {
            "get": _req.get_res,
            "post": _req.post_res,
            "put": _req.put_res,
            "request": _req.request,
        }

    @property
    def uid(self):
        if not getattr(self, '_uid', None):
            setattr(self, '_uid', self.username())
        return getattr(self, '_uid')

    @cached(maxsize=1024, ttl=60 * 60 * 6)
    def __cached_invoke(self, method, *args, **kwargs):
        return self.req_method[method](*args, **kwargs)

    @retry(ExceptionToCheck=ConnectionError, logger=logger)
    def __invoke(self, method, url, key: str=None, call_cached=True, data=None, json: dict=None, **kwargs):
        req_url = self._base_url + url
        params = {}
        if kwargs:
            params.update(kwargs)
        if call_cached:
            resp = self.__cached_invoke(method, url=req_url, params=params, data=data, json=json)
        else:
            resp = self.req_method[method](url=req_url, params=params, data=data, json=json)
        # 检查响应
        if resp is None:
            raise ConnectionError(f"{method}: {req_url}, 返回值为空")
        # 处理202, 204状态码（无内容）
        elif resp.status_code in (202, 204):
            return True

        result = resp.json()
        err_msg = f"{resp.status_code}: {result.get('title')}, {result.get('description')}"
        if resp.status_code in (400, 401):
            logger.warning(err_msg)
            raise ImmediateException(err_msg)
        elif resp.status_code == 404:
            logger.warning(err_msg)
        else:
            # 如果指定了key，则提取对应字段
            return result.get(key) if key else result

    def username(self):
        """
        获取用户信息
        """
        return self.__invoke("get", self._urls["myself"], key="username")

    def search(self, title: str, air_date: str) -> List[dict]:
        """
        搜索媒体信息
        """
        if not title:
            return []
        post_json = {
                "keyword": title,
                "sort": "match",
                "filter": {
                    "type": [2]
                },
            }
        if air_date:
            _air_date = datetime.strptime(air_date, "%Y-%m-%d").date()
            start_date = _air_date - timedelta(days=10)
            end_date = _air_date + timedelta(days=10)
            post_json["filter"]["air_date"] = [f">={start_date}", f"<={end_date}"]

        return self.__invoke("post", self._urls["search"], json=post_json, key="data") or []

    def detail(self, bid: int) -> Optional[dict]:
        """
        获取番剧详情
        """
        return self.__invoke("get", self._urls["detail"] % bid)

    def subjects(self, bid: int):
        """
        获取关联条目信息
        """
        return self.__invoke("get", self._urls["subjects"] % bid)

    def episodes(self, bid: int, type: int = 0, limit: int = 1, offset: int = 0) -> List[dict]:
        """
        获取所有集信息
        """
        kwargs = {k: v for k, v in locals().items() if k not in ("self", "bid")}
        return self.__invoke("get", self._urls["episodes"] % bid, key="data", **kwargs) or []

    def get_collection_status(self, bid: int) -> Optional[int]:
        """
        获取收藏信息
        0: 未看, 1: 想看, 2: 看过, 3: 在看, 4: 搁置, 5: 抛弃
        """
        return self.__invoke("get", self._urls["collection"] % (self.uid, bid), key="type", call_cached=False)

    def post_collection_status(self, bid: int, status: int = 3) -> Optional[bool]:
        """
        更新收藏信息
        0: 未看, 1: 想看, 2: 看过, 3: 在看, 4: 搁置, 5: 抛弃
        """
        post_data = {
            "type": status,
            "comment": "",
            "private": False,
        }

        return self.__invoke("post", self._urls["collection"] % ("-", bid), call_cached=False, json=post_data)

    def get_episode_status(self, eid: int) -> Optional[int]:
        """
        获取集状态
        0: 未收藏, 1: 想看, 2: 看过, 3: 抛弃
        """
        return self.__invoke("get", self._urls["episodecollection"] % eid, key="type", call_cached=False)

    def put_episode_status(self, eid: int, status: int = 2) -> Optional[bool]:
        """
        更新集状态
        0: 未收藏, 1: 想看, 2: 看过, 3: 抛弃
        """
        return self.__invoke("put", self._urls["episodecollection"] % eid, call_cached=False, json={"type": status})


class BangumiSync(_PluginBase):
    # 插件名称
    plugin_name = "Bangumi打格子"
    # 插件描述
    plugin_desc = "将在看记录同步到bangumi"
    # 插件图标
    plugin_icon = "https://raw.githubusercontent.com/honue/MoviePilot-Plugins/main/icons/bangumi.jpg"
    # 插件版本
    plugin_version = "2.0.1"
    # 插件作者
    plugin_author = "honue,happyTonakai"
    # 作者主页
    author_url = "https://github.com/happyTonakai"
    # 插件配置项ID前缀
    plugin_config_prefix = "bangumisync_"
    # 加载顺序
    plugin_order = 20
    # 可使用的用户级别
    auth_level = 1

    UA = "honue/MoviePilot-Plugins (https://github.com/honue/MoviePilot-Plugins)"

    ANIME_KEYWORDS_PATTERN = re.compile(
        r"(日番|cartoon|动漫|动画|ani|anime|新番|番剧|特摄|bangumi|ova|映画|国漫|日漫)",
        re.IGNORECASE,
    )

    _enable: bool = False
    _user: str = ""
    _uniqueid_match: bool = False
    _notify: bool = False

    def init_plugin(self, config: dict = None):
        if config:
            self._enable = config.get('enable', False)
            self._user = config.get('user', "")
            self._uniqueid_match = config.get('uniqueid_match', False)
            self._notify = config.get('notify', False)
        if self._enable and (_token := config.get('token')):
            self.bangumi_client = BangumiAPIClient(token=_token, ua=BangumiSync.UA)
            logger.info(f"Bangumi在看同步插件 v{BangumiSync.plugin_version} 初始化成功")

    @eventmanager.register(EventType.WebhookMessage)
    def hook(self, event: Event):
        # 插件未启用
        if not self._enable:
            return
        try:
            logger.debug(f"收到webhook事件: {event.event_data}")
            event_info: WebhookEventInfo = event.event_data
            # 不是指定用户, 不处理
            if event_info.user_name not in self._user.split(','):
                return
            play_start = {"playback.start", "media.play", "PlaybackStart"}
            # 不是播放停止事件, 或观看进度不足90% 不处理
            if not (event_info.event in play_start or event_info.percentage and event_info.percentage > 90):
                logger.info(f"{event_info.item_name} 播放进度不足90%, 不处理")
                return
            # 根据路径判断是不是番剧
            if not BangumiSync.is_anime(event_info):
                return

            title = None
            air_date = None
            epinfo = None
            logger.info(f"匹配播放事件 {event_info.item_name} ...")
            # 解析事件元数据
            meta = self.parse_event_meta(event_info)
            # 获取媒体信息
            mediainfo = self.chain.recognize_media(meta)
            if not mediainfo:
                title = meta.name
            else:
                # 使用源语言标题
                title = mediainfo.original_title

                if mediainfo.type == MediaType.TV:
                    air_date, epinfo = self.__lookup_episode(season=meta.begin_season, episode=meta.begin_episode,
                                                             mediainfo=mediainfo, unique_id=event_info.tmdb_id)

                if air_date is None:
                    air_date = self._season_air_date(mediainfo=mediainfo, season=meta.begin_season)

            # 匹配Bangumi 条目
            subject_id = self.get_subjectid(title=title, air_date=air_date, type=meta.type)

            if meta.type == MediaType.MOVIE:
                # 电影直接更新状态
                self.update_collection_status(subject_id, 2)
            else:
                self.sync_tv_status(subject_id, meta.begin_episode, epinfo)
        except Exception as e:
            err_msg = f"{self._prefix} 同步失败:\n {str(e)}"
            logger.error(err_msg)
            if self._notify and isinstance(e, ImmediateException):
                self.post_message(
                    mtype=NotificationType.Manual,
                    title=self.plugin_name,
                    text=err_msg,
                    image=mediainfo.get_message_image() if mediainfo else None,
                )

    def parse_event_meta(self, event_info: WebhookEventInfo) -> MetaBase:
        meta = MetaInfoPath(Path(event_info.item_path))
        meta.set_season(event_info.season_id)
        meta.set_episode(event_info.episode_id)
        meta.type = MediaType.MOVIE if event_info.media_type in ["Movie", "MOV"] else MediaType.TV

        self._prefix = meta.name
        if meta.year:
            self._prefix += f" ({meta.year})"
        if meta.season_episode:
            self._prefix += f" {meta.season_episode}"

        def from_event(meta: MetaBase, event_info: WebhookEventInfo):
            if meta.type != MediaType.TV and event_info.tmdb_id:
                logger.info(f"通过事件获取 TMDB ID：{event_info.tmdb_id}")
                return event_info.tmdb_id

        def from_mediaserver_api(server_name, itemid):
            iteminfo = MediaServerChain().iteminfo(server_name, itemid)
            if iteminfo and iteminfo.tmdbid:
                logger.info(f"通过 {iteminfo.server} API 获取到 TMDB ID：{iteminfo.tmdbid}")
                return iteminfo.tmdbid
            return None

        def from_local_db(itemid):
            item = MediaServerItem.get_by_itemid(db=None, item_id=itemid)
            if item and item.tmdbid:
                logger.info(f"通过本地数据库获取到 TMDB ID：{item.tmdbid}")
                return item.tmdbid
            return None

        tmdb_id = None

        # 获取itemid
        itemid = self.get_itemid(event_info)

        # 定义获取 TMDB ID 的方法链
        fetch_methods = [
            lambda: from_event(meta, event_info),
            lambda: from_mediaserver_api(event_info.server_name, itemid),
            lambda: from_local_db(itemid),
        ]

        for method in fetch_methods:
            tmdb_id = method()
            if tmdb_id:
                break

        meta.tmdbid = tmdb_id
        return meta

    def __lookup_episode(self, season: int, episode: int, mediainfo: MediaInfo, unique_id) -> tuple[Optional[str], Any]:
        """
        通过tmdb获取播出日期和剧集信息

        :param season: 季号
        :param episode: 集号
        :param mediainfo: 媒体信息
        :param unique_id: 唯一标识
        """
        episodes: list[dict] = None
        language = mediainfo.original_language

        tmdb_obj = self.chain.modulemanager.get_running_module("TheMovieDbModule")

        def _get_episodes_by_group(tmdbid: int, season: int):
            """
            通过episode group获取剧集信息
            """
            from app.db.subscribe_oper import SubscribeOper

            group_id = None

            subs = SubscribeOper().list_by_tmdbid(tmdbid, season)
            for sub in subs:
                if sub.episode_group:
                    group_id = sub.episode_group
                    break
            if not group_id:
                # 有些番剧拥有多个Seasons结果，比如我独自升级，其中一个Seasons是将总集篇作为一集，因此我们选择episode_count最小的一个
                seasons = [
                    result for result in mediainfo.episode_groups if result.get("name") == "Seasons"
                ]
                if seasons:
                    season_group = min(seasons, key=lambda x: x.get("episode_count"))
                    group_id = season_group.get("id")
            if group_id:
                resp = tmdb_obj.tmdb.tv.group_episodes(group_id) or []
                for group in resp:
                    if group["order"] == season:
                        return group
            return None

        with self.temporary_attributes(
            tmdb_obj,
            **{"tmdb.season_obj.language": language, "tmdb.tv.language": language},
        ):
            result = self.chain.tmdb_info(
                mediainfo.tmdb_id, mediainfo.type, season
            ) or _get_episodes_by_group(mediainfo.tmdb_id, season)

        if result:
            episodes = result.get("episodes")

        if not episodes:
            logger.warning(f"{self._prefix}: 没有剧集信息")
            return None, None

        if unique_id and not isinstance(unique_id, int):
            try:
                unique_id = int(unique_id)
            except ValueError:
                unique_id = None

        # 初始化播出日期
        air_date = None
        matched_episode = None

        for ep in episodes:
            if air_date is None:
                air_date = ep.get("air_date")
            if self._uniqueid_match and unique_id:
                if ep.get("id") == unique_id:
                    matched_episode = ep
                    break
            elif ep.get("order", -99) + 1 == episode:
                matched_episode = ep
                break
            elif ep.get("episode_number") == episode:
                matched_episode = ep
                break
            if ep.get("episode_type") in ["finale", "mid_season"]:
                air_date = None

        if not matched_episode:
            logger.warning(f"{self._prefix}: 未找到匹配的TMDB剧集")
            air_date = None

        return air_date, matched_episode

    def get_subjectid(self, title, air_date, type: MediaType) -> Optional[int]:
        """
        获取 bangumi 条目

        :param title: 标题
        :param air_date: 上映/首播日期
        :param type: 媒体类型
        """
        logger.info(f"{self._prefix}: 正在搜索 Bangumi 对应条目...")

        if resp := self.bangumi_client.search(title=title, air_date=air_date):
            logger.debug(f"{self._prefix}: 搜索结果: {resp}")

            for subject in resp:
                mtype = MediaType.MOVIE if subject.get("platform") in {"剧场版", "电影"} else MediaType.TV
                if mtype == type:
                    subject_id = subject["id"]
                    logger.info(f"{subject.get('name_cn', '')} https://bgm.tv/subject/{subject_id}")
                    return subject_id

        raise ImmediateException("未找到对应的 Bangumi 条目")

    def sync_tv_status(self, subject_id, episode, tmdb_epinfo: dict):

        # 更新合集状态
        self.update_collection_status(subject_id)

        # 获取episode id
        ep_info = self.get_episodes_info(subject_id)

        found_episode_id = None
        if ep_info:
            # 收集所有匹配项
            candidates = []

            episode_name = tmdb_epinfo.get("name") if tmdb_epinfo else None

            episode_airdate = tmdb_epinfo.get("air_date") if tmdb_epinfo else None

            if episode_airdate:
                _air_date = datetime.strptime(episode_airdate, "%Y-%m-%d").date()
                # 格式化为字符串
                start_date = (_air_date - timedelta(days=1)).strftime("%Y-%m-%d")
                end_date = (_air_date + timedelta(days=1)).strftime("%Y-%m-%d")

            for info in ep_info:
                score = 0
                matched_fields = {}
                # name
                name = info.get("name", "")
                # airdate
                airdate = info.get("airdate")
                # sort
                sort = info.get("sort")
                # ep
                ep = info.get("ep")

                # 名称匹配
                if (episode_name and
                    name == episode_name):
                    score += 4
                    matched_fields["name"] = name

                # 播出日期匹配
                if (episode_airdate and
                    airdate and
                    start_date <= airdate <= end_date):
                    score += 4
                    matched_fields["airdate"] = airdate

                # sort字段匹配
                if sort == episode:
                    score += 3
                    matched_fields["sort"] = sort

                # ep字段匹配
                if ep == episode:
                    score += 2
                    matched_fields["ep"] = ep

                # 只有得分大于0的才考虑
                if score > 0:
                    candidates.append({
                        "info": info,
                        "score": score,
                        "matched_fields": matched_fields
                    })

            if candidates:
                # 按得分排序，得分高的在前
                candidates.sort(key=lambda x: x["score"], reverse=True)
                # 选择得分最高的
                best_candidate = candidates[0]
                found_episode_id = best_candidate["info"]["id"]
                matched_info = best_candidate["info"]

                # 记录匹配详情
                logger.info(f"{self._prefix}: 匹配完成 - 得分: {best_candidate['score']}, "
                            f"匹配字段: {best_candidate['matched_fields']}")

        if not found_episode_id:
            raise ImmediateException("未找到episode，可能因为TMDB和BGM的episode映射关系不一致")

        last_episode = matched_info == ep_info[-1]

        # 点格子
        self.update_episode_status(found_episode_id)

        # 最后一集，更新状态为看过
        if last_episode:
            self.update_collection_status(subject_id, 2)

    def update_collection_status(self, subject_id, new_type=3):
        resp = self.bangumi_client.get_collection_status(subject_id)
        type_dict = {0:"未看", 1:"想看", 2:"看过", 3:"在看", 4:"搁置", 5:"抛弃"}
        old_type = resp or 0
        if old_type == 2:
            # 已经看过，避免刷屏
            logger.info(f"{self._prefix}: 合集状态 {type_dict[old_type]} => {type_dict[new_type]}，无需更新在看状态")
            return
        if old_type == new_type == 3:
            # 已经在看，避免刷屏
            logger.info(f"{self._prefix}: 合集状态 {type_dict[old_type]} => {type_dict[new_type]}，无需更新在看状态")
            return
        # 更新在看状态
        resp = self.bangumi_client.post_collection_status(subject_id, status=new_type)
        if resp:
            logger.info(f"{self._prefix}: 合集状态 {type_dict[old_type]} => {type_dict[new_type]}，在看状态更新成功")
        else:
            raise ImmediateException(f"合集状态 {type_dict[old_type]} => {type_dict[new_type]}，在看状态更新失败")

    def get_episodes_info(self, subject_id) -> List[dict]:
        all_episodes = []
        offset = 0
        # 使用最大 limit 减少请求次数
        limit = 1000

        while True:
            episodes = self.bangumi_client.episodes(bid=subject_id, limit=limit, offset=offset)

            if not episodes:
                break

            all_episodes.extend(episodes)

            # 检查是否还有更多数据
            if len(episodes) < limit:
                break

            offset += limit

        if not all_episodes:
            raise ImmediateException("未获取到任何 episode info")

        logger.debug(f"{self._prefix}: 获取 episode info 成功，共 {len(all_episodes)} 集")

        return all_episodes

    def update_episode_status(self, episode_id):
        resp = self.bangumi_client.get_episode_status(episode_id)
        if resp == 2:
            logger.info(f"{self._prefix}: 单集已经点过格子了")
            return
        resp = self.bangumi_client.put_episode_status(episode_id)
        if resp:
            logger.info(f"{self._prefix}: 单集点格子成功")
        else:
            raise ImmediateException("单集点格子失败")

    @staticmethod
    def _season_air_date(mediainfo: MediaInfo, season: int) -> Optional[str]:
        """
        获取指定季度的播出日期

        :param mediainfo: 媒体信息
        :param season: 季号
        :return: 播出日期，如果未找到则返回媒体的发布日期
        """
        air_date = next(
            (
                info.get("air_date")
                for info in mediainfo.season_info
                if season == info.get("season_number")
            ),
            mediainfo.release_date,
        )
        return air_date

    @contextmanager
    def temporary_attributes(self, obj, **kwargs):
        """
        临时修改对象属性的上下文管理器

        :param obj: 要修改的对象
        :param kwargs: 嵌套属性字典，如 {"tmdb.language": "zh-CN"}
        """
        obj_name = obj.__class__.__name__
        # 获取当前上下文状态
        state = _temp_attrs_state.get().copy()

        @retry(ExceptionToCheck=ValueError, tries=5, delay=0.1, logger=logger)
        def wait_and_check(target_obj, attr_name, expected_value, old_value):
            """
            等待属性值变为期望值或原始值

            :param target_obj: 目标对象
            :param attr_name: 属性名
            :param expected_value: 期望值（设置的值）
            :param old_value: 原始值
            :return: (current_value, should_restore) 元组，should_restore表示是否需要恢复
            """
            current_value = getattr(target_obj, attr_name, None)

            # 当前值等于设置的值，则可以恢复
            if current_value == expected_value:
                return current_value, True

            # 当前值等于原始值，说明已经被恢复了
            if current_value == old_value:
                return current_value, False

            raise ValueError(f"Attribute value mismatch: expected {expected_value}, got {current_value}")

        try:
            # 应用修改
            for attr_path, new_value in kwargs.items():
                attrs = attr_path.split('.')
                current_obj = obj

                # 导航到目标对象
                for attr in attrs[:-1]:
                    if not hasattr(current_obj, attr):
                        setattr(current_obj, attr, type('DynamicObj', (), {})())
                    current_obj = getattr(current_obj, attr)

                # 保存原始值
                final_attr = attrs[-1]
                old_value = getattr(current_obj, final_attr, None)

                 # 如果当前值已经等于目标值，则跳过修改
                if old_value == new_value:
                    logger.debug(f"Skip: {obj_name}.{attr_path} already equals {new_value}")
                    continue

                state[attr_path] = (current_obj, final_attr, old_value, new_value)

                # 设置新值
                setattr(current_obj, final_attr, new_value)
                logger.debug(f"Set: {obj_name}.{attr_path} = {new_value}")

            # 更新上下文状态
            token = _temp_attrs_state.set(state)

            yield

        finally:
            # 恢复原始值
            for attr_path, modification in state.items():
                target_obj, attr_name, old_value, new_value = modification
                try:
                    current_value, should_restore = wait_and_check(target_obj, attr_name, new_value, old_value)

                    # 如果不需要恢复（已经被其他线程恢复），则跳过
                    if not should_restore:
                        continue

                    # 当前值不等于设置的值
                    if current_value != new_value:
                        logger.warn(f"Already restored: {obj_name}.{attr_path} is already {old_value}")

                    if old_value is not None:
                        setattr(target_obj, attr_name, old_value)
                        logger.debug(f"Restore: {obj_name}.{attr_path} = {old_value}")
                    elif hasattr(target_obj, attr_name):
                        delattr(target_obj, attr_name)
                        logger.debug(f"Remove: {obj_name}.{attr_path}")

                except ValueError as e:
                    logger.error(f"Timeout: {obj_name}.{attr_path} was modified by another thread, "
                                 f"{str(e)}, force restore")
            # 恢复上下文
            _temp_attrs_state.reset(token)

    @staticmethod
    def is_anime(event_info: WebhookEventInfo) -> bool:
        """
        通过路径关键词来确定是不是anime媒体库
        """
        if event_info.channel in ["emby", "jellyfin"]:
            path = event_info.item_path
        elif event_info.channel == "plex":
            path = event_info.json_object.get("Metadata", {}).get("librarySectionTitle", "")
        else:
            return False

        if BangumiSync.ANIME_KEYWORDS_PATTERN.search(path):
            return True

        logger.debug(f"{path} 不是动漫媒体库")
        return False

    @staticmethod
    def get_itemid(event_data: WebhookEventInfo) -> Optional[str]:
        json_object = event_data.json_object
        if event_data.channel == "emby":
            return event_data.item_id
        elif event_data.channel == "jellyfin":
            return json_object.get("SeriesId") or json_object.get("ItemId")
        elif event_data.channel == "plex":
            return event_data.item_id

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
                            },
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
                                            'model': 'uniqueid_match',
                                            'label': '集唯一ID匹配',
                                        }
                                    }
                                ]
                            },
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
                                            'model': 'notify',
                                            'label': '出现异常时发送通知',
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
                                            'model': 'token',
                                            'label': 'Bangumi Access-token',
                                            'placeholder': 'dY123qxXcdaf234Gj6u3va123Ohh'
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
                                            'text': True
                                        },
                                        'content': [
                                            {
                                                'component': 'div',
                                                'style': 'white-space: pre-line;',
                                                'props': {
                                                    'innerHTML': '<a href="https://next.bgm.tv/demo/access-token" target="_blank">'
                                                    '<u>获取access-token</u></a><br>'
                                                    'emby添加你mp的webhook(event要包括播放): '
                                                    'http://127.0.0.1:3001/api/v1/webhook?token=moviepilot<br>'
                                                    '感谢@HankunYu的想法'
                                                }
                                            }
                                        ]
                                    }
                                ]
                            }
                        ]
                    }
                ]
            }
        ], {
            "enable": False,
            "uniqueid_match": False,
            "notify": False,
            "user": "",
            "token": ""
        }

    def get_page(self) -> List[dict]:
        pass

    def get_state(self) -> bool:
        return self._enable

    def stop_service(self):
        pass
