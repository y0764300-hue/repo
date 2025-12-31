import streamlit as st
import pandas as pd
from datetime import datetime
import os
import google.generativeai as genai

# ==========================================
# 1. 파일 및 초기 설정
# ==========================================
NOTE_FILE = 'my_work_notes.csv'
CHAT_FILE = 'my_chat_history.csv'
CONFIG_FILE = 'menu_config.csv'

# --- 설정 파일 생성 ---
if not os.path.exists(CONFIG_FILE):
    default_data = {
        "메뉴명": ["📦 피킹지시", "🔍 재고조회", "🚛 입고처리", "🏷️ 바코드 관리"],
        "시트정보": [
            "검증결과, Log, 12월사본, 자재 정보, 소요 정보, DB, 볼트 상세, 단가",
            "전사재고, 실시간재고, 불량현황, 3공장 재고",
            "입고검수, 반품이력, 협력사정보, 품질리포트",
            "라벨이력, ZPL템플릿, 프린터설정"
        ],
        "트리거정보": [
            "함수: runSmartUpdate(), 매일 06시~07시 자동실행",
            "함수: checkStock(), 수정 시 실행(OnEdit)",
            "함수: registerItem(), 폼 제출 시 실행",
            "함수: printLabel(), 버튼 클릭 시 실행"
        ],
        "업무설명": [
            "피킹 리스트를 생성하고 현장에 전달하는 업무. 오전 9시 전까지 완료 필수.",
            "ERP와 실물 재고를 비교하여 차이점을 파악하는 업무.",
            "협력사로부터 입고된 자재를 검수하고 시스템에 등록함.",
            "부품 식별표(바코드)를 출력하여 적재된 자재에 부착."
        ],
        "메일발송설정": [True, False, False, True]
    }
    pd.DataFrame(default_data).to_csv(CONFIG_FILE, index=False)
else:
    df_temp = pd.read_csv(CONFIG_FILE)
    if '업무설명' not in df_temp.columns:
        df_temp['업무설명'] = "설명을 입력해주세요."
        df_temp.to_csv(CONFIG_FILE, index=False)

if not os.path.exists(NOTE_FILE):
    pd.DataFrame(columns=['날짜', '시간', '메뉴', '유형', '내용']).to_csv(NOTE_FILE, index=False)
if not os.path.exists(CHAT_FILE):
    pd.DataFrame(columns=['날짜', '시간', '주제', '전체내용']).to_csv(CHAT_FILE, index=False)
else:
    df_chat_check = pd.read_csv(CHAT_FILE)
    if '전체내용' not in df_chat_check.columns and '질문' in df_chat_check.columns:
        df_chat_check['전체내용'] = "Q: " + df_chat_check['질문'].fillna('') + "\n\nA: " + df_chat_check['답변'].fillna('')
        df_chat_check.to_csv(CHAT_FILE, index=False)

# ==========================================
# 2. 스타일 (CSS) - 버튼 간격 초미세 조정
# ==========================================
st.markdown("""
<style>
    .badge-container { display: flex; flex-wrap: wrap; gap: 6px; margin-bottom: 10px; }
    .sheet-badge { background-color: #E3F2FD; color: #1565C0; padding: 4px 10px; border-radius: 15px; font-size: 13px; font-weight: 600; border: 1px solid #90CAF9; }
    .trigger-box { background-color: #F1F8E9; padding: 8px 12px; border-radius: 6px; margin-bottom: 6px; font-size: 14px; border-left: 4px solid #66BB6A; }
    .report-box { background-color: #FAFAFA; padding: 20px; border-radius: 10px; border: 1px solid #EEE; box-shadow: 0 2px 5px rgba(0,0,0,0.05); }
    /* 버튼 스타일 조정 (작고 딱 붙게) */
    .stButton button { height: 34px; padding: 0 8px; min-width: 0px; margin: 0px; }
    /* 텍스트 영역 스크롤바 숨기기 */
    .stTextArea textarea { overflow-y: hidden; }
    /* 컬럼 간격 강제 축소 (Streamlit 내부 클래스 타겟팅) */
    [data-testid="column"] { padding: 0px !important; }
</style>
""", unsafe_allow_html=True)

# ==========================================
# 3. 앱 메인 로직
# ==========================================
st.set_page_config(page_title="스마트 업무 비서", layout="wide")

if "GEMINI_API_KEY" in st.secrets:
    gemini_api_key = st.secrets["GEMINI_API_KEY"]
else:
    gemini_api_key = None

with st.sidebar:
    st.markdown("### 🔑 AI 설정")
    if gemini_api_key:
        genai.configure(api_key=gemini_api_key)
        st.success("AI 자동 연결됨 (Secrets) 🟢")
    else:
        user_key = st.text_input("Google API Key 입력", type="password")
        if user_key:
            genai.configure(api_key=user_key)
            gemini_api_key = user_key
            st.success("AI 연결됨! 🟢")
        else:
            st.warning("API 키가 없습니다.")

mode = st.sidebar.radio("모드 선택", ["📝 업무 기록하기", "💬 코드/대화 이력", "📊 일일 리포트", "⚙️ 메뉴/설정 관리"])

# ------------------------------------------
# [모드 1] 업무 기록하기
# ------------------------------------------
if mode == "📝 업무 기록하기":
    config_df = pd.read_csv(CONFIG_FILE)
    menu_list = config_df['메뉴명'].tolist()
    selected_menu_name = st.sidebar.radio("업무 선택", menu_list)
    current_idx = config_df.index[config_df['메뉴명'] == selected_menu_name][0]
    current_row = config_df.iloc[current_idx]
    
    st.header(f"{selected_menu_name}")
    
    # [1] 업무 설명
    with st.expander("ℹ️ 업무 설명 (클릭하여 편집)", expanded=True):
        description = str(current_row['업무설명'])
        new_desc = st.text_area("설명 수정", value=description, height=70, label_visibility="collapsed")
        if new_desc != description:
            if st.button("설명 업데이트 저장"):
                config_df.at[current_idx, '업무설명'] = new_desc
                config_df.to_csv(CONFIG_FILE, index=False)
                st.success("업무 설명이 업데이트되었습니다!")
                st.rerun()

    # [2] 시트 & 트리거 정보
    col1, col2 = st.columns(2)
    with col1:
        with st.container(border=True):
            st.markdown("##### 📂 시트 정보")
            sheet_items = [item.strip() for item in str(current_row['시트정보']).split(',') if item.strip()]
            badges_html = '<div class="badge-container">'
            for item in sheet_items: badges_html += f'<span class="sheet-badge">{item}</span>'
            badges_html += '</div>'
            st.markdown(badges_html, unsafe_allow_html=True)
    with col2:
        with st.container(border=True):
            st.markdown("##### ⚡ 트리거 정보")
            trigger_items = [item.strip() for item in str(current_row['트리거정보']).split(',') if item.strip()]
            for item in trigger_items:
                formatted_item = item.replace("함수:", "<strong>🛠️ 함수:</strong>")
                st.markdown(f'<div class="trigger-box">{formatted_item}</div>', unsafe_allow_html=True)

    if current_row['메일발송설정']: st.info("📧 이 업무는 **메일 발송** 프로세스가 포함되어 있습니다.")
    st.divider()
    
    # [3] 입력 폼
    st.write("###### 📝 기록 유형")
    note_type = st.radio("유형", ["💡 아이디어", "✅ 업데이트", "🔥 문제점"], horizontal=True, label_visibility="collapsed")
    
    input_key = f"note_{selected_menu_name}"
    ph = "내용을 입력하세요."
    if "아이디어" in note_type: ph = "개선 아이디어 입력"
    elif "문제점" in note_type: ph = "발생한 오류나 이슈 기록"
    
    st.text_area("내용 입력", height=100, placeholder=ph, key=input_key, label_visibility="collapsed")
    
    if st.button("💾 기록 저장", type="primary"):
        safe_content = st.session_state.get(input_key, "")
        if safe_content.strip():
            now = datetime.now()
            # 유형에서 이모지 제거하고 텍스트만 저장할 수도 있지만, 현재는 통일성 위해 그대로 저장
            new_note = {'날짜': now.strftime("%Y-%m-%d"), '시간': now.strftime("%H:%M:%S"), '메뉴': selected_menu_name, '유형': note_type, '내용': safe_content}
            
            if os.path.exists(NOTE_FILE): df_note = pd.read_csv(NOTE_FILE)
            else: df_note = pd.DataFrame(columns=['날짜', '시간', '메뉴', '유형', '내용'])
            
            df_note = pd.concat([pd.DataFrame([new_note]), df_note], ignore_index=True)
            df_note.to_csv(NOTE_FILE, index=False)
            st.toast("저장되었습니다!", icon="✅")
            del st.session_state[input_key]
            st.rerun()
        else:
            st.warning("⚠️ 내용을 입력해주세요.")

    # [4] 히스토리 (필터링 + 버튼 간격 조정)
    st.divider()
    
    # 필터링 UI (타이틀과 나란히 배치)
    col_h_title, col_h_filter = st.columns([0.6, 0.4])
    with col_h_title:
        st.subheader(f"📊 히스토리")
    with col_h_filter:
        # 멀티셀렉트로 필터링 (기본값: 전체)
        filter_options = ["💡 아이디어", "✅ 업데이트", "🔥 문제점"]
        selected_filters = st.multiselect("유형 필터", filter_options, default=filter_options, label_visibility="collapsed")

    if os.path.exists(NOTE_FILE):
        df = pd.read_csv(NOTE_FILE).fillna("")
        # 1. 메뉴 필터링
        df_filtered = df[df['메뉴'] == selected_menu_name]
        
        # 2. 유형 필터링 (선택된 것만 포함)
        # 저장된 데이터가 "💡 아이디어" 형태일 수도 있고 그냥 "아이디어"일 수도 있어서 유연하게 처리
        mask = df_filtered['유형'].apply(lambda x: any(f in x for f in selected_filters))
        df_final = df_filtered[mask]
        
        my_notes_idx = df_final.index.tolist()[::-1]
        
        if my_notes_idx:
            for idx in my_notes_idx:
                row = df.loc[idx]
                icon = "🔥" if "문제점" in str(row['유형']) else ("💡" if "아이디어" in str(row['유형']) else "✅")
                
                with st.container(border=True):
                    # 상단 바 레이아웃: 내용(88%) + 버튼(12%) - 버튼 공간을 더 줄임
                    col_txt, col_btn = st.columns([0.88, 0.12])
                    
                    with col_txt:
                        st.markdown(f"**{icon} [{row['유형']}] {row['날짜']} {row['시간']}**")
                    
                    with col_btn:
                        edit_mode_key = f"edit_mode_{idx}"
                        is_editing = st.session_state.get(edit_mode_key, False)
                        
                        # gap="small"보다 더 좁게 쓰기 위해 CSS class 활용 (위에서 정의함)
                        # 버튼들을 한 컬럼 안에 몰아넣지 않고, 2개의 아주 좁은 컬럼으로 나눔
                        b1, b2 = st.columns([1, 1], gap="small")
                        
                        with b1:
                            if is_editing:
                                if st.button("💾", key=f"save_{idx}", help="저장"):
                                    new_content = st.session_state.get(f"txt_{idx}", row['내용'])
                                    df.at[idx, '내용'] = new_content
                                    df.to_csv(NOTE_FILE, index=False)
                                    st.session_state[edit_mode_key] = False
                                    st.toast("수정 완료!", icon="💾")
                                    st.rerun()
                            else:
                                if st.button("✏️", key=f"edit_{idx}", help="수정"):
                                    st.session_state[edit_mode_key] = True
                                    st.rerun()
                        with b2:
                            if st.button("🗑️", key=f"del_{idx}", help="삭제"):
                                df = df.drop(idx)
                                df.to_csv(NOTE_FILE, index=False)
                                st.toast("삭제됨!", icon="🗑️")
                                st.rerun()

                    if is_editing:
                        st.text_area("내용 수정", value=str(row['내용']), key=f"txt_{idx}", height=120, label_visibility="collapsed")
                    else:
                        display_text = str(row['내용']).replace("\n", "  \n")
                        st.markdown(display_text)
                        
        else: st.info("조건에 맞는 기록이 없습니다.")
    else: st.caption("기록이 없습니다.")

# ------------------------------------------
# [모드 2, 3, 4]는 기존과 동일
# ------------------------------------------
elif mode == "💬 코드/대화 이력":
    st.title("💬 코드 수정 이력 관리 (AI)")
    # ... (기존 코드 유지)
    with st.expander("📥 대화 내용 가져오기", expanded=True):
        tab1, tab2 = st.tabs(["📝 직접 붙여넣기", "📂 파일 업로드"])
        with tab1:
            raw_text_input = st.text_area("전체 대화 내용 (Ctrl+V)", height=200, placeholder="내용 붙여넣기")
        with tab2:
            uploaded_file = st.file_uploader("마크다운(.md) 또는 텍스트(.txt) 파일 드래그", type=["md", "txt"])
            file_content = ""
            if uploaded_file is not None:
                stringio = uploaded_file.getvalue().decode("utf-8")
                file_content = stringio
                st.success(f"📂 파일 로드됨: {uploaded_file.name}")
        st.divider()
        final_content = raw_text_input if raw_text_input else file_content
        ai_summary = ""
        if final_content and gemini_api_key:
            if st.button("🤖 AI 자동 요약 실행"):
                with st.spinner("AI 모델 찾는 중..."):
                    try:
                        available_models = [m.name for m in genai.list_models() if 'generateContent' in m.supported_generation_methods]
                        model_name = available_models[0] if available_models else 'gemini-pro'
                        model = genai.GenerativeModel(model_name)
                        response = model.generate_content(f"다음 내용을 50자 이내로 핵심만 요약해줘 (코드 수정 사항 위주로): \n\n{final_content[:10000]}")
                        ai_summary = response.text.strip()
                        st.toast(f"AI 요약 완료 ({model_name})", icon="🤖")
                    except Exception as e:
                        st.error(f"AI 호출 실패: {e}")
        summary_val = ai_summary if ai_summary else (f"파일 업로드: {uploaded_file.name}" if uploaded_file else "")
        summary = st.text_input("📝 핵심 요약 (AI 추천)", value=summary_val)
        if st.button("🚀 이력 저장하기", type="primary"):
            if final_content and summary:
                now = datetime.now()
                new_chat = {'날짜': now.strftime("%Y-%m-%d"), '시간': now.strftime("%H:%M:%S"), '주제': summary, '전체내용': final_content}
                if os.path.exists(CHAT_FILE): df_chat = pd.read_csv(CHAT_FILE)
                else: df_chat = pd.DataFrame(columns=['날짜', '시간', '주제', '전체내용'])
                df_chat = pd.concat([pd.DataFrame([new_chat]), df_chat], ignore_index=True)
                df_chat.to_csv(CHAT_FILE, index=False)
                st.success("✅ 저장되었습니다!")
                st.balloons()
            else: st.warning("내용이 비어있습니다.")
    st.divider()
    st.subheader("📚 수정 히스토리")
    if os.path.exists(CHAT_FILE):
        df_chat = pd.read_csv(CHAT_FILE).fillna("")
        if not df_chat.empty:
            for idx in df_chat.index[::-1]:
                row = df_chat.loc[idx]
                with st.container(border=True):
                    c1, c2 = st.columns([0.85, 0.15])
                    with c1:
                        st.markdown(f"**[{row['날짜']}] {row['주제']}**")
                        st.caption(f"🕒 {row['시간']}")
                    with c2:
                        b_del, _ = st.columns([1, 1])
                        with b_del:
                             if st.button("🗑️", key=f"del_chat_{idx}", help="삭제"):
                                df_chat = df_chat.drop(idx)
                                df_chat.to_csv(CHAT_FILE, index=False)
                                st.toast("삭제됨!", icon="🗑️")
                                st.rerun()
                    with st.expander("내용 보기"):
                         st.code(row['전체내용'])
        else: st.caption("기록이 없습니다.")

elif mode == "📊 일일 리포트":
    st.title("📊 일일 업무 리포트 자동 생성")
    st.info("오늘 하루 동안 **[📝 업무 기록하기]**에 남긴 메모들을 AI가 취합해서 보고서를 써줍니다.")
    today_str = datetime.now().strftime("%Y-%m-%d")
    if os.path.exists(NOTE_FILE):
        df = pd.read_csv(NOTE_FILE).fillna("")
        today_notes = df[df['날짜'] == today_str]
        if not today_notes.empty:
            st.write(f"📅 **{today_str}** 총 **{len(today_notes)}건**의 업무 기록이 있습니다.")
            notes_text = ""
            for idx, row in today_notes.iterrows():
                safe_content = str(row['내용']) if str(row['내용']) != "" else "(내용 없음)"
                notes_text += f"- [{row['메뉴']}] ({row['유형']}): {safe_content}\n"
            with st.expander("📋 오늘 기록된 원본 데이터 보기"):
                st.text(notes_text)
            if st.button("🚀 AI 리포트 생성하기", type="primary"):
                if gemini_api_key:
                    with st.spinner("보고서 작성 중... ✍️"):
                        try:
                            available_models = [m.name for m in genai.list_models() if 'generateContent' in m.supported_generation_methods]
                            model_name = available_models[0] if available_models else 'gemini-pro'
                            model = genai.GenerativeModel(model_name)
                            prompt = f"다음은 오늘 나의 자재관리 업무 로그야. 이 내용을 바탕으로 팀장님께 보고할 '일일 업무 보고서'를 작성해줘.\n\n[조건]\n1. 말투는 '~~함', '~~임' 같은 간결한 보고체(개조식)로 써줘.\n2. 업무별로 카테고리를 나눠서 정리해줘.\n3. '🔥 문제점'으로 기록된 건은 '금일 특이사항'에 강조해서 넣어줘.\n4. 한국어로 작성해줘.\n\n[업무 로그]\n{notes_text}"
                            response = model.generate_content(prompt)
                            report_content = response.text
                            st.subheader("📑 생성된 업무 보고서")
                            st.markdown(f'<div class="report-box">{report_content}</div>', unsafe_allow_html=True)
                            st.balloons()
                        except Exception as e:
                            st.error(f"AI 리포트 생성 실패: {e}")
                else:
                    st.warning("API 키가 설정되지 않았습니다.")
        else:
            st.warning(f"📅 {today_str}에 작성된 업무 기록이 없습니다.")
    else:
        st.error("아직 데이터 파일이 없습니다.")

elif mode == "⚙️ 메뉴/설정 관리":
    st.title("⚙️ 설정 관리")
    if os.path.exists(CONFIG_FILE):
        config_df = pd.read_csv(CONFIG_FILE)
        edited_df = st.data_editor(config_df, num_rows="dynamic", use_container_width=True, hide_index=True)
        if st.button("저장하기", type="primary"):
            edited_df.to_csv(CONFIG_FILE, index=False)
            st.success("설정이 저장되었습니다!")
