import os
import subprocess
import threading
from datetime import datetime, timedelta
from typing import List, Tuple, Dict, Any

import pytz
from apscheduler.schedulers.background import BackgroundScheduler
from p115 import P115Client, P115FileSystem

from app.core.config import settings
from app.core.event import eventmanager, Event
from app.db.subscribe_oper import SubscribeOper
from app.log import logger
from app.plugins import _PluginBase
from app.schemas import TransferInfo, MediaInfo
from app.schemas.types import EventType, SystemConfigKey

lock = threading.Lock()


class Transfer115(_PluginBase):
    # 插件名称
    plugin_name = "115转移"
    # 插件描述
    plugin_desc = "将新入库的媒体文件，转移到115"
    # 插件图标
    plugin_icon = "https://raw.githubusercontent.com/honue/MoviePilot-Plugins/main/icons/clouddrive.png"
    # 插件版本
    plugin_version = "0.1.1"
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
    # 软链接前缀
    _softlink_prefix_path = '/softlink/'
    # 115网盘媒体库路径前缀
    _p115_media_prefix_path = '/emby/'
    # cd2挂载本地媒体库前缀
    _cd_mount_prefix_path = '/CloudNAS/CloudDrive/115/emby/'

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
            self._softlink_prefix_path = config.get('softlink_prefix_path', '/downloads/link/')
            self._p115_media_prefix_path = config.get('p115_media_prefix_path', '/emby/')
            self._cd_mount_prefix_path = config.get('cd_mount_prefix_path', '/CloudNAS/CloudDrive/115/emby/')

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
                'softlink_prefix_path': self._softlink_prefix_path,
                'cd_mount_prefix_path': self._cd_mount_prefix_path
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
            # 等待转移的文件的软链接的完整路径
            waiting_process_list = self.get_data('waiting_process_list') or []
            waiting_process_list = waiting_process_list + transfer_info.file_list_new
            self.save_data('waiting_process_list', waiting_process_list)

        logger.info(f'新入库，加入待转移列表 {transfer_info.file_list_new}')

        # 判断段转移任务开始时间 新剧晚点上传 老剧立马上传
        media_info: MediaInfo = event.event_data.get('mediainfo', {})
        if media_info:
            is_exist = self._subscribe_oper.exists(tmdbid=media_info.tmdb_id, doubanid=media_info.douban_id,
                                                   season=media_info.season)
            if is_exist:
                if not self._scheduler.get_jobs():
                    logger.info(f'追更剧集,{self._cron}分钟后开始执行任务...')
                try:
                    self._scheduler.remove_all_jobs()
                    self._scheduler.add_job(func=self.task, trigger='date',
                                            run_date=datetime.now(tz=pytz.timezone(settings.TZ)) + timedelta(
                                                minutes=self._cron),
                                            name="转移115")
                except Exception as err:
                    logger.error(f"定时任务配置错误：{str(err)}")
            else:
                if not self._scheduler.get_jobs():
                    logger.info(f'已完结剧集,立即执行上传任务...')
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
            for softlink_source in waiting_process_list:
                # 软链接目录前缀 替换为 115网盘 目录前缀  这个文件的115保存路径
                p115_dest = softlink_source.replace(self._softlink_prefix_path, self._p115_media_prefix_path)
                if self._upload_file(softlink_source=softlink_source, p115_dest=p115_dest):
                    process_list.remove(softlink_source)
                    logger.info(f'【{total_num - len(process_list)}/{total_num}】 上传成功 {softlink_source} {p115_dest}')
                    # 上传成功 软链接 更改为 clouddrive2 挂载的路径
                    cd2_dest = p115_dest.replace(self._p115_media_prefix_path, self._cd_mount_prefix_path)
                    softlink_dir = os.path.dirname(softlink_source)
                    if not os.path.exists(softlink_dir):
                        logger.info(f'软链接文件夹不存在 创建文件夹 {softlink_dir}')
                        os.makedirs(softlink_dir)
                    subprocess.run(['ln', '-sf', cd2_dest, softlink_source])
                    logger.info(f'更新软链接: {softlink_source} -> 云盘文件: {cd2_dest}')
                else:
                    logger.error(f'上传失败 {softlink_source} {p115_dest}')
                self.save_data('waiting_process_list', process_list)

    def _upload_file(self, softlink_source: str = None, p115_dest: str = None) -> bool:
        logger.info('')
        try:
            p115_dest_folder, p115_dest_file_name = os.path.split(p115_dest)

            if not self._client.exists(p115_dest_folder):
                self._client.makedirs(p115_dest_folder)
                logger.info(f'创建文件夹 {p115_dest_folder}')

            # 将本地媒体库文件上传
            # 获取软链接的真实文件路径 用于上传
            real_source = os.readlink(softlink_source)
            real_source_folder, real_source_file_name = os.path.split(real_source)
            logger.info(f'源文件路径 {real_source}')
            if not self._client.exists(p115_dest):
                # 将文件上传到当前文件夹
                self._client.chdir(p115_dest_folder)
                self._client.upload(real_source)
                # 将种子名重命名为媒体名
                self._client.rename(p115_dest_folder + '/' + real_source_file_name, p115_dest)
            else:
                logger.info(f'{p115_dest_file_name} 已存在')
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
                                    'md': 4
                                },
                                'content': [
                                    {
                                        'component': 'VTextField',
                                        'props': {
                                            'model': 'softlink_prefix_path',
                                            'label': '本地软链接媒体库路径前缀',
                                            'placeholder': '/softlink/'
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
                                            'model': 'p115_media_prefix_path',
                                            'label': '115媒体库路径前缀',
                                            'placeholder': '/emby/'
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
                                            'model': 'cd_mount_prefix_path',
                                            'label': 'cd2挂载媒体库路径前缀',
                                            'placeholder': '/CloudNAS/CloudDrive/115/emby/'
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
            'softlink_prefix_path': self._softlink_prefix_path,
            'cd_mount_prefix_path': self._cd_mount_prefix_path
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
            # 更新插件 cookie
            self._cookie = cookie
            self.update_config({
                'enable': self._enable,
                'cron': self._cron,
                'onlyonce': False,
                'cookie': self._cookie,
                'p115_media_prefix_path': self._p115_media_prefix_path,
                'softlink_prefix_path': self._softlink_prefix_path,
                'cd_mount_prefix_path': self._cd_mount_prefix_path
            })

            # 更新mp 115 cookie
            parts = cookie.split(';')
            cookie_data = {}
            for part in parts:
                if '=' in part:
                    key, value = part.split('=', 1)
                    cookie_data[key] = value
            self.systemconfig.set(SystemConfigKey.User115Params, cookie_data)

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
