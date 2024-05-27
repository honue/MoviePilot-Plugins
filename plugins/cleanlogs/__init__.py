from datetime import datetime, timedelta

import pytz
from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.cron import CronTrigger

from pathlib import Path
from typing import List, Tuple, Dict, Any, Optional

from app.utils.string import StringUtils
from app.helper.plugin import PluginHelper
from app.core.config import settings
from app.core.plugin import PluginManager
from app.db.systemconfig_oper import SystemConfigOper
from app.log import logger
from app.plugins import _PluginBase
from app.schemas.types import SystemConfigKey


class CleanLogs(_PluginBase):
    # 插件名称
    plugin_name = "插件日志清理"
    # 插件描述
    plugin_desc = "定时清理插件产生的日志"
    # 插件图标
    plugin_icon = "clean.png"
    # 插件版本
    plugin_version = "1.1"
    # 插件作者
    plugin_author = "honue"
    # 作者主页
    author_url = "https://github.com/honue"
    # 插件配置项ID前缀
    plugin_config_prefix = "cleanlogs_"
    # 加载顺序
    plugin_order = 50
    # 可使用的用户级别
    auth_level = 1

    _enable = False
    _onlyonce = False
    _cron = '30 3 * * *'
    _selected_ids: List[str] = []
    _rows = 300

    # 定时器
    _scheduler: Optional[BackgroundScheduler] = None

    def init_plugin(self, config: dict = None):
        # 停止现有任务
        self.stop_service()

        if config:
            self._enable = config.get('enable', False)
            self._selected_ids = config.get('selected_ids', [])
            self._rows = int(config.get('rows', 300))
            self._onlyonce = config.get('onlyonce', False)
            self._cron = config.get('cron', '30 3 * * *')

        # 定时服务
        self._scheduler = BackgroundScheduler(timezone=settings.TZ)

        if self._onlyonce:
            self._onlyonce = False
            self.update_config({
                "onlyonce": self._onlyonce,
                "rows": self._rows,
                "enable": self._enable,
                "selected_ids": self._selected_ids,
                "cron": self._cron,
            })
            self._scheduler.add_job(func=self._task, trigger='date',
                                    run_date=datetime.now(tz=pytz.timezone(settings.TZ)) + timedelta(seconds=2),
                                    name="插件日志清理")
        if self._enable and self._cron:
            try:
                self._scheduler.add_job(func=self._task,
                                        trigger=CronTrigger.from_crontab(self._cron),
                                        name="插件日志清理")
            except Exception as err:
                logger.error(f"插件日志清理, 定时任务配置错误：{str(err)}")

        # 启动任务
        if self._scheduler.get_jobs():
            self._scheduler.print_jobs()
            self._scheduler.start()

    def _task(self):
        clean_plugin = self._selected_ids[:]

        if not clean_plugin:
            local_plugins = PluginManager().get_local_plugins()
            for plugin in local_plugins:
                clean_plugin.append(plugin.id)

        for plugin_id in clean_plugin:
            log_path = settings.LOG_PATH / Path("plugins") / f"{plugin_id.lower()}.log"
            if not log_path.exists():
                logger.debug(f"{plugin_id} 日志文件不存在")
                continue

            with open(log_path, 'r', encoding='utf-8') as file:
                lines = file.readlines()

            if self._rows == 0:
                top_lines = []
            else:
                top_lines = lines[-min(self._rows, len(lines)):]

            with open(log_path, 'w', encoding='utf-8') as file:
                file.writelines(top_lines)

            if (len(lines) - self._rows) > 0:
                logger.info(f"已清理 {plugin_id} {len(lines) - self._rows} 行日志")

    def get_form(self) -> Tuple[List[dict], Dict[str, Any]]:
        # 已安装插件
        local_plugins = self.get_local_plugins()
        # 编历 local_plugins，生成插件类型选项
        plugin_options = []

        for plugin_id in list(local_plugins.keys()):
            local_plugin = local_plugins.get(plugin_id)
            plugin_options.append({
                "title": f"{local_plugin.get('plugin_name')} v{local_plugin.get('plugin_version')}",
                "value": local_plugin.get("id")
            })

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
                            },
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
                                            'model': 'onlyonce',
                                            'label': '立即运行一次',
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
                                            'model': 'cron',
                                            'label': '定时删除日志',
                                            'placeholder': '5位cron表达式'
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
                                            'model': 'rows',
                                            'label': '保留Top行数',
                                            'placeholder': '300'
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
                                        'component': 'VSelect',
                                        'props': {
                                            'multiple': True,
                                            'chips': True,
                                            'model': 'selected_ids',
                                            'label': '删除插件日志,不指定默认全选',
                                            'items': plugin_options
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
                                            'text': '谢谢t佬的指点。'
                                        }
                                    }
                                ]
                            }
                        ]
                    }
                ]
            }
        ], {
            "enable": self._enable,
            "onlyonce": self._onlyonce,
            "rows": self._rows,
            "cron": self._cron,
            "selected_ids": [],
        }

    @staticmethod
    def get_local_plugins():
        """
        获取本地插件
        """
        # 已安装插件
        install_plugins = SystemConfigOper().get(SystemConfigKey.UserInstalledPlugins) or []

        local_plugins = {}
        # 线上插件列表
        markets = settings.PLUGIN_MARKET.split(",")
        for market in markets:
            online_plugins = PluginHelper().get_plugins(market) or {}
            for pid, plugin in online_plugins.items():
                if pid in install_plugins:
                    local_plugin = local_plugins.get(pid)
                    if local_plugin:
                        if StringUtils.compare_version(local_plugin.get("plugin_version"), plugin.get("version")) < 0:
                            local_plugins[pid] = {
                                "id": pid,
                                "plugin_name": plugin.get("name"),
                                "repo_url": market,
                                "plugin_version": plugin.get("version")
                            }
                    else:
                        local_plugins[pid] = {
                            "id": pid,
                            "plugin_name": plugin.get("name"),
                            "repo_url": market,
                            "plugin_version": plugin.get("version")
                        }

        return local_plugins

    def get_state(self) -> bool:
        return self._enable

    @staticmethod
    def get_command() -> List[Dict[str, Any]]:
        pass

    def get_api(self) -> List[Dict[str, Any]]:
        pass

    def get_page(self) -> List[dict]:
        pass

    def stop_service(self):
        pass
