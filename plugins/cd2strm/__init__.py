import os
import shutil
import threading
import time
from datetime import datetime, timedelta
from time import sleep
from typing import List, Tuple, Dict, Any

import pytz
from apscheduler.schedulers.background import BackgroundScheduler

from app.core.config import settings
from app.core.context import MediaInfo
from app.core.event import eventmanager, Event
from app.core.meta import MetaBase
from app.core.metainfo import MetaInfo
from app.db import get_db
from app.db.models.transferhistory import TransferHistory
from app.db.subscribe_oper import SubscribeOper
from app.db.transferhistory_oper import TransferHistoryOper
from app.log import logger
from app.plugins import _PluginBase
from app.schemas import TransferInfo, Notification, WebhookEventInfo
from app.schemas.types import EventType, MediaType, NotificationType

lock = threading.Lock()


class Cd2Strm(_PluginBase):
    # 插件名称
    plugin_name = "cd2Strm"
    # 插件描述
    plugin_desc = "将新入库的媒体文件，通过cd2上传生成strm（自用）"
    # 插件图标
    plugin_icon = "https://raw.githubusercontent.com/honue/MoviePilot-Plugins/main/icons/clouddrive.png"
    # 插件版本
    plugin_version = "0.0.2"
    # 插件作者
    plugin_author = "honue"
    # 作者主页
    author_url = "https://github.com/honue"
    # 插件配置项ID前缀
    plugin_config_prefix = "Cd2Strm_"
    # 加载顺序
    plugin_order = 1
    # 可使用的用户级别
    auth_level = 1

    _enable = True
    _cron = '20'
    _save_days = '3'
    _onlyonce = False
    _cleanlocal = False

    # 链接前缀
    _local_media_prefix_path = '/strm/'
    # cd2挂载本地媒体库前缀
    _cd_mount_prefix_path = '/CloudNAS/115/emby/'

    _scheduler = None

    _subscribe_oper = SubscribeOper()
    _history_oper = TransferHistoryOper()

    _data_key_waiting_upload = "waiting_upload_list_id"
    _data_key_uploaded = "uploaded_list_id"

    def init_plugin(self, config: dict = None):
        if config:
            self._enable = config.get('enable', False)
            self._cron: int = int(config.get('cron', '20'))
            self._save_days: int = int(config.get('save_days', '3'))
            self._onlyonce = config.get('onlyonce', False)
            self._cleanlocal = config.get('cleanlocal', False)
            self._local_media_prefix_path = config.get('local_media_prefix_path', '/strm/')
            # 用于修改链接
            self._cd_mount_prefix_path = config.get('cd_mount_prefix_path', '/CloudNAS/CloudDrive/115/emby/')

        self.stop_service()

        if not self._enable:
            return

        self._scheduler = BackgroundScheduler(timezone=settings.TZ)

        if self._onlyonce:
            self._scheduler.add_job(func=self.upload_task, trigger='date',
                                    run_date=datetime.now(tz=pytz.timezone(settings.TZ)) + timedelta(seconds=10),
                                    name="cd2转移")
            logger.info(f"立即上传一次")

        if self._cleanlocal:
            self._scheduler.add_job(func=self.del_dest_create_strm_task, kwargs={"now_delete": True}, trigger='date',
                                    run_date=datetime.now(tz=pytz.timezone(settings.TZ)) + timedelta(seconds=3),
                                    name="立即清理本地媒体库文件，创建Strm文件")

        # 媒体文件保留周期？，超过这个时间清理媒体文件，生成strm指向网盘
        self._scheduler.add_job(func=self.del_dest_create_strm_task, trigger='interval', minutes=20,
                                name="清理本地媒体库文件，创建Strm文件")

        if self._scheduler.get_jobs():
            # 启动服务
            self._scheduler.print_jobs()
            self._scheduler.start()

        # 更新配置
        self.update_config({
            'enable': self._enable,
            'cron': self._cron,
            'save_days': self._save_days,
            'onlyonce': False,
            'cleanlocal': False,
            'local_media_prefix_path': self._local_media_prefix_path,
            'cd_mount_prefix_path': self._cd_mount_prefix_path
        })

    @eventmanager.register(EventType.TransferComplete)
    def update_waiting_upload_list(self, event: Event):
        transfer_info: TransferInfo = event.event_data.get('transferinfo', {})
        if not transfer_info.file_list_new:
            return
        # 判断是不是网盘整理的剧,网盘剧跳过
        isCloudFile = False
        for source_file in transfer_info.file_list:
            if self._cd_mount_prefix_path in source_file:
                isCloudFile = True
        with lock:
            for target_file in transfer_info.file_list_new:
                history: TransferHistory = self._history_oper.get_by_dest(dest=target_file)
                logger.info(history.src)
                if isCloudFile:
                    logger.info(f"整理的是网盘文件 {history.src} ，不加入上传列表")
                    self.del_dest_file(history.id)
                    self.create_strm_task(history.id)
                    return
                else:
                    waiting_upload_list_id = self.get_data(self._data_key_waiting_upload) or []
                    waiting_upload_list_id = waiting_upload_list_id.append(history.id)
                    # 去重
                    waiting_upload_list_id = list(dict.fromkeys(waiting_upload_list_id))
                    self.save_data(self._data_key_waiting_upload, waiting_upload_list_id)

        logger.info(f'新入库文件，加入待上传列表 {transfer_info.file_list_new}')

        # 判断段转移任务开始时间 新剧晚点上传 老剧立马上传
        media_info: MediaInfo = event.event_data.get('mediainfo', {})
        meta: MetaBase = event.event_data.get("meta")

        if media_info:
            is_exist = self._subscribe_oper.exists(tmdbid=media_info.tmdb_id, doubanid=media_info.douban_id,
                                                   season=media_info.season)
            if is_exist:
                if not self._scheduler.get_jobs():
                    logger.info(f'追更剧集,{self._cron}分钟后开始执行上传任务...')
                try:
                    for job in self._scheduler.get_jobs():
                        if job.func == self.upload_task:
                            self._scheduler.remove_job(job.id)
                    # 移除其他已有的上传job
                    self._scheduler.add_job(func=self.upload_task, trigger='date',
                                            kwargs={"media_info": media_info, "meta": meta},
                                            run_date=datetime.now(tz=pytz.timezone(settings.TZ)) + timedelta(
                                                minutes=self._cron),
                                            name="cd2上传任务")
                except Exception as err:
                    logger.error(f"定时任务配置错误：{str(err)}")
            else:
                if not self._scheduler.get_jobs():
                    logger.info(f'已完结剧集,立即执行上传任务...')
                # 移除其他已有的上传job
                for job in self._scheduler.get_jobs():
                    if job.func == self.upload_task:
                        self._scheduler.remove_job(job.id)

                self._scheduler.add_job(func=self.upload_task, trigger='date',
                                        run_date=datetime.now(tz=pytz.timezone(settings.TZ)) + timedelta(seconds=5),
                                        name="cd2上传任务")
            self._scheduler.start()

    def upload_task(self, media_info: MediaInfo = None, meta: MetaBase = None):
        with lock:
            waiting_upload_id_list = self.get_data(self._data_key_waiting_upload) or []
            uploaded_id_list = self.get_data(self._data_key_uploaded) or []
            if not waiting_upload_id_list:
                logger.info('没有需要上传的媒体文件')
                return
            logger.info(f'开始执行上传任务 转移记录：{waiting_upload_id_list} ')
            task_list = waiting_upload_id_list.copy()
            total_num = len(waiting_upload_id_list)
            for id in waiting_upload_id_list:
                # 链接目录前缀 替换为 cd2挂载前缀
                history: TransferHistory = self._history_oper.get(id)
                cd2_dest = history.dest.replace(self._local_media_prefix_path, self._cd_mount_prefix_path)
                if self._upload_file(local_source=history.src, cd2_dest=cd2_dest):
                    task_list.remove(id)
                    uploaded_id_list.append(id)
                    logger.info(f'【{total_num - len(task_list)}/{total_num}】 上传成功 {history.src} {cd2_dest}')
                else:
                    logger.error(f'上传失败 {history.src} {cd2_dest}')
                    continue
            logger.info("上传完毕")

            task_list = list(dict.fromkeys(task_list))
            uploaded_id_list = list(dict.fromkeys(uploaded_id_list))

            self.save_data(self._data_key_waiting_upload, task_list)
            self.save_data(self._data_key_uploaded, uploaded_id_list)

    def _upload_file(self, local_source: str = None, cd2_dest: str = None) -> bool:
        logger.info('')
        try:
            cd2_dest_folder, cd2_dest_file_name = os.path.split(cd2_dest)

            if not os.path.exists(cd2_dest_folder):
                os.makedirs(cd2_dest_folder)
                logger.info(f'创建文件夹 {cd2_dest_folder}')

            logger.info(f'源文件路径 {local_source}')
            if self._cd_mount_prefix_path in local_source:
                logger.info(f'源文件 {local_source} 是网盘文件，不上传')
                return True

            if not os.path.exists(cd2_dest):
                # 将文件上传到当前文件夹 同步
                shutil.copy2(local_source, cd2_dest, follow_symlinks=True)
            else:
                logger.info(f'{cd2_dest_file_name} 已存在 {cd2_dest}')
            return True
        except Exception as e:
            logger.error(e)
            return False

    def del_dest_create_strm_task(self, now_delete: bool = False):
        with lock:
            uploaded_id_list = self.get_data(self._data_key_uploaded) or []
            temp_list = uploaded_id_list.copy()
            for id in temp_list:
                history: TransferHistory = self._history_oper.get(id)
                if (datetime.now() - history.date).total_seconds() > self._save_days * 86400:
                    logger.info(f"{history.dest} 超过 {self._save_days} 天, 开始删除本地媒体文件，创建Strm")
                    self.del_dest_file(id)
                    self.create_strm_task(id)
                    continue
                if now_delete:
                    logger.info(f"立即删除本地媒体文件，创建Strm")
                    self.del_dest_file(id)
                    self.create_strm_task(id)

    def del_dest_file(self, id: int):
        try:
            history: TransferHistory = self._history_oper.get(id)
            os.remove(history.dest)
            logger.info(f"清除目标文件 {history.dest}")
        except FileNotFoundError:
            logger.warning(f"无法删除 {history.dest} 目标文件，目标文件不存在")
        except OSError as e:
            logger.error(f"删除 {history.dest} 目标文件失败: {e}")

    def create_strm_task(self, id: int):
        history: TransferHistory = self._history_oper.get(id)
        if self._cd_mount_prefix_path in history.src:
            isCloudFile = True
        # 构造 CloudDrive2 目标路径
        cd2_dest = ""
        if isCloudFile:
            cd2_dest = history.src
        else:
            cd2_dest = history.dest.replace(self._local_media_prefix_path, self._cd_mount_prefix_path)

        strm_file_path = os.path.splitext(history.dest)[0] + '.strm'

        try:
            with open(strm_file_path, "w") as strm_file:
                strm_file.write(cd2_dest)
            logger.info(f"生成strm文件 {strm_file_path} <- 写入 {cd2_dest} ")
        except OSError as e:
            logger.error(f"写入 STRM 文件失败: {e}")

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
                                            'label': '立即上传一次',
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
                                        'component': 'VSwitch',
                                        'props': {
                                            'model': 'cleanlocal',
                                            'label': '立即清理本地媒体文件，生成Strm',
                                        }
                                    }
                                ]
                            }
                        ]
                    }
                    , {
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
                                            'model': 'cron',
                                            'label': '追更剧集入库（分钟）后上传',
                                            'placeholder': '20'
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
                                            'model': 'save_days',
                                            'label': '清理本地媒体库文件任务间隔（天）',
                                            'placeholder': '3'
                                        }
                                    }
                                ]
                            }
                        ]
                    }
                    , {
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
                                            'placeholder': '/strm/'
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
                                            'model': 'cd_mount_prefix_path',
                                            'label': 'cd2挂载媒体库路径前缀',
                                            'placeholder': '/CloudNAS/115/emby/'
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
            'cleanlocal': self._cleanlocal,
            'local_media_prefix_path': self._local_media_prefix_path,
            'cd_mount_prefix_path': self._cd_mount_prefix_path
        }

    def get_api(self) -> List[Dict[str, Any]]:
        return []

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
