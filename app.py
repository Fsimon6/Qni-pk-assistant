# -*- coding: utf-8 -*-
"""歌手PK助手 - 基于 Streamlit 的歌单管理工具

性能优化版：使用增量 CRUD，避免全量同步。
"""

import io
import os
import uuid
import wave

import streamlit as st
from audio_recorder_streamlit import audio_recorder
import lameenc

import db as db_module
import storage as storage_module

# ===== 配置 =====
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DATA_DIR = os.path.join(BASE_DIR, "data")
AUDIO_DIR = os.path.join(DATA_DIR, "audio")
MAX_RECORD_SECONDS = 10
COLS_PER_ROW = 4

_db = None


def _get_db():
    global _db
    if _db is None:
        _db = db_module.Database()
    return _db


# ===== 数据接口 =====
def load_data() -> dict:
    return _get_db().load_data()


def _get_cached_data() -> dict:
    """获取缓存数据，避免每次 rerun 查询数据库。"""
    cache_key = "singer_data_cache"
    if cache_key not in st.session_state:
        st.session_state[cache_key] = load_data()
    return st.session_state[cache_key]


def _invalidate_cache() -> None:
    """清除缓存。"""
    st.session_state.pop("singer_data_cache", None)


def gen_id() -> str:
    return uuid.uuid4().hex[:8]


# ===== 录音处理 =====
def save_audio_as_mp3(audio_bytes, sid: str) -> str:
    """将录音字节（WAV）保存为 MP3 并上传到 Supabase Storage。

    使用带时间戳的唯一文件名（格式：``{sid}_{timestamp}.mp3``），
    避免重录时命中 Supabase Storage CDN 的旧版本缓存。
    """
    if not audio_bytes:
        return ""
    try:
        # 强制转为 bytes，兼容 bytearray / memoryview
        if not isinstance(audio_bytes, bytes):
            audio_bytes = bytes(audio_bytes)
        with wave.open(io.BytesIO(audio_bytes), "rb") as wav:
            sample_rate = wav.getframerate()
            channels = wav.getnchannels()
            max_frames = sample_rate * MAX_RECORD_SECONDS
            frames = wav.readframes(min(wav.getnframes(), max_frames))
        encoder = lameenc.Encoder()
        encoder.set_bit_rate(128)
        encoder.set_in_sample_rate(sample_rate)
        encoder.set_channels(channels)
        encoder.set_quality(2)
        mp3_data = encoder.encode(frames) + encoder.flush()
        # 带时间戳/微秒的唯一文件名，保证每次重录都是新路径，不命中 CDN 旧缓存
        import time as _time
        ts = int(_time.time() * 1_000_000)  # 微秒级，足够唯一
        filename = f"{sid}_{ts}.mp3"
        storage_module.upload_recording(mp3_data, sid, file_name=filename)
        return filename
    except Exception as e:
        st.error(f"保存录音失败: {e}")
        return ""


def get_audio_path(song: dict):
    """返回歌曲是否有关联的录音（基于 DB 的 audio 字段）。
    不再访问 Storage，避免逐首歌曲扫描。"""
    audio = song.get("audio")
    if audio:
        return audio
    return None


def delete_audio(song: dict) -> None:
    """删除歌曲关联的 Storage 录音和 DB 中的 audio_path。"""
    sid = song.get("id")
    if sid:
        storage_module.delete_recording(sid)
        _get_db().delete_song_audio_path(sid)
    song.pop("audio", None)


# ===== 歌单解析 =====
def extract_lyrics(song_token: str) -> tuple:
    if "（" in song_token and song_token.endswith("）"):
        idx = song_token.find("（")
        name = song_token[:idx].strip()
        lyrics = song_token[idx + 1:-1].strip()
        if name:
            return name, lyrics
    if "(" in song_token and song_token.endswith(")"):
        idx = song_token.find("(")
        name = song_token[:idx].strip()
        lyrics = song_token[idx + 1:-1].strip()
        if name:
            return name, lyrics
    if "：" in song_token:
        idx = song_token.find("：")
        name = song_token[:idx].strip()
        lyrics = song_token[idx + 1:].strip()
        if name and lyrics:
            return name, lyrics
    if ":" in song_token:
        idx = song_token.find(":")
        name = song_token[:idx].strip()
        lyrics = song_token[idx + 1:].strip()
        if name and lyrics:
            return name, lyrics
    return song_token.strip(), ""


def parse_text(text: str, separator: str = " - ", fmt: str = "auto") -> dict:
    if fmt == "auto":
        lines_with_sep = sum(1 for l in text.splitlines() if separator in l)
        fmt = "singer_dash_song" if lines_with_sep >= 1 else "singer_line_song_line"

    singers = {}
    if fmt == "singer_dash_song":
        for line in text.splitlines():
            line = line.strip()
            if not line or separator not in line:
                continue
            parts = line.split(separator, 1)
            singer_name = parts[0].strip()
            song_name, lyrics = extract_lyrics(parts[1].strip())
            if singer_name and song_name:
                singers.setdefault(singer_name, [])
                if not any(s["name"] == song_name for s in singers[singer_name]):
                    singers[singer_name].append({"name": song_name, "lyrics": lyrics})
    else:
        lines = [l.rstrip() for l in text.splitlines()]
        i = 0
        n = len(lines)
        while i < n:
            line = lines[i].strip()
            if not line:
                i += 1
                continue
            singer_name = line
            songs_list = []
            if i + 1 < n:
                song_line = lines[i + 1].strip()
                for token in song_line.split():
                    song_name, lyrics = extract_lyrics(token)
                    if song_name and not any(s["name"] == song_name for s in songs_list):
                        songs_list.append({"name": song_name, "lyrics": lyrics})
            singers[singer_name] = songs_list
            i += 2
    return singers


def parse_docx(file_bytes, separator: str = " - ", fmt: str = "auto") -> dict:
    from docx import Document
    from io import BytesIO
    doc = Document(BytesIO(file_bytes))
    text = "\n".join(p.text for p in doc.paragraphs)
    return parse_text(text, separator, fmt)


# ===== 会话状态 =====
def init_state() -> None:
    if "page" not in st.session_state:
        st.session_state.page = "main"
    if "selected_singer" not in st.session_state:
        st.session_state.selected_singer = None
    if "selected_song" not in st.session_state:
        st.session_state.selected_song = None
    if "search_query" not in st.session_state:
        st.session_state.search_query = ""


# ===== 主页 =====
def main_page() -> None:
    st.title("🎵 歌手PK助手")
    db = _get_db()

    with st.sidebar:
        st.header("⚙️ 操作")
        if st.button("📥 导入歌单", use_container_width=True):
            st.session_state.page = "import"
            st.rerun()
        if st.button("🔄 重置所有标记", use_container_width=True):
            data = _get_cached_data()
            changed = False
            for singer in data["singers"]:
                for song in singer["songs"]:
                    if song.get("sung", False):
                        db.set_song_sung(song["id"], False)
                        song["sung"] = False
                        changed = True
            if changed:
                st.success("已重置所有已唱标记！")
                st.rerun()
            else:
                st.info("暂无已唱标记需要重置。")

        st.divider()
        st.header("➕ 添加歌手")
        new_singer = st.text_input("歌手名", key="new_singer_input", label_visibility="collapsed")
        if st.button("添加歌手", use_container_width=True):
            if new_singer.strip():
                data = _get_cached_data()
                if any(s["name"] == new_singer.strip() for s in data["singers"]):
                    st.warning("该歌手已存在！")
                else:
                    new_id = gen_id()
                    db.create_singer(new_id, new_singer.strip())
                    data["singers"].append({"id": new_id, "name": new_singer.strip(), "songs": []})
                    st.success(f"已添加歌手：{new_singer.strip()}")
                    st.rerun()
            else:
                st.warning("请输入歌手名。")

    data = _get_cached_data()
    search = st.text_input("🔍 搜索歌手", value=st.session_state.search_query, placeholder="输入歌手名进行筛选")
    st.session_state.search_query = search

    singers = data["singers"]
    if search:
        singers = [s for s in singers if search.lower() in s["name"].lower()]

    total_singers = len(data["singers"])
    total_songs = sum(len(s["songs"]) for s in data["singers"])
    total_sung = sum(1 for s in data["singers"] for song in s["songs"] if song.get("sung", False))
    st.caption(f"共 {total_singers} 位歌手 · {total_songs} 首歌 · 已唱 {total_sung} 首")
    st.subheader(f"歌手列表（{len(singers)} 位）")

    if not singers:
        st.info("暂无歌手，请通过侧边栏添加或导入歌单。")
        return

    cols = st.columns(COLS_PER_ROW)
    for i, singer in enumerate(singers):
        with cols[i % COLS_PER_ROW]:
            song_count = len(singer["songs"])
            sung_count = sum(1 for s in singer["songs"] if s.get("sung", False))
            label = f"🎤 {singer['name']}\n({sung_count}/{song_count})"
            if st.button(label, key=f"singer_{singer['id']}", use_container_width=True):
                st.session_state.selected_singer = singer["id"]
                st.session_state.selected_song = None
                st.session_state.page = "singer"
                st.rerun()


# ===== 歌曲行 Fragment =====
@st.fragment
def _render_song_row(sid: str, singer_id: str) -> None:
    """渲染单个歌曲行（fragment 版本，支持独立重新渲染）。"""
    data = st.session_state.get("singer_data_cache", {})
    singer = next((s for s in data.get("singers", []) if s["id"] == singer_id), None)
    song = next((s for s in singer.get("songs", []) if s["id"] == sid), None) if singer else None

    if not song:
        st.markdown("<div style='text-align:center; opacity:0.3'>（歌曲已删除）</div>", unsafe_allow_html=True)
        return

    db = _get_db()
    is_sung = song.get("sung", False)
    has_audio = song.get("audio")

    c1_stat, c2_name, c3_lyrics, c4_btn1, c5_btn2, c6_btn3, c7_audio = st.columns([0.25, 1.4, 2.8, 1.0, 1.0, 1.0, 1.0])

    with c1_stat:
        if is_sung:
            st.markdown("<div style='font-size:1.3rem; line-height:2.2rem; text-align:center'>🔴</div>", unsafe_allow_html=True)
        else:
            st.markdown("<div style='font-size:1.3rem; line-height:2.2rem; text-align:center; opacity:0.1'>·</div>", unsafe_allow_html=True)

    with c2_name:
        new_name = st.text_input("歌名", value=song["name"], key=f"name_{sid}", label_visibility="collapsed")
        if is_sung:
            st.caption("⚠️已唱")

    with c3_lyrics:
        new_lyrics = st.text_area("歌词", value=song.get("lyrics", ""), key=f"lyrics_{sid}", label_visibility="collapsed", height=34, placeholder="歌词...")

    with c4_btn1:
        if is_sung:
            if st.button("↩️取消", key=f"unsung_{sid}", use_container_width=True):
                db.set_song_sung(sid, False)
                song["sung"] = False
                st.rerun(scope="fragment")
        else:
            if st.button("🔴标记", key=f"sung_{sid}", use_container_width=True):
                db.set_song_sung(sid, True)
                song["sung"] = True
                st.rerun(scope="fragment")

    with c5_btn2:
        if st.button("💾保存", key=f"save_{sid}", use_container_width=True):
            if new_name.strip():
                db.update_song(sid, name=new_name.strip(), lyrics=new_lyrics)
                song["name"] = new_name.strip()
                song["lyrics"] = new_lyrics
                st.success("已保存修改")
                st.rerun(scope="fragment")
            else:
                st.warning("歌名不能为空")

    with c6_btn3:
        if st.button("🗑️删除", key=f"del_{sid}", type="secondary", use_container_width=True):
            storage_module.delete_recording(sid)
            db.delete_song(sid)
            singer["songs"] = [s for s in singer["songs"] if s["id"] != sid]
            st.rerun(scope="app")

    with c7_audio:
        if not has_audio:
            audio_bytes = audio_recorder(
                text="",
                icon_size="1.5x",
                key=f"rec_{sid}",
            )
            if audio_bytes:
                filename = save_audio_as_mp3(audio_bytes, sid)
                if filename:
                    db.set_song_audio_path(sid, f"recordings/{filename}")
                    song["audio"] = f"recordings/{filename}"
                    st.rerun(scope="fragment")
        else:
            audio_bytes = storage_module.get_recording_bytes(sid)
            if audio_bytes:
                st.audio(audio_bytes, format="audio/mp3")
            else:
                st.caption("录音不可用")
            if st.button("重录", key=f"rerec_{sid}", use_container_width=True):
                storage_module.delete_recording(sid)
                db.delete_song_audio_path(sid)
                song.pop("audio", None)
                st.rerun(scope="fragment")

    st.divider()


# ===== 歌手详情页 =====
def singer_page() -> None:
    data = _get_cached_data()
    db = _get_db()
    singer = next((s for s in data["singers"] if s["id"] == st.session_state.selected_singer), None)

    if not singer:
        st.error("歌手不存在！")
        if st.button("返回主页"):
            st.session_state.page = "main"
            st.rerun()
        return

    col_back, col_spacer, col_del = st.columns([1, 6, 1])
    with col_back:
        if st.button("⬅️ 返回"):
            st.session_state.page = "main"
            st.session_state.selected_song = None
            st.rerun()
    with col_del:
        if st.button("🗑️ 删除歌手", type="secondary"):
            for song in singer["songs"]:
                storage_module.delete_recording(song["id"])
            db.delete_singer(singer["id"])
            st.session_state.page = "main"
            st.session_state.selected_song = None
            st.rerun()

    st.title("🎤 " + singer["name"])

    with st.expander("✏️ 编辑歌手名"):
        new_name = st.text_input("歌手名", value=singer["name"], key=f"edit_singer_{singer['id']}")
        if st.button("保存歌手名", key=f"save_singer_{singer['id']}"):
            if new_name.strip():
                db.update_singer(singer["id"], new_name.strip())
                singer["name"] = new_name.strip()
                st.success("已保存")
                st.rerun()
            else:
                st.warning("歌手名不能为空。")

    with st.expander("➕ 添加歌曲"):
        add_col1, add_col2, add_col3 = st.columns([2, 3, 1])
        with add_col1:
            new_song_name = st.text_input("歌名", key=f"new_song_{singer['id']}", label_visibility="collapsed", placeholder="请输入歌名")
        with add_col2:
            new_song_lyrics = st.text_input("歌词（可选）", key=f"new_song_lyrics_{singer['id']}", label_visibility="collapsed", placeholder="歌词（可选）")
        with add_col3:
            if st.button("添加歌曲", key=f"add_song_btn_{singer['id']}", use_container_width=True):
                if new_song_name.strip():
                    if any(s["name"] == new_song_name.strip() for s in singer["songs"]):
                        st.warning("该歌曲已存在！")
                    else:
                        new_song_id = gen_id()
                        db.create_song(
                            singer["id"], new_song_id,
                            new_song_name.strip(), new_song_lyrics.strip()
                        )
                        singer["songs"].append({
                            "id": new_song_id,
                            "name": new_song_name.strip(),
                            "lyrics": new_song_lyrics.strip(),
                            "sung": False
                        })
                        st.success(f"已添加：{new_song_name.strip()}")
                        st.rerun()
                else:
                    st.warning("请输入歌名。")

    song_query = st.text_input(
        "🔍 搜索歌曲",
        value=st.session_state.get("song_search", ""),
        placeholder="输入歌名或歌词关键词...",
        key="song_search_input",
    )
    if song_query:
        st.session_state["song_search"] = song_query
    else:
        st.session_state["song_search"] = ""

    songs = singer["songs"]
    q = song_query.strip().lower()
    if q:
        songs = [s for s in songs if q in s["name"].lower() or q in s.get("lyrics", "").lower()]

    sung_count = sum(1 for s in songs if s.get("sung", False))
    all_sung_count = sum(1 for s in singer["songs"] if s.get("sung", False))
    caption = f"找到 {len(songs)} 首 · 已唱 {sung_count} 首"
    if q:
        caption += f"（全部共 {len(singer['songs'])} 首 · 已唱 {all_sung_count} 首）"
    st.caption(caption)
    st.subheader(f"歌曲列表（{len(songs) if q else len(singer['songs'])} 首 · 已唱 {sung_count if q else all_sung_count} 首）")

    if not singer["songs"]:
        st.info("暂无歌曲，请添加或导入。")
        return
    if q and not songs:
        st.warning("未找到匹配的歌曲。")
        return

    st.markdown(
        """
        <style>
        hr { margin: 0.1rem 0 !important; padding: 0 !important; }
        [data-testid="stTextInput"] > div > div,
        [data-testid="stTextArea"] > div > div {
            height: 34px !important; min-height: 34px !important; max-height: 34px !important;
            padding-top: 0 !important; padding-bottom: 0 !important;
        }
        [data-testid="stTextInput"] > div,
        [data-testid="stTextArea"] > div,
        [data-testid="stButton"] > div {
            padding-top: 0 !important; padding-bottom: 0 !important;
            margin-top: 0 !important; margin-bottom: 0 !important;
        }
        [data-testid="stTextInput"] input {
            padding-top: 0.25rem !important; padding-bottom: 0.25rem !important;
            height: 34px !important; box-sizing: border-box !important; border-radius: 4px !important;
        }
        [data-testid="stTextArea"] textarea {
            padding-top: 0.35rem !important; padding-bottom: 0.35rem !important;
            padding-left: 0.6rem !important; padding-right: 0.6rem !important;
            line-height: 1.05 !important; height: 34px !important; min-height: 34px !important;
            max-height: 34px !important; box-sizing: border-box !important;
            resize: none !important; overflow-y: hidden !important; overflow-x: auto !important;
            border-radius: 4px !important;
        }
        [data-testid="stCaptionContainer"] {
            margin-top: 0.05rem !important; margin-bottom: 0 !important;
            padding-top: 0 !important; padding-bottom: 0 !important;
        }
        [data-testid="stCaptionContainer"] p {
            margin-top: 0 !important; margin-bottom: 0 !important;
            line-height: 1 !important; padding-top: 0 !important; padding-bottom: 0 !important;
        }
        div[data-testid="stHorizontalBlock"] {
            gap: 0.4rem !important; row-gap: 0 !important;
        }
        </style>
        """,
        unsafe_allow_html=True
    )

    hdr_s, hdr_n, hdr_l, hdr_b1, hdr_b2, hdr_b3, hdr_b4 = st.columns([0.25, 1.4, 2.8, 1.0, 1.0, 1.0, 1.0])
    with hdr_s: st.caption("状态")
    with hdr_n: st.caption("歌名")
    with hdr_l: st.caption("歌词（支持增删改查）")
    with hdr_b1: st.caption("PK标记")
    with hdr_b2: st.caption("保存")
    with hdr_b3: st.caption("删除")
    with hdr_b4: st.caption("录音")

    for song in songs:
        _render_song_row(song["id"], singer["id"])


# ===== 导入页 =====
def import_page() -> None:
    st.title("📥 导入歌单")
    db = _get_db()

    if st.button("⬅️ 返回主页"):
        st.session_state.page = "main"
        if "import_preview" in st.session_state:
            del st.session_state.import_preview
        st.rerun()

    st.write("支持两种格式（自动识别）：")
    st.write("• **格式A**：每行一首，`歌手名 - 歌名`")
    st.write("• **格式B**：歌手名独占一行，下一行为该歌手的歌曲（空格分隔）")

    separator = st.text_input("格式A分隔符", value=" - ", help="仅格式A使用")

    tab_text, tab_docx = st.tabs(["📝 纯文本粘贴", "📄 Word文档上传"])

    with tab_text:
        text = st.text_area("粘贴歌单文本", height=300, key="import_text", placeholder="周杰伦\n晴天 七里香\n\n林俊杰\n江南...")
        if st.button("🔍 解析预览", key="parse_text_btn"):
            if text.strip():
                result = parse_text(text, separator)
                if result:
                    st.session_state.import_preview = result
                    st.rerun()
                else:
                    st.warning("未解析到有效数据，请检查格式和分隔符。")
            else:
                st.warning("请先粘贴歌单文本。")

    with tab_docx:
        uploaded = st.file_uploader("上传 .docx 文件", type=["docx"], key="upload_docx")
        if uploaded is not None:
            try:
                result = parse_docx(uploaded.getvalue(), separator)
                if result:
                    st.session_state.import_preview = result
                    st.rerun()
                else:
                    st.warning("文档中未解析到有效数据。")
            except Exception as e:
                st.error(f"解析失败：{e}")

    if "import_preview" in st.session_state:
        result = st.session_state.import_preview
        if not result:
            st.warning("未解析到有效数据。")
            return

        st.divider()
        total_songs = sum(len(songs) for songs in result.values())
        total_with_lyrics = sum(1 for songs in result.values() for s in songs if s.get("lyrics"))
        st.subheader(f"📋 解析预览（{len(result)} 位歌手 · {total_songs} 首歌 · {total_with_lyrics} 首含歌词）")
        st.caption("💡 提示：英文多词歌名会被空格拆分，导入后可在歌手详情页手动修正。")

        for singer_name, songs in result.items():
            with st.expander(f"🎤 {singer_name}（{len(songs)} 首）"):
                rows = [{"歌名": s["name"], "歌词": s.get("lyrics", "")} for s in songs]
                if rows:
                    st.table(rows)
                else:
                    st.write("（无歌曲）")

        col1, col2 = st.columns(2)
        with col1:
            if st.button("✅ 确认导入", type="primary", use_container_width=True):
                data = _get_cached_data()
                added_singers = 0
                added_songs = 0
                for singer_name, songs in result.items():
                    singer = next((s for s in data["singers"] if s["name"] == singer_name), None)
                    if not singer:
                        new_id = gen_id()
                        db.create_singer(new_id, singer_name)
                        data["singers"].append({"id": new_id, "name": singer_name, "songs": []})
                        singer = data["singers"][-1]
                        added_singers += 1
                    for song in songs:
                        if not any(s["name"] == song["name"] for s in singer["songs"]):
                            new_song_id = gen_id()
                            db.create_song(
                                singer["id"], new_song_id,
                                song["name"], song.get("lyrics", "")
                            )
                            singer["songs"].append({
                                "id": new_song_id,
                                "name": song["name"],
                                "lyrics": song.get("lyrics", ""),
                                "sung": False
                            })
                            added_songs += 1
                del st.session_state.import_preview
                st.success(f"导入成功！新增 {added_singers} 位歌手，{added_songs} 首歌。")
                st.session_state.page = "main"
                st.rerun()
        with col2:
            if st.button("❌ 取消", use_container_width=True):
                del st.session_state.import_preview
                st.rerun()


# ===== 主入口 =====
def main() -> None:
    st.set_page_config(
        page_title="歌手PK助手",
        page_icon="🎵",
        layout="wide",
        initial_sidebar_state="expanded"
    )
    st.markdown(
        """
        <style>
        div.stButton > button {
            white-space: pre-line;
            word-break: break-word;
        }
        </style>
        """,
        unsafe_allow_html=True
    )
    init_state()
    page = st.session_state.page
    if page == "main":
        main_page()
    elif page == "singer":
        singer_page()
    elif page == "import":
        import_page()


if __name__ == "__main__":
    main()
