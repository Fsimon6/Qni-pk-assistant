# -*- coding: utf-8 -*-
"""配置读取：从 Streamlit Secrets 或环境变量获取 Supabase 连接信息。"""

import os

try:
    import streamlit as st
    _HAS_STREAMLIT = True
except ImportError:
    _HAS_STREAMLIT = False


def _get_secret(key: str) -> str:
    """优先从 Streamlit Secrets 读取，其次从环境变量读取。"""
    if _HAS_STREAMLIT:
        try:
            value = st.secrets.get(key)
            if value:
                return value
        except Exception:
            pass
    value = os.environ.get(key)
    if value:
        return value
    raise RuntimeError(f"未找到配置项：{key}，请在 Streamlit Secrets 或环境变量中设置。")


def get_supabase_url() -> str:
    return _get_secret("SUPABASE_URL")


def get_supabase_key() -> str:
    return _get_secret("SUPABASE_ANON_KEY")
