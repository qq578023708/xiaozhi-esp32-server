from urllib.parse import urlencode

import requests
from config.logger import setup_logging
import os
import random
import difflib
import re
import traceback
from core.handle.sendAudioHandle import send_stt_message

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
        else:
            self.music_dir = os.path.abspath("./music")
            self.music_cache_dir=os.path.abspath("./music/cache")
            self.music_related_keywords = ["来一首歌", "唱一首歌", "播放音乐", "来点音乐", "背景音乐", "放首歌",
                                           "播放歌曲", "来点背景音乐", "我想听歌", "我要听歌", "放点音乐"]

    async def handle_music_command(self, conn, text):
        """处理音乐播放指令"""
        clean_text = re.sub(r'[^\w\s]', '', text).strip()
        logger.bind(tag=TAG).debug(f"检查是否是音乐命令: {clean_text}")

        # 尝试匹配具体歌名
        if os.path.exists(self.music_dir):
            music_files = [f for f in os.listdir(self.music_dir) if f.endswith('.mp3')]
            logger.bind(tag=TAG).debug(f"找到的音乐文件: {music_files}")

            potential_song = _extract_song_name(clean_text)
            if potential_song:
                best_match = _find_best_match(potential_song, music_files)
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
                music_files = [f for f in os.listdir(self.music_dir) if f.endswith('.mp3')]
                if not music_files:
                    logger.bind(tag=TAG).error("未找到MP3音乐文件")
                    return
                selected_music = random.choice(music_files)
                music_path = os.path.join(self.music_dir, selected_music)
            text = f"正在播放{selected_music}"
            await send_stt_message(conn, text)
            conn.tts_first_text = selected_music
            conn.tts_last_text = selected_music
            conn.llm_finish_task = True
            opus_packets, duration = conn.tts.wav_to_opus_data(music_path)

            conn.audio_play_queue.put((opus_packets, selected_music))

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
            # logger.bind(tag=TAG).info(f"{response.json()}")
            response_data = response.json()
            if response_data["code"] != 200:
                logger.bind(tag=TAG).error(
                    f"获取歌曲链接失败！{response_data['error']}"
                )
            for song in response_data["data"]:
                song_type = song["type"]
                song_link = song["link"]
                song_id = song["songid"]
                song_title = song["title"]
                song_lrc = song["lrc"]
                song_url = song["url"]
                song_pic = song["pic"]
                
                logger.bind(tag=TAG).info(f"title:{song_title}, url:{song_url}")
                # 获取跳转前链接
                jump_response = requests.get(song_url, stream=True)
                jump_response.raise_for_status()
                if "audio/mpeg" in jump_response.headers["Content-Type"]:
                    # 获取到有效链接
                    # 保存文件
                    song_file_name = "song.mp3"
                    save_path = os.path.join(self.music_cache_dir, song_file_name)
                    with open(save_path, "wb") as file:
                        for chunk in jump_response.iter_content(chunk_size=1024):
                            file.write(chunk)
                    print(f"文件已下载到:{save_path}")
                    text = f"正在播放{song_title}"
                    await send_stt_message(conn, text)
                    conn.tts_first_text = song_title
                    conn.tts_last_text = song_title
                    conn.llm_finish_task = True
                    if save_path.endswith(".p3"):
                        opus_packets, duration = p3.decode_opus_from_file(save_path)
                    else:
                        opus_packets, duration = conn.tts.wav_to_opus_data(save_path)
                    conn.audio_play_queue.put((opus_packets, song_title))
                    break
        except Exception as e:
            logger.bind(tag=TAG).error(f"获取网络音乐列表失败:{traceback.format_exc()}")
