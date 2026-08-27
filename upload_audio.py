# -*- coding: utf-8 -*-
"""仅上传本地录音到 Supabase Storage，并回填对应歌曲的 audio_path。"""

import json
import os

from supabase import create_client

import config


DATA_FILE = os.path.join(os.path.dirname(__file__), "data", "songlist.json")
AUDIO_DIR = os.path.join(os.path.dirname(__file__), "data", "audio")
BUCKET_NAME = "recordings"


def upload_audio_only():
    with open(DATA_FILE, "r", encoding="utf-8") as f:
        data = json.load(f)

    url = config.get_supabase_url()
    key = config.get_supabase_key()
    client = create_client(url, key)

    uploaded = 0
    for singer in data.get("singers", []):
        for song in singer.get("songs", []):
            audio_filename = song.get("audio")
            if not audio_filename:
                continue
            local_path = os.path.join(AUDIO_DIR, audio_filename)
            if not os.path.exists(local_path):
                print(f"  跳过不存在的本地录音：{audio_filename}")
                continue
            with open(local_path, "rb") as f:
                audio_bytes = f.read()
            path = f"{song['id']}.mp3"
            client.storage.from_(BUCKET_NAME).upload(
                path, audio_bytes, {"content-type": "audio/mpeg", "upsert": "true"},
            )
            client.table("songs").update(
                {"audio_path": f"{BUCKET_NAME}/{audio_filename}"}
            ).eq("legacy_id", song["id"]).execute()
            print(f"  已上传并回填：{song.get('name')} -> {path}")
            uploaded += 1

    print(f"完成，共处理 {uploaded} 个录音文件。")


if __name__ == "__main__":
    upload_audio_only()