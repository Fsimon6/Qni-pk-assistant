# -*- coding: utf-8 -*-
"""从 JSON 备份恢复 Supabase 数据库。

快速批量恢复：使用 batch insert/upsert，而不是逐条处理。
"""
import json
import os
import sys

from supabase import create_client

import config


DATA_FILE = os.path.join(os.path.dirname(__file__), "data", "songlist.json")


def restore():
    print("从 JSON 备份恢复数据库...")

    with open(DATA_FILE, "r", encoding="utf-8") as f:
        data = json.load(f)

    url = config.get_supabase_url()
    key = config.get_supabase_key()
    client = create_client(url, key)

    # 1. 获取当前数据库中的歌手和歌曲
    existing_singers = (
        client.table("singers")
        .select("id, legacy_id, name")
        .limit(10000)
        .execute()
        .data or []
    )
    existing_songs = (
        client.table("songs")
        .select("id, legacy_id, singer_id, name")
        .limit(10000)
        .execute()
        .data or []
    )

    existing_singer_by_legacy = {s["legacy_id"]: s for s in existing_singers}
    existing_song_by_legacy = {s["legacy_id"]: s for s in existing_songs}

    print(f"  当前数据库: {len(existing_singers)} 歌手, {len(existing_songs)} 歌曲")

    # 2. 准备要插入/更新的歌手
    singers_to_insert = []
    singers_to_update = []
    for singer in data.get("singers", []):
        legacy_id = singer["id"]
        if legacy_id in existing_singer_by_legacy:
            existing = existing_singer_by_legacy[legacy_id]
            if existing["name"] != singer["name"]:
                singers_to_update.append({
                    "id": existing["id"],
                    "name": singer["name"],
                })
        else:
            singers_to_insert.append({
                "legacy_id": legacy_id,
                "name": singer["name"],
            })

    print(f"  歌手: 新增 {len(singers_to_insert)}, 更新 {len(singers_to_update)}")

    # 3. 批量插入歌手
    if singers_to_insert:
        client.table("singers").insert(singers_to_insert).execute()
        print(f"  ✅ 批量插入 {len(singers_to_insert)} 位歌手")

    # 4. 批量更新歌手
    for s in singers_to_update:
        client.table("singers").update({"name": s["name"]}).eq("id", s["id"]).execute()
    if singers_to_update:
        print(f"  ✅ 更新 {len(singers_to_update)} 位歌手")

    # 5. 重新获取歌手 UUID 映射
    singers_resp = (
        client.table("singers")
        .select("id, legacy_id")
        .limit(10000)
        .execute()
    )
    singer_uuid_by_legacy = {s["legacy_id"]: s["id"] for s in singers_resp.data or []}

    # 6. 准备要插入/更新的歌曲
    songs_to_insert = []
    songs_to_update = []
    for singer in data.get("singers", []):
        singer_legacy = singer["id"]
        singer_uuid = singer_uuid_by_legacy.get(singer_legacy)
        if singer_uuid is None:
            continue
        for song in singer.get("songs", []):
            song_legacy = song["id"]
            audio_path = None
            if song.get("audio"):
                audio_path = f"recordings/{song['audio']}"

            if song_legacy in existing_song_by_legacy:
                existing = existing_song_by_legacy[song_legacy]
                songs_to_update.append({
                    "id": existing["id"],
                    "singer_id": singer_uuid,
                    "name": song["name"],
                    "lyrics": song.get("lyrics", ""),
                    "sung": song.get("sung", False),
                    "audio_path": audio_path,
                })
            else:
                songs_to_insert.append({
                    "legacy_id": song_legacy,
                    "singer_id": singer_uuid,
                    "name": song["name"],
                    "lyrics": song.get("lyrics", ""),
                    "sung": song.get("sung", False),
                    "audio_path": audio_path,
                })

    print(f"  歌曲: 新增 {len(songs_to_insert)}, 更新 {len(songs_to_update)}")

    # 7. 批量插入歌曲（分批，每批 100 条，使用 upsert 避免冲突）
    batch_size = 100
    for i in range(0, len(songs_to_insert), batch_size):
        batch = songs_to_insert[i:i + batch_size]
        client.table("songs").upsert(batch, on_conflict="legacy_id").execute()
        print(f"  ✅ 批量插入歌曲 {i + 1}-{min(i + batch_size, len(songs_to_insert))}/{len(songs_to_insert)}")

    # 8. 批量更新歌曲（逐条，因为需要指定 id）
    if songs_to_update:
        for s in songs_to_update:
            client.table("songs").update({
                "singer_id": s["singer_id"],
                "name": s["name"],
                "lyrics": s["lyrics"],
                "sung": s["sung"],
                "audio_path": s["audio_path"],
            }).eq("id", s["id"]).execute()
        print(f"  ✅ 更新 {len(songs_to_update)} 首歌曲")

    # 9. 删除数据库中多余的歌手的歌曲（级联删除）
    json_singer_legacy_ids = {s["id"] for s in data.get("singers", [])}
    for legacy_id, existing in existing_singer_by_legacy.items():
        if legacy_id not in json_singer_legacy_ids:
            client.table("singers").delete().eq("id", existing["id"]).execute()
            print(f"   删除多余歌手: {existing['name']}")

    # 10. 验证
    final_singers = (
        client.table("singers")
        .select("id")
        .limit(10000)
        .execute()
        .data or []
    )
    final_songs = (
        client.table("songs")
        .select("id")
        .limit(10000)
        .execute()
        .data or []
    )
    print(f"\n恢复完成: {len(final_singers)} 歌手, {len(final_songs)} 歌曲")


if __name__ == "__main__":
    restore()
