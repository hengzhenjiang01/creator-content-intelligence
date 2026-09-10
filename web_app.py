from pathlib import Path
import sys

import streamlit as st


PROJECT_ROOT = Path(__file__).resolve().parent
SRC_DIR = PROJECT_ROOT / "src"
if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))

from ai_growth_copilot.config import Settings  # noqa: E402
from ai_growth_copilot.web import (  # noqa: E402
    claim_request,
    execute_web_request,
    parse_report_for_web,
    prepare_web_request,
    release_request,
    request_fingerprint,
    result_issue_lines,
    safe_web_error,
)


STAGES = ("抓取内容", "转录", "视觉处理", "综合分析", "生成报告")
STATUS_ICONS = {
    "pending": "⚪",
    "running": "🔄",
    "completed": "✅",
    "failed": "⚠️",
    "skipped": "➖",
}


def main() -> None:
    st.set_page_config(page_title="Creator Content Intelligence", page_icon="🔎", layout="wide")
    st.title("Creator Content Intelligence")
    st.caption("面向公开 Instagram 内容的证据约束分析 · V3")

    mode = st.radio("输入模式", ("创作者主页", "指定 Reel/Post"), horizontal=True)
    if mode == "创作者主页":
        account_url = st.text_input(
            "Instagram 创作者主页 URL",
            placeholder="https://www.instagram.com/creator_username/",
        )
        reel_text = ""
    else:
        account_url = ""
        reel_text = st.text_area(
            "Instagram Reel/Post URL（每行一条，最多 3 条）",
            placeholder=(
                "https://www.instagram.com/reel/REEL_ID_1/\n"
                "https://www.instagram.com/p/POST_ID_2/"
            ),
            height=120,
        )

    st.subheader("分析设置")
    focus_text = st.text_input(
        "重点产品",
        value="Lovart, Higgsfield, CapCut",
        help="仅提醒模型优先识别，不会被当作内容已经提及的产品。",
    )
    vision = st.checkbox("启用视觉分析", value=True)
    st.warning("点击“开始分析”将真实调用 Apify、Supadata 和 DeepSeek，并可能产生少量 API 费用。")

    _initialize_state()
    start = st.button(
        "开始分析",
        type="primary",
        disabled=bool(st.session_state.analysis_running),
        use_container_width=True,
    )
    if start:
        try:
            request = prepare_web_request(account_url, reel_text, focus_text, vision)
        except Exception as exc:
            st.error(safe_web_error(exc))
        else:
            fingerprint = request_fingerprint(request)
            if claim_request(st.session_state, fingerprint):
                st.session_state.pending_request = request
                st.session_state.progress_states = {stage: "pending" for stage in STAGES}
                st.session_state.last_error = ""
                st.rerun()
            else:
                st.info("当前分析仍在运行，请勿重复提交。")

    progress_slot = st.empty()
    _render_progress(progress_slot)

    if st.session_state.analysis_running and st.session_state.pending_request is not None:
        request = st.session_state.pending_request
        st.session_state.pending_request = None

        def on_progress(stage: str, status: str) -> None:
            if stage in STAGES:
                st.session_state.progress_states[stage] = status
                _render_progress(progress_slot)

        try:
            settings = Settings.from_env(PROJECT_ROOT / ".env")
            result = execute_web_request(
                request,
                settings,
                PROJECT_ROOT / "outputs",
                progress_callback=on_progress,
            )
            report_text = result.report_path.read_text(encoding="utf-8")
            st.session_state.last_result = result
            st.session_state.last_report_text = report_text
        except Exception as exc:
            st.session_state.last_error = safe_web_error(exc)
        finally:
            release_request(st.session_state)
        st.rerun()

    if st.session_state.last_error:
        st.error(st.session_state.last_error)
    _render_result()


def _initialize_state() -> None:
    defaults = {
        "analysis_running": False,
        "pending_request": None,
        "progress_states": {},
        "last_result": None,
        "last_report_text": "",
        "last_error": "",
    }
    for key, value in defaults.items():
        if key not in st.session_state:
            st.session_state[key] = value


def _render_progress(slot: "st.delta_generator.DeltaGenerator") -> None:
    states = st.session_state.progress_states
    if not states:
        return
    lines = [
        f"{STATUS_ICONS.get(states.get(stage, 'pending'), '⚪')} {stage}"
        for stage in STAGES
    ]
    slot.markdown("### 处理进度\n\n" + "  \n".join(lines))


def _render_result() -> None:
    result = st.session_state.last_result
    report_text = st.session_state.last_report_text
    if result is None or not report_text:
        return
    st.divider()
    st.subheader("分析结果")
    success_col, failure_col, visual_col = st.columns(3)
    success_col.metric("成功条数", result.success_count)
    failure_col.metric("失败条数", result.failure_count)
    visual_col.metric("视觉缺失条数", result.visual_failure_count)

    issues = result_issue_lines(result)
    if issues:
        with st.expander("失败与数据质量提示"):
            for issue in issues:
                st.warning(issue)

    st.download_button(
        "下载 Markdown 报告",
        data=report_text,
        file_name=result.report_path.name,
        mime="text/markdown",
    )
    report_view = parse_report_for_web(report_text)
    if report_view.body_markdown:
        st.markdown(report_view.body_markdown)
    if report_view.appendices:
        st.subheader("原始证据附录")
        for appendix in report_view.appendices:
            with st.expander(appendix.title, expanded=False):
                st.markdown(appendix.markdown)


if __name__ == "__main__":
    main()
