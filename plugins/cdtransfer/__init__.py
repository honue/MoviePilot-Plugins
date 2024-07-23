import os
import threading
from datetime import datetime, timedelta

from typing import List, Tuple, Dict, Any

import pytz
from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.cron import CronTrigger

from app.core.config import settings
from app.core.event import eventmanager, Event
from app.log import logger
from app.plugins import _PluginBase
from app.schemas import TransferInfo
from app.schemas.types import EventType
from clouddrive import CloudDriveClient, CloudDriveFileSystem

lock = threading.Lock()


class CDTransfer(_PluginBase):
    # 插件名称
    plugin_name = "clouddrive转移"
    # 插件描述
    plugin_desc = "将新入库的媒体文件，使用cd2转移到网盘"
    # 插件图标
    plugin_icon = "https://raw.githubusercontent.com/honue/MoviePilot-Plugins/main/icons/clouddrive.png"
    # 插件版本
    plugin_version = "0.1.2"
    # 插件作者
    plugin_author = "honue"
    # 作者主页
    author_url = "https://github.com/honue"
    # 插件配置项ID前缀
    plugin_config_prefix = "cdtransfer_"
    # 加载顺序
    plugin_order = 20
    # 可使用的用户级别
    auth_level = 1

    _enable = True
    _cron = '*/30 * * * *'
    _onlyonce = False
    # cd网盘媒体库路径前缀
    _cd_media_prefix_path = '/115/emby/'
    # 本地媒体库路径前缀
    _local_media_prefix_path = '/downloads/link/'

    _server = ''
    _username = ''
    _password = ''
    _client = None
    _fs = None

    _scheduler = None

    def init_plugin(self, config: dict = None):
        if config:
            self._enable = config.get('enable', False)
            self._cron = config.get('cron', '*/30 * * * *')
            self._onlyonce = config.get('onlyonce', False)
            self._server = config.get('server', '')
            self._username = config.get('username', '')
            self._password = config.get('password', '')
            self._cd_media_prefix_path = config.get('cd_media_prefix_path', '/115/emby/')
            self._local_media_prefix_path = config.get('local_media_prefix_path', '/downloads/link/')

        self.stop_service()

        if not self._enable:
            return

        if self._server and self._username and self._password:
            self._client = CloudDriveClient(origin=self._server, username=self._username, password=self._password)
            self._fs = CloudDriveFileSystem(self._client)
        else:
            logger.error(f'请检查cd配置填写')
            self._enable = False
            return

        self._scheduler = BackgroundScheduler(timezone=settings.TZ)

        if self._enable and self._cron:
            try:
                self._scheduler.add_job(func=self.task,
                                        trigger=CronTrigger.from_crontab(self._cron),
                                        name="clouddrive转移")
                logger.info(f'clouddrive转移定时任务创建成功：{self._cron}')
            except Exception as err:
                logger.error(f"定时任务配置错误：{str(err)}")

        if self._onlyonce:
            self._scheduler.add_job(func=self.task, trigger='date',
                                    run_date=datetime.now(tz=pytz.timezone(settings.TZ)) + timedelta(seconds=3),
                                    name="clouddrive转移")
            logger.info(f"clouddrive转移，立即运行一次")

            self.update_config({
                'enable': self._enable,
                'cron': self._cron,
                'onlyonce': False,
                'server': self._server,
                'username': self._username,
                'password': self._password,
                'cd_media_prefix_path': self._cd_media_prefix_path,
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
        logger.info(f'{transfer_info.file_list_new} 加入待转移列表')

    def task(self):
        with lock:
            waiting_process_list = self.get_data('waiting_process_list') or []
            logger.debug(f'开始执行上传任务 {waiting_process_list}')
            process_list = waiting_process_list.copy()
            for file in waiting_process_list:
                process_list.remove(file) if self._upload_file(file) else None
                self.save_data('waiting_process_list', process_list)
                logger.info(f'待上传文件数: {len(process_list)}')

    def _upload_file(self, file_path: str = None):
        try:
            # /downloads/link/series/日韩剧/财阀X刑警 (2024)/Season 1/财阀X刑警 - S01E12 - 第 12 集.mkv
            # /115/emby/series/日韩剧/财阀X刑警 (2024)/Season 1/财阀X刑警 - S01E12 - 第 12 集.mkv
            dest_path = file_path.replace(self._local_media_prefix_path, self._cd_media_prefix_path)
            # folder /115/emby/series/日韩剧/财阀X刑警 (2024)/Season 1/  file_name 财阀X刑警 - S01E12 - 第 12 集.mkv
            folder, file_name = os.path.split(dest_path)
            if not self._fs.exists(folder):
                self._fs.makedirs(folder)
                logger.info(f'创建文件夹 {folder}')
            self._fs.chdir(folder)
            # 将本地媒体库文件上传
            self._fs.upload(file_path)
            logger.info(f'成功上传 {file_name} 至 {dest_path}')
            return True
        except Exception as e:
            logger.error(f'上传失败 {file_path}')
            logger.error(e)
            return False

    def get_state(self) -> bool:
        return self._enable

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
                                            'label': '定时上传任务周期',
                                            'placeholder': '*/30 * * * *'
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
                                            'model': 'server',
                                            'label': 'cd2地址',
                                            'placeholder': 'http://192.168.33.100:19798'
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
                                            'model': 'username',
                                            'label': 'cd2用户名',
                                            'placeholder': 'honue@email.com'
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
                                            'model': 'password',
                                            'label': 'cd2密码',
                                            'placeholder': 'password'
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
                                            'model': 'cd_media_prefix_path',
                                            'label': 'cd媒体库路径前缀',
                                            'placeholder': '/115/emby/'
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
            'server': self._server,
            'username': self._username,
            'password': self._password,
            'cd_media_prefix_path': self._cd_media_prefix_path,
            'local_media_prefix_path': self._local_media_prefix_path
        }

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
