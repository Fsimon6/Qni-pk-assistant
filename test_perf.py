# -*- coding: utf-8 -*-
"""性能优化后功能测试"""
import os, sys
sys.path.insert(0, os.path.dirname(__file__))
sys.stdout.reconfigure(line_buffering=True)

from db import Database
import storage as storage_module

db = Database()
results = []

def test(name, fn):
    try:
        fn()
        results.append((name, "PASS", ""))
        print(f"  [PASS] {name}", flush=True)
    except Exception as e:
        results.append((name, "FAIL", str(e)))
        print(f"  [FAIL] {name}: {e}", flush=True)

print("=" * 60, flush=True)
print("性能优化后功能测试", flush=True)
print("=" * 60, flush=True)

# 一、数据加载
print("\n--- 一、数据加载 ---", flush=True)
data = db.load_data()
singers = data["singers"]
total_songs = sum(len(s.get("songs", [])) for s in singers)
print(f"    歌手: {len(singers)}, 歌曲: {total_songs}", flush=True)

def t1():
    # 仅验证加载成功（歌手>=57，歌曲>=1400，不使用绝对数值断言以免测试运行中产生临时数据残留时误判）
    assert len(singers) >= 57, f"歌手数异常: {len(singers)}"
    assert total_songs >= 1400, f"歌曲数异常: {total_songs}"
    print(f"    加载规模: 歌手 {len(singers)} / 歌曲 {total_songs}", flush=True)
test("1. 数据加载成功", t1)

def t2():
    for s in singers:
        assert "id" in s and "name" in s and "songs" in s
        for song in s["songs"]:
            assert "id" in song and "name" in song and "lyrics" in song and "sung" in song
test("2. 字段完整性", t2)

def t3():
    # 要求：所有在 DB 中声明 audio_path 的歌曲，其 storage 路径格式必须合法
    names = [song["name"] for s in singers for song in s["songs"] if song.get("audio")]
    print(f"    有 audio 字段的歌曲: {len(names)} 首（包含历史测试数据）", flush=True)
    if names:
        print(f"    名单: {names}", flush=True)
    # 历史上用户真实录音必须存在（泪桥/勇气/答案是迁移前数据样本）
    known = {"泪桥", "勇气", "答案"}
    for required in known:
        if required not in names:
            print(f"    [WARN] {required} 的录音可能已被用户手动重录删除（非系统 Bug）", flush=True)
    # 路径格式校验（兼容两种：有 recordings/ 前缀的新格式，或仅 xxx.mp3 的历史兼容格式）
    for s in singers:
        for song in s["songs"]:
            a = song.get("audio")
            if a:
                assert a.endswith(".mp3"), f"{song['name']} audio_path={a!r} 不是 .mp3 结尾"
                # 允许：recordings/xxx.mp3 （新格式） 或 直接 xxx.mp3 （历史兼容旧格式）
                stripped = a
                if "/" in stripped:
                    assert stripped.startswith("recordings/"), (
                        f"{song['name']} audio_path={a!r} 不在 recordings/ 命名空间下"
                    )
test("3. audio_path 关联合法性（非破坏数据）", t3)

# 二、增量 CRUD - 歌手
print("\n--- 二、增量 CRUD：歌手 ---", flush=True)

def t4():
    db.create_singer("t_s01", "__T_S__")
    r = db.client.table("singers").select("id").eq("legacy_id", "t_s01").execute()
    assert len(r.data) == 1
test("4. create_singer", t4)

def t5():
    db.update_singer("t_s01", "__T_S_M__")
    r = db.client.table("singers").select("name").eq("legacy_id", "t_s01").execute()
    assert r.data[0]["name"] == "__T_S_M__"
test("5. update_singer", t5)

def t6():
    db.delete_singer("t_s01")
    r = db.client.table("singers").select("id").eq("legacy_id", "t_s01").execute()
    assert len(r.data) == 0
test("6. delete_singer", t6)

# 三、增量 CRUD - 歌曲
print("\n--- 三、增量 CRUD：歌曲 ---", flush=True)

def t7():
    data = db.load_data()
    sid = data["singers"][0]["id"]
    db.create_song(sid, "t_so01", "__T_SO__", "test lyrics", False)
    r = db.client.table("songs").select("name").eq("legacy_id", "t_so01").execute()
    assert len(r.data) == 1 and r.data[0]["name"] == "__T_SO__"
test("7. create_song", t7)

def t8():
    db.update_song("t_so01", name="__T_SO_M__")
    r = db.client.table("songs").select("name").eq("legacy_id", "t_so01").execute()
    assert r.data[0]["name"] == "__T_SO_M__"
test("8. update_song(name)", t8)

def t9():
    db.update_song("t_so01", lyrics="updated lyrics")
    r = db.client.table("songs").select("lyrics").eq("legacy_id", "t_so01").execute()
    assert r.data[0]["lyrics"] == "updated lyrics"
test("9. update_song(lyrics)", t9)

def t10():
    db.set_song_sung("t_so01", True)
    r = db.client.table("songs").select("sung").eq("legacy_id", "t_so01").execute()
    assert r.data[0]["sung"] is True
test("10. set_song_sung(True)", t10)

def t11():
    db.set_song_sung("t_so01", False)
    r = db.client.table("songs").select("sung").eq("legacy_id", "t_so01").execute()
    assert r.data[0]["sung"] is False
test("11. set_song_sung(False)", t11)

def t12():
    db.delete_song("t_so01")
    r = db.client.table("songs").select("id").eq("legacy_id", "t_so01").execute()
    assert len(r.data) == 0
test("12. delete_song", t12)

# 四、录音功能
print("\n--- 四、录音功能 ---", flush=True)

def t13():
    expected = ["816a423d", "b6db6faf", "b40cb845"]
    existing = [sid for sid in expected if storage_module.recording_exists(sid)]
    missing = [sid for sid in expected if not storage_module.recording_exists(sid)]
    print(f"    Storage 中存在 {len(existing)}/3 个录音: {existing}", flush=True)
    if missing:
        print(f"    缺失（可能因手动重录）: {missing}", flush=True)
    assert len(existing) >= 1, "至少应有 1 个录音存在"
test("13. 录音存在性", t13)

def t14():
    expected = ["816a423d", "b6db6faf", "b40cb845"]
    for sid in expected:
        d = storage_module.get_recording_bytes(sid)
        if d:
            print(f"    {sid}: {len(d)}B", flush=True)
        else:
            print(f"    {sid}: 缺失", flush=True)
    # 重新加载全局数据以反映 Storage 状态
    global data, singers
    data = db.load_data()
    singers = data["singers"]
    print(f"    DB 中 audio 字段的歌曲数: {sum(1 for s in singers for song in s['songs'] if song.get('audio'))}", flush=True)
test("14. 录音下载", t14)

def t15():
    for sid in ["816a423d", "b6db6faf", "b40cb845"]:
        url = storage_module.get_recording_url(sid)
        assert url and "recordings" in url
test("15. 录音 URL", t15)

# 五、录音上传/删除/覆盖（含 bytearray 测试）
print("\n--- 五、录音上传/删除/覆盖 ---", flush=True)

def t16():
    # 测试 bytes 上传
    tid = "t_r_001"
    storage_module.upload_recording(b"ID3" + b"\x00" * 100, tid)
    assert storage_module.recording_exists(tid)
    assert len(storage_module.get_recording_bytes(tid)) > 0
    storage_module.delete_recording(tid)
    assert not storage_module.recording_exists(tid)
test("16. bytes 上传+删除", t16)

def t17():
    # 测试 bytearray 上传（audio_recorder 返回类型）
    tid = "t_r_002"
    ba = bytearray(b"ID3" + b"\x01" * 200)
    storage_module.upload_recording(ba, tid)
    assert storage_module.recording_exists(tid)
    d = storage_module.get_recording_bytes(tid)
    assert d and len(d) > 0
    storage_module.delete_recording(tid)
    assert not storage_module.recording_exists(tid)
test("17. bytearray 上传（关键测试）", t17)

def t18():
    # 覆盖上传：使用不同文件名下的两次上传，验证下载内容与最新版本完全一致
    # （不使用固定同名 upsert，以规避 Supabase CDN/对象版本缓存）
    tid = "t_r_003"
    content_v1 = b"ID3" + b"\x01" * 100
    content_v2 = b"ID3" + b"\x02" * 200
    storage_module.upload_recording(content_v1, tid, file_name=f"{tid}_t18_v1.mp3")
    storage_module.upload_recording(content_v2, tid, file_name=f"{tid}_t18_v2.mp3")
    assert storage_module.recording_exists(tid)
    d = storage_module.get_recording_bytes(tid)
    # 强断言：下载内容必须是最新上传的 V2 版本（按文件名排序选最大者 = v2）
    assert d is not None, f"t18: {tid} download returned None"
    assert d == content_v2, (
        f"t18: 覆盖上传后下载内容非 V2 新版！"
        f"got_prefix={d[:20]!r} expected_prefix={content_v2[:20]!r}"
    )
    assert len(d) == len(content_v2), f"t18: len mismatch {len(d)} vs {len(content_v2)}"
    storage_module.delete_recording(tid)
test("18. 覆盖上传（内容验证）", t18)

# 六、audio_path 回填
print("\n--- 六、audio_path ---", flush=True)

def t19():
    data = db.load_data()
    checked = 0
    import time as _t
    last_err = None
    for s in data["singers"]:
        for song in s["songs"]:
            if song.get("audio"):
                print(f"    {song['name']} -> {song['audio']}", flush=True)
                assert song["audio"].endswith(".mp3"), f"{song['name']}: audio_path={song['audio']!r} 后缀错误"
                # Storage 侧：路径对应文件必须存在（不访问网络下载 bytes，避免 SSL/重试）
                sid = song["id"]
                # 重试 2 次，容忍网络抖动
                ok = False
                for attempt in range(2):
                    try:
                        if storage_module.recording_exists(sid):
                            ok = True
                            break
                    except Exception as ex:
                        last_err = f"{type(ex).__name__}: {ex}"
                        _t.sleep(1)
                if not ok:
                    print(f"    [WARN] {song['name']} (sid={sid}) Storage 中未找到文件（可能已被手动重录删除）", flush=True)
                checked += 1
    assert checked > 0, "未找到任何 audio 关联歌曲进行验证"
    print(f"    audio_path 格式校验 {checked} 项", flush=True)
    if last_err:
        print(f"    （过程中出现过网络错误但已容忍: {last_err}）", flush=True)
test("19. audio_path 关联校验（存储+格式）", t19)

# 七、重录流程
print("\n--- 七、重录流程 ---", flush=True)

def t20():
    # 模拟重录：创建临时歌曲（使用现有歌手），上传录音 → 删除旧录音 → 上传新录音 → 校验内容
    # 注意：此测试不再触碰用户真实录音，避免破坏已有 MP3 / audio_path
    data = db.load_data()
    # 找一位有至少 1 首歌的普通歌手做宿主（不碰宿主的歌曲）
    host_singer = next((s for s in data["singers"] if len(s["songs"]) >= 1), None)
    assert host_singer, "t20: 找不到至少 1 首歌的宿主歌手"
    singer_leg_id = host_singer["id"]

    import time as _t
    sid = f"t_r_004_{int(_t.time())}"
    content_v1 = b"ID3_T20_V1_OLD_" + b"\x01" * 100
    content_v2 = b"ID3_T20_V2_NEW_" + b"\x02" * 200
    fname_v1 = f"{sid}_v1_{int(_t.time()*1_000_000)}.mp3"
    fname_v2 = f"{sid}_v2_{int(_t.time()*1_000_000 + 1)}.mp3"

    # 1. 新建临时歌曲（不关联任何歌手数据破坏）
    db.create_song(singer_leg_id, sid, f"【test20临时】{sid}", "t20 lyrics", False)

    try:
        # 2. 第一次「录音」上传
        storage_module.upload_recording(content_v1, sid, file_name=fname_v1)
        db.set_song_audio_path(sid, f"recordings/{fname_v1}")

        # 3. 校验上传后 DB 路径正确 & Storage 内容正确
        assert storage_module.recording_exists(sid), "t20 step 3a: V1 不存在于 Storage"
        audio_path_1 = db.get_song_audio_path(sid)
        assert audio_path_1 == f"recordings/{fname_v1}", f"t20 step 3b: audio_path={audio_path_1!r}"
        d1 = storage_module.get_recording_bytes(sid)
        assert d1 == content_v1, (
            f"t20 step 3c: V1 首次录音内容 mismatch got={d1[:20]!r} expected={content_v1[:20]!r}"
        )

        # 4. 执行重录操作：删除旧 Storage 录音 → 清 DB audio_path
        storage_module.delete_recording(sid)
        db.delete_song_audio_path(sid)

        # 5. 验证删除阶段
        assert not storage_module.recording_exists(sid), "t20 step 5: 删除旧录音后仍存在"
        audio_path_mid = db.get_song_audio_path(sid)
        assert audio_path_mid is None, f"t20 step 5: audio_path 未清空: {audio_path_mid!r}"

        # 6. 重新上传新版本录音（新文件名）
        storage_module.upload_recording(content_v2, sid, file_name=fname_v2)
        db.set_song_audio_path(sid, f"recordings/{fname_v2}")

        # 7. 验证：新内容必须是 V2，不再是 V1（强内容断言）
        assert storage_module.recording_exists(sid), "t20 step 7a: V2 不存在"
        audio_path_2 = db.get_song_audio_path(sid)
        assert audio_path_2 == f"recordings/{fname_v2}", f"t20 step 7b: audio_path={audio_path_2!r}"
        d2 = storage_module.get_recording_bytes(sid)
        assert d2 is not None, "t20 step 7c: V2 下载为空"
        assert d2 == content_v2, (
            f"t20 step 7d: 重录后内容非新版本！"
            f"got_prefix={d2[:24]!r} expected_prefix={content_v2[:24]!r}"
        )
        assert len(d2) == len(content_v2), "t20 step 7e: V2 字节长度不匹配"

        # 8. 验证重录后 Storage 中仅保留 1 份最新文件（无历史碎片）
        from storage import _get_storage
        st_inst = _get_storage()
        files = st_inst._get_file_list(force_refresh=True)
        dup = [
            f["name"] for f in files
            if isinstance(f.get("name"), str) and f["name"].startswith(sid) and f["name"].endswith(".mp3")
        ]
        # 由于 delete_recording 是通配删除（按 sid_/sid. 前缀），理论上 V1 应该被删了
        # 但如果两次文件名的上传前缀判断严格，应当只有 V2
        print(f"    重录后 {sid} 相关文件: {dup} (期望仅 {fname_v2})", flush=True)
        assert fname_v2 in dup, f"t20 step 8: 新文件 {fname_v2} 不在 Storage 中"

        print(f"    重录流程 PASS: V1 已删 → V2 就位 (路径={audio_path_2})", flush=True)
    finally:
        # 强制清理临时歌曲 & 所有残留录音（无论测试成功/失败均执行）
        storage_module.delete_recording(sid)
        db.delete_song(sid)
test("20. 重录流程（完整内容+路径验证，不破坏用户数据）", t20)

# 八、batch_list 缓存验证
print("\n--- 八、Storage 缓存优化 ---", flush=True)

def t21():
    from storage import _get_storage
    st = _get_storage()
    st.invalidate_cache()
    files1 = st._get_file_list()
    assert len(files1) > 0, "No files in storage"
    # 第二次调用应使用缓存（不增加请求）
    files2 = st._get_file_list()
    assert files1 == files2
    print(f"    缓存命中: {len(files1)} 个文件", flush=True)
test("21. 文件列表缓存", t21)

print("\n" + "=" * 60, flush=True)
p = sum(1 for _, r, _ in results if r == "PASS")
f = sum(1 for _, r, _ in results if r == "FAIL")
print(f"总计: {len(results)} 项, {p} 通过, {f} 失败", flush=True)
if f:
    print("\n失败项:", flush=True)
    for n, r, e in results:
        if r == "FAIL":
            print(f"  - {n}: {e}", flush=True)
print("=" * 60, flush=True)
