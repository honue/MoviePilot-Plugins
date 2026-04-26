import time
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple
from urllib.parse import quote, urlparse, urlunparse

import pytz
from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.cron import CronTrigger

from app.core.config import settings
from app.log import logger
from app.plugins import _PluginBase
from app.utils.http import RequestUtils


class ANiStrm(_PluginBase):
    plugin_name = "ANiStrm"
    plugin_desc = "自动获取当季所有番剧，免去下载，轻松拥有一个番剧媒体库"
    plugin_icon = "https://raw.githubusercontent.com/honue/MoviePilot-Plugins/main/icons/anistrm.png"
    plugin_version = "2.5.0"
    plugin_author = "honue"
    author_url = "https://github.com/honue"
    plugin_config_prefix = "anistrm_"
    plugin_order = 15
    auth_level = 2

    _enabled = False
    _use_proxy = True
    _cron = None
    _onlyonce = False
    _storageplace = None
    _selected_seasons: List[str] = []
    _proxy_base = None
    _scheduler: Optional[BackgroundScheduler] = None

    def __init__(self):
        super().__init__()
        self._client = AniStrmClient()
        self._strm_service = StrmFileService()

    def init_plugin(self, config: dict = None):
        self.stop_service()

        config = config or {}
        self._enabled = config.get("enabled", False)
        use_proxy = config.get("use_proxy")
        self._use_proxy = True if use_proxy is None else use_proxy
        self._cron = config.get("cron") or "20 22,23,0,1 * * *"
        self._onlyonce = config.get("onlyonce", False)
        self._storageplace = config.get("storageplace") or "/downloads/strm"
        if "selected_seasons" in config:
            self._selected_seasons = config.get("selected_seasons") or []
        else:
            self._selected_seasons = ["latest"]
        self._proxy_base = config.get("proxy_base") or "https://openani.an-i.workers.dev"
        self._client.set_use_proxy(self._use_proxy)
        self._client.set_proxy_base(self._proxy_base)
        logger.info(
            f"ANi-Strm配置加载：enabled={self._enabled}, onlyonce={self._onlyonce}, "
            f"use_proxy={self._use_proxy}, proxy_base={self._proxy_base}, "
            f"seasons={self._selected_seasons or []}, storage={self._storageplace}"
        )

        if not (self._enabled or self._onlyonce):
            logger.info("ANi-Strm未启用且未触发立即运行，跳过任务注册")
            return

        self._scheduler = BackgroundScheduler(timezone=settings.TZ)

        if self._enabled and self._cron:
            try:
                self._scheduler.add_job(
                    func=self.__task,
                    trigger=CronTrigger.from_crontab(self._cron),
                    name="ANiStrm文件创建",
                )
                logger.info(f"ANi-Strm定时任务创建成功：{self._cron}")
            except Exception as err:
                logger.error(f"定时任务配置错误：{err}")

        if self._onlyonce:
            logger.info("ANi-Strm服务启动，立即运行一次")
            self._scheduler.add_job(
                func=self.__task,
                trigger="date",
                run_date=datetime.now(tz=pytz.timezone(settings.TZ)) + timedelta(seconds=3),
                name="ANiStrm文件创建",
            )
            self._onlyonce = False

        self.__update_config()

        if self._scheduler.get_jobs():
            self._scheduler.print_jobs()
            self._scheduler.start()

    def __task(self):
        seasons = self.__get_target_seasons()
        if not seasons:
            logger.info("未选择任何季度，任务结束")
            return

        logger.info(
            f"ANi-Strm任务开始：seasons={seasons}, storage={self._storageplace}, "
            f"proxy={self._client._get_openani_base()}, use_proxy={self._use_proxy}"
        )

        total_files = 0
        total_created = 0
        total_exists = 0
        total_failed = 0

        for season in seasons:
            file_names = self._client.get_season_list(season)
            season_total = len(file_names)
            season_created = 0
            season_exists = 0
            season_failed = 0
            logger.info(f"ANi-Strm开始处理季度：{season}，文件数={season_total}")
            for file_name in file_names:
                status = self._strm_service.touch_strm_file(
                    storage_path=self._storageplace,
                    file_name=file_name,
                    season=season,
                    base_url=self._client._get_openani_base(),
                )
                if status == "created":
                    season_created += 1
                elif status == "exists":
                    season_exists += 1
                else:
                    season_failed += 1

            total_files += season_total
            total_created += season_created
            total_exists += season_exists
            total_failed += season_failed
            logger.info(
                f"ANi-Strm季度处理完成：{season}，总数={season_total}，"
                f"新增={season_created}，跳过={season_exists}，失败={season_failed}"
            )

        logger.info(
            f"ANi-Strm任务完成：季度数={len(seasons)}，文件总数={total_files}，"
            f"新增={total_created}，跳过={total_exists}，失败={total_failed}"
        )

    def __get_target_seasons(self) -> List[str]:
        if self._selected_seasons:
            seasons: List[str] = []
            for season in self._selected_seasons:
                if season == "latest":
                    latest = self._client.get_current_season()
                    if latest:
                        seasons.append(latest)
                else:
                    seasons.append(season)
            return list(dict.fromkeys(seasons))
        return []

    def get_current_season_list(self) -> List[str]:
        return self._client.get_current_season_list()

    def get_state(self) -> bool:
        return self._enabled

    @staticmethod
    def get_command() -> List[Dict[str, Any]]:
        pass

    def get_api(self) -> List[Dict[str, Any]]:
        pass

    def get_form(self) -> Tuple[List[dict], Dict[str, Any]]:
        season_options = self.__build_season_options()
        return [
            {
                "component": "VForm",
                "content": [
                    {
                        "component": "VRow",
                        "content": [
                            {
                                "component": "VCol",
                                "props": {
                                    "cols": 12,
                                    "md": 4,
                                },
                                "content": [
                                    {
                                        "component": "VSwitch",
                                        "props": {
                                            "model": "enabled",
                                            "label": "启用插件",
                                        },
                                    }
                                ],
                            },
                            {
                                "component": "VCol",
                                "props": {
                                    "cols": 12,
                                    "md": 4,
                                },
                                "content": [
                                    {
                                        "component": "VSwitch",
                                        "props": {
                                            "model": "use_proxy",
                                            "label": "使用代理",
                                        },
                                    }
                                ],
                            },
                            {
                                "component": "VCol",
                                "props": {
                                    "cols": 12,
                                    "md": 4,
                                },
                                "content": [
                                    {
                                        "component": "VSwitch",
                                        "props": {
                                            "model": "onlyonce",
                                            "label": "立即运行一次",
                                        },
                                    }
                                ],
                            },
                        ],
                    },
                    {
                        "component": "VRow",
                        "content": [
                            {
                                "component": "VCol",
                                "props": {
                                    "cols": 12,
                                    "md": 6,
                                },
                                "content": [
                                    {
                                        "component": "VTextField",
                                        "props": {
                                            "model": "cron",
                                            "label": "执行周期",
                                            "placeholder": "0 0 ? ? ?",
                                        },
                                    }
                                ],
                            },
                            {
                                "component": "VCol",
                                "props": {
                                    "cols": 12,
                                    "md": 6,
                                },
                                "content": [
                                    {
                                        "component": "VTextField",
                                        "props": {
                                            "model": "proxy_base",
                                            "label": "反代地址",
                                            "placeholder": "https://openani.an-i.workers.dev",
                                            "hint": "用于季度目录读取和strm源地址生成，留空则使用官方默认地址",
                                            "persistent-hint": True,
                                        },
                                    }
                                ],
                            },
                        ],
                    },
                    {
                        "component": "VRow",
                        "content": [
                            {
                                "component": "VCol",
                                "props": {
                                    "cols": 12,
                                    "md": 6,
                                },
                                "content": [
                                    {
                                        "component": "VSelect",
                                        "props": {
                                            "model": "selected_seasons",
                                            "label": "拉取季度",
                                            "items": season_options,
                                            "multiple": True,
                                            "chips": True,
                                            "clearable": True,
                                            "hint": "按所选季度检查并补齐strm；已存在文件会自动跳过。留空则本次不执行",
                                            "persistent-hint": True,
                                        },
                                    }
                                ],
                            },
                            {
                                "component": "VCol",
                                "props": {
                                    "cols": 12,
                                    "md": 6,
                                },
                                "content": [
                                    {
                                        "component": "VTextField",
                                        "props": {
                                            "model": "storageplace",
                                            "label": "Strm存储地址",
                                            "placeholder": "/downloads/strm",
                                        },
                                    }
                                ],
                            },
                        ],
                    },
                    {
                        "component": "VRow",
                        "content": [
                            {
                                "component": "VCol",
                                "props": {
                                    "cols": 12,
                                },
                                "content": [
                                    {
                                        "component": "VAlert",
                                        "props": {
                                            "type": "info",
                                            "variant": "tonal",
                                            "text": "自动从open ANi抓取下载直链生成strm文件\n"
                                            "默认选择最新季，也支持季度多选，自动补齐所选季度缺失的strm\n"
                                            "配合目录监控使用，strm文件创建在/downloads/strm\n"
                                            "通过目录监控转移到link媒体库文件夹 如/downloads/link/strm mp会完成刮削",
                                            "style": "white-space: pre-line;",
                                        },
                                    },
                                    {
                                        "component": "VAlert",
                                        "props": {
                                            "type": "info",
                                            "variant": "tonal",
                                            "text": "emby容器需要设置代理，docker的环境变量必须要有http_proxy代理变量，大小写敏感，否则无法提取媒体信息，具体见readme.\n"
                                            "https://github.com/honue/MoviePilot-Plugins",
                                            "style": "white-space: pre-line;",
                                        },
                                    },
                                ],
                            }
                        ],
                    },
                ],
            }
        ], {
            "enabled": False,
            "use_proxy": True,
            "onlyonce": False,
            "storageplace": "/downloads/strm",
            "selected_seasons": ["latest"],
            "proxy_base": "https://openani.an-i.workers.dev",
            "cron": "20 22,23,0,1 * * *",
        }

    def __build_season_options(self) -> List[Dict[str, str]]:
        seasons = self._client.get_available_seasons() or [self._client._get_local_season()]
        return [{"title": "最新季", "value": "latest"}] + [
            {"title": season, "value": season} for season in seasons
        ]

    def __update_config(self):
        self.update_config(
            {
                "onlyonce": self._onlyonce,
                "cron": self._cron,
                "enabled": self._enabled,
                "use_proxy": self._use_proxy,
                "storageplace": self._storageplace,
                "selected_seasons": self._selected_seasons,
                "proxy_base": self._proxy_base,
            }
        )

    def get_page(self) -> List[dict]:
        pass

    def stop_service(self):
        try:
            if self._scheduler:
                self._scheduler.remove_all_jobs()
                if self._scheduler.running:
                    self._scheduler.shutdown()
                self._scheduler = None
        except Exception as err:
            logger.error(f"退出插件失败：{err}")


class AniStrmClient:
    def __init__(self, request_factory=None, proxy_base: Optional[str] = None, use_proxy: bool = False):
        self._request_factory = request_factory or self._build_request_utils
        self._proxy_base = self.normalize_proxy_base(proxy_base)
        self._season_options_cache: List[str] = []
        self._use_proxy = use_proxy

    def set_proxy_base(self, proxy_base: Optional[str]):
        self._proxy_base = self.normalize_proxy_base(proxy_base)
        self._season_options_cache = []

    def set_use_proxy(self, use_proxy: bool):
        self._use_proxy = use_proxy
        self._season_options_cache = []

    def get_current_season(self, idx_month: Optional[int] = None, now: Optional[datetime] = None) -> str:
        remote_season = self._get_latest_remote_season()
        if remote_season:
            return remote_season
        return self._get_local_season(idx_month=idx_month, now=now)

    def _get_local_season(self, idx_month: Optional[int] = None, now: Optional[datetime] = None) -> str:
        current = now or datetime.now()
        current_month = idx_month or current.month
        season_month = ((current_month - 1) // 3) * 3 + 1
        return f"{current.year}-{season_month}"

    def get_current_season_list(self) -> List[str]:
        season = self.get_current_season()
        return self.get_season_list(season)

    def get_season_list(self, season: str) -> List[str]:
        def operation():
            payload = self._fetch_folder_payload(f"{self._get_openani_base()}/{season}/")
            files = payload.get("files") or []
            return [file_info["name"] for file_info in files if file_info.get("name")]

        return self._with_retry(operation, default=[])

    def _get_latest_remote_season(self) -> Optional[str]:
        def operation():
            payload = self._fetch_folder_payload(f"{self._get_openani_base()}/")
            return self._extract_latest_season(payload.get("files") or [])

        return self._with_retry(operation, default=None)

    def get_available_seasons(self, use_cache: bool = True) -> List[str]:
        if use_cache and self._season_options_cache:
            return list(self._season_options_cache)

        def operation():
            payload = self._fetch_folder_payload(f"{self._get_openani_base()}/")
            seasons = []
            for file_info in payload.get("files") or []:
                name = file_info.get("name") or ""
                mime_type = file_info.get("mimeType") or ""
                if mime_type != "application/vnd.google-apps.folder":
                    continue
                parts = name.split("-", 1)
                if len(parts) != 2 or not parts[0].isdigit() or not parts[1].isdigit():
                    continue
                seasons.append(name)
            seasons.sort(key=lambda item: tuple(map(int, item.split("-"))), reverse=True)
            return seasons

        seasons = self._with_retry(operation, default=[])
        self._season_options_cache = list(seasons)
        return seasons

    def _fetch_folder_payload(self, url: str) -> Dict[str, Any]:
        response = self._request_factory().post(
            url=url,
            data='{"password":""}',
        )
        if not response:
            raise ValueError(f"目录请求失败：{url}")
        try:
            return response.json()
        finally:
            response.close()

    def _build_request_utils(self) -> RequestUtils:
        return RequestUtils(
            ua=settings.USER_AGENT if settings.USER_AGENT else None,
            proxies=settings.PROXY if self._use_proxy and settings.PROXY else None,
        )

    @staticmethod
    def normalize_proxy_base(proxy_base: Optional[str]) -> str:
        if not proxy_base:
            return "https://openani.an-i.workers.dev"
        return proxy_base.strip().rstrip("/")

    def _get_openani_base(self) -> str:
        return self._proxy_base or "https://openani.an-i.workers.dev"

    def normalize_stream_link(self, link: str) -> str:
        parsed = urlparse(link)
        if parsed.netloc not in {"resources.ani.rip", "openani.an-i.workers.dev"}:
            return link

        proxy_parsed = urlparse(self._get_openani_base())
        return urlunparse(
            (
                proxy_parsed.scheme,
                proxy_parsed.netloc,
                parsed.path,
                parsed.params,
                parsed.query,
                parsed.fragment,
            )
        )

    @staticmethod
    def _with_retry(operation, default, tries: int = 3, delay: int = 3):
        remaining = tries
        wait_seconds = delay
        while remaining > 0:
            try:
                return operation()
            except Exception as err:
                remaining -= 1
                if remaining == 0:
                    logger.warning(f"ANiStrm请求失败，已达到最大重试次数：{err}")
                    break
                logger.warning(f"未获取到文件信息，{wait_seconds}秒后重试 ...")
                time.sleep(wait_seconds)
        logger.warning("请确保当前季度番剧文件夹存在或检查网络问题")
        return default

    @staticmethod
    def _extract_latest_season(files: List[Dict[str, str]]) -> Optional[str]:
        seasons: List[Tuple[int, int]] = []
        for file_info in files:
            name = file_info.get("name") or ""
            mime_type = file_info.get("mimeType") or ""
            if mime_type != "application/vnd.google-apps.folder":
                continue
            parts = name.split("-", 1)
            if len(parts) != 2 or not parts[0].isdigit() or not parts[1].isdigit():
                continue
            seasons.append((int(parts[0]), int(parts[1])))
        if not seasons:
            return None
        year, month = max(seasons)
        return f"{year}-{month}"


class StrmFileService:
    @staticmethod
    def build_season_url(season: str, file_name: str, base_url: str) -> str:
        encoded_filename = quote(file_name, safe="")
        return f"{base_url.rstrip('/')}/{season}/{encoded_filename}"

    @staticmethod
    def normalize_stream_url(url: str) -> str:
        if url.endswith(".mp4"):
            return url
        if url.endswith(".mp4?d=true"):
            return url[:-7]
        if "?d=mp4" in url:
            return url.replace("?d=mp4", ".mp4")
        if "?d=true" in url and ".mp4?d=true" not in url:
            return url.replace("?d=true", "")
        return f"{url}.mp4"

    def touch_strm_file(
        self,
        storage_path: str,
        file_name: str,
        season: Optional[str] = None,
        file_url: Optional[str] = None,
        base_url: str = "https://openani.an-i.workers.dev",
    ) -> str:
        if not storage_path:
            logger.error("创建strm源文件失败：未配置存储目录")
            return "failed"

        if file_url:
            src_url = self.normalize_stream_url(file_url)
        else:
            if not season:
                logger.error("创建strm源文件失败：未提供季度信息")
                return "failed"
            src_url = self.build_season_url(season, file_name, base_url=base_url)

        directory = Path(storage_path)
        file_path = directory / f"{file_name}.strm"
        if file_path.exists():
            logger.debug(f"ANi-Strm跳过已存在文件：{file_path.name}")
            return "exists"

        try:
            directory.mkdir(parents=True, exist_ok=True)
            file_path.write_text(src_url, encoding="utf-8")
            logger.debug(f"ANi-Strm创建成功：{file_path.name}")
            return "created"
        except Exception as err:
            logger.error(f"创建strm源文件失败：{file_path.name} - {err}")
            return "failed"
