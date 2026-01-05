import requests
from app.log import logger
from datetime import datetime

def get_headers(api_key):
    return {'X-Emby-Token': api_key}  # ← api_key 有值，正常返回

def format_time(seconds):
    # 将秒数转换为 datetime.timedelta 对象
    delta = datetime.utcfromtimestamp(seconds) - datetime.utcfromtimestamp(0)
    # 将 timedelta 对象格式化为时:分:秒.毫秒的格式
    formatted_time = str(delta).split(".")[0] + "." + str(delta.microseconds).zfill(6)[:3]
    return formatted_time


def get_next_episode_ids(item_id, season_id, episode_id, base_url, api_key) -> list:
    try:
        ids = []
        response = requests.get(f'{base_url}Shows/{item_id}/Episodes', headers = get_headers(api_key))
        episodes_info = response.json()
        # 查找下一集的 ID
        for idx, episode in enumerate(episodes_info['Items']):
            if episode['IndexNumber'] >= episode_id and season_id == episode['ParentIndexNumber']:
                next_episode_item_id = episode['Id']
                logger.debug(f'第{episode_id + idx}集的 item_ID 为: {next_episode_item_id}')
                ids.append(next_episode_item_id)
        return ids
    except Exception as e:
        logger.error("异常错误：%s" % str(e))


def get_current_video_item_id(item_id, season_id, episode_id, base_url, api_key):
    try:
        response = requests.get(f'{base_url}Shows/{item_id}/Episodes', headers = get_headers(api_key))
        episodes_info = response.json()
        # 查找当前集的 ID
        for episode in episodes_info['Items']:
            if episode['IndexNumber'] == episode_id and episode['ParentIndexNumber'] == season_id:
                item_id = episode['Id']
                logger.debug(f'第{episode_id}集的 item_ID 为: {item_id}')
                return item_id
        return -1
    except Exception as e:
        logger.error("异常错误：%s" % str(e))


def update_intro(item_id, intro_end, base_url, api_key):
    try:
        # 每次先移除旧的introskip
        chapter_info = requests.get(f"{base_url}emby/chapter_api/get_chapters?id={item_id}",
                                    headers = get_headers(api_key)).json()
        old_tags = [chapter['Index'] for chapter in chapter_info['chapters'] if
                    chapter['MarkerType'].startswith('Intro')]
        # 删除旧的
        requests.get(
            f"{base_url}emby/chapter_api/update_chapters?id={item_id}&index_list={','.join(map(str, old_tags))}&action=remove",
            headers = get_headers(api_key))
        # 添加新的片头开始
        requests.get(
            f"{base_url}emby/chapter_api/update_chapters?id={item_id}&action=add&name=%E7%89%87%E5%A4%B4&type=intro_start&time=00:00:00.000",
            headers = get_headers(api_key))
        # 新的片头结束
        requests.get(
            f"{base_url}emby/chapter_api/update_chapters?id={item_id}&action=add&name=%E7%89%87%E5%A4%B4%E7%BB%93%E6%9D%9F&type=intro_end&time={format_time(intro_end)}",
            headers = get_headers(api_key))
        return intro_end
    except Exception as e:
        logger.error("异常错误：%s" % str(e))


def update_credits(item_id, credits_start, base_url, api_key):
    try:
        chapter_info = requests.get(f"{base_url}emby/chapter_api/get_chapters?id={item_id}",
                                    headers = get_headers(api_key)).json()
        old_tags = [chapter['Index'] for chapter in chapter_info['chapters'] if
                    chapter['MarkerType'].startswith('Credits')]
        # 删除旧的
        requests.get(
            f"{base_url}emby/chapter_api/update_chapters?id={item_id}&index_list={','.join(map(str, old_tags))}&action=remove",
            headers = get_headers(api_key))

        # 添加新的片尾开始
        requests.get(
            f"{base_url}emby/chapter_api/update_chapters?id={item_id}&action=add&name=%E7%89%87%E5%B0%BE&type=credits_start&time={format_time(credits_start)}",
            headers = get_headers(api_key))
        return credits_start
    except Exception as e:
        logger.error("异常错误：%s" % str(e))


def get_total_time(item_id,  base_url, api_key):
    try:
        response = requests.get(f'{base_url}emby/Items/{item_id}/PlaybackInfo?api_key={api_key}')
        video_info = response.json()
        if video_info['MediaSources']:
            video_info = video_info['MediaSources'][0]
            total_time_ticks = video_info['RunTimeTicks']
            total_time_seconds = total_time_ticks / 10000000  # 将 ticks 转换为秒
            # logger.info(f"{video_info['Name']} 总时长为{total_time_seconds}秒")
            return total_time_seconds
        else:
            logger.error("无法获取视频总时长")
            return 0
    except Exception as e:
        logger.error("异常错误：%s" % str(e))
        return 0


def include_keyword(path: str, keywords: str) -> dict:
    keyword_list: list = keywords.split(',')
    flag = False
    msg = ""
    for keyword in keyword_list:
        if keyword in path:
            flag = True
            msg = keyword
            break
    if flag:
        return {'ret': True, 'msg': msg}
    else:
        return {'ret': False, 'msg': ''}


def exclude_keyword(path: str, keywords: str) -> dict:
    keyword_list: list = keywords.split(',') if keywords else []
    for keyword in keyword_list:
        if keyword in path:
            return {'ret': False, 'msg': keyword}
    return {'ret': True, 'msg': ''}


if __name__ == '__main__':
    # pause_time('7')
    print(*get_next_episode_ids(5842, 2, 2))
    # get_total_time(1847)
