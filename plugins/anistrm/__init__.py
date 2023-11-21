from datetime import datetime, timedelta

import pytz
from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.cron import CronTrigger

from app.utils.http import RequestUtils
from app.core.config import settings
from app.plugins import _PluginBase
from typing import Any, List, Dict, Tuple, Optional
from app.log import logger


class ANiStrm(_PluginBase):
    # 插件名称
    plugin_name = "ANiStrm"
    # 插件描述
    plugin_desc = "生成strm文件，mp目录监控转移刮削，emby播放直链资源"
    # 插件图标
    plugin_icon = "https://cdn.jsdelivr.net/gh/RyanL-29/aniopen/aniopen.png"
    # 主题色
    plugin_color = "#e6e6e6"
    # 插件版本
    plugin_version = "1.0"
    # 插件作者
    plugin_author = "honue"
    # 作者主页
    author_url = "https://github.com/honue"
    # 插件配置项ID前缀
    plugin_config_prefix = "anistrm_"
    # 加载顺序
    plugin_order = 30
    # 可使用的用户级别
    auth_level = 2

    # 私有属性
    _enabled = False
    # 任务执行间隔
    _cron = None
    _onlyonce = False
    _fulladd = False
    _storageplace = None

    # 定时器
    _scheduler: Optional[BackgroundScheduler] = None

    def init_plugin(self, config: dict = None):
        # 停止现有任务
        self.stop_service()

        if config:
            self._enabled = config.get("enabled")
            self._cron = config.get("cron")
            self._onlyonce = config.get("onlyonce")
            self._fulladd = config.get("fulladd")
            self._storageplace = config.get("storageplace")
            # 加载模块
        if self._enabled or self._onlyonce:
            # 定时服务
            self._scheduler = BackgroundScheduler(timezone=settings.TZ)

            if self._enabled and self._cron:
                try:
                    self._scheduler.add_job(func=self.__task,
                                            trigger=CronTrigger.from_crontab(self._cron),
                                            name="ANiStrm文件刷新")
                except Exception as err:
                    logger.error(f"定时任务配置错误：{str(err)}")

            if self._onlyonce:
                logger.info(f"ANiStrm文件刷新服务启动，立即运行一次")
                self._scheduler.add_job(func=self.__task(fulladd=self._fulladd), trigger='date',
                                        run_date=datetime.now(tz=pytz.timezone(settings.TZ)) + timedelta(seconds=3),
                                        name="ANiStrm文件刷新")
                # 关闭一次性开关 全量转移
                self._onlyonce = False
                self._fulladd = False
            self.__update_config()

            # 启动任务
            if self._scheduler.get_jobs():
                self._scheduler.print_jobs()
                self._scheduler.start()

    def __get_ani_season(self, idx_month: int = None) -> str:
        current_date = datetime.now()
        current_year = current_date.year
        current_month = idx_month if idx_month else current_date.month
        for month in range(current_month, 0, -1):
            if month in [10, 7, 4, 1]:
                self._date = f'{current_year}-{month}'
                return f'{current_year}-{month}'

    def __get_name_list(self) -> List:
        url = f'https://aniopen.an-i.workers.dev/{self.__get_ani_season()}/'
        try:
            rep = RequestUtils(ua=settings.USER_AGENT if settings.USER_AGENT else None,
                               proxies=settings.PROXY if settings.PROXY else None).post(url=url)
            files_json = rep.json()['files']
            name_list = []
            for file in files_json:
                name_list.append(file['name'])
            return name_list
        except Exception as e:
            logger.error(str(e))
            pass
        # self.save_data("history", pulgin_history)

    def __touch_strm_file(self, file_name):
        src_url = f'https://resources.ani.rip/{self._date}/{file_name}?d=true'
        file_path = f'{self._storageplace}/{file_name}.strm'
        try:
            with open(file_path, 'w') as file:
                file.write(src_url)
        except Exception as e:
            logger.error('创建strm源文件失败：' + str(e))
            pass

    def __task(self, fulladd: bool = False):
        name_list = self.__get_name_list()
        if not fulladd:
            name_list = name_list[:15]
        for file_name in name_list:
            self.__touch_strm_file(file_name=file_name)

    def get_state(self) -> bool:
        return self._enabled

    @staticmethod
    def get_command() -> List[Dict[str, Any]]:
        pass

    def get_api(self) -> List[Dict[str, Any]]:
        pass

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
                                            'model': 'enabled',
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
                                            'model': 'onlyonce',
                                            'label': '立即运行一次',
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
                                            'model': 'fulladd',
                                            'label': '下次运行创建当前季度所有番剧strm',
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
                                            'model': 'cron',
                                            'label': '执行周期',
                                            'placeholder': '0 0 ? ? ?'
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
                                        'component': 'VTextField',
                                        'props': {
                                            'model': 'storageplace',
                                            'label': 'strm存储地址',
                                            'placeholder': '/downloads/cartoonstrm'
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
                                            'text': '建议配合目录监控使用，strm文件创建在/downloads/cartoonstrm 通过目录监控转移到link媒体库文件夹 如/downloads/link/cartoonstrm,mp会完成刮削，不开启一次性创建全部，则每次运行会创建ani最新季度的top15个文件。emby需要设置代理，源来自 https://aniopen.an-i.workers.dev/  创建的Strm在串流模式下一定可以播放，直接播放：1.在Windows小秘能播放 2.网页端和fileball播放测试失败。（log是tcp connect timeout）'
                                        }
                                    }
                                ]
                            }
                        ]
                    }
                ]
            }
        ], {
            "enabled": False,
            "onlyonce": False,
            "fulladd": False,
            "storageplace": '/downloads/cartoonstrm',
            "cron": "*/20 22-2 * * *",
        }

    def __update_config(self):
        self.update_config({
            "onlyonce": self._onlyonce,
            "cron": self._cron,
            "enabled": self._enabled,
            "fulladd": self._fulladd,
            "storageplace": self._storageplace,
        })

    def get_page(self) -> List[dict]:
        pass

    def stop_service(self):
        """
        退出插件
        """
        try:
            if self._scheduler:
                self._scheduler.remove_all_jobs()
                if self._scheduler.running:
                    self._scheduler.shutdown()
                self._scheduler = None
        except Exception as e:
            logger.error("退出插件失败：%s" % str(e))
