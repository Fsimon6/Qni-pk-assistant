# -*- coding: utf-8 -*-
"""
扫描 data/audio/ 目录，根据文件名自动在 songlist.json 中添加 audio 字段。
文件名格式：{song_id}.mp3
"""

import json
import os

DATA_FILE = os.path.join(os.path.dirname(__file__), "data", "songlist.json")
AUDIO_DIR = os.path.join(os.path.dirname(__file__), "data", "audio")


def fix_audio_mapping():
    # 1. 扫描音频文件
    audio_files = {}
    for filename in os.listdir(AUDIO_DIR):
        if filename.endswith(".mp3"):
            song_id = filename[:-4]  # 去掉 .mp3
            audio_files[song_id] = filename

    print(f"找到 {len(audio_files)} 个音频文件：{list(audio_files.keys())}")

    # 2. 读取 songlist.json
    with open(DATA_FILE, "r", encoding="utf-8") as f:
        data = json.load(f)

    # 3. 遍历所有歌曲，添加 audio 字段
    updated_count = 0
    for singer in data.get("singers", []):
        for song in singer.get("songs", []):
            song_id = song.get("id")
            if song_id in audio_files:
                song["audio"] = audio_files[song_id]
                updated_count += 1
                print(f"  已关联：{song.get('name')} -> {audio_files[song_id]}")

    # 4. 保存 songlist.json
    with open(DATA_FILE, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)

    print(f"\n完成！共更新 {updated_count} 首歌曲的 audio 字段")


if __name__ == "__main__":
    fix_audio_mapping()
