from urllib.parse import quote, urlencode

import requests
from config.logger import setup_logging
import os
import random
import difflib
import re
import traceback
from pathlib import Path
import time
from core.handle.sendAudioHandle import send_stt_message
from core.utils import p3

TAG = __name__
logger = setup_logging()

MusicApiUrl = "https://y.0msl.com/"
lrc_pattern = re.compile(r"\[(\d{2}):(\d{2})\.(\d{2})\](.*)")


def _extract_song_name(text):
    """从用户输入中提取歌名"""
    for keyword in ["听", "播放", "放", "唱"]:
        if keyword in text:
            parts = text.split(keyword)
            if len(parts) > 1:
                return parts[1].strip()
    return None


def _find_best_match(potential_song, music_files):
    """查找最匹配的歌曲"""
    best_match = None
    highest_ratio = 0

    for music_file in music_files:
        song_name = os.path.splitext(music_file)[0]
        ratio = difflib.SequenceMatcher(None, potential_song, song_name).ratio()
        if ratio > highest_ratio and ratio > 0.4:
            highest_ratio = ratio
            best_match = music_file
    return best_match

class MusicManager:
    def __init__(self, music_dir, music_ext):
        self.music_dir = Path(music_dir)
        self.music_ext = music_ext

    def get_music_files(self):
        music_files = []
        for file in self.music_dir.rglob("*"):
            # 判断是否是文件
            if file.is_file():
                # 获取文件扩展名
                ext = file.suffix.lower()
                # 判断扩展名是否在列表中
                if ext in self.music_ext:
                    # music_files.append(str(file.resolve()))  # 添加绝对路径
                    # 添加相对路径
                    music_files.append(str(file.relative_to(self.music_dir)))
        return music_files

class MusicHandler:
    def __init__(self, config):
        self.config = config
        self.music_related_keywords = []

        if "music" in self.config:
            self.music_config = self.config["music"]
            self.music_dir = os.path.abspath(
                self.music_config.get("music_dir", "./music")  # 默认路径修改
            )
            self.music_cache_dir=os.path.abspath(
                self.music_config.get("music_cache_dir","./music/cache") #缓存路径
            )
            self.music_related_keywords = self.music_config.get("music_commands", [])
            self.music_ext = self.music_config.get("music_ext", (".mp3", ".wav", ".p3"))
            self.refresh_time = self.music_config.get("refresh_time", 60)
        else:
            self.music_dir = os.path.abspath("./music")
            self.music_cache_dir=os.path.abspath("./music/cache")
            self.music_related_keywords = ["来一首歌", "唱一首歌", "播放音乐", "来点音乐", "背景音乐", "放首歌",
                                           "播放歌曲", "来点背景音乐", "我想听歌", "我要听歌", "放点音乐"]
            self.music_ext = (".mp3", ".wav", ".p3")
            self.refresh_time = 60

        # 获取音乐文件列表
        self.music_files = MusicManager(self.music_dir, self.music_ext).get_music_files()
        self.scan_time = time.time()
        logger.bind(tag=TAG).debug(f"找到的音乐文件: {self.music_files}")

    async def handle_music_command(self, conn, text):
        """处理音乐播放指令"""
        clean_text = re.sub(r'[^\w\s]', '', text).strip()
        logger.bind(tag=TAG).debug(f"检查是否是音乐命令: {clean_text}")

        # 尝试匹配具体歌名
        if os.path.exists(self.music_dir):
            if time.time() - self.scan_time > self.refresh_time:
                # 刷新音乐文件列表
                self.music_files = MusicManager(self.music_dir, self.music_ext).get_music_files()
                self.scan_time = time.time()
                logger.bind(tag=TAG).debug(f"刷新的音乐文件: {self.music_files}")

            potential_song = _extract_song_name(clean_text)
            if potential_song:
                best_match = _find_best_match(potential_song, self.music_files)
                if best_match:
                    logger.bind(tag=TAG).info(f"找到最匹配的歌曲: {best_match}")
                    await self.play_local_music(conn, specific_file=best_match)
                    return True
                else:
                    await self.play_net_music(conn,potential_song)
                    return True

        # 检查是否是通用播放音乐命令
        if any(cmd in clean_text for cmd in self.music_related_keywords):
            await self.play_net_music(conn)
            return True

        return False

    async def play_local_music(self, conn, specific_file=None):
        """播放本地音乐文件"""
        try:
            if not os.path.exists(self.music_dir):
                logger.bind(tag=TAG).error(f"音乐目录不存在: {self.music_dir}")
                return

            # 确保路径正确性
            if specific_file:
                music_path = os.path.join(self.music_dir, specific_file)
                if not os.path.exists(music_path):
                    logger.bind(tag=TAG).error(f"指定的音乐文件不存在: {music_path}")
                    return
                selected_music = specific_file
            else:
                if time.time() - self.scan_time > self.refresh_time:
                    # 刷新音乐文件列表
                    self.music_files = MusicManager(self.music_dir, self.music_ext).get_music_files()
                    self.scan_time = time.time()
                    logger.bind(tag=TAG).debug(f"刷新的音乐文件列表: {self.music_files}")

                if not self.music_files:
                    logger.bind(tag=TAG).error("未找到MP3音乐文件")
                    return
                selected_music = random.choice(self.music_files)
                music_path = os.path.join(self.music_dir, selected_music)
                if not os.path.exists(music_path):
                    logger.bind(tag=TAG).error(f"选定的音乐文件不存在: {music_path}")
                    return
            text = f"正在播放{selected_music}"
            await send_stt_message(conn, text)
            conn.tts_first_text_index = 0
            conn.tts_last_text_index = 0
            conn.llm_finish_task = True
            if music_path.endswith(".p3"):
                opus_packets, duration = p3.decode_opus_from_file(music_path)
            else:
                opus_packets, duration = conn.tts.wav_to_opus_data(music_path)
            conn.audio_play_queue.put((opus_packets, selected_music, 0))

        except Exception as e:
            logger.bind(tag=TAG).error(f"播放音乐失败: {str(e)}")
            logger.bind(tag=TAG).error(f"详细错误: {traceback.format_exc()}")

    async def play_net_music(self,conn,song_name=None):
        if song_name is None:
            music_list=["卜卦","土坡上的狗尾草","没关系","一点","越来越不懂","特别的人","唯一","舍得","平凡日子里的挣扎"]
            select_music=random.choice(music_list)
        else:
            select_music=song_name
        try:
            song_url= await self.get_music_url_adapter_2(select_music)
            if song_url is None:
                logger.bind(tag=TAG).error(f"获取网络音乐地址失败")
                return
            jump_response = requests.get(song_url, stream=True)
            jump_response.raise_for_status()
            song_file_name = "song.mp3"
            save_path = os.path.join(self.music_cache_dir, song_file_name)
            with open(save_path, "wb") as file:
                for chunk in jump_response.iter_content(chunk_size=1024):
                    file.write(chunk)
                text = f"正在播放{select_music}"
                await send_stt_message(conn, text)
                conn.tts_first_text = select_music
                conn.tts_last_text = select_music
                conn.llm_finish_task = True
                opus_packets, duration = conn.tts.wav_to_opus_data(save_path)
                conn.audio_play_queue.put((opus_packets, song_file_name, 0))
        except Exception as e:
            logger.bind(tag=TAG).error(f"获取网络音乐列表失败:{traceback.format_exc()}")

    async def get_music_url_adapter_1(self,select_music):
        try:
            payload = {
                "input": select_music,
                "filter": "name",
                "type": "migu",
                "page": "1",
            }
            encode_data = urlencode(payload)
            headers = {
                "Content-Type": "application/x-www-form-urlencoded",
                "x-requested-with": "XMLHttpRequest",
            }
            response = requests.post(MusicApiUrl, data=encode_data, headers=headers)
            response.raise_for_status()
            response_data = response.json()
            if response_data["code"] != 200:
                logger.bind(tag=TAG).error(
                    f"获取歌曲链接失败！{response_data['error']}"
                )
                return None
            for song in response_data["data"]:
                song_title = song["title"]
                song_url = song["url"]
                # 获取跳转前链接
                jump_response = requests.get(song_url, stream=True)
                jump_response.raise_for_status()
                if "audio/mpeg" in jump_response.headers["Content-Type"]:
                    # 获取到有效链接
                    return song_url
            return None
        except Exception as e:
            logger.bind(tag=TAG).error(f"获取网络音乐列表失败:{traceback.format_exc()}")

    async def get_music_url_adapter_2(self,select_music):
        getway="https://www.gequbao.com"
        try:
            url=f"{getway}/s/{quote(select_music)}"
            headers = {
                "Content-Type": "application/x-www-form-urlencoded",
                "x-requested-with": "XMLHttpRequest",
            }
            response = requests.get(url,headers=headers)
            response.raise_for_status()
            response_data = response.text
            #正则取出链接
            pattern=r'href="(.*?)".*?播放&下载'
            match=re.search(pattern,response_data)
            if match:
                ext=match.group(1)
                response= requests.get(f"{getway}{ext}")
                response.raise_for_status()
                response_data=response.text
                pattern=r"window.play_id = '(.*?)'"
                match=re.search(pattern,response_data)
                if match:
                    song_id=match.group(1)
                    payload={"id":song_id}
                    headers={"content-type":"application/x-www-form-urlencoded; charset=UTF-8"}
                    post_data=urlencode(payload)
                    response= requests.post(f"{getway}/api/play-url",data=post_data,headers=headers)
                    response.raise_for_status()
                    response_data=response.json()
                    if response_data['code'] != 1:
                        return None
                    return response_data['data']['url']
            return None

        except Exception as e:
            logger.bind(tag=TAG).error(f"获取网络音乐列表失败:{traceback.format_exc()}")
