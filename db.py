# -*- coding: utf-8 -*-
"""数据访问层：封装所有 Supabase 数据库操作。

对外提供与原有 JSON 兼容的数据结构，内部使用 Supabase PostgreSQL 持久化。
支持增量 CRUD 操作，避免全量同步的性能问题。
"""

from supabase import Client, create_client

import config


class Database:
    """Supabase 数据库访问封装。"""

    PAGE_SIZE = 1000

    def __init__(self) -> None:
        url = config.get_supabase_url()
        key = config.get_supabase_key()
        self.client: Client = create_client(url, key)

    def _fetch_all(self, query_builder) -> list:
        """分页获取所有行，绕过 Supabase API 的 1000 行硬限制。"""
        all_rows = []
        offset = 0
        while True:
            resp = query_builder.range(offset, offset + self.PAGE_SIZE - 1).execute()
            data = resp.data or []
            all_rows.extend(data)
            if len(data) < self.PAGE_SIZE:
                break
            offset += self.PAGE_SIZE
        return all_rows

    def _get_singer_uuid(self, legacy_id: str) -> str | None:
        """根据 legacy_id 获取歌手的 UUID。"""
        resp = (
            self.client.table("singers")
            .select("id")
            .eq("legacy_id", legacy_id)
            .execute()
        )
        data = resp.data or []
        return data[0]["id"] if data else None

    # ===== 读取：兼容原 JSON 结构 =====
    def load_data(self) -> dict:
        """返回与原 JSON 兼容的数据结构：{'singers': [...]}。"""
        singer_rows = self._fetch_all(
            self.client.table("singers")
            .select("id, legacy_id, name")
            .order("created_at")
        )
        song_rows = self._fetch_all(
            self.client.table("songs")
            .select("id, legacy_id, singer_id, name, lyrics, sung, audio_path")
            .order("created_at")
        )

        singer_legacy_by_id = {s["id"]: s["legacy_id"] for s in singer_rows}
        singer_songs = {}
        for song in song_rows:
            singer_uuid = song["singer_id"]
            singer_legacy = singer_legacy_by_id.get(singer_uuid)
            if singer_legacy is None:
                continue
            singer_songs.setdefault(singer_legacy, []).append(song)

        data = {"singers": []}
        for s in singer_rows:
            legacy_id = s["legacy_id"]
            songs = []
            for song in singer_songs.get(legacy_id, []):
                songs.append(self._song_to_dict(song))
            data["singers"].append({
                "id": legacy_id,
                "name": s["name"],
                "songs": songs,
            })
        return data

    def _song_to_dict(self, song: dict) -> dict:
        result = {
            "id": song["legacy_id"],
            "name": song["name"],
            "lyrics": song.get("lyrics", "") or "",
            "sung": bool(song.get("sung", False)),
        }
        audio_path = song.get("audio_path")
        if audio_path:
            result["audio"] = audio_path.split("/")[-1]
        return result

    # ===== 写入：全量同步（保留用于兼容，性能差）=====
    def save_data(self, data: dict) -> None:
        """保存完整数据结构（全量同步，性能差，仅用于兼容）。"""
        singers_in = {s["id"]: s for s in data.get("singers", [])}

        existing_singers = self._fetch_all(
            self.client.table("singers")
            .select("id, legacy_id, name")
        )
        existing_singer_by_legacy = {s["legacy_id"]: s for s in existing_singers}

        for legacy_id, singer in singers_in.items():
            if legacy_id in existing_singer_by_legacy:
                existing = existing_singer_by_legacy[legacy_id]
                if existing["name"] != singer["name"]:
                    self.client.table("singers").update({"name": singer["name"]}).eq(
                        "id", existing["id"]
                    ).execute()
            else:
                self.client.table("singers").insert({
                    "legacy_id": legacy_id,
                    "name": singer["name"],
                }).execute()

        for legacy_id, existing in existing_singer_by_legacy.items():
            if legacy_id not in singers_in:
                self.client.table("singers").delete().eq("id", existing["id"]).execute()

        singers_resp = self._fetch_all(
            self.client.table("singers")
            .select("id, legacy_id")
        )
        singer_uuid_by_legacy = {s["legacy_id"]: s["id"] for s in singers_resp}

        existing_songs = self._fetch_all(
            self.client.table("songs")
            .select("id, legacy_id, singer_id, name, lyrics, sung, audio_path")
        )
        existing_song_by_legacy = {s["legacy_id"]: s for s in existing_songs}

        songs_in_by_legacy = {}
        for singer in data.get("singers", []):
            singer_legacy = singer["id"]
            for song in singer.get("songs", []):
                songs_in_by_legacy[song["id"]] = (song, singer_legacy)

        for song_legacy, (song, singer_legacy) in songs_in_by_legacy.items():
            singer_uuid = singer_uuid_by_legacy.get(singer_legacy)
            if singer_uuid is None:
                continue
            audio_path = None
            if song.get("audio"):
                audio_path = f"recordings/{song['audio']}"
            if song_legacy in existing_song_by_legacy:
                existing = existing_song_by_legacy[song_legacy]
                if audio_path is None and existing.get("audio_path"):
                    audio_path = existing["audio_path"]
                self.client.table("songs").update({
                    "singer_id": singer_uuid,
                    "name": song["name"],
                    "lyrics": song.get("lyrics", ""),
                    "sung": song.get("sung", False),
                    "audio_path": audio_path,
                }).eq("id", existing["id"]).execute()
            else:
                self.client.table("songs").insert({
                    "legacy_id": song_legacy,
                    "singer_id": singer_uuid,
                    "name": song["name"],
                    "lyrics": song.get("lyrics", ""),
                    "sung": song.get("sung", False),
                    "audio_path": audio_path,
                }).execute()

        for song_legacy, existing in existing_song_by_legacy.items():
            if song_legacy not in songs_in_by_legacy:
                self.client.table("songs").delete().eq("id", existing["id"]).execute()

    # ===== 增量 CRUD：歌手 =====
    def create_singer(self, legacy_id: str, name: str) -> None:
        """添加一位歌手。"""
        self.client.table("singers").insert({
            "legacy_id": legacy_id,
            "name": name,
        }).execute()

    def update_singer(self, legacy_id: str, name: str) -> None:
        """更新歌手名。"""
        self.client.table("singers").update({"name": name}).eq(
            "legacy_id", legacy_id
        ).execute()

    def delete_singer(self, legacy_id: str) -> None:
        """删除歌手及其所有歌曲（级联删除由数据库保证）。"""
        singer_uuid = self._get_singer_uuid(legacy_id)
        if singer_uuid:
            self.client.table("singers").delete().eq("id", singer_uuid).execute()

    # ===== 增量 CRUD：歌曲 =====
    def create_song(
        self,
        singer_legacy_id: str,
        legacy_id: str,
        name: str,
        lyrics: str = "",
        sung: bool = False,
    ) -> None:
        """添加一首歌曲。"""
        singer_uuid = self._get_singer_uuid(singer_legacy_id)
        if singer_uuid is None:
            return
        self.client.table("songs").insert({
            "legacy_id": legacy_id,
            "singer_id": singer_uuid,
            "name": name,
            "lyrics": lyrics,
            "sung": sung,
        }).execute()

    def update_song(self, legacy_id: str, **kwargs) -> None:
        """更新歌曲字段（name / lyrics / sung 等）。"""
        valid_fields = {"name", "lyrics", "sung", "audio_path", "singer_id"}
        updates = {k: v for k, v in kwargs.items() if k in valid_fields}
        if not updates:
            return
        self.client.table("songs").update(updates).eq(
            "legacy_id", legacy_id
        ).execute()

    def delete_song(self, legacy_id: str) -> None:
        """删除一首歌曲。"""
        self.client.table("songs").delete().eq(
            "legacy_id", legacy_id
        ).execute()

    def set_song_sung(self, legacy_id: str, sung: bool) -> None:
        """标记/取消标记已唱。"""
        self.client.table("songs").update({"sung": sung}).eq(
            "legacy_id", legacy_id
        ).execute()

    # ===== 录音相关 =====
    def get_song_audio_path(self, song_legacy_id: str) -> str | None:
        """获取歌曲当前保存的 audio_path。"""
        resp = (
            self.client.table("songs")
            .select("audio_path")
            .eq("legacy_id", song_legacy_id)
            .execute()
        )
        data = resp.data or []
        if data:
            return data[0].get("audio_path")
        return None

    def set_song_audio_path(self, song_legacy_id: str, audio_path: str | None) -> None:
        """设置歌曲的 audio_path。"""
        self.client.table("songs").update({"audio_path": audio_path}).eq(
            "legacy_id", song_legacy_id
        ).execute()

    def delete_song_audio_path(self, song_legacy_id: str) -> None:
        """清空歌曲的 audio_path。"""
        self.client.table("songs").update({"audio_path": None}).eq(
            "legacy_id", song_legacy_id
        ).execute()

    def batch_get_audio_paths(self, song_legacy_ids: list) -> dict:
        """批量获取多首歌曲的 audio_path，返回 {legacy_id: audio_path}。"""
        if not song_legacy_ids:
            return {}
        resp = (
            self.client.table("songs")
            .select("legacy_id, audio_path")
            .in_("legacy_id", song_legacy_ids)
            .execute()
        )
        return {r["legacy_id"]: r.get("audio_path") for r in (resp.data or [])}
