import streamlit as st
import pandas as pd
from datetime import datetime, timedelta
import pytz
import os
import google.generativeai as genai
from streamlit_gsheets import GSheetsConnection
from google.oauth2 import service_account
from googleapiclient.discovery import build
from googleapiclient.http import MediaIoBaseUpload
import io
from PIL import Image
from streamlit_paste_button import paste_image_button as pbutton

# 한국 시간대 설정
TZ_KST = pytz.timezone("Asia/Seoul")

def now_kst():
    """항상 한국 시간(datetime) 반환"""
    return datetime.now(TZ_KST)

def today_kst_str():
    """한국 기준 오늘 날짜 문자열(YYYY-MM-DD)"""
    return now_kst().strftime("%Y-%m-%d")

# Google Sheets 연결
conn = st.connection("gsheets", type=GSheetsConnection)

def load_sheet(worksheet):
    """시트 로드"""
    try:
        df = conn.read(worksheet=worksheet, ttl=0)
        for col in df.columns:
            if df[col].dtype == 'object':
                df[col] = df[col].astype(str)
        return df
    except Exception as e:
        st.error(f"시트 로드 실패: {e}")
        return pd.DataFrame()

def save_sheet(df, worksheet):
    """시트 저장"""
    try:
        conn.update(worksheet=worksheet, data=df)
        return True
    except Exception as e:
        st.error(f"저장 실패: {e}")
        return False

def upload_to_drive(image_file, filename):
    """Google Drive에 이미지 업로드"""
    try:
        credentials = service_account.Credentials.from_service_account_info(
            st.secrets["gcp_service_account"],
            scopes=['https://www.googleapis.com/auth/drive.file']
        )
        service = build('drive', 'v3', credentials=credentials)
        
        file_metadata = {
            'name': filename,
            'parents': [st.secrets["drive_folder_id"]]
        }
        
        media = MediaIoBaseUpload(
            io.BytesIO(image_file.getvalue()),
            mimetype=image_file.type,
            resumable=True
        )
        
        file = service.files().create(
            body=file_metadata,
            media_body=media,
            fields='id, webViewLink'
        ).execute()
        
        return file.get('webViewLink')
    except Exception as e:
        st.error(f"이미지 업로드 실패: {e}")
        return None

# Gemini API 설정
if "GEMINI_API_KEY" in st.secrets:
    genai.configure(api_key=st.secrets["GEMINI_API_KEY"])

# 페이지 설정
st.set_page_config(page_title="스마트 업무 비서", page_icon="📝", layout="wide")
st.title("📝 스마트 업무 비서")

# ========== 사이드바: API 상태 & 캐시 초기화 ==========
with st.sidebar:
    st.markdown("### 🔑 AI 설정")
    if "GEMINI_API_KEY" in st.secrets:
        st.success("🟢 Gemini AI 연결됨")
    else:
        st.warning("🔴 API 키 없음")
    
    st.markdown("---")
    
    if st.button("🔄 캐시 초기화", help="데이터 새로고침"):
        st.session_state.clear()
        st.cache_data.clear()
        st.cache_resource.clear()
        st.success("✅ 캐시 초기화 완료!")
        st.rerun()
    
    st.markdown("---")

# 사이드바 모드 선택
mode = st.sidebar.radio(
    "모드 선택",
    ["📝 업무 기록하기", "💬 대화 이력", "📊 일일 리포트", "⚙️ 메뉴/설정 관리"]
)

# ================== 모드 1: 업무 기록하기 ==================
if mode == "📝 업무 기록하기":
    st.header("📝 업무 기록하기")
    
    # config 로드
    config_df = load_sheet("config")
    
    if not config_df.empty:
        menu_list = config_df["메뉴명"].tolist()
        
        # 클립보드 이미지 붙여넣기 (Form 밖)
        st.write("**🖼️ 이미지 추가 (선택)**")
        paste_result = pbutton(
            label="📋 클립보드에서 이미지 붙여넣기",
            key="clipboard_paste"
        )
        
        # 클립보드 이미지 미리보기
        if paste_result.image_data is not None:
            st.success("✅ 클립보드 이미지 준비됨!")
            st.image(paste_result.image_data, width=200)
            st.session_state["pending_image"] = paste_result.image_data
        
        st.divider()
        
        # 폼 사용으로 자동 초기화
        with st.form(key="note_form", clear_on_submit=True):
            selected_menu = st.selectbox("업무 선택", menu_list)
            note_type = st.radio("유형", ["💡 아이디어", "✅ 업데이트"], horizontal=True)
            content = st.text_area("내용", height=150)
            
            uploaded_file = st.file_uploader(
                "또는 파일 업로드",
                type=['png', 'jpg', 'jpeg'],
                key="file_upload"
            )
            
            submit = st.form_submit_button("💾 저장", type="primary")
            
            if submit:
                if content.strip():
                    # 이미지 처리
                    image_url = None
                    
                    # 클립보드 이미지 우선
                    if "pending_image" in st.session_state:
                        timestamp = now_kst().strftime("%Y%m%d_%H%M%S")
                        filename = f"clipboard_{timestamp}.png"
                        
                        img_byte_arr = io.BytesIO()
                        st.session_state["pending_image"].save(img_byte_arr, format='PNG')
                        img_byte_arr.seek(0)
                        
                        class FakeFile:
                            def __init__(self, data):
                                self.data = data
                                self.type = "image/png"
                            def getvalue(self):
                                return self.data
                        
                        fake_file = FakeFile(img_byte_arr.getvalue())
                        image_url = upload_to_drive(fake_file, filename)
                        del st.session_state["pending_image"]
                    
                    elif uploaded_file is not None:
                        timestamp = now_kst().strftime("%Y%m%d_%H%M%S")
                        filename = f"{timestamp}_{uploaded_file.name}"
                        image_url = upload_to_drive(uploaded_file, filename)
                    
                    # notes 시트에 저장
                    notes_df = load_sheet("notes")
                    new_row = pd.DataFrame([{
                        "날짜": today_kst_str(),
                        "시간": now_kst().strftime("%H:%M:%S"),
                        "메뉴": selected_menu,
                        "유형": note_type,
                        "내용": content,
                        "이미지": image_url if image_url else ""
                    }])
                    
                    updated_df = pd.concat([notes_df, new_row], ignore_index=True)
                    
                    if save_sheet(updated_df, "notes"):
                        st.success("✅ 저장 완료!")
                        st.rerun()
                    else:
                        st.error("❌ 저장 실패")
                else:
                    st.warning("⚠️ 내용을 입력하세요")
        
        # ========== 업무별 히스토리 (필터링 + 편집 + 삭제) ==========
        st.divider()
        st.subheader("📚 업무 기록 히스토리")
        
        notes_df = load_sheet("notes")
        
        if not notes_df.empty:
            # 필터링 옵션
            col1, col2 = st.columns([1, 1])
            with col1:
                filter_menu = st.selectbox(
                    "업무 필터",
                    ["전체 보기"] + menu_list
                )
            with col2:
                filter_type = st.selectbox(
                    "유형 필터",
                    ["전체", "💡 아이디어", "✅ 업데이트"]
                )
            
            # 필터 적용
            filtered_df = notes_df.copy()
            if filter_menu != "전체 보기":
                filtered_df = filtered_df[filtered_df["메뉴"] == filter_menu]
            if filter_type != "전체":
                filtered_df = filtered_df[filtered_df["유형"] == filter_type]
            
            if not filtered_df.empty:
                for idx, row in filtered_df.iloc[::-1].iterrows():
                    col1, col2 = st.columns([5, 1])
                    
                    with col1:
                        with st.expander(f"{row['유형']} [{row['메뉴']}] {row['날짜']} {row['시간']}"):
                            # 편집 모드
                            if f"edit_{idx}" in st.session_state and st.session_state[f"edit_{idx}"]:
                                new_content = st.text_area(
                                    "내용 수정",
                                    value=row['내용'],
                                    key=f"edit_content_{idx}",
                                    height=150
                                )
                                
                                col_save, col_cancel = st.columns(2)
                                with col_save:
                                    if st.button("💾 저장", key=f"save_{idx}", type="primary"):
                                        notes_df.loc[idx, '내용'] = new_content
                                        if save_sheet(notes_df, "notes"):
                                            st.success("✅ 수정 완료!")
                                            st.session_state[f"edit_{idx}"] = False
                                            st.rerun()
                                with col_cancel:
                                    if st.button("❌ 취소", key=f"cancel_{idx}"):
                                        st.session_state[f"edit_{idx}"] = False
                                        st.rerun()
                            else:
                                # 일반 보기 모드
                                st.markdown(row['내용'])
                                if row['이미지'] and str(row['이미지']) != 'nan' and str(row['이미지']).strip():
                                    st.markdown(f"[🖼️ 이미지 보기]({row['이미지']})")
                    
                    with col2:
                        if f"edit_{idx}" not in st.session_state or not st.session_state[f"edit_{idx}"]:
                            if st.button("✏️", key=f"edit_btn_{idx}", help="수정"):
                                st.session_state[f"edit_{idx}"] = True
                                st.rerun()
                        
                        if st.button("🗑️", key=f"del_{idx}", help="삭제"):
                            notes_df = notes_df.drop(idx)
                            if save_sheet(notes_df, "notes"):
                                st.success("✅ 삭제 완료!")
                                st.rerun()
            else:
                st.info("📭 조건에 맞는 기록이 없습니다")
        else:
            st.info("📭 아직 기록이 없습니다")
    else:
        st.warning("설정 메뉴에서 업무를 먼저 등록하세요")

# ================== 모드 2: 대화 이력 ==================
elif mode == "💬 대화 이력":
    st.header("💬 대화 이력")
    
    with st.form(key="chat_form", clear_on_submit=True):
        chat_topic = st.text_input("주제/제목")
        chat_content = st.text_area("대화 내용 (전체 복사 붙여넣기)", height=300)
        
        submit = st.form_submit_button("💾 저장", type="primary")
        
        if submit:
            if chat_topic.strip() and chat_content.strip():
                chats_df = load_sheet("chats")
                new_row = pd.DataFrame([{
                    "날짜": today_kst_str(),
                    "시간": now_kst().strftime("%H:%M:%S"),
                    "주제": chat_topic,
                    "전체내용": chat_content
                }])
                
                updated_df = pd.concat([chats_df, new_row], ignore_index=True)
                
                if save_sheet(updated_df, "chats"):
                    st.success("✅ 저장 완료!")
                else:
                    st.error("❌ 저장 실패")
            else:
                st.warning("⚠️ 주제와 내용을 모두 입력하세요")
    
    # AI 요약 기능
    st.divider()
    st.subheader("🤖 AI 요약")
    
    if "GEMINI_API_KEY" in st.secrets:
        if st.button("📋 오늘 대화 AI 요약하기"):
            chats_df = load_sheet("chats")
            today_str = today_kst_str()
            today_chats = chats_df[chats_df["날짜"] == today_str]
            
            if not today_chats.empty:
                all_content = "\n\n---\n\n".join(today_chats["전체내용"].tolist())
                
                prompt = f"""다음은 오늘({today_str}) 나눈 대화 내용입니다.
이 대화를 분석하여 다음 형식으로 요약해주세요:

## 📌 주요 질문
- [질문 1]
- [질문 2]

## 💡 해결 내용
- [해결 1]
- [해결 2]

## 📝 코드/파일 변경사항
- [변경 1]
- [변경 2]

## 🎯 다음 할 일
- [할일 1]
- [할일 2]

대화 내용:
{all_content[:30000]}
"""
                
                try:
                    model = genai.GenerativeModel('gemini-2.5-flash')
                    response = model.generate_content(prompt)
                    summary = response.text
                    
                    st.session_state["ai_summary"] = summary
                    st.session_state["summary_topic"] = f"{today_str} 일일 요약"
                    
                    st.success("✅ 요약 완료!")
                except Exception as e:
                    st.error(f"❌ 요약 실패: {e}")
            else:
                st.warning("⚠️ 오늘 기록이 없습니다")
    
    # AI 요약 결과 표시 및 저장
    if "ai_summary" in st.session_state:
        st.markdown("### 📄 요약 결과")
        st.markdown(st.session_state["ai_summary"])
        
        col1, col2, col3 = st.columns([2, 1, 1])
        
        with col1:
            config_df = load_sheet("config")
            if not config_df.empty:
                related_menu = st.selectbox(
                    "관련 업무 (선택)",
                    ["없음"] + config_df["메뉴명"].tolist()
                )
            else:
                related_menu = "없음"
        
        with col2:
            if st.button("💾 이중 저장 (chats + notes)", type="primary"):
                summary = st.session_state["ai_summary"]
                topic = st.session_state["summary_topic"]
                
                # 1. chats 저장
                chats_df = load_sheet("chats")
                chat_row = pd.DataFrame([{
                    "날짜": today_kst_str(),
                    "시간": now_kst().strftime("%H:%M:%S"),
                    "주제": topic,
                    "전체내용": summary
                }])
                chats_updated = pd.concat([chats_df, chat_row], ignore_index=True)
                save_sheet(chats_updated, "chats")
                
                # 2. notes 저장 (업무 선택 시)
                if related_menu != "없음":
                    notes_df = load_sheet("notes")
                    note_row = pd.DataFrame([{
                        "날짜": today_kst_str(),
                        "시간": now_kst().strftime("%H:%M:%S"),
                        "메뉴": related_menu,
                        "유형": "💡 아이디어",
                        "내용": summary,
                        "이미지": ""
                    }])
                    notes_updated = pd.concat([notes_df, note_row], ignore_index=True)
                    save_sheet(notes_updated, "notes")
                
                st.success("✅ 이중 저장 완료!")
                del st.session_state["ai_summary"]
                del st.session_state["summary_topic"]
                st.rerun()
        
        with col3:
            if st.button("🗑️ 삭제"):
                del st.session_state["ai_summary"]
                del st.session_state["summary_topic"]
                st.rerun()
    
    # 히스토리 표시 (날짜 필터 + 삭제)
    st.divider()
    st.subheader("📚 대화 히스토리")
    chats_df = load_sheet("chats")
    
    if not chats_df.empty:
        # 날짜 필터
        col1, col2 = st.columns([1, 3])
        with col1:
            filter_option = st.selectbox(
                "기간 선택",
                ["전체 보기", "오늘만", "이번 주", "이번 달"]
            )
        
        # 필터링 로직
        filtered_df = chats_df.copy()
        
        if filter_option == "오늘만":
            filtered_df = filtered_df[filtered_df["날짜"] == today_kst_str()]
        elif filter_option == "이번 주":
            week_ago = (now_kst() - timedelta(days=7)).strftime("%Y-%m-%d")
            filtered_df = filtered_df[filtered_df["날짜"] >= week_ago]
        elif filter_option == "이번 달":
            this_month = now_kst().strftime("%Y-%m")
            filtered_df = filtered_df[filtered_df["날짜"].astype(str).str.startswith(this_month)]
        
        # 전체 표시 (최신순)
        if not filtered_df.empty:
            for idx, row in filtered_df.iloc[::-1].iterrows():
                col1, col2 = st.columns([5, 1])
                
                with col1:
                    with st.expander(f"📅 {row['날짜']} {row['시간']} - {row['주제']}"):
                        st.markdown(row['전체내용'])
                
                with col2:
                    if st.button("🗑️", key=f"del_chat_{idx}", help="삭제"):
                        chats_df = chats_df.drop(idx)
                        if save_sheet(chats_df, "chats"):
                            st.success("✅ 삭제 완료!")
                            st.rerun()
        else:
            st.info(f"📭 {filter_option} 기록이 없습니다")
    else:
        st.info("📭 아직 대화 기록이 없습니다")

# ================== 모드 3: 일일 리포트 ==================
elif mode == "📊 일일 리포트":
    st.header(f"📊 {today_kst_str()} 일일 리포트")
    
    notes_df = load_sheet("notes")
    today_str = today_kst_str()
    today_notes = notes_df[notes_df["날짜"] == today_str]
    
    if not today_notes.empty:
        # 업무별로 정리
        for menu in today_notes["메뉴"].unique():
            st.subheader(f"📌 {menu}")
            menu_notes = today_notes[today_notes["메뉴"] == menu]
            
            for idx, row in menu_notes.iterrows():
                col1, col2 = st.columns([3, 1])
                with col1:
                    st.markdown(f"{row['유형']} **{row['시간']}**")
                    st.markdown(row['내용'])
                with col2:
                    if row['이미지'] and str(row['이미지']) != 'nan' and str(row['이미지']).strip():
                        st.markdown(f"[🖼️ 이미지 보기]({row['이미지']})")
            st.divider()
        
        # AI 요약 버튼
        st.divider()
        if "GEMINI_API_KEY" in st.secrets:
            if st.button("🤖 오늘 업무 AI 요약하기", type="primary"):
                all_content = "\n\n".join([
                    f"[{row['메뉴']}] {row['유형']}\n{row['내용']}"
                    for idx, row in today_notes.iterrows()
                ])
                
                prompt = f"""다음은 오늘({today_str}) 작성한 업무 기록입니다.
이를 분석하여 다음 형식으로 요약해주세요:

## 📊 업무별 요약
- [업무1]: 주요 내용
- [업무2]: 주요 내용

## 💡 주요 성과
- 성과 1
- 성과 2

## 🎯 내일 할 일
- 할일 1
- 할일 2

업무 내용:
{all_content[:30000]}
"""
                
                try:
                    model = genai.GenerativeModel('gemini-2.5-flash')
                    response = model.generate_content(prompt)
                    st.markdown("### 📄 AI 요약 결과")
                    st.markdown(response.text)
                except Exception as e:
                    st.error(f"❌ 요약 실패: {e}")
    else:
        st.warning(f"📅 {today_str}에 작성된 업무 기록이 없습니다")

# ================== 모드 4: 설정 관리 ==================
elif mode == "⚙️ 메뉴/설정 관리":
    st.title("⚙️ 설정 관리")
    config_df = load_sheet("config")
    
    edited_df = st.data_editor(
        config_df,
        num_rows="dynamic",
        use_container_width=True,
        hide_index=True
    )
    
    if st.button("💾 저장", type="primary"):
        if save_sheet(edited_df, "config"):
            st.success("✅ 설정이 저장되었습니다!")
