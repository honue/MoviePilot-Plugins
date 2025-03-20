import os
import shutil
import threading
import time
from datetime import datetime, timedelta
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
from app.log import logger
from app.plugins import _PluginBase
from app.schemas import TransferInfo, Notification, WebhookEventInfo
from app.schemas.types import EventType, MediaType, NotificationType

lock = threading.Lock()


class Cd2Upload(_PluginBase):
    # 插件名称
    plugin_name = "cd2上传"
    # 插件描述
    plugin_desc = "将新入库的媒体文件，通过cd2上传生成strm（自用）"
    # 插件图标
    plugin_icon = "https://raw.githubusercontent.com/honue/MoviePilot-Plugins/main/icons/clouddrive.png"
    # 插件版本
    plugin_version = "0.0.9"
    # 插件作者
    plugin_author = "honue"
    # 作者主页
    author_url = "https://github.com/honue"
    # 插件配置项ID前缀
    plugin_config_prefix = "cd2upload_"
    # 加载顺序
    plugin_order = 1
    # 可使用的用户级别
    auth_level = 3

    _enable = True
    _cron = '20'
    _onlyonce = False
    _cleanlink = False

    # 链接前缀
    _softlink_prefix_path = '/strm/'
    # cd2挂载本地媒体库前缀
    _cd_mount_prefix_path = '/CloudNAS/115/emby/'

    _scheduler = None

    _subscribe_oper = SubscribeOper()

    def init_plugin(self, config: dict = None):
        if config:
            self._enable = config.get('enable', False)
            self._cron: int = int(config.get('cron', '20'))
            self._onlyonce = config.get('onlyonce', False)
            self._cleanlink = config.get('cleanlink', False)
            self._cookie = config.get('cookie', '')
            self._softlink_prefix_path = config.get('softlink_prefix_path', '/strm/')
            # 用于修改链接
            self._cd_mount_prefix_path = config.get('cd_mount_prefix_path', '/CloudNAS/CloudDrive/115/emby/')

        self.stop_service()

        if not self._enable:
            return

        # 待定
        file_num = int(os.getenv('FULL_RECENT', '0')) if os.getenv('FULL_RECENT', '0').isdigit() else 0
        if file_num:
            recent_files = [transfer_history.dest for transfer_history in
                            TransferHistory.list_by_page(count=file_num, db=get_db())]
            logger.info(f"补全 {len(recent_files)} \n {recent_files}")
            with lock:
                # 等待转移的文件的链接的完整路径
                waiting_process_list = self.get_data('waiting_process_list') or []
                waiting_process_list = waiting_process_list + recent_files
                self.save_data('waiting_process_list', waiting_process_list)

        self._scheduler = BackgroundScheduler(timezone=settings.TZ)

        if self._onlyonce:
            self._scheduler.add_job(func=self.task, trigger='date',
                                    run_date=datetime.now(tz=pytz.timezone(settings.TZ)) + timedelta(seconds=10),
                                    name="cd2转移")
            logger.info(f"cd2转移，立即运行一次")

        if self._cleanlink:
            # 清理无效软链接
            self._scheduler.add_job(func=self.clean, kwargs={"cleanlink": True}, trigger='date',
                                    run_date=datetime.now(tz=pytz.timezone(settings.TZ)) + timedelta(seconds=3),
                                    name="清理无效软链接")

        self._scheduler.add_job(func=self.clean, kwargs={"cleanlink": False}, trigger='interval', minutes=20,
                                name="清理无效软链接")

        if self._scheduler.get_jobs():
            # 启动服务
            self._scheduler.print_jobs()
            self._scheduler.start()

        # 更新配置
        self.update_config({
            'enable': self._enable,
            'cron': self._cron,
            'onlyonce': False,
            'cleanlink': False,
            'softlink_prefix_path': self._softlink_prefix_path,
            'cd_mount_prefix_path': self._cd_mount_prefix_path
        })

    @eventmanager.register(EventType.TransferComplete)
    def update_waiting_list(self, event: Event):
        transfer_info: TransferInfo = event.event_data.get('transferinfo', {})
        if not transfer_info.file_list_new:
            return
        with lock:
            # 等待转移的文件的链接的完整路径
            waiting_process_list = self.get_data('waiting_process_list') or []
            waiting_process_list = waiting_process_list + transfer_info.file_list_new
            self.save_data('waiting_process_list', waiting_process_list)

        logger.info(f'新入库，加入待转移列表 {transfer_info.file_list_new}')

        # 判断段转移任务开始时间 新剧晚点上传 老剧立马上传
        media_info: MediaInfo = event.event_data.get('mediainfo', {})
        meta: MetaBase = event.event_data.get("meta")

        if media_info:
            is_exist = self._subscribe_oper.exists(tmdbid=media_info.tmdb_id, doubanid=media_info.douban_id,
                                                   season=media_info.season)
            if is_exist:
                if not self._scheduler.get_jobs():
                    logger.info(f'追更剧集,{self._cron}分钟后开始执行任务...')
                try:
                    self._scheduler.remove_all_jobs()
                    self._scheduler.add_job(func=self.task, trigger='date',
                                            kwargs={"media_info": media_info, "meta": meta},
                                            run_date=datetime.now(tz=pytz.timezone(settings.TZ)) + timedelta(
                                                minutes=self._cron),
                                            name="cd2转移")
                except Exception as err:
                    logger.error(f"定时任务配置错误：{str(err)}")
            else:
                if not self._scheduler.get_jobs():
                    logger.info(f'已完结剧集,立即执行上传任务...')
                self._scheduler.remove_all_jobs()
                self._scheduler.add_job(func=self.task, trigger='date',
                                        run_date=datetime.now(tz=pytz.timezone(settings.TZ)) + timedelta(seconds=5),
                                        name="cd2转移")
            self._scheduler.start()

    def task(self, media_info: MediaInfo = None, meta: MetaBase = None):
        start_time = time.time()
        with (lock):
            waiting_process_list = self.get_data('waiting_process_list') or []
            processed_list = self.get_data('processed_list') or []

            if not waiting_process_list:
                logger.info('没有需要转移的媒体文件')
                return
            logger.info('strm文件将在源文件被清理后生成 软链接符号将被替换 strm和链接符号只会存在一个')
            logger.info(f'开始执行上传任务 {waiting_process_list} ')
            process_list = waiting_process_list.copy()
            total_num = len(waiting_process_list)
            for softlink_source in waiting_process_list:
                # 链接目录前缀 替换为 cd2挂载前缀
                cd2_dest = softlink_source.replace(self._softlink_prefix_path, self._cd_mount_prefix_path)
                if self._upload_file(softlink_source=softlink_source, cd2_dest=cd2_dest):
                    process_list.remove(softlink_source)
                    processed_list.append(softlink_source)
                    logger.info(f'【{total_num - len(process_list)}/{total_num}】 上传成功 {softlink_source} {cd2_dest}')
                else:
                    logger.error(f'上传失败 {softlink_source} {cd2_dest}')
                    continue
            logger.info("上传完毕，STRM文件将在链接文件失效后生成")
            self.save_data('waiting_process_list', process_list)
            self.save_data('processed_list', processed_list)
            end_time = time.time()

            favor: Dict = self.get_data('favor') or {}
            tmdb_id = str(media_info.tmdb_id)

            if media_info and favor.get(tmdb_id) and media_info.type == MediaType.TV:
                self.chain.post_message(Notification(
                    mtype=NotificationType.Plugin,
                    title=f"{media_info.title_year} {meta.episodes}",
                    text=f"上传成功 用时{int(end_time - start_time)}秒",
                    image=media_info.get_message_image()))

    def _upload_file(self, softlink_source: str = None, cd2_dest: str = None) -> bool:
        logger.info('')
        try:
            cd2_dest_folder, cd2_dest_file_name = os.path.split(cd2_dest)

            if not os.path.exists(cd2_dest_folder):
                os.makedirs(cd2_dest_folder)
                logger.info(f'创建文件夹 {cd2_dest_folder}')

            real_source = os.readlink(softlink_source)
            logger.info(f'源文件路径 {real_source}')

            if not os.path.exists(cd2_dest):
                # 将文件上传到当前文件夹 同步
                shutil.copy2(softlink_source, cd2_dest, follow_symlinks=True)
            else:
                logger.info(f'{cd2_dest_file_name} 已存在 {cd2_dest}')
            return True
        except Exception as e:
            logger.error(e)
            return False

    def clean(self, cleanlink: bool = False):
        with lock:
            waiting_process_list = self.get_data('processed_list') or []
            processed_list = waiting_process_list.copy()
            logger.info(f"已处理列表：{processed_list}")
            logger.debug(f"cleanlink {cleanlink}")

            for file in waiting_process_list:
                if not os.path.islink(file):
                    processed_list.remove(file)
                    logger.info(f"软链接符号不存在 {file}")
                    continue
                if cleanlink and os.path.islink(file):
                    try:
                        target_file = os.readlink(file)
                        os.remove(target_file)
                        logger.info(f"清除源文件 {target_file}")
                    except FileNotFoundError:
                        logger.warning(f"无法删除 {file} 指向的目标文件，目标文件不存在")
                    except OSError as e:
                        logger.error(f"删除 {file} 目标文件失败: {e}")

                if os.path.islink(file) and not os.path.exists(file):
                    os.remove(file)
                    processed_list.remove(file)
                    logger.info(f"删除本地链接文件 {file}")

                    # 构造 CloudDrive2 目标路径
                    cd2_dest = file.replace(self._softlink_prefix_path, self._cd_mount_prefix_path)
                    strm_file_path = os.path.splitext(file)[0] + '.strm'

                    try:
                        with open(strm_file_path, "w") as strm_file:
                            strm_file.write(cd2_dest)
                        logger.info(f"{cd2_dest} 写入STRM文件 -> {strm_file_path}")
                    except OSError as e:
                        logger.error(f"写入 STRM 文件失败: {e}")

                else:
                    logger.debug(f"{file} 未失效，跳过")

            self.save_data('processed_list', processed_list)

    @eventmanager.register(EventType.WebhookMessage)
    def record_favor(self, event: Event):
        """
        记录favor剧集
        event='item.rate' channel='emby' item_type='TV' item_name='幽游白书' item_id=None item_path='/media/series/日韩剧/幽游白书 (2023)' season_id=None episode_id=None tmdb_id='121659' overview='该剧改编自富坚义博的同名漫画。讲述叛逆少年浦饭幽助（北村匠海 饰）为了救小孩不幸车祸身亡，没想到因此获得重生机会并成为灵界侦探，展开一段不可思议的人生。' percentage=None ip=None device_name=None client=None user_name='honue' image_url=None item_favorite=None save_reason=None item_isvirtual=None media_type='Series'
        """
        event_info: WebhookEventInfo = event.event_data
        # 只处理剧集喜爱
        if event_info.event != "item.rate" or event_info.item_type != "TV":
            return
        if event_info.channel != "emby":
            logger.info("目前只支持Emby服务端")
            return
        title = event_info.item_name
        tmdb_id = event_info.tmdb_id
        if title.count(" S"):
            logger.info("只处理喜爱整季，单集喜爱不处理")
            return
        try:
            meta = MetaInfo(title)
            mediainfo: MediaInfo = self.chain.recognize_media(meta=meta, tmdbid=tmdb_id, mtype=MediaType.TV)
            # 存储历史记录
            favor: Dict = self.get_data('favor') or {}
            if favor.get(tmdb_id):
                favor.pop(tmdb_id)
                logger.info(f"{mediainfo.title_year} 取消更新通知")
                self.chain.post_message(Notification(
                    mtype=NotificationType.Plugin,
                    title=f"{mediainfo.title_year} 取消更新通知", text=None, image=mediainfo.get_message_image()))
            else:
                favor[tmdb_id] = {
                    "title": title,
                    "type": mediainfo.type.value,
                    "year": mediainfo.year,
                    "poster": mediainfo.get_poster_image(),
                    "overview": mediainfo.overview,
                    "tmdbid": mediainfo.tmdb_id,
                    "time": datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                }
                logger.info(f"{mediainfo.title_year} 加入更新通知")
                self.chain.post_message(Notification(
                    mtype=NotificationType.Plugin,
                    title=f"{mediainfo.title_year} 加入更新通知", text=None, image=mediainfo.get_message_image()))
            self.save_data('favor', favor)
        except Exception as e:
            logger.error(str(e))

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
                                    'md': 3
                                },
                                'content': [
                                    {
                                        'component': 'VSwitch',
                                        'props': {
                                            'model': 'cleanlink',
                                            'label': '立即清理生成',
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
                                    'md': 4
                                },
                                'content': [
                                    {
                                        'component': 'VTextField',
                                        'props': {
                                            'model': 'softlink_prefix_path',
                                            'label': '本地链接媒体库路径前缀',
                                            'placeholder': '/strm/'
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
            'cleanlink': self._cleanlink,
            'softlink_prefix_path': self._softlink_prefix_path,
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
