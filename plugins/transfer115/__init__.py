import os
import subprocess
import threading
from datetime import datetime, timedelta

from typing import List, Tuple, Dict, Any

import pytz
from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.cron import CronTrigger
from p115 import P115Client, P115FileSystem

from app.core.config import settings
from app.core.event import eventmanager, Event
from app.db.subscribe_oper import SubscribeOper
from app.log import logger
from app.plugins import _PluginBase
from app.schemas import TransferInfo, MediaInfo
from app.schemas.types import EventType

lock = threading.Lock()


class Transfer115(_PluginBase):
    # 插件名称
    plugin_name = "115转移"
    # 插件描述
    plugin_desc = "将新入库的媒体文件，转移到115"
    # 插件图标
    plugin_icon = "https://raw.githubusercontent.com/honue/MoviePilot-Plugins/main/icons/clouddrive.png"
    # 插件版本
    plugin_version = "0.0.10"
    # 插件作者
    plugin_author = "honue"
    # 作者主页
    author_url = "https://github.com/honue"
    # 插件配置项ID前缀
    plugin_config_prefix = "transfer115_"
    # 加载顺序
    plugin_order = 19
    # 可使用的用户级别
    auth_level = 3

    _enable = True
    _cron = '20'
    _onlyonce = False
    # 115网盘媒体库路径前缀
    _p115_media_prefix_path = '/emby/'
    # 本地媒体库路径前缀
    _local_media_prefix_path = '/downloads/link/'
    # 软链接前缀
    _softlink_prefix_path = '/softlink/'

    _server = ''
    _username = ''
    _password = ''
    _cookie = ''

    _client = None
    _fs = None

    _scheduler = None

    _subscribe_oper = SubscribeOper()

    def init_plugin(self, config: dict = None):
        if config:
            self._enable = config.get('enable', False)
            self._cron: int = int(config.get('cron', '20'))
            self._onlyonce = config.get('onlyonce', False)
            self._cookie = config.get('cookie', '')
            self._p115_media_prefix_path = config.get('p115_media_prefix_path', '/emby/')
            self._local_media_prefix_path = config.get('local_media_prefix_path', '/downloads/link/')

        self.stop_service()

        if not self._enable:
            return

        if self._cookie:
            self._client = P115Client(self._cookie)
            self._fs = P115FileSystem(self._client)
        else:
            logger.error(f'请检查填写cookie')
            self._enable = False
            return

        self._scheduler = BackgroundScheduler(timezone=settings.TZ)

        if self._onlyonce:
            self._scheduler.add_job(func=self.task, trigger='date',
                                    run_date=datetime.now(tz=pytz.timezone(settings.TZ)) + timedelta(seconds=3),
                                    name="转移115")
            logger.info(f"115转移，立即运行一次")

            self.update_config({
                'enable': self._enable,
                'cron': self._cron,
                'onlyonce': False,
                'cookie': self._cookie,
                'p115_media_prefix_path': self._p115_media_prefix_path,
                'local_media_prefix_path': self._local_media_prefix_path
            })

        if self._scheduler.get_jobs():
            # 启动服务
            self._scheduler.print_jobs()
            self._scheduler.start()

    @eventmanager.register(EventType.TransferComplete)
    def update_waiting_list(self, event: Event):
        transfer_info: TransferInfo = event.event_data.get('transferinfo', {})
        if not transfer_info.file_list_new:
            return
        with lock:
            waiting_process_list = self.get_data('waiting_process_list') or []
            waiting_process_list = waiting_process_list + transfer_info.file_list_new
            self.save_data('waiting_process_list', waiting_process_list)

        # 上传前先创建 链接本地媒体文件的软链接 以便扫库 保障可观看
        for local_file in transfer_info.file_list_new:
            softlink_file = local_file.replace(self._local_media_prefix_path, self._softlink_prefix_path)
            subprocess.run(['ln', '-sf', local_file, softlink_file])
            logger.info(f'创建软链接{softlink_file} -> 本地文件{local_file}')

        logger.info(f'新入库，加入待转移列表 {transfer_info.file_list_new}')

        media_info: MediaInfo = event.event_data.get('mediainfo', {})
        if media_info:
            is_exist = self._subscribe_oper.exists(tmdbid=media_info.tmdb_id, doubanid=media_info.douban_id,
                                                   season=media_info.season)
            if is_exist:
                logger.info(f'追更剧集,{self._cron}分钟后再上传...')
                try:
                    self._scheduler.remove_all_jobs()
                    self._scheduler.add_job(func=self.task, trigger='date',
                                            run_date=datetime.now(tz=pytz.timezone(settings.TZ)) + timedelta(
                                                minutes=self._cron),
                                            name="转移115")
                except Exception as err:
                    logger.error(f"定时任务配置错误：{str(err)}")
            else:
                logger.info(f'已完结剧集,立即上传...')
                self._scheduler.remove_all_jobs()
                self._scheduler.add_job(func=self.task, trigger='date',
                                        run_date=datetime.now(tz=pytz.timezone(settings.TZ)) + timedelta(seconds=5),
                                        name="转移115")
            self._scheduler.start()

    def task(self):
        with (lock):
            waiting_process_list = self.get_data('waiting_process_list') or []
            if not waiting_process_list:
                logger.info('没有需要转移的媒体文件')
                return
            logger.info(f'开始执行上传任务 {waiting_process_list}')
            process_list = waiting_process_list.copy()
            total_num = len(waiting_process_list)
            for file in waiting_process_list:
                dest_file = file.replace(self._local_media_prefix_path, self._p115_media_prefix_path)
                if self._upload_file(file):
                    process_list.remove(file)
                    logger.info(f'上传成功 {total_num - len(process_list)}/{total_num} {dest_file}')
                    # 上传成功 创建软链接 指向路径 前缀无所谓 可以通过emby2alist 替换
                    softlink_file = dest_file.replace(self._p115_media_prefix_path, self._softlink_prefix_path)
                    subprocess.run(['ln', '-sf', '/CloudNAS/CloudDrive/115' + dest_file, softlink_file])
                    logger.info(f'创建软链接{softlink_file} -> 云盘文件{dest_file}')
                else:
                    logger.error(f'上传失败 {dest_file}')
                self.save_data('waiting_process_list', process_list)

    def _upload_file(self, file_path: str = None):
        try:
            # /downloads/link/series/日韩剧/财阀X刑警 (2024)/Season 1/财阀X刑警 - S01E12 - 第 12 集.mkv
            # /emby/series/日韩剧/财阀X刑警 (2024)/Season 1/财阀X刑警 - S01E12 - 第 12 集.mkv
            dest_path = file_path.replace(self._local_media_prefix_path, self._p115_media_prefix_path)
            # folder /emby/series/日韩剧/财阀X刑警 (2024)/Season 1  file_name 财阀X刑警 - S01E12 - 第 12 集.mkv
            folder, file_name = os.path.split(dest_path)
            if not self._fs.exists(folder):
                self._fs.makedirs(folder)
                logger.info(f'创建文件夹 {folder}')
            self._fs.chdir(folder)
            # 将本地媒体库文件上传
            if not self._fs.exists(dest_path):
                self._fs.upload(file_path)
            else:
                logger.info(f'{file_name} 已存在')
            return True
        except Exception as e:
            logger.error(e)
            return False

    def get_state(self) -> bool:
        return self._enable

    @staticmethod
    def get_command() -> List[Dict[str, Any]]:
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
                                            'model': 'onlyonce',
                                            'label': '立即运行一次',
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
                                            'model': 'cron',
                                            'label': '追更剧集入库（分钟）后上传',
                                            'placeholder': '20'
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
                                        'component': 'VTextField',
                                        'props': {
                                            'model': 'cookie',
                                            'label': '115 cookie',
                                            'placeholder': "UID=...;CID=...;SEID=..."
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
                                            'model': 'local_media_prefix_path',
                                            'label': '本地媒体库路径前缀',
                                            'placeholder': '/downloads/link/'
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
                                            'model': 'p115_media_prefix_path',
                                            'label': '115媒体库路径前缀',
                                            'placeholder': '/emby/'
                                        }
                                    }
                                ]
                            }
                        ]
                    }
                ]
            }
        ], {
            'enable': self._enable,
            'cron': self._cron,
            'onlyonce': self._onlyonce,
            'cookie': self._cookie,
            'p115_media_prefix_path': self._p115_media_prefix_path,
            'local_media_prefix_path': self._local_media_prefix_path
        }

    def get_api(self) -> List[Dict[str, Any]]:
        return [
            {
                "path": "/update_cookie",
                "endpoint": self.update_cookie,
                "methods": ["GET", "POST"],
                "summary": "更新115cookie",
                "description": "更新115cookie",
            }
        ]

    def update_cookie(self, cookie, plugin_key):
        if settings.API_TOKEN != plugin_key:
            logger.error(f"plugin_key错误：{plugin_key}")
            return f"plugin_key错误：{plugin_key}"
        else:
            self._cookie = cookie
            self.update_config({
                'enable': self._enable,
                'cron': self._cron,
                'onlyonce': False,
                'cookie': self._cookie,
                'p115_media_prefix_path': self._p115_media_prefix_path,
                'local_media_prefix_path': self._local_media_prefix_path
            })
            return "更新115cookie成功"

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
