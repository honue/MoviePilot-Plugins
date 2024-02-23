from typing import List, Tuple, Dict, Any

import datetime
import pytz
from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.cron import CronTrigger

from app.core.config import settings
from app.utils.http import RequestUtils
from app.log import logger

from app.plugins import _PluginBase
from ...db.systemconfig_oper import SystemConfigOper
from ...schemas.types import SystemConfigKey
from app.utils.common import retry


class RemoteIdentifiers(_PluginBase):
    # 插件名称
    plugin_name = "共享识别词"
    # 插件描述
    plugin_desc = "从Github、Etherpad远程文件中，获取共享识别词并添加"
    # 插件图标
    plugin_icon = "https://raw.githubusercontent.com/honue/MoviePilot-Plugins/main/icons/words.png"
    # 插件版本
    plugin_version = "1.3"
    # 插件作者
    plugin_author = "honue"
    # 作者主页
    author_url = "https://github.com/honue"
    # 插件配置项ID前缀
    plugin_config_prefix = "RemoteIdentifiers_"
    # 加载顺序
    plugin_order = 10
    # 可使用的用户级别
    auth_level = 1

    _enable = False
    _cron = '30 4 * * *'
    _file_urls = ''
    _onlyonce = False
    _flitter = True
    # 定时器
    _scheduler = None

    def init_plugin(self, config: dict = None):
        # 停止后台任务
        self.stop_service()
        if config:
            self._enable = config.get("enable") or False
            self._onlyonce = config.get("onlyonce") or False
            self._cron = config.get("cron") or '30 4 * * *'
            self._file_urls = config.get("file_urls") or ''
            self._flitter = config.get("flitter") or True
            # config操作
            self.systemconfig = SystemConfigOper()

        if self._enable or self._onlyonce:
            self._scheduler = BackgroundScheduler(timezone=settings.TZ)
            if self._enable and self._cron:
                logger.info(f"获取远端识别词,订阅服务启动，周期：{self._cron}")
                try:
                    self._scheduler.add_job(func=self.__task,
                                            trigger=CronTrigger.from_crontab(self._cron),
                                            name="获取远端识别词")
                except Exception as e:
                    logger.error(f"获取远端识别词,订阅服务启动失败，错误信息：{str(e)}")
            if self._onlyonce:
                logger.info("获取远端识别词,订阅服务启动，立即运行一次")
                self._scheduler.add_job(func=self.__task, trigger='date',
                                        run_date=datetime.datetime.now(
                                            tz=pytz.timezone(settings.TZ)) + datetime.timedelta(seconds=3)
                                        )
                self._onlyonce = False
            self.__update_config()
            if self._scheduler.get_jobs():
                # 启动服务
                self._scheduler.print_jobs()
                self._scheduler.start()

    @retry(Exception, tries=3, delay=3, backoff=2, logger=logger)
    def get_file_content(self, file_urls: list) -> List[str]:
        ret: List[str] = ['======以下识别词由RemoteIdentifiers插件添加======']
        for file_url in file_urls:
            # https://etherpad.wikimedia.org/p/mp_anime_words
            if file_url.find("etherpad") and file_url.find("export") < 0:
                real_url = file_url + "/export/txt"
            else:
                real_url = file_url
            response = res = RequestUtils(proxies=settings.PROXY,
                                          headers=settings.GITHUB_HEADERS if real_url.find("git") else None,
                                          timeout=15).get_res(real_url)
            if not response:
                raise Exception("文件 {file_url} 下载失败！")
            elif response.status_code != 200:
                raise Exception(f"下载文件 {file_url} 失败：{res.status_code} - {res.reason}")
            text = response.content.decode('utf-8')
            if text.find("doctype html") > 0:
                raise Exception(f"下载文件 {file_url} 失败：{res.status_code} - {res.reason}")
            identifiers: List[str] = text.split('\n')
            ret += identifiers
        # flitter 过滤空行
        if self._flitter:
            filtered_ret = []
            for item in ret:
                if item != '':
                    filtered_ret.append(item)
            ret = filtered_ret
        logger.info(f"获取到远端识别词{len(ret) - 1}条: {ret[1:]}")
        return ret

    def __task(self):
        words: List[str] = self.systemconfig.get(SystemConfigKey.CustomIdentifiers) or []
        file_urls: list = self._file_urls.split('\n') if self._file_urls else []
        remote_words: list = self.get_file_content(file_urls)
        # 找出用户自己加的
        cnt = 0
        for word in words:
            if "RemoteIdentifiers" in word:
                break
            else:
                cnt += 1
        words = words[:cnt]
        words += remote_words
        self.systemconfig.set(SystemConfigKey.CustomIdentifiers, words)
        logger.info("远端识别词添加成功")

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
                            }, {
                                'component': 'VCol',
                                'props': {
                                    'cols': 12,
                                    'md': 3
                                },
                                'content': [
                                    {
                                        'component': 'VSwitch',
                                        'props': {
                                            'model': 'flitter',
                                            'label': '过滤空白行',
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
                                        'component': 'VSwitch',
                                        'props': {
                                            'model': 'onlyonce',
                                            'label': '立即运行一次',
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
                                            'model': 'cron',
                                            'label': '定时任务周期',
                                            'placeholder': '30 4 * * *',
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
                                            'model': 'file_urls',
                                            'rows': 6,
                                            'label': '远程文件url（若有多个，一行一个）',
                                            'placeholder': '如果是Github文件地址请注意填写包含raw的! 这个才是文件地址，其他的是这个文件的页面地址',
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
                                            'type': 'success',
                                            'variant': 'tonal',
                                            'text': '有愿意持续更新识别词的可以找我，我会将地址放在这推荐给大家！文件格式就是建个txt识别词一行一个。'
                                        }
                                    }, {
                                        'component': 'VAlert',
                                        'props': {
                                            'type': 'success',
                                            'variant': 'tonal',
                                            'text': '下面提供几个共享词库给大家，大家也可以直接打开编辑。在此感谢每一位贡献者！！！'
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
                                            'text': '电视剧：https://etherpad.wikimedia.org/p/mp_series_words'
                                        }
                                    }, {
                                        'component': 'VAlert',
                                        'props': {
                                            'type': 'info',
                                            'variant': 'tonal',
                                            'text': '番剧：https://etherpad.wikimedia.org/p/mp_anime_words'
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
            "onlyonce": False,
            "flitter": True,
            "cron": '30 4 * * *',
            "file_urls": '',
        }

    def __update_config(self):
        self.update_config({
            "onlyonce": self._onlyonce,
            "cron": self._cron,
            "enable": self._enable,
            "flitter": self._flitter,
            "file_urls": self._file_urls,
        })

    def stop_service(self):
        try:
            if self._scheduler:
                self._scheduler.remove_all_jobs()
                if self._scheduler.running:
                    self._scheduler.shutdown()
                self._scheduler = None
        except Exception as e:
            logger.error("退出插件失败：%s" % str(e))

    def get_page(self) -> List[dict]:
        pass

    def get_state(self) -> bool:
        return self._enable

    @staticmethod
    def get_command() -> List[Dict[str, Any]]:
        pass

    def get_api(self) -> List[Dict[str, Any]]:
        pass
