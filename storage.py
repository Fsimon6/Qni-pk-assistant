# -*- coding: utf-8 -*-
"""Supabase Storage 访问层：封装录音文件的上传、下载、删除和 URL 获取。"""

import io

from supabase import Client, create_client

import config


BUCKET_NAME = "recordings"


class AudioStorage:
    """Supabase Storage 录音文件访问封装。"""

    def __init__(self) -> None:
        url = config.get_supabase_url()
        key = config.get_supabase_key()
        self.client: Client = create_client(url, key)
        self._cached_file_list: list | None = None

    def invalidate_cache(self) -> None:
        """清除文件列表缓存。"""
        self._cached_file_list = None

    def _get_file_list(self, force_refresh: bool = False) -> list:
        """获取桶内所有文件列表（带缓存）。"""
        if self._cached_file_list is None or force_refresh:
            self._cached_file_list = self.client.storage.from_(BUCKET_NAME).list() or []
        return self._cached_file_list

    def _path(self, song_legacy_id: str) -> str:
        return f"{song_legacy_id}.mp3"

    def upload_recording(self, audio_bytes: bytes, song_legacy_id: str, file_name: str | None = None) -> str:
        """上传 MP3 录音到 Supabase Storage，返回 storage 路径。

        - 自动将 bytearray / memoryview 等类型转换为 bytes。
        - 默认 file_name 为空时使用 {song_legacy_id}.mp3；重录场景建议传带时间戳的 file_name，避免命中 CDN 旧版本缓存。
        - 附带禁止 CDN 缓存的 header，减少重录时的缓存不一致风险。
        """
        if file_name:
            path = file_name if file_name.endswith(".mp3") else file_name + ".mp3"
        else:
            path = self._path(song_legacy_id)
        # 强制转换为 bytes，兼容 bytearray / memoryview 等
        if not isinstance(audio_bytes, bytes):
            audio_bytes = bytes(audio_bytes)
        self.client.storage.from_(BUCKET_NAME).upload(
            path,
            audio_bytes,
            {
                "content-type": "audio/mpeg",
                "upsert": "true",
                "cache-control": "no-store, no-cache, must-revalidate, max-age=0",
            },
        )
        self.invalidate_cache()
        return f"{BUCKET_NAME}/{path}"

    def _find_path(self, song_legacy_id: str, force_refresh: bool = False) -> str | None:
        """在桶内查找某首歌的实际 MP3 文件名。

        重录场景下文件名可能是 ``{sid}.mp3`` 或 ``{sid}_{timestamp}.mp3``，
        本方法返回桶中实际存在的匹配项（优先最新/时间戳最大）。
        """
        files = self._get_file_list(force_refresh=force_refresh)
        prefix_dot = f"{song_legacy_id}."
        prefix_us = f"{song_legacy_id}_"
        candidates = [
            f.get("name") for f in files
            if (
                isinstance(f.get("name"), str)
                and f.get("name").endswith(".mp3")
                and (
                    f["name"].startswith(prefix_dot)
                    or f["name"].startswith(prefix_us)
                )
            )
        ]
        if not candidates:
            return None
        # 优先选择字典序更大的（通常意味着时间戳后缀更晚的版本）
        candidates.sort(reverse=True)
        return candidates[0]

    def recording_exists(self, song_legacy_id: str) -> bool:
        """检查某首歌曲是否已有录音文件（使用缓存的文件列表）。"""
        try:
            return self._find_path(song_legacy_id) is not None
        except Exception:
            return False

    def batch_recordings_exist(self, song_legacy_ids: list) -> dict:
        """批量检查多首歌曲是否有录音，返回 {legacy_id: bool}。"""
        try:
            files = self._get_file_list()
            names = {f.get("name") for f in files if isinstance(f.get("name"), str)}
            result = {}
            for sid in song_legacy_ids:
                prefix_dot = f"{sid}."
                prefix_us = f"{sid}_"
                found = any(
                    n.endswith(".mp3") and (n.startswith(prefix_dot) or n.startswith(prefix_us))
                    for n in names
                )
                result[sid] = found
            return result
        except Exception:
            return {sid: False for sid in song_legacy_ids}

    def get_recording_bytes(self, song_legacy_id: str) -> bytes | None:
        """下载 MP3 文件字节，失败返回 None。
        先通过缓存列表确认文件存在，并使用 _find_path 定位实际文件名（可能含时间戳）。"""
        try:
            path = self._find_path(song_legacy_id, force_refresh=True)
            if not path:
                return None
            return self.client.storage.from_(BUCKET_NAME).download(path)
        except Exception:
            return None

    def get_recording_url(self, song_legacy_id: str) -> str | None:
        """获取录音文件的公开 URL。使用桶内真实文件名（可能含时间戳）。"""
        try:
            path = self._find_path(song_legacy_id)
            if not path:
                return None
            res = self.client.storage.from_(BUCKET_NAME).get_public_url(path)
            return res
        except Exception:
            return None

    def delete_recording(self, song_legacy_id: str) -> None:
        """删除某首歌曲的录音文件。

        为了兼容重录场景下使用带时间戳的唯一文件名（如 sid_1787800000.mp3），
        这里会删除存储桶中所有以 ``{song_legacy_id}.`` 或 ``{song_legacy_id}_``
        开头的 mp3 文件，保证无历史碎片残留。
        """
        try:
            files = self._get_file_list(force_refresh=True)
            prefix_dot = f"{song_legacy_id}."
            prefix_us = f"{song_legacy_id}_"
            to_remove = [
                f.get("name") for f in files
                if (
                    isinstance(f.get("name"), str)
                    and f.get("name").endswith(".mp3")
                    and (
                        f["name"].startswith(prefix_dot)
                        or f["name"].startswith(prefix_us)
                    )
                )
            ]
            if to_remove:
                self.client.storage.from_(BUCKET_NAME).remove(to_remove)
                self.invalidate_cache()
        except Exception:
            pass


_audio_storage: AudioStorage | None = None


def _get_storage() -> AudioStorage:
    global _audio_storage
    if _audio_storage is None:
        _audio_storage = AudioStorage()
    return _audio_storage


def upload_recording(audio_bytes: bytes, song_legacy_id: str, file_name: str | None = None) -> str:
    return _get_storage().upload_recording(audio_bytes, song_legacy_id, file_name=file_name)


def recording_exists(song_legacy_id: str) -> bool:
    return _get_storage().recording_exists(song_legacy_id)


def batch_recordings_exist(song_legacy_ids: list) -> dict:
    return _get_storage().batch_recordings_exist(song_legacy_ids)


def get_recording_bytes(song_legacy_id: str) -> bytes | None:
    return _get_storage().get_recording_bytes(song_legacy_id)


def get_recording_url(song_legacy_id: str) -> str | None:
    return _get_storage().get_recording_url(song_legacy_id)


def delete_recording(song_legacy_id: str) -> None:
    _get_storage().delete_recording(song_legacy_id)
