import streamlit as st
import pandas as pd
from datetime import datetime
import pytz
import os
import google.generativeai as genai
from streamlit_gsheets import GSheetsConnection
from google.oauth2 import service_account
from googleapiclient.discovery import build
from googleapiclient.http import MediaIoBaseUpload
import io
from PIL import Image

# ==========================================
# 📌 공통: KST 타임존 설정
# ==========================================
TZ_KST = pytz.timezone("Asia/Seoul")

def now_kst():
    """항상 한국 시간(datetime) 반환"""
    return datetime.now(TZ_KST)

def today_kst_str():
    """한국 기준 오늘 날짜 문자열(YYYY-MM-DD)"""
    return now_kst().strftime("%Y-%m-%d")

# ==========================================
# 📌 Google Drive 업로드 함수
# ==========================================
def upload_to_drive(image_file, filename):
    """이미지를 Google Drive에 업로드하고 공개 URL 반환"""
    try:
        # 서비스 계정 인증
        credentials = service_account.Credentials.from_service_account_info(
            st.secrets["gcp_service_account"],
            scopes=['https://www.googleapis.com/auth/drive.file']
        )
        service = build('drive', 'v3', credentials=credentials)
        
        folder_id = st.secrets["GOOGLE_DRIVE_FOLDER_ID"]
        
        # 파일 메타데이터
        file_metadata = {
            'name': filename,
            'parents': [folder_id]
        }
        
        # 이미지 업로드
        media = MediaIoBaseUpload(
            io.BytesIO(image_file.read()),
            mimetype=image_file.type,
            resumable=True
        )
        
        file = service.files().create(
            body=file_metadata,
            media_body=media,
            fields='id, webViewLink, webContentLink'
        ).execute()
        
        # 파일을 공개로 설정
        service.permissions().create(
            fileId=file['id'],
            body={'type': 'anyone', 'role': 'reader'}
        ).execute()
        
        # 직접 이미지 URL 생성
        image_url = f"https://drive.google.com/uc?export=view&id={file['id']}"
        
        return image_url
        
    except Exception as e:
        st.error(f"Drive 업로드 실패: {e}")
        return None

# ==========================================
# 📌 구글 시트 연결 설정
# ==========================================
conn = st.connection("gsheets", type=GSheetsConnection)

# ==========================================
# 📌 데이터 로드/저장 함수
# ==========================================
def load_sheet(worksheet_name):
    """구글 시트에서 데이터를 불러오는 함수"""
    try:
        df = conn.read(worksheet=worksheet_name, ttl=0)
        df = df.copy()
        
        if df.empty or df.shape[1] == 0:
            if worksheet_name == "notes":
                return pd.DataFrame(columns=['날짜', '시간', '메뉴', '유형', '내용', '이미지'])
            elif worksheet_name == "chats":
                return pd.DataFrame(columns=['날짜', '시간', '주제', '전체내용'])
            elif worksheet_name == "config":
                return pd.DataFrame(columns=["메뉴명", "시트정보", "트리거정보", "업무설명", "메일발송설정"])
        
        df = df.fillna("")
        
        for col in df.columns:
            try:
                df[col] = df[col].apply(
                    lambda x: str(x).encode('utf-8', errors='ignore').decode('utf-8').strip() 
                    if pd.notna(x) and str(x).strip() != '' else ""
                )
            except Exception:
                df[col] = df[col].astype(str)
        
        return df
        
    except Exception as e:
        st.error(f"시트 읽기 실패 ({worksheet_name}): {e}")
        if worksheet_name == "notes":
            return pd.DataFrame(columns=['날짜', '시간', '메뉴', '유형', '내용', '이미지'])
        elif worksheet_name == "chats":
            return pd.DataFrame(columns=['날짜', '시간', '주제', '전체내용'])
        elif worksheet_name == "config":
            return pd.DataFrame(columns=["메뉴명", "시트정보", "트리거정보", "업무설명", "메일발송설정"])

def save_sheet(df, worksheet_name):
    """구글 시트에 데이터를 저장하는 함수"""
    try:
        conn.update(worksheet=worksheet_name, data=df)
        return True
    except Exception as e:
        st.error(f"시트 저장 실패 ({worksheet_name}): {e}")
        return False

# ==========================================
# 2. 스타일 (CSS)
# ==========================================
st.markdown("""
<style>
    .badge-container { display: flex; flex-wrap: wrap; gap: 6px; margin-bottom: 10px; }
    .sheet-badge { background-color: #E3F2FD; color: #1565C0; padding: 4px 10px; border-radius: 15px; font-size: 13px; font-weight: 600; border: 1px solid #90CAF9; }
    .trigger-box { background-color: #F1F8E9; padding: 8px 12px; border-radius: 6px; margin-bottom: 6px; font-size: 14px; border-left: 4px solid #66BB6A; }
    .report-box { background-color: #FAFAFA; padding: 20px; border-radius: 10px; border: 1px solid #EEE; box-shadow: 0 2px 5px rgba(0,0,0,0.05); }
    .stButton button { height: 34px; padding: 0 8px; min-width: 0px; margin: 0px; }
    .stTextArea textarea { overflow-y: hidden; }
    [data-testid="column"] { padding: 0px !important; }
    .note-image { max-width: 100%; border-radius: 8px; margin-top: 10px; border: 1px solid #ddd; cursor: pointer; }
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
    
    st.divider()
    if st.button("🔄 캐시 초기화"):
        st.session_state.clear()
        st.cache_data.clear()
        st.cache_resource.clear()
        st.success("초기화 완료!")
        st.rerun()

mode = st.sidebar.radio("모드 선택", ["📝 업무 기록하기", "💬 코드/대화 이력", "📊 일일 리포트", "⚙️ 메뉴/설정 관리"])

# ------------------------------------------
# [모드 1] 업무 기록하기
# ------------------------------------------
if mode == "📝 업무 기록하기":
    config_df = load_sheet("config")
    
    if config_df.empty or len(config_df) == 0:
        st.error("⚠️ config 시트가 비어있습니다. Google Sheets에서 데이터를 복구해주세요.")
        st.info("📋 config 시트 필수 컬럼: 메뉴명, 시트정보, 트리거정보, 업무설명, 메일발송설정")
        st.stop()
    
    menu_list = config_df['메뉴명'].tolist()
    selected_menu_name = st.sidebar.radio("업무 선택", menu_list)
    
    try:
        current_idx = config_df.index[config_df['메뉴명'] == selected_menu_name][0]
        current_row = config_df.iloc[current_idx]
    except (IndexError, KeyError):
        st.error("⚠️ config 시트 데이터가 올바르지 않습니다.")
        st.stop()
    
    st.header(f"{selected_menu_name}")
    
    with st.expander("ℹ️ 업무 설명 (클릭하여 편집)", expanded=True):
        description = str(current_row['업무설명'])
        new_desc = st.text_area("설명 수정", value=description, height=70, label_visibility="collapsed")
        if new_desc != description:
            if st.button("설명 업데이트 저장"):
                config_df.at[current_idx, '업무설명'] = new_desc
                if save_sheet(config_df, "config"):
                    st.success("업무 설명이 업데이트되었습니다!")
                    st.rerun()

    col1, col2 = st.columns(2)
    with col1:
        with st.container(border=True):
            st.markdown("##### 📂 시트 정보")
            sheet_items = [item.strip() for item in str(current_row['시트정보']).split(',') if item.strip()]
            badges_html = '<div class="badge-container">'
            for item in sheet_items:
                badges_html += f'<span class="sheet-badge">{item}</span>'
            badges_html += '</div>'
            st.markdown(badges_html, unsafe_allow_html=True)
    with col2:
        with st.container(border=True):
            st.markdown("##### ⚡ 트리거 정보")
            trigger_items = [item.strip() for item in str(current_row['트리거정보']).split(',') if item.strip()]
            for item in trigger_items:
                formatted_item = item.replace("함수:", "<strong>🛠️ 함수:</strong>")
                st.markdown(f'<div class="trigger-box">{formatted_item}</div>', unsafe_allow_html=True)

    if current_row['메일발송설정']:
        st.info("📧 이 업무는 **메일 발송** 프로세스가 포함되어 있습니다.")
    st.divider()
    
    st.write("###### 📝 기록 유형")
    note_type = st.radio("유형", ["💡 아이디어", "✅ 업데이트", "🔥 문제점"],
                         horizontal=True, label_visibility="collapsed")
    
    input_key = f"note_{selected_menu_name}"
    ph = "내용을 입력하세요."
    if "아이디어" in note_type:
        ph = "개선 아이디어 입력"
    elif "문제점" in note_type:
        ph = "발생한 오류나 이슈 기록"
    
    st.text_area("내용 입력", height=100, placeholder=ph,
                 key=input_key, label_visibility="collapsed")
    
    # ✅ 이미지 업로드 추가
    uploaded_image = st.file_uploader("📸 캡처 이미지 첨부 (선택)", 
                                      type=['png', 'jpg', 'jpeg'],
                                      key=f"img_{selected_menu_name}")
    
    if st.button("💾 기록 저장", type="primary"):
        safe_content = st.session_state.get(input_key, "")
        if safe_content.strip():
            image_url = ""
            
            # 이미지 업로드 처리
            if uploaded_image is not None:
                with st.spinner("📤 Google Drive에 이미지 업로드 중..."):
                    # 파일명: 날짜_시간_메뉴명.확장자
                    now = now_kst()
                    ext = uploaded_image.name.split('.')[-1]
                    filename = f"{now.strftime('%Y%m%d_%H%M%S')}_{selected_menu_name}.{ext}"
                    
                    image_url = upload_to_drive(uploaded_image, filename)
                    if image_url:
                        st.success("✅ 이미지 업로드 완료!")
                    else:
                        st.warning("⚠️ 이미지 업로드 실패 (기록은 저장됨)")
            
            now = now_kst()
            new_note = {
                '날짜': now.strftime("%Y-%m-%d"),
                '시간': now.strftime("%H:%M:%S"),
                '메뉴': selected_menu_name,
                '유형': note_type,
                '내용': safe_content,
                '이미지': image_url
            }
            
            df_note = load_sheet("notes")
            df_note = pd.concat([pd.DataFrame([new_note]), df_note], ignore_index=True)
            
            if save_sheet(df_note, "notes"):
                st.toast("저장되었습니다!", icon="✅")
                del st.session_state[input_key]
                st.rerun()
        else:
            st.warning("⚠️ 내용을 입력해주세요.")

    st.divider()
    
    st.subheader(f"📊 히스토리")

    df = load_sheet("notes").fillna("")
    df = df[~df['날짜'].str.contains('2025', na=False)]
    df_filtered = df[df['메뉴'] == selected_menu_name]
    
    if not df_filtered.empty:
        for idx in df_filtered.index[::-1]:
            row = df.loc[idx]
            
            try:
                note_date = str(row['날짜']).strip()
                note_time = str(row['시간']).strip()
                note_type = str(row['유형']).strip()
                note_content = str(row['내용']).strip()
                note_image = str(row.get('이미지', '')).strip() if '이미지' in row else ""
                
                if not note_content or note_content == 'nan' or len(note_content) < 2:
                    continue
                    
            except Exception:
                continue
            
            icon = "🔥" if "문제점" in note_type else ("💡" if "아이디어" in note_type else "✅")
            
            with st.container(border=True):
                col_txt, col_btn = st.columns([0.88, 0.12])
                
                with col_txt:
                    st.markdown(f"**{icon} [{note_type}] {note_date} {note_time}**")
                
                with col_btn:
                    if st.button("🗑️", key=f"del_{idx}", help="삭제"):
                        df = df.drop(idx)
                        if save_sheet(df, "notes"):
                            st.toast("삭제됨!", icon="🗑️")
                            st.rerun()
                
                st.markdown(note_content.replace("\n", "  \n"))
                
                # ✅ 이미지 표시
                if note_image and note_image != 'nan' and note_image.startswith('http'):
                    st.image(note_image, use_container_width=True)
    else:
        st.info("조건에 맞는 기록이 없습니다.")

# ------------------------------------------
# [모드 2] 코드/대화 이력 (AI 강화)
# ------------------------------------------
elif mode == "💬 코드/대화 이력":
    st.title("💬 코드 수정 이력 관리 (AI)")
    
    with st.expander("📥 대화 내용 가져오기", expanded=True):
        tab1, tab2 = st.tabs(["📝 직접 붙여넣기", "📂 파일 업로드"])
        
        with tab1:
            raw_text_input = st.text_area("전체 대화 내용 (Ctrl+V)", height=200,
                                          placeholder="Perplexity 대화 복사해서 붙여넣기")
        
        with tab2:
            uploaded_file = st.file_uploader(
                "마크다운(.md) 또는 텍스트(.txt) 파일", type=["md", "txt"]
            )
            file_content = ""
            if uploaded_file is not None:
                file_content = uploaded_file.getvalue().decode("utf-8")
                st.success(f"📂 파일 로드됨: {uploaded_file.name}")
        
        st.divider()
        final_content = raw_text_input if raw_text_input else file_content
        ai_summary = ""
        
        if final_content and gemini_api_key:
            if st.button("🤖 AI 자동 요약 실행", type="primary"):
                with st.spinner("AI가 대화 내용을 분석 중... 🔍"):
                    try:
                        available_models = [
                            m.name for m in genai.list_models()
                            if 'generateContent' in m.supported_generation_methods
                        ]
                        model_name = available_models[0] if available_models else 'gemini-pro'
                        model = genai.GenerativeModel(model_name)
                        
                        # ✅ 강화된 프롬프트
                        prompt = f"""
다음은 나와 AI의 대화 내용이야. 이 대화를 완벽하게 요약해줘.

[요약 형식]
**제목**: [한 줄 요약 (10자 이내)]

**질문 요약**:
- 내가 무엇을 물어봤는지

**해결 과정**:
- AI가 제시한 해결 방법
- 시도했던 방법들
- 발생했던 문제점

**최종 해결책**:
- 어떤 코드를 수정했는지
- 어떤 파일을 변경했는지
- 핵심 코드 스니펫 (있다면)

**결과**:
- 문제가 해결됐는지
- 남은 이슈

[대화 내용]
{final_content[:15000]}
"""
                        
                        response = model.generate_content(prompt)
                        ai_summary = response.text.strip()
                        st.success(f"✅ AI 요약 완료 ({model_name})")
                        
                        # 요약 결과 미리보기
                        with st.container(border=True):
                            st.markdown("### 📋 AI 요약 결과")
                            st.markdown(ai_summary)
                        
                    except Exception as e:
                        st.error(f"AI 호출 실패: {e}")
        
        summary_val = ai_summary if ai_summary else (
            f"파일: {uploaded_file.name}" if uploaded_file else ""
        )
        
        summary = st.text_area("📝 최종 요약 (수정 가능)", value=summary_val, height=150)
        
        if st.button("🚀 이력 저장하기", type="primary"):
            if final_content and summary:
                now = now_kst()
                new_chat = {
                    '날짜': now.strftime("%Y-%m-%d"),
                    '시간': now.strftime("%H:%M:%S"),
                    '주제': summary.split('\n')[0][:100],  # 첫 줄을 주제로
                    '전체내용': f"## 요약\n\n{summary}\n\n## 원본 대화\n\n{final_content}"
                }
                
                df_chat = load_sheet("chats")
                df_chat = pd.concat([pd.DataFrame([new_chat]), df_chat], ignore_index=True)
                
                if save_sheet(df_chat, "chats"):
                    st.success("✅ 저장되었습니다!")
                    st.balloons()
                    st.rerun()
            else:
                st.warning("내용이 비어있습니다.")
    
    st.divider()
    st.subheader("📚 수정 히스토리")
    
    df_chat = load_sheet("chats").fillna("")
    if not df_chat.empty:
        for idx in df_chat.index[::-1]:
            row = df_chat.loc[idx]
            with st.container(border=True):
                c1, c2 = st.columns([0.85, 0.15])
                with c1:
                    st.markdown(f"**[{row['날짜']}] {row['주제']}**")
                    st.caption(f"🕒 {row['시간']}")
                with c2:
                    if st.button("🗑️", key=f"del_chat_{idx}", help="삭제"):
                        df_chat = df_chat.drop(idx)
                        if save_sheet(df_chat, "chats"):
                            st.toast("삭제됨!", icon="🗑️")
                            st.rerun()
                
                with st.expander("📖 전체 내용 보기"):
                    st.markdown(row['전체내용'])
    else:
        st.info("기록이 없습니다.")

# ------------------------------------------
# [모드 3] 일일 리포트
# ------------------------------------------
elif mode == "📊 일일 리포트":
    st.title("📊 일일 업무 리포트 자동 생성")
    st.info("오늘 하루 동안 **[📝 업무 기록하기]**에 남긴 메모들을 AI가 취합해서 보고서를 써줍니다.")
    
    today_str = today_kst_str()
    df = load_sheet("notes").fillna("")
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
                        available_models = [
                            m.name for m in genai.list_models()
                            if 'generateContent' in m.supported_generation_methods
                        ]
                        model_name = available_models[0] if available_models else 'gemini-pro'
                        model = genai.GenerativeModel(model_name)
                        
                        prompt = (
                            "다음은 오늘 나의 자재관리 업무 로그야. "
                            "이 내용을 바탕으로 팀장님께 보고할 '일일 업무 보고서'를 작성해줘.\n\n"
                            "[조건]\n"
                            "1. 말투는 '~~함', '~~임' 같은 간결한 보고체(개조식)로 써줘.\n"
                            "2. 업무별로 카테고리를 나눠서 정리해줘.\n"
                            "3. '🔥 문제점'으로 기록된 건은 '금일 특이사항'에 강조해서 넣어줘.\n"
                            "4. 한국어로 작성해줘.\n\n"
                            f"[업무 로그]\n{notes_text}"
                        )
                        
                        response = model.generate_content(prompt)
                        report_content = response.text
                        
                        st.subheader("📑 생성된 업무 보고서")
                        st.markdown(
                            f'<div class="report-box">{report_content}</div>',
                            unsafe_allow_html=True
                        )
                        st.balloons()
                    except Exception as e:
                        st.error(f"AI 리포트 생성 실패: {e}")
            else:
                st.warning("API 키가 설정되지 않았습니다.")
    else:
        st.warning(f"📅 {today_str}에 작성된 업무 기록이 없습니다.")

# ------------------------------------------
# [모드 4] 메뉴/설정 관리
# ------------------------------------------
elif mode == "⚙️ 메뉴/설정 관리":
    st.title("⚙️ 설정 관리")
    config_df = load_sheet("config")
    edited_df = st.data_editor(config_df, num_rows="dynamic",
                               use_container_width=True, hide_index=True)
    if st.button("저장하기", type="primary"):
        if save_sheet(edited_df, "config"):
            st.success("설정이 저장되었습니다!")
