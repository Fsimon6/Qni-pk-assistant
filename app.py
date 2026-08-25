# -*- coding: utf-8 -*-
"""歌手PK助手 - 基于 Streamlit 的歌单管理工具"""

import io
import json
import os
import uuid
import wave

import streamlit as st
from audio_recorder_streamlit import audio_recorder
import lameenc

# ===== 配置 =====
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DATA_DIR = os.path.join(BASE_DIR, "data")
DATA_FILE = os.path.join(DATA_DIR, "songlist.json")
AUDIO_DIR = os.path.join(DATA_DIR, "audio")  # 录音文件存放目录
MAX_RECORD_SECONDS = 10  # 录音最长 10 秒

# 每行显示的卡片数（响应式网格）
COLS_PER_ROW = 4


# ===== 数据存储 =====
def load_data() -> dict:
    """读取本地 JSON 数据"""
    if os.path.exists(DATA_FILE):
        try:
            with open(DATA_FILE, "r", encoding="utf-8") as f:
                return json.load(f)
        except (json.JSONDecodeError, OSError):
            return {"singers": []}
    return {"singers": []}


def save_data(data: dict) -> None:
    """保存数据到本地 JSON"""
    os.makedirs(DATA_DIR, exist_ok=True)
    with open(DATA_FILE, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


def gen_id() -> str:
    """生成短ID"""
    return uuid.uuid4().hex[:8]


# ===== 录音处理 =====
def save_audio_as_mp3(audio_bytes: bytes, sid: str) -> str:
    """将录音字节（WAV）保存为 MP3，截断到 MAX_RECORD_SECONDS 秒。
    返回保存的文件名（不含目录）；失败返回空串。"""
    if not audio_bytes:
        return ""
    os.makedirs(AUDIO_DIR, exist_ok=True)
    try:
        # 解析 WAV 字节，截取前 N 秒的 PCM 帧
        with wave.open(io.BytesIO(audio_bytes), "rb") as wav:
            sample_rate = wav.getframerate()
            channels = wav.getnchannels()
            max_frames = sample_rate * MAX_RECORD_SECONDS
            frames = wav.readframes(min(wav.getnframes(), max_frames))
        # 用 lameenc 编码为 MP3（纯 Python，无需 ffmpeg）
        encoder = lameenc.Encoder()
        encoder.set_bit_rate(128)
        encoder.set_in_sample_rate(sample_rate)
        encoder.set_channels(channels)
        encoder.set_quality(2)
        mp3_data = encoder.encode(frames) + encoder.flush()
        filename = f"{sid}.mp3"
        with open(os.path.join(AUDIO_DIR, filename), "wb") as f:
            f.write(mp3_data)
        return filename
    except Exception as e:
        st.error(f"保存录音失败: {e}")
        return ""


def get_audio_path(song: dict):
    """返回歌曲关联的音频绝对路径，不存在则返回 None。"""
    audio = song.get("audio")
    if not audio:
        return None
    path = os.path.join(AUDIO_DIR, audio)
    return path if os.path.exists(path) else None


def delete_audio(song: dict) -> None:
    """删除歌曲关联的音频文件和字段。"""
    path = get_audio_path(song)
    if path and os.path.exists(path):
        os.remove(path)
    song.pop("audio", None)


# ===== 歌单解析 =====
def extract_lyrics(song_token: str) -> tuple:
    """从歌名 token 中提取歌名与歌词。

    支持的歌词标记：
    - 中文括号：歌名（歌词）
    - 英文括号：歌名(歌词)
    - 中文冒号：歌名：歌词
    - 英文冒号：歌名:歌词
    """
    # 中文括号
    if "（" in song_token and song_token.endswith("）"):
        idx = song_token.find("（")
        name = song_token[:idx].strip()
        lyrics = song_token[idx + 1:-1].strip()
        if name:
            return name, lyrics
    # 英文括号
    if "(" in song_token and song_token.endswith(")"):
        idx = song_token.find("(")
        name = song_token[:idx].strip()
        lyrics = song_token[idx + 1:-1].strip()
        if name:
            return name, lyrics
    # 中文冒号
    if "：" in song_token:
        idx = song_token.find("：")
        name = song_token[:idx].strip()
        lyrics = song_token[idx + 1:].strip()
        if name and lyrics:
            return name, lyrics
    # 英文冒号
    if ":" in song_token:
        idx = song_token.find(":")
        name = song_token[:idx].strip()
        lyrics = song_token[idx + 1:].strip()
        if name and lyrics:
            return name, lyrics
    return song_token.strip(), ""


def parse_text(text: str, separator: str = " - ", fmt: str = "auto") -> dict:
    """解析纯文本歌单。

    支持两种格式：
    - "singer_dash_song"：每行一首，格式 `歌手名<分隔符>歌名`
    - "singer_line_song_line"：歌手名独占一行，下一行为该歌手的歌曲（空格分隔）

    返回：{歌手: [{"name":..., "lyrics":...}, ...]}
    """
    if fmt == "auto":
        # 自动检测：包含分隔符的行数 >= 1 则按格式A
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
        # 歌手独占一行，下一行是歌曲列表（空格分隔）
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
    """解析 Word 文档"""
    from docx import Document
    from io import BytesIO

    doc = Document(BytesIO(file_bytes))
    text = "\n".join(p.text for p in doc.paragraphs)
    return parse_text(text, separator, fmt)


# ===== 会话状态初始化 =====
def init_state() -> None:
    if "page" not in st.session_state:
        st.session_state.page = "main"  # main / singer / import
    if "selected_singer" not in st.session_state:
        st.session_state.selected_singer = None
    if "selected_song" not in st.session_state:
        st.session_state.selected_song = None
    if "search_query" not in st.session_state:
        st.session_state.search_query = ""


# ===== 页面：主页（歌手列表） =====
def main_page() -> None:
    st.title("🎵 歌手PK助手")

    # 侧边栏操作
    with st.sidebar:
        st.header("⚙️ 操作")
        if st.button("📥 导入歌单", use_container_width=True):
            st.session_state.page = "import"
            st.rerun()
        if st.button("🔄 重置所有标记", use_container_width=True):
            data = load_data()
            changed = False
            for singer in data["singers"]:
                for song in singer["songs"]:
                    if song.get("sung", False):
                        song["sung"] = False
                        changed = True
            if changed:
                save_data(data)
                st.success("已重置所有已唱标记！")
                st.rerun()
            else:
                st.info("暂无已唱标记需要重置。")

        st.divider()
        st.header("➕ 添加歌手")
        new_singer = st.text_input("歌手名", key="new_singer_input", label_visibility="collapsed")
        if st.button("添加歌手", use_container_width=True):
            if new_singer.strip():
                data = load_data()
                if any(s["name"] == new_singer.strip() for s in data["singers"]):
                    st.warning("该歌手已存在！")
                else:
                    data["singers"].append({
                        "id": gen_id(),
                        "name": new_singer.strip(),
                        "songs": []
                    })
                    save_data(data)
                    st.success(f"已添加歌手：{new_singer.strip()}")
                    st.rerun()
            else:
                st.warning("请输入歌手名。")

    data = load_data()

    # 搜索框
    search = st.text_input("🔍 搜索歌手", value=st.session_state.search_query, placeholder="输入歌手名进行筛选")
    st.session_state.search_query = search

    singers = data["singers"]
    if search:
        singers = [s for s in singers if search.lower() in s["name"].lower()]

    # 统计信息
    total_singers = len(data["singers"])
    total_songs = sum(len(s["songs"]) for s in data["singers"])
    total_sung = sum(1 for s in data["singers"] for song in s["songs"] if song.get("sung", False))
    st.caption(f"共 {total_singers} 位歌手 · {total_songs} 首歌 · 已唱 {total_sung} 首")

    st.subheader(f"歌手列表（{len(singers)} 位）")

    if not singers:
        st.info("暂无歌手，请通过侧边栏添加或导入歌单。")
        return

    # 响应式网格
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


# ===== 页面：歌手详情 =====
def singer_page() -> None:
    data = load_data()
    singer = next((s for s in data["singers"] if s["id"] == st.session_state.selected_singer), None)

    if not singer:
        st.error("歌手不存在！")
        if st.button("返回主页"):
            st.session_state.page = "main"
            st.rerun()
        return

    # 顶部导航
    col_back, col_spacer, col_del = st.columns([1, 6, 1])
    with col_back:
        if st.button("⬅️ 返回"):
            st.session_state.page = "main"
            st.session_state.selected_song = None
            st.rerun()
    with col_del:
        if st.button("🗑️ 删除歌手", type="secondary"):
            data["singers"] = [s for s in data["singers"] if s["id"] != singer["id"]]
            save_data(data)
            st.session_state.page = "main"
            st.session_state.selected_song = None
            st.rerun()

    # 歌手名
    st.title("🎤 " + singer["name"])

    # 编辑歌手名
    with st.expander("✏️ 编辑歌手名"):
        new_name = st.text_input("歌手名", value=singer["name"], key=f"edit_singer_{singer['id']}")
        if st.button("保存歌手名", key=f"save_singer_{singer['id']}"):
            if new_name.strip():
                singer["name"] = new_name.strip()
                save_data(data)
                st.success("已保存")
                st.rerun()
            else:
                st.warning("歌手名不能为空。")

    # 添加歌曲
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
                        singer["songs"].append({
                            "id": gen_id(),
                            "name": new_song_name.strip(),
                            "lyrics": new_song_lyrics.strip(),
                            "sung": False
                        })
                        save_data(data)
                        st.success(f"已添加：{new_song_name.strip()}")
                        st.rerun()
                else:
                    st.warning("请输入歌名。")

    # 歌曲搜索框
    song_query = st.text_input(
        "🔍 搜索歌曲",
        value=st.session_state.get("song_search", ""),
        placeholder="输入歌名或歌词关键词...",
        key="song_search_input",
        on_change=lambda: setattr(st.session_state, "song_search", st.session_state.song_search_input) if False else None
    )
    # 同步搜索词到 session（兼容 Streamlit 状态）
    if song_query:
        st.session_state["song_search"] = song_query
    else:
        st.session_state["song_search"] = ""

    # 歌曲列表（表格行布局：状态 / 歌名 / 歌词 / 标记已唱 / 保存修改 / 删除歌曲）
    songs = singer["songs"]
    # 应用搜索过滤
    q = song_query.strip().lower()
    if q:
        songs = [
            s for s in songs
            if q in s["name"].lower() or q in s.get("lyrics", "").lower()
        ]
    sung_count = sum(1 for s in songs if s.get("sung", False))
    all_sung_count = sum(1 for s in singer["songs"] if s.get("sung", False))
    if q:
        st.caption(f"找到 {len(songs)} 首 · 已唱 {sung_count} 首（全部共 {len(singer['songs'])} 首 · 已唱 {all_sung_count} 首）")
    st.subheader(f"歌曲列表（{len(songs) if q else len(singer['songs'])} 首 · 已唱 {sung_count if q else all_sung_count} 首）")

    if not singer["songs"]:
        st.info("暂无歌曲，请添加或导入。")
        return

    if q and not songs:
        st.warning("未找到匹配的歌曲。")
        return

    # 自定义 CSS：压缩行距 + 控件间距
    st.markdown(
        """
        <style>
        /* 歌与歌之间的分隔线：上下边距从默认压缩到约原来的 1/4 */
        hr { margin: 0.1rem 0 !important; padding: 0 !important; }

        /* ===== 歌名输入框 / 歌词框 高度完全一致 ===== */
        /* 统一两个输入框外层 div 的高度（强制固定） */
        [data-testid="stTextInput"] > div > div,
        [data-testid="stTextArea"] > div > div {
            height: 34px !important;
            min-height: 34px !important;
            max-height: 34px !important;
            padding-top: 0 !important;
            padding-bottom: 0 !important;
        }
        /* 压缩所有输入框/文本框/按钮容器的上下内边距 */
        [data-testid="stTextInput"] > div,
        [data-testid="stTextArea"] > div,
        [data-testid="stButton"] > div {
            padding-top: 0 !important;
            padding-bottom: 0 !important;
            margin-top: 0 !important;
            margin-bottom: 0 !important;
        }
        /* 歌名单行 input：内部高度填充 */
        [data-testid="stTextInput"] input {
            padding-top: 0.25rem !important;
            padding-bottom: 0.25rem !important;
            height: 34px !important;
            box-sizing: border-box !important;
            border-radius: 4px !important;
        }
        /* 歌词 textarea：强制高度、去掉右下调节把手、禁止纵向扩大 */
        [data-testid="stTextArea"] textarea {
            padding-top: 0.35rem !important;
            padding-bottom: 0.35rem !important;
            padding-left: 0.6rem !important;
            padding-right: 0.6rem !important;
            line-height: 1.05 !important;
            height: 34px !important;
            min-height: 34px !important;
            max-height: 34px !important;
            box-sizing: border-box !important;
            resize: none !important;               /* 去掉右下角拖动手柄 */
            overflow-y: hidden !important;         /* 高度超出不显示滚动条 */
            overflow-x: auto !important;
            border-radius: 4px !important;
        }
        /* caption 不要额外占高度（已唱提示） */
        [data-testid="stCaptionContainer"] {
            margin-top: 0.05rem !important;
            margin-bottom: 0 !important;
            padding-top: 0 !important;
            padding-bottom: 0 !important;
        }
        [data-testid="stCaptionContainer"] p {
            margin-top: 0 !important;
            margin-bottom: 0 !important;
            line-height: 1 !important;
            padding-top: 0 !important;
            padding-bottom: 0 !important;
        }
        /* 列容器也压缩上下边距 */
        div[data-testid="stHorizontalBlock"] {
            gap: 0.4rem !important;
            row-gap: 0 !important;
        }
        </style>
        """,
        unsafe_allow_html=True
    )

    # 表头行（静态文字说明）
    hdr_s, hdr_n, hdr_l, hdr_b1, hdr_b2, hdr_b3, hdr_b4 = st.columns([0.25, 1.4, 2.8, 1.0, 1.0, 1.0, 1.0])
    with hdr_s:
        st.caption("状态")
    with hdr_n:
        st.caption("歌名")
    with hdr_l:
        st.caption("歌词（支持增删改查）")
    with hdr_b1:
        st.caption("PK标记")
    with hdr_b2:
        st.caption("保存")
    with hdr_b3:
        st.caption("删除")
    with hdr_b4:
        st.caption("录音")

    # 每首歌一行
    for song in songs:
        is_sung = song.get("sung", False)
        sid = song["id"]

        c1_stat, c2_name, c3_lyrics, c4_btn1, c5_btn2, c6_btn3, c7_audio = st.columns([0.25, 1.4, 2.8, 1.0, 1.0, 1.0, 1.0])

        # 状态列：红色🔴或空（字号缩小让高度不撑大）
        with c1_stat:
            if is_sung:
                st.markdown(
                    "<div style='font-size:1.3rem; line-height:2.2rem; text-align:center'>🔴</div>",
                    unsafe_allow_html=True
                )
            else:
                st.markdown(
                    "<div style='font-size:1.3rem; line-height:2.2rem; text-align:center; opacity:0.1'>·</div>",
                    unsafe_allow_html=True
                )

        # 歌名列：text_input；已唱时在下方显示小字提示
        with c2_name:
            new_name = st.text_input(
                "歌名",
                value=song["name"],
                key=f"name_{sid}",
                label_visibility="collapsed"
            )
            if is_sung:
                st.caption("⚠️已唱")

        # 歌词列：text_area 高度和歌名输入框对齐（与CSS强制高度一致34px）
        with c3_lyrics:
            new_lyrics = st.text_area(
                "歌词",
                value=song.get("lyrics", ""),
                key=f"lyrics_{sid}",
                label_visibility="collapsed",
                height=34,
                placeholder="歌词..."
            )

        # 操作按钮1：标记已唱 / 取消已唱（去掉空格避免换行）
        with c4_btn1:
            if is_sung:
                if st.button("↩️取消", key=f"unsung_{sid}", use_container_width=True):
                    song["sung"] = False
                    save_data(data)
                    st.rerun()
            else:
                if st.button("🔴标记", key=f"sung_{sid}", use_container_width=True):
                    song["sung"] = True
                    save_data(data)
                    st.rerun()

        # 操作按钮2：保存修改
        with c5_btn2:
            if st.button("💾保存", key=f"save_{sid}", use_container_width=True):
                if new_name.strip():
                    song["name"] = new_name.strip()
                    song["lyrics"] = new_lyrics
                    save_data(data)
                    st.success("已保存修改")
                    st.rerun()
                else:
                    st.warning("歌名不能为空")

        # 操作按钮3：删除歌曲
        with c6_btn3:
            if st.button("🗑️删除", key=f"del_{sid}", type="secondary", use_container_width=True):
                # 删除歌曲时一并删除录音
                delete_audio(song)
                singer["songs"] = [s for s in singer["songs"] if s["id"] != sid]
                save_data(data)
                st.rerun()

        # 录音列：未录音显示录音按钮，已录音显示播放器+重录按钮
        with c7_audio:
            audio_path = get_audio_path(song)
            if not audio_path:
                # 未录音：显示麦克风录音按钮（点击开始，再点击结束并返回音频）
                audio_bytes = audio_recorder(
                    text="",
                    icon_size="1.5x",
                    key=f"rec_{sid}",
                )
                if audio_bytes:
                    filename = save_audio_as_mp3(audio_bytes, sid)
                    if filename:
                        song["audio"] = filename
                        save_data(data)
                        st.rerun()
            else:
                # 已录音：显示播放器 + 小字"重录"按钮
                with open(audio_path, "rb") as f:
                    audio_bytes = f.read()
                st.audio(audio_bytes, format="audio/mp3")
                if st.button("重录", key=f"rerec_{sid}", use_container_width=True):
                    delete_audio(song)
                    save_data(data)
                    st.rerun()

        st.divider()


# ===== 页面：导入歌单 =====
def import_page() -> None:
    st.title("📥 导入歌单")

    if st.button("⬅️ 返回主页"):
        st.session_state.page = "main"
        if "import_preview" in st.session_state:
            del st.session_state.import_preview
        st.rerun()

    st.write("支持两种格式（自动识别）：")
    st.write("• **格式A**：每行一首，`歌手名 - 歌名`")
    st.write("• **格式B**：歌手名独占一行，下一行为该歌手的歌曲（空格分隔）")
    st.write("格式B下，歌名后可用括号或冒号附带歌词，例如：")
    st.code("等不到你(就这样远远看着你)\n微光：我想我是一道微光", language="text")

    separator = st.text_input("格式A分隔符", value=" - ", help="仅格式A使用，例如：周杰伦 - 晴天")

    tab_text, tab_docx = st.tabs(["📝 纯文本粘贴", "📄 Word文档上传"])

    # 纯文本导入
    with tab_text:
        text = st.text_area(
            "粘贴歌单文本",
            height=300,
            key="import_text",
            placeholder="周杰伦\n晴天 七里香\n\n林俊杰\n江南..."
        )
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

    # Word 文档导入
    with tab_docx:
        uploaded = st.file_uploader("上传 .docx 文件", type=["docx"], key="upload_docx")
        if uploaded is not None:
            try:
                result = parse_docx(uploaded.getvalue(), separator)
                if result:
                    st.session_state.import_preview = result
                    st.rerun()
                else:
                    st.warning("文档中未解析到有效数据，请检查格式。")
            except Exception as e:
                st.error(f"解析失败：{e}")

    # 解析预览
    if "import_preview" in st.session_state:
        result = st.session_state.import_preview
        if not result:
            st.warning("未解析到有效数据。")
            return

        st.divider()
        total_songs = sum(len(songs) for songs in result.values())
        total_with_lyrics = sum(1 for songs in result.values() for s in songs if s.get("lyrics"))
        st.subheader(f"📋 解析预览（{len(result)} 位歌手 · {total_songs} 首歌 · {total_with_lyrics} 首含歌词）")
        st.caption("💡 提示：英文多词歌名（如 'love love love'）会被空格拆分，导入后可在歌手详情页手动修正。")

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
                data = load_data()
                added_singers = 0
                added_songs = 0
                for singer_name, songs in result.items():
                    # 查找是否已存在该歌手
                    singer = next((s for s in data["singers"] if s["name"] == singer_name), None)
                    if not singer:
                        singer = {
                            "id": gen_id(),
                            "name": singer_name,
                            "songs": []
                        }
                        data["singers"].append(singer)
                        added_singers += 1
                    for song in songs:
                        if not any(s["name"] == song["name"] for s in singer["songs"]):
                            singer["songs"].append({
                                "id": gen_id(),
                                "name": song["name"],
                                "lyrics": song.get("lyrics", ""),
                                "sung": False
                            })
                            added_songs += 1
                save_data(data)
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
    # 页面配置
    st.set_page_config(
        page_title="歌手PK助手",
        page_icon="🎵",
        layout="wide",
        initial_sidebar_state="expanded"
    )

    # 自定义 CSS：让已唱过的按钮显示红色文字
    st.markdown(
        """
        <style>
        /* 按钮文字允许换行 */
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
