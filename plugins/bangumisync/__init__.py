import contextvars
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
from app.schemas import WebhookEventInfo, TmdbEpisode
from app.schemas.exception import ImmediateException
from app.schemas.types import EventType, MediaType, NotificationType
from app.utils.common import retry
from app.utils.http import RequestUtils
from app.utils.string import StringUtils


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

    def search(self, title: str, air_date: Optional[str] = None) -> List[dict]:
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
    plugin_version = "2.0.2"
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

            logger.info(f"匹配播放事件 {event_info.item_name} ...")
            # 解析事件元数据
            meta = self.parse_event_meta(event_info)
            # 获取媒体信息
            mediainfo = self.chain.recognize_media(meta)
            if not mediainfo:
                logger.debug(f"未识别到媒体信息: {event_info.item_name}, 不处理")
                return
            if 16 not in mediainfo.genre_ids:
                # 不是动漫, 不处理
                logger.info(f"{event_info.item_name} 不是动漫, 不处理")
                return

            # 匹配 Bangumi 条目
            if mediainfo.type == MediaType.TV:
                result = self._match_tv_subject(mediainfo, meta, event_info.tmdb_id)
            else:
                result = self._match_movie_subject(mediainfo)

            # 同步条目状态
            self.sync_subject_status(*result)

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

    def _match_tv_subject(self, mediainfo: MediaInfo, meta: MetaBase, unique_id) -> tuple:
        """
        匹配Bangumi TV条目

        :param mediainfo: 媒体信息
        :param meta: 元数据信息
        :param unique_id: 唯一ID
        :return tuple: (subject_id, episode_id, mark_as_watched)
        :raises ImmediateException: 当找不到匹配项时抛出异常
        """
        ep_num = meta.begin_episode
        # 先获取tmdb集信息
        tmdb_episodes = self.get_original_language_tmdb_episodes(
            mediainfo=mediainfo,
            season=meta.begin_season
        )

        # 通过tmdb获取播出日期和剧集信息
        air_date, epinfo = self.__lookup_episode(
            episodes=tmdb_episodes,
            ep_num=ep_num,
            unique_id=unique_id
        )
        air_date = air_date or self._season_air_date(mediainfo=mediainfo, season=meta.begin_season)
        # 获取Bangumi 条目
        logger.info(f"{self._prefix}: 正在搜索 Bangumi 对应条目...")
        resp = self.bangumi_client.search(
            title=mediainfo.original_title,
            air_date=air_date
        )

        if not resp:
            # 未搜索到相关条目, 去掉播出日期再搜索一次
            resp = self.bangumi_client.search(title=mediainfo.original_title)

        if not resp:
            raise ImmediateException("未找到对应的 Bangumi 条目")

        logger.debug(f"{self._prefix}: 搜索结果: {resp}")

        # 将tmdb的集信息提取
        tmdb_episodes_info = [TmdbEpisode(**ep) for ep in tmdb_episodes]

        for subject in resp:
            if subject.get("platform") in {"剧场版", "电影"}:
                continue

            # 获取Bangumi集信息进一步确认
            bangumi_episodes = self.get_bgm_episodes(subject_id=subject["id"])

            # 验证TMDB与Bangumi集信息的匹配度
            matched_episodes_count = self._validate_episode_matching(tmdb_episodes_info, bangumi_episodes)

            # 如果匹配率超过70%，认为是正确的条目
            if matched_episodes_count >= 0.7:
                logger.info(f"{self._prefix}: 找到匹配的条目: {subject.get('name_cn', '')} "
                        f"https://bgm.tv/subject/{subject['id']}")

                # 匹配特定集数
                found_episode_id, mark_as_watched = self._find_matching_episode(
                    bangumi_episodes=bangumi_episodes,
                    tmdb_episode_info=epinfo,
                    ep_num=ep_num
                )

                if not found_episode_id:
                    raise ImmediateException("未找到episode，可能因为TMDB和BGM的episode映射关系不一致")

                # 记录匹配详情
                logger.info(f"{self._prefix}: 匹配完成 - 找到episode ID: {found_episode_id}")
                subject_id = subject["id"]
                return subject_id, found_episode_id, mark_as_watched

        raise ImmediateException("未能找到匹配的Bangumi条目")

    def _match_movie_subject(self, mediainfo: MediaInfo) -> tuple:
        """
        匹配Bangumi 电影条目

        :param mediainfo: 媒体信息
        :return tuple: (subject_id, mark_as_watched)
        :raise ImmediateException: 当找不到匹配项时抛出异常
        """
        resp = self.bangumi_client.search(
            title=mediainfo.original_title,
            air_date=mediainfo.release_date
        )

        if not resp:
            # 未搜索到相关条目, 去掉播出日期再搜索一次
            resp = self.bangumi_client.search(title=mediainfo.original_title)

        if not resp:
            raise ImmediateException("未找到对应的 Bangumi 条目")

        logger.debug(f"{self._prefix}: 搜索结果: {resp}")

        # 字段映射表
        FIELD_MAPPING = {
            '中文名': 'title',
            '别名': 'names',
            '上映年度': 'release_date',
            '片长': 'duration',
            '官方网站': 'website',
            '导演': 'director',
            '副导演': 'co_director',
            '发售日期': 'release_date_digital'
        }
        release_date_timeamp = StringUtils.str_to_timestamp(mediainfo.release_date)

        for subject in resp:
            if subject.get("platform") not in {"剧场版", "电影"}:
                continue

            result = {}
            for item in subject.get("infobox", []):
                key = item.get("key")
                value = item.get("value")

                # 处理数组格式的值
                if isinstance(value, list):
                    if value and isinstance(value[0], dict) and "v" in value[0]:
                        value = [v.get("v") for v in value if v.get("v")]

                # 应用字段映射
                mapped_key = FIELD_MAPPING.get(key, key.lower().replace(" ", "_"))
                result[mapped_key] = value
            # 获取两个可能的发行日期
            release_dates = [
                result.get("release_date"),
                result.get("release_date_digital")
            ]

            # 检查任一日期是否在15天范围内
            for release_date in release_dates:
                if release_date and abs(
                    release_date_timeamp - StringUtils.str_to_timestamp(release_date)
                ) < 86400 * 15:
                    return subject["id"], None, True

        raise ImmediateException("未能找到匹配的Bangumi条目")

    @staticmethod
    def _validate_episode_matching(tmdb_episodes_info: List[TmdbEpisode],
                                bangumi_episodes: List[dict]) -> float:
        """
        验证TMDB与Bangumi集信息的匹配度

        :param tmdb_episodes_info: TMDB集信息列表
        :param bangumi_episodes: Bangumi集信息列表
        :return float: 匹配率
        """
        # 计算时间戳
        future_limit = (datetime.now() + timedelta(days=5)).timestamp()

        tmdb_episodes_info = [ep for ep in tmdb_episodes_info if ep.air_date and StringUtils.str_to_timestamp(ep.air_date) <= future_limit]

        bangumi_episodes = [ep for ep in bangumi_episodes if (airdate := ep.get("airdate")) and StringUtils.str_to_timestamp(airdate) <= future_limit]

        match_eps = set()

        for tmdb_ep in tmdb_episodes_info:
            tmdb_airdate_timestamp = StringUtils.str_to_timestamp(tmdb_ep.air_date)

            for i, ep_info in enumerate(bangumi_episodes):
                if i in match_eps:
                    continue

                score = 0
                ep = ep_info.get("ep")
                sort = ep_info.get("sort")
                name = ep_info.get("name")
                airdate = ep_info.get("airdate")

                # 匹配集号
                if tmdb_ep.episode_number == ep or tmdb_ep.episode_number == sort:
                    score += 1
                # 匹配名称
                if tmdb_ep.name == name:
                    score += 1
                # 匹配播出日期（相差不超过一天）
                if (airdate and
                    abs(tmdb_airdate_timestamp - StringUtils.str_to_timestamp(airdate)) <= 86400):
                    score += 1

                if score >= 2:
                    match_eps.add(i)
                    break

        return len(match_eps) / len(bangumi_episodes)

    def _find_matching_episode(self, bangumi_episodes: List[dict],
                            tmdb_episode_info: dict,
                            ep_num: int) -> Tuple[Optional[int], bool]:
        """
        查找匹配的集信息

        :param bangumi_episodes: Bangumi集信息列表
        :param tmdb_episode_info: tmdb单集信息
        :param ep_num: 集号
        """
        # 收集所有匹配项
        candidates = []
        episode_name = tmdb_episode_info.get("name")
        airdate_timestamp = StringUtils.str_to_timestamp(tmdb_episode_info.get("air_date"))

        for info in bangumi_episodes:
            score = 0
            matched_fields = {}

            # 提取Bangumi集信息
            name = info.get("name", "")
            airdate = info.get("airdate")
            sort = info.get("sort")
            ep = info.get("ep")
            episode_id = info.get("id")

            # 名称匹配（权重4）
            if episode_name and name == episode_name:
                score += 4
                matched_fields["name"] = name

            # 播出日期匹配（权重4）
            if (airdate and
                abs(airdate_timestamp - StringUtils.str_to_timestamp(airdate)) < 86400):
                score += 4
                matched_fields["airdate"] = airdate

            # sort字段匹配（权重3）
            if sort == ep_num:
                score += 3
                matched_fields["sort"] = sort

            # ep字段匹配（权重2）
            if ep == ep_num:
                score += 2
                matched_fields["ep"] = ep

            # 只有得分大于0的才考虑
            if score > 0:
                candidates.append({
                    "info": info,
                    "score": score,
                    "matched_fields": matched_fields,
                    "episode_id": episode_id
                })

        if candidates:
            # 按得分排序，得分高的在前
            candidates.sort(key=lambda x: x["score"], reverse=True)
            # 选择得分最高的
            best_candidate = candidates[0]

            logger.info(f"{self._prefix}: 匹配完成 - 得分: {best_candidate['score']}, "
                    f"匹配字段: {best_candidate['matched_fields']}")

            found_episode_id = best_candidate["episode_id"]
            # 判断是否是最后一集
            mark_as_watched = (best_candidate["info"] == bangumi_episodes[-1])

            return found_episode_id, mark_as_watched

        return None, False

    def parse_event_meta(self, event_info: WebhookEventInfo) -> MetaBase:
        meta = MetaInfoPath(
            Path(event_info.item_path).parent / event_info.item_name
            if event_info.item_path
            else Path(event_info.item_name)
        )
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

    def __lookup_episode(self, episodes: Optional[dict], ep_num: int, unique_id) -> tuple[Optional[str], Any]:
        """
        通过tmdb获取播出日期和剧集信息

        :param episodes: TMDB的集信息
        :param ep_num: 集号
        :param unique_id: 唯一标识
        """
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
            elif ep.get("order", -99) + 1 == ep_num:
                matched_episode = ep
                break
            elif ep.get("episode_number") == ep_num:
                matched_episode = ep
                break
            if ep.get("episode_type") in ["finale", "mid_season"]:
                air_date = None

        if not matched_episode:
            logger.warning(f"{self._prefix}: 未找到匹配的TMDB剧集")
            air_date = None

        return air_date, matched_episode

    def get_original_language_tmdb_episodes(self, mediainfo: MediaInfo, season: int) -> list[dict]:
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

        return result.get("episodes", []) if isinstance(result, dict) else []

    def sync_subject_status(self, subject_id: int, episode_id: Optional[int] = None, mark_as_watched: bool = False):

        if episode_id:
            self.update_collection_status(subject_id)
            # 更新单集状态
            self.update_episode_status(episode_id)

        # 更新条目状态为看过
        if mark_as_watched:
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

    def get_bgm_episodes(self, subject_id) -> List[dict]:
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
        :return str: 播出日期，如果未找到则返回媒体的发布日期
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
