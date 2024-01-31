import datetime
import xml.dom.minidom
from threading import Event
from typing import Tuple, List, Dict, Any

import pytz
from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.cron import CronTrigger

from app.chain.download import DownloadChain
from app.chain.subscribe import SubscribeChain
from app.core.config import settings
from app.core.context import MediaInfo
from app.core.metainfo import MetaInfo
from app.log import logger
from app.plugins import _PluginBase
from app.utils.dom import DomUtils
from app.utils.http import RequestUtils
from app.db import get_db
from app.db.models.subscribe import Subscribe


class BangumiRank(_PluginBase):
    # 插件名称
    plugin_name = "Bangumi榜单 & 想看订阅"
    # 插件描述
    plugin_desc = "Bangumi 成员关注动画榜，个人想看订阅"
    # 插件图标
    plugin_icon = "https://raw.githubusercontent.com/honue/MoviePilot-Plugins/main/icons/miku.jpg"
    # 插件版本
    plugin_version = "1.2"
    # 插件作者
    plugin_author = "honue"
    # 作者主页
    author_url = "https://github.com/honue"
    # 插件配置项ID前缀
    plugin_config_prefix = "bangumirank_"
    # 加载顺序
    plugin_order = 30
    # 可使用的用户级别
    auth_level = 2

    # 退出事件
    _event = Event()
    # 私有属性
    downloadchain: DownloadChain = None
    subscribechain: SubscribeChain = None
    _scheduler = None
    _enabled = False
    _cron = ""
    _onlyonce = False
    _clear = False
    _clearflag = False
    _proxy = False

    _rank_top = None
    _uid = None
    _wish_top = None

    def init_plugin(self, config: dict = None):
        self.downloadchain = DownloadChain()
        self.subscribechain = SubscribeChain()

        if config:
            self._enabled = config.get("enabled")
            self._cron = config.get("cron")
            self._proxy = config.get("proxy")
            self._onlyonce = config.get("onlyonce")
            # 想看
            self._uid = config.get("uid")
            self._wish_top = config.get("wish_top")
            # 排行榜
            self._rank_top = config.get("rank_top")
            # 清理订阅记录
            self._clear = config.get("clear")
            # 订阅筛选
            self._include = config.get("include")
            self._exclude = config.get("exclude")

        # 停止现有任务
        self.stop_service()

        # 启动服务
        if self._enabled or self._onlyonce:
            self._scheduler = BackgroundScheduler(timezone=settings.TZ)
            if self._enabled and self._cron:
                logger.info(f"Bangumi榜单&想看,订阅服务启动，周期：{self._cron}")
                try:
                    self._scheduler.add_job(func=self.__refresh_rss,
                                            trigger=CronTrigger.from_crontab(self._cron),
                                            name="Bangumi榜单&想看")
                except Exception as e:
                    logger.error(f"Bangumi榜单&想看,订阅服务启动失败，错误信息：{str(e)}")
                    self.systemmessage.put(f"Bangumi榜单&想看,订阅服务启动失败，错误信息：{str(e)}")
            else:
                self._enabled = False
            if self._onlyonce:
                logger.info("Bangumi榜单&想看,订阅服务启动，立即运行一次")
                self._scheduler.add_job(func=self.__refresh_rss, trigger='date',
                                        run_date=datetime.datetime.now(
                                            tz=pytz.timezone(settings.TZ)) + datetime.timedelta(seconds=3)
                                        )

            if self._onlyonce or self._clear:
                # 关闭一次性开关
                self._onlyonce = False
                # 记录缓存清理标志
                self._clearflag = self._clear
                # 关闭清理缓存
                self._clear = False
                # 保存配置
                self.__update_config()

            if self._scheduler.get_jobs():
                # 启动服务
                self._scheduler.print_jobs()
                self._scheduler.start()

    def get_state(self) -> bool:
        return self._enabled

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
                                    'md': 3
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
                                    'md': 3
                                },
                                'content': [
                                    {
                                        'component': 'VSwitch',
                                        'props': {
                                            'model': 'proxy',
                                            'label': '使用代理服务器',
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
                                            'model': 'clear',
                                            'label': '清理历史记录',
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
                                            'model': 'cron',
                                            'label': '执行周期',
                                            'placeholder': '5位cron表达式'
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
                                            'model': 'rank_top',
                                            'label': '排行榜榜单top',
                                            'placeholder': '5'
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
                                            'model': 'uid',
                                            'label': 'Bangumi uid(数字)',
                                            'placeholder': '233333'
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
                                            'model': 'wish_top',
                                            'label': '想看的top',
                                            'placeholder': '5'
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
                                            'model': 'include',
                                            'label': '包含（关键字、正则式）',
                                            'placeholder': 'ADWeb'
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
                                            'model': 'exclude',
                                            'label': '包含（关键字、正则式）',
                                            'placeholder': 'exclude'
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
                                            'text': '排行榜数据源自 https://bangumi.tv/anime',
                                            'style': 'white-space: pre-line;'
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
            "cron": "",
            "proxy": True,
            "onlyonce": False,
            "uid": "",
            "wish_top": 0,
            "rank_top": 0,
            "clear": False,
            "include": "",
            "exclude": ""
        }

    def get_page(self) -> List[dict]:
        """
        拼装插件详情页面，需要返回页面配置，同时附带数据
        """
        # 查询历史记录
        historys = self.get_data('history')
        if not historys:
            return [
                {
                    'component': 'div',
                    'text': '暂无数据',
                    'props': {
                        'class': 'text-center',
                    }
                }
            ]
        # 数据按时间降序排序
        historys = sorted(historys, key=lambda x: x.get('time'), reverse=True)
        # 拼装页面
        contents = []
        for history in historys:
            title = history.get("title")
            poster = history.get("poster")
            mtype = history.get("type")
            time_str = history.get("time")
            bangumi_link = history.get("bangumi_link")
            contents.append(
                {
                    'component': 'VCard',
                    'content': [
                        {
                            'component': 'div',
                            'props': {
                                'class': 'd-flex justify-space-start flex-nowrap flex-row',
                            },
                            'content': [
                                {
                                    'component': 'div',
                                    'content': [
                                        {
                                            'component': 'VImg',
                                            'props': {
                                                'src': poster,
                                                'height': 120,
                                                'width': 80,
                                                'aspect-ratio': '2/3',
                                                'class': 'object-cover shadow ring-gray-500',
                                                'cover': True
                                            }
                                        }
                                    ]
                                },
                                {
                                    'component': 'div',
                                    'content': [
                                        {
                                            'component': 'VCardSubtitle',
                                            'props': {
                                                'class': 'pa-2 font-bold break-words whitespace-break-spaces'
                                            },
                                            'content': [
                                                {
                                                    'component': 'a',
                                                    'props': {
                                                        'href': bangumi_link,
                                                        'target': '_blank'
                                                    },
                                                    'text': title
                                                }
                                            ]
                                        },
                                        {
                                            'component': 'VCardText',
                                            'props': {
                                                'class': 'pa-0 px-2'
                                            },
                                            'text': f'类型：{mtype}'
                                        },
                                        {
                                            'component': 'VCardText',
                                            'props': {
                                                'class': 'pa-0 px-2'
                                            },
                                            'text': f'时间：{time_str}'
                                        }
                                    ]
                                }
                            ]
                        }
                    ]
                }
            )

        return [
            {
                'component': 'div',
                'props': {
                    'class': 'grid gap-3 grid-info-card',
                },
                'content': contents
            }
        ]

    def stop_service(self):
        """
        停止服务
        """
        try:
            if self._scheduler:
                self._scheduler.remove_all_jobs()
                if self._scheduler.running:
                    self._event.set()
                    self._scheduler.shutdown()
                    self._event.clear()
                self._scheduler = None
        except Exception as e:
            print(str(e))

    def __update_config(self):
        """
        列新配置
        """
        self.update_config({
            "enabled": self._enabled,
            "cron": self._cron,
            "onlyonce": self._onlyonce,
            "proxy": self._proxy,
            "clear": self._clear,
            "uid": self._uid,
            "wish_top": self._wish_top,
            "rank_top": self._rank_top,
            "include": self._include,
            "exclude": self._exclude
        })

    def __refresh_rss(self):
        """
        刷新RSS
        """
        addr_list = []
        # 'https://rsshub.app/bangumi/tv/followrank'
        # 'https://rsshub.app/bangumi/tv/user/wish/{uid}'
        if self._uid and self._wish_top:
            addr_list.append(f"https://rsshub.app/bangumi/tv/user/wish/{self._uid}?limit={self._wish_top}")
        else:
            logger.info(f"未设置uid或wish_top，不执行想看订阅")

        if self._rank_top:
            addr_list.append(f"https://rsshub.app/bangumi/tv/followrank?limit={self._rank_top}")
        else:
            logger.info(f"未设置rank_top，不执行榜单订阅")

        if not addr_list:
            logger.info(f"未设置相关参数，运行结束")
            return

        # 读取历史记录
        if self._clearflag:
            history = []
        else:
            history: List[dict] = self.get_data('history') or []

        logger.info(f"开始刷新Bangumi榜单 ...")
        for addr in addr_list:
            if not addr:
                continue
            try:
                logger.info(f"获取RSS：{addr} ...")
                rss_infos = self.__get_rss_info(addr)
                if not rss_infos:
                    logger.error(f"RSS地址：{addr} ，未查询到数据")
                    continue
                else:
                    logger.info(f"RSS地址：{addr} ，获取 {len(rss_infos)} 条数据")
                for rss_info in rss_infos:
                    if self._event.is_set():
                        logger.info(f"订阅服务停止")
                        return

                    title = rss_info.get('title')
                    bangumi_link = rss_info.get('link')
                    unique_flag = f"bangumirank: {title} (DB:{bangumi_link})"
                    # 检查是否已处理过
                    if unique_flag in [h.get("unique") for h in history]:
                        logger.info(f"{title} 已处理过")
                        continue
                    # 元数据
                    meta = MetaInfo(title)
                    mediainfo: MediaInfo = self.chain.recognize_media(meta=meta)
                    if not mediainfo:
                        logger.warn(f'未识别到媒体信息，标题：{title}，link：{bangumi_link}')
                        continue

                    # 查询缺失的媒体信息
                    exist_flag, _ = self.downloadchain.get_no_exists_info(meta=meta, mediainfo=mediainfo)
                    if exist_flag:
                        logger.info(f'{mediainfo.title_year} 媒体库中已存在')
                        continue
                    # 判断用户是否已经添加订阅
                    if self.subscribechain.exists(mediainfo=mediainfo, meta=meta):
                        logger.info(f'{mediainfo.title_year} 订阅已存在')
                        continue
                    # 添加订阅
                    sid, msg = self.subscribechain.add(title=mediainfo.title,
                                                       year=mediainfo.year,
                                                       mtype=mediainfo.type,
                                                       tmdbid=mediainfo.tmdb_id,
                                                       season=meta.begin_season,
                                                       exist_ok=True,
                                                       username="bangumi榜单&想看")
                    db = get_db()
                    subscribe: Subscribe = Subscribe.get(db, sid)
                    if not subscribe:
                        logger.error("订阅不存在，设置过滤关键词失败")
                        return
                    subscribe.include = self._include
                    subscribe.exclude = self._exclude
                    subscribe.update(db=db, payload=subscribe.to_dict())

                    # 存储历史记录
                    history.append({
                        "title": title,
                        "type": mediainfo.type.value,
                        "year": mediainfo.year,
                        "poster": mediainfo.get_poster_image(),
                        "overview": mediainfo.overview,
                        "tmdbid": mediainfo.tmdb_id,
                        "bangumi_link": bangumi_link,
                        "time": datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                        "unique": unique_flag
                    })
            except Exception as e:
                logger.error(str(e))

        # 保存历史记录
        self.save_data('history', history)
        # 缓存只清理一次
        self._clearflag = False
        logger.info(f"所有榜单RSS刷新完成")

    def __get_rss_info(self, addr) -> List[dict]:
        """
        获取RSS
        """
        try:
            if self._proxy:
                ret = RequestUtils(proxies=settings.PROXY).get_res(addr)
            else:
                ret = RequestUtils().get_res(addr)
            if not ret:
                return []
            ret_xml = ret.text
            ret_array = []
            # 解析XML
            dom_tree = xml.dom.minidom.parseString(ret_xml)
            rootNode = dom_tree.documentElement
            items = rootNode.getElementsByTagName("item")
            for item in items:
                try:
                    rss_info = {}
                    # 标题
                    title = DomUtils.tag_value(item, "title", default="")
                    # 链接
                    link = DomUtils.tag_value(item, "link", default="")

                    if not title and not link:
                        logger.warn(f"条目标题和链接均为空，无法处理")
                        continue
                    rss_info['title'] = title
                    rss_info['link'] = link
                    # 返回对象
                    ret_array.append(rss_info)
                except Exception as e1:
                    logger.error("解析RSS条目失败：" + str(e1))
                    continue
            return ret_array
        except Exception as e:
            logger.error("获取RSS失败：" + str(e))
            return []
