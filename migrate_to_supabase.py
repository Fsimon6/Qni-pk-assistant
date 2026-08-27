# -*- coding: utf-8 -*-
"""迁移脚本：将本地录音文件上传到 Supabase Storage，并回填 songs.audio_path。

用法：
  venv\Scripts\python.exe migrate_to_supabase.py            仅迁移录音文件
  venv\Scripts\python.exe migrate_to_supabase.py --full     完整迁移（歌手+歌曲+录音）

运行前请确保：
1. 已在 .streamlit/secrets.toml 或环境变量中配置 SUPABASE_URL 和 SUPABASE_ANON_KEY
2. Supabase 中已创建 singers 表、songs 表和 recordings bucket

安全策略：
- 仅新增和更新，不删除数据库中已有的数据
- 可重复执行，不会产生重复记录
- 不修改本地 JSON 和音频文件
"""

import json
import os
import sys

from supabase import create_client

import config


DATA_FILE = os.path.join(os.path.dirname(__file__), "data", "songlist.json")
AUDIO_DIR = os.path.join(os.path.dirname(__file__), "data", "audio")
BUCKET_NAME = "recordings"


def load_json_data():
    if not os.path.exists(DATA_FILE):
        print("未找到本地 JSON 数据文件。")
        return {"singers": []}
    with open(DATA_FILE, "r", encoding="utf-8") as f:
        return json.load(f)


def migrate_singers_and_songs(client, data):
    # 迁移歌手和歌曲到 PostgreSQL（仅新增和更新，不删除）
    print("迁移歌手和歌曲数据...")
    added_singers = 0
    added_songs = 0
    updated_songs = 0

    for singer in data.get("singers", []):
        # 检查歌手是否已存在
        existing = (
            client.table("singers")
            .select("id, legacy_id, name")
            .eq("legacy_id", singer["id"])
            .execute()
        )
        if existing.data:
            singer_uuid = existing.data[0]["id"]
            # 更新歌手名（如果有变化）
            if existing.data[0]["name"] != singer["name"]:
                client.table("singers").update({"name": singer["name"]}).eq(
                    "id", singer_uuid
                ).execute()
        else:
            result = (
                client.table("singers")
                .insert({"legacy_id": singer["id"], "name": singer["name"]})
                .execute()
            )
            singer_uuid = result.data[0]["id"]
            added_singers += 1

        # 迁移歌曲
        for song in singer.get("songs", []):
            existing_song = (
                client.table("songs")
                .select("id, legacy_id, audio_path")
                .eq("legacy_id", song["id"])
                .execute()
            )

            audio_path = None
            if song.get("audio"):
                audio_path = f"{BUCKET_NAME}/{song['audio']}"

            if existing_song.data:
                song_uuid = existing_song.data[0]["id"]
                # 保留已有 audio_path（除非 JSON 中有明确值）
                existing_audio = existing_song.data[0].get("audio_path")
                if not audio_path and existing_audio:
                    audio_path = existing_audio
                client.table("songs").update({
                    "singer_id": singer_uuid,
                    "name": song["name"],
                    "lyrics": song.get("lyrics", ""),
                    "sung": song.get("sung", False),
                    "audio_path": audio_path,
                }).eq("id", song_uuid).execute()
                updated_songs += 1
            else:
                client.table("songs").insert({
                    "legacy_id": song["id"],
                    "singer_id": singer_uuid,
                    "name": song["name"],
                    "lyrics": song.get("lyrics", ""),
                    "sung": song.get("sung", False),
                    "audio_path": audio_path,
                }).execute()
                added_songs += 1

    print(f"歌手数据迁移完成：新增 {added_singers} 位歌手")
    print(f"歌曲数据迁移完成：新增 {added_songs} 首，更新 {updated_songs} 首")


def migrate_audio(client, data):
    # 迁移录音文件到 Supabase Storage，并回填 songs.audio_path
    print("迁移录音文件...")
    migrated_files = 0
    for singer in data.get("singers", []):
        for song in singer.get("songs", []):
            audio_filename = song.get("audio")
            if not audio_filename:
                continue
            local_path = os.path.join(AUDIO_DIR, audio_filename)
            if not os.path.exists(local_path):
                print(f"  跳过不存在的本地录音：{audio_filename}")
                continue
            try:
                with open(local_path, "rb") as f:
                    audio_bytes = f.read()
                path = f"{song['id']}.mp3"
                client.storage.from_(BUCKET_NAME).upload(
                    path,
                    audio_bytes,
                    {"content-type": "audio/mpeg", "upsert": "true"},
                )
                client.table("songs").update(
                    {"audio_path": f"{BUCKET_NAME}/{audio_filename}"}
                ).eq("legacy_id", song["id"]).execute()
                print(f"  已上传录音：{song.get('name')} -> {path}")
                migrated_files += 1
            except Exception as e:
                print(f"  上传录音失败 {audio_filename}: {e}")

    print(f"迁移完成，共迁移 {migrated_files} 个录音文件。")


def migrate(full: bool = False):
    print("开始迁移..." + ("（完整模式）" if full else "（仅录音文件）"))
    data = load_json_data()

    url = config.get_supabase_url()
    key = config.get_supabase_key()
    client = create_client(url, key)

    if full:
        migrate_singers_and_songs(client, data)

    migrate_audio(client, data)


if __name__ == "__main__":
    migrate(full="--full" in sys.argv)
