import streamlit as st
import pandas as pd
from datetime import datetime, timedelta
import pytz
import os
import re
import google.generativeai as genai
from streamlit_gsheets import GSheetsConnection
from google.oauth2 import service_account
from googleapiclient.discovery import build
from googleapiclient.http import MediaIoBaseUpload
import io
from PIL import Image

# streamlit_paste_button 선택적 import
try:
    from streamlit_paste_button import paste_image_button as pbutton
    PASTE_BUTTON_AVAILABLE = True
except ImportError:
    PASTE_BUTTON_AVAILABLE = False

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
    """시트 로드 - 안전한 버전"""
    try:
        df = conn.read(worksheet=worksheet, ttl=0)
        
        if df is None or len(df) == 0:
            if worksheet == "notes":
                return pd.DataFrame(columns=['날짜', '시간', '메뉴', '유형', '내용', '이미지', '알림시간', '완료'])
            elif worksheet == "chats":
                return pd.DataFrame(columns=['날짜', '시간', '주제', '전체내용'])
            elif worksheet == "config":
                return pd.DataFrame(columns=["메뉴명", "시트정보", "트리거정보", "업무설명", "메일발송설정"])
        
        df = df.fillna("")
        
        # 기존 데이터에 새 컬럼 추가 (없으면)
        if worksheet == "notes":
            if "알림시간" not in df.columns:
                df["알림시간"] = ""
            if "완료" not in df.columns:
                df["완료"] = ""
        
        for col in df.columns:
            if df[col].dtype == 'object':
                df[col] = df[col].astype(str).str.strip()
        
        return df
        
    except Exception as e:
        st.error(f"시트 로드 실패 ({worksheet}): {e}")
        if worksheet == "notes":
            return pd.DataFrame(columns=['날짜', '시간', '메뉴', '유형', '내용', '이미지', '알림시간', '완료'])
        elif worksheet == "chats":
            return pd.DataFrame(columns=['날짜', '시간', '주제', '전체내용'])
        elif worksheet == "config":
            return pd.DataFrame(columns=["메뉴명", "시트정보", "트리거정보", "업무설명", "메일발송설정"])

def save_sheet(df, worksheet):
    """시트 저장"""
    try:
        conn.update(worksheet=worksheet, data=df)
        return True
    except Exception as e:
        st.error(f"저장 실패 ({worksheet}): {e}")
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
            'parents': [st.secrets["GOOGLE_DRIVE_FOLDER_ID"]]
        }
        
        if hasattr(image_file, 'read'):
            image_data = image_file.read()
            image_file.seek(0)
        else:
            image_data = image_file.getvalue()
        
        media = MediaIoBaseUpload(
            io.BytesIO(image_data),
            mimetype='image/png',
            resumable=True
        )
        
        file = service.files().create(
            body=file_metadata,
            media_body=media,
            fields='id, webViewLink'
        ).execute()
        
        service.permissions().create(
            fileId=file['id'],
            body={'type': 'anyone', 'role': 'reader'}
        ).execute()
        
        return f"https://drive.google.com/uc?export=view&id={file['id']}"
        
    except Exception as e:
        st.error(f"이미지 업로드 실패: {e}")
        return None

def ai_classify_note(content, menu_list, config_df):
    """AI로 업무와 유형 자동 분류"""
    try:
        if "GEMINI_API_KEY" not in st.secrets:
            return None, None, None
        
        model = genai.GenerativeModel('gemini-2.5-flash')
        
        menu_info = ""
        for idx, row in config_df.iterrows():
            if "업무설명" in config_df.columns and str(row["업무설명"]).strip():
                menu_info += f"{idx+1}. {row['메뉴명']}: {row['업무설명']}\n"
            else:
                menu_info += f"{idx+1}. {row['메뉴명']}\n"
        
        prompt = f"""다음 메모를 분석해서 업무와 유형을 판단해줘.

등록된 업무:
{menu_info}

유형 설명:
- 아이디어: 새로운 제안, 개선안, 창의적 생각
- 할일: 앞으로 해야 할 작업, 처리 필요한 업무
- 업데이트: 진행 상황, 완료 보고, 현황
- 문제점: 발생한 이슈, 해결 필요한 문제

메모 내용:
{content}

아래 형식으로 정확히 답변해줘:
업무번호: [1~{len(menu_list)} 중 하나]
유형: [아이디어/할일/업데이트/문제점 중 하나]
시간: [할일이고 시간 언급되면 YYYY-MM-DD HH:MM, 없으면 없음]"""

        response = model.generate_content(prompt)
        result = response.text.strip()
        
        menu = None
        note_type = None
        alarm_time = None
        
        lines = result.split('\n')
        for line in lines:
            line = line.strip()
            
            if '업무' in line and ':' in line:
                try:
                    num_str = line.split(':')[1].strip()
                    numbers = re.findall(r'\d+', num_str)
                    if numbers:
                        menu_idx = int(numbers[0]) - 1
                        if 0 <= menu_idx < len(menu_list):
                            menu = menu_list[menu_idx]
                except:
                    pass
            
            elif '유형' in line and ':' in line:
                type_str = line.split(':')[1].strip().lower()
                
                if '아이디어' in type_str or 'idea' in type_str:
                    note_type = '아이디어'
                elif '할' in type_str and '일' in type_str or 'todo' in type_str:
                    note_type = '할일'
                elif '업데이트' in type_str or 'update' in type_str:
                    note_type = '업데이트'
                elif '문제' in type_str or 'issue' in type_str:
                    note_type = '문제점'
            
            elif '시간' in line and ':' in line:
                time_str = line.split(':', 1)[1].strip()
                if '없음' not in time_str and len(time_str) > 5:
                    time_pattern = r'\d{4}-\d{2}-\d{2}\s+\d{2}:\d{2}'
                    matches = re.findall(time_pattern, time_str)
                    if matches:
                        alarm_time = matches[0]
        
        if not menu and menu_list:
            menu = menu_list[0]
        
        if not note_type:
            note_type = '업데이트'
        
        return menu, note_type, alarm_time
        
    except Exception as e:
        st.error(f"AI 분류 오류: {str(e)}")
        if menu_list:
            return menu_list[0], '업데이트', None
        return None, None, None

def check_pending_tasks():
    """할 일 알림 체크"""
    notes_df = load_sheet("notes")
    
    if notes_df.empty:
        return []
    
    todos = notes_df[notes_df["유형"] == "할일"].copy()
    
    if todos.empty:
        return []
    
    pending = []
    now = now_kst()
    
    for idx, row in todos.iterrows():
        if str(row.get("완료", "")).strip().lower() in ["o", "완료", "done", "x"]:
            continue
        
        alarm = str(row.get("알림시간", "")).strip()
        if not alarm or alarm == "nan":
            continue
        
        try:
            alarm_dt = datetime.strptime(alarm, "%Y-%m-%d %H:%M")
            alarm_dt = TZ_KST.localize(alarm_dt)
            
            if alarm_dt - timedelta(minutes=30) <= now <= alarm_dt + timedelta(hours=2):
                time_diff = alarm_dt - now
                minutes = int(time_diff.total_seconds() / 60)
                
                if minutes < 0:
                    status = f"⏰ {abs(minutes)}분 지남"
                elif minutes == 0:
                    status = "⏰ 지금!"
                else:
                    status = f"⏰ {minutes}분 후"
                
                pending.append({
                    "메뉴": row["메뉴"],
                    "내용": row["내용"],
                    "알림시간": alarm,
                    "상태": status,
                    "idx": idx
                })
        except:
            pass
    
    return pending

# Gemini API 설정
if "GEMINI_API_KEY" in st.secrets:
    genai.configure(api_key=st.secrets["GEMINI_API_KEY"])

# 페이지 설정
st.set_page_config(
    page_title="스마트 업무 비서", 
    page_icon="📝", 
    layout="wide",
    initial_sidebar_state="expanded"
)

# ============ 개선된 CSS 스타일 ============
st.markdown("""
<style>
    /* 전체 배경 및 기본 설정 */
    .block-container {
        padding-top: 1.5rem !important;
        padding-bottom: 1rem !important;
        max-width: 1400px !important;
    }
    
    /* 헤더 스타일 */
    h1 {
        color: #1f2937 !important;
        font-weight: 700 !important;
        margin-bottom: 0.5rem !important;
        padding-bottom: 0.5rem !important;
        border-bottom: 3px solid #3b82f6 !important;
    }
    
    h2 {
        color: #374151 !important;
        font-weight: 600 !important;
        margin-top: 1.5rem !important;
        margin-bottom: 1rem !important;
    }
    
    h3 {
        color: #4b5563 !important;
        font-weight: 500 !important;
    }
    
    /* 사이드바 스타일 */
    [data-testid="stSidebar"] {
        background: linear-gradient(180deg, #f8fafc 0%, #e2e8f0 100%) !important;
        padding: 1rem !important;
    }
    
    [data-testid="stSidebar"] .stRadio > label {
        font-weight: 600 !important;
        font-size: 1.1rem !important;
        color: #1e293b !important;
        margin-bottom: 1rem !important;
    }
    
    [data-testid="stSidebar"] [role="radiogroup"] label {
        padding: 0.75rem 1rem !important;
        border-radius: 0.5rem !important;
        margin-bottom: 0.5rem !important;
        transition: all 0.2s !important;
        background: white !important;
        border: 1px solid #e2e8f0 !important;
    }
    
    [data-testid="stSidebar"] [role="radiogroup"] label:hover {
        background: #eff6ff !important;
        border-color: #3b82f6 !important;
        transform: translateX(4px) !important;
    }
    
    /* 입력 폼 스타일 */
    .stTextInput > div > div > input,
    .stTextArea > div > div > textarea,
    .stSelectbox > div > div > select {
        border: 2px solid #e5e7eb !important;
        border-radius: 0.5rem !important;
        padding: 0.75rem !important;
        font-size: 1rem !important;
        transition: border-color 0.2s !important;
    }
    
    .stTextInput > div > div > input:focus,
    .stTextArea > div > div > textarea:focus,
    .stSelectbox > div > div > select:focus {
        border-color: #3b82f6 !important;
        box-shadow: 0 0 0 3px rgba(59, 130, 246, 0.1) !important;
    }
    
    /* 버튼 스타일 */
    .stButton > button {
        border-radius: 0.5rem !important;
        padding: 0.6rem 1.5rem !important;
        font-weight: 600 !important;
        transition: all 0.2s !important;
        border: none !important;
    }
    
    .stButton > button[kind="primary"] {
        background: linear-gradient(135deg, #3b82f6 0%, #2563eb 100%) !important;
        color: white !important;
        box-shadow: 0 4px 6px rgba(59, 130, 246, 0.3) !important;
    }
    
    .stButton > button[kind="primary"]:hover {
        transform: translateY(-2px) !important;
        box-shadow: 0 6px 12px rgba(59, 130, 246, 0.4) !important;
    }
    
    .stButton > button[kind="secondary"] {
        background: white !important;
        border: 2px solid #e5e7eb !important;
        color: #374151 !important;
    }
    
    .stButton > button[kind="secondary"]:hover {
        border-color: #3b82f6 !important;
        color: #3b82f6 !important;
    }
    
    /* 배지 스타일 */
    .badge {
        display: inline-block;
        padding: 0.3rem 0.8rem;
        border-radius: 1rem;
        font-size: 0.85rem;
        font-weight: 600;
        margin-right: 0.5rem;
    }
    
    .badge-idea {
        background: #fef3c7;
        color: #92400e;
    }
    
    .badge-todo {
        background: #dbeafe;
        color: #1e40af;
    }
    
    .badge-update {
        background: #d1fae5;
        color: #065f46;
    }
    
    .badge-issue {
        background: #fee2e2;
        color: #991b1b;
    }
    
    /* Expander 스타일 */
    .streamlit-expanderHeader {
        background: white !important;
        border: 1px solid #e5e7eb !important;
        border-radius: 0.5rem !important;
        padding: 1rem !important;
        font-weight: 500 !important;
        transition: all 0.2s !important;
    }
    
    .streamlit-expanderHeader:hover {
        border-color: #3b82f6 !important;
        background: #f9fafb !important;
    }
    
    /* 알림 박스 */
    .stAlert {
        border-radius: 0.75rem !important;
        border-left: 4px solid !important;
        padding: 1rem 1.5rem !important;
        margin: 1rem 0 !important;
    }
    
    /* 구분선 */
    hr {
        margin: 2rem 0 !important;
        border: none !important;
        height: 2px !important;
        background: linear-gradient(90deg, transparent, #e5e7eb, transparent) !important;
    }
    
    /* 라디오 버튼 */
    .stRadio [role="radiogroup"] {
        gap: 1rem !important;
    }
    
    .stRadio [role="radiogroup"] label {
        background: white !important;
        padding: 0.75rem 1.5rem !important;
        border: 2px solid #e5e7eb !important;
        border-radius: 0.5rem !important;
        transition: all 0.2s !important;
    }
    
    .stRadio [role="radiogroup"] label:hover {
        border-color: #3b82f6 !important;
        background: #eff6ff !important;
    }
    
    /* 파일 업로더 */
    [data-testid="stFileUploader"] {
        background: #f9fafb !important;
        border: 2px dashed #d1d5db !important;
        border-radius: 0.75rem !important;
        padding: 2rem !important;
    }
    
    [data-testid="stFileUploader"]:hover {
        border-color: #3b82f6 !important;
        background: #eff6ff !important;
    }
    
    /* 이미지 */
    img {
        border-radius: 0.5rem !important;
        box-shadow: 0 4px 6px rgba(0,0,0,0.1) !important;
    }
    
    /* 탭 스타일 */
    .stTabs [data-baseweb="tab-list"] {
        gap: 0.5rem;
    }
    
    .stTabs [data-baseweb="tab"] {
        background: white;
        border: 2px solid #e5e7eb;
        border-radius: 0.5rem 0.5rem 0 0;
        padding: 0.75rem 1.5rem;
        font-weight: 500;
    }
    
    .stTabs [aria-selected="true"] {
        background: #3b82f6;
        color: white;
        border-color: #3b82f6;
    }
</style>
""", unsafe_allow_html=True)

# ============ 헤더 ============
st.markdown("# 스마트 업무 비서")
st.caption("🤖 AI 기반 업무 기록 및 관리 시스템")
st.divider()

# ============ 할 일 알림 ============
pending_tasks = check_pending_tasks()
if pending_tasks:
    st.warning(f"⏰ **{len(pending_tasks)}개의 할 일 알림**")
    for task in pending_tasks:
        with st.expander(f"{task['상태']} - [{task['메뉴']}] {task['내용'][:30]}..."):
            st.markdown(f"**예정 시간:** {task['알림시간']}")
            st.markdown(f"**내용:** {task['내용']}")
            
            if st.button("✅ 완료 처리", key=f"done_{task['idx']}"):
                notes_df = load_sheet("notes")
                notes_df.loc[task['idx'], '완료'] = 'O'
                if save_sheet(notes_df, "notes"):
                    st.success("완료!")
                    st.rerun()

# ============ 사이드바: 모드 선택 ============
with st.sidebar:
    st.markdown("## 메뉴")
    
    mode = st.radio(
        "선택",
        ["업무 기록하기", "전체 히스토리", "대화 이력", "일일 리포트", "메뉴/설정 관리"],
        label_visibility="collapsed"
    )

# ================== 모드 1: 업무 기록하기 ==================
if mode == "업무 기록하기":
    
    config_df = load_sheet("config")
    
    if config_df.empty or len(config_df) == 0:
        st.error("⚠️ config 시트를 불러올 수 없습니다!")
        st.info("💡 '메뉴/설정 관리'에서 업무를 먼저 등록하세요")
        st.stop()
    
    if "메뉴명" not in config_df.columns:
        st.error("❌ config 시트에 '메뉴명' 컬럼이 없습니다!")
        st.stop()
    
    menu_list = config_df["메뉴명"].tolist()
    
    if len(menu_list) == 0:
        st.warning("⚠️ 등록된 업무가 없습니다. 설정 메뉴에서 업무를 먼저 등록하세요.")
        st.stop()
    
    # ============ 2단 레이아웃 ============
    col_left, col_right = st.columns([1.2, 1], gap="large")
    
    with col_left:
        st.markdown("## 📝 업무 기록하기")
        
        ai_mode = st.radio(
            "입력 모드",
            ["🤖 AI 자동 분류", "✋ 수동 선택"],
            horizontal=True,
            help="AI 모드: 내용만 입력하면 AI가 업무, 유형을 자동 판단"
        )
        
        if "uploaded_images" not in st.session_state:
            st.session_state.uploaded_images = []
        
        with st.form(key="note_form", clear_on_submit=True):
            
            if ai_mode == "✋ 수동 선택":
                selected_menu = st.selectbox("📁 업무 선택", menu_list)
                note_type = st.radio(
                    "🏷️ 유형", 
                    ["아이디어", "할일", "업데이트", "문제점"], 
                    horizontal=True
                )
                content = st.text_area(
                    "📝 내용 입력", 
                    height=200,
                    placeholder="여기에 내용을 입력하세요..."
                )
                
                alarm_time = None
                if note_type == "할일":
                    st.markdown("**⏰ 알림 설정 (선택사항)**")
                    col1, col2 = st.columns(2)
                    with col1:
                        alarm_date = st.date_input("날짜", value=None)
                    with col2:
                        alarm_time_input = st.time_input("시간", value=None)
                    
                    if alarm_date and alarm_time_input:
                        alarm_time = f"{alarm_date.strftime('%Y-%m-%d')} {alarm_time_input.strftime('%H:%M')}"
            else:
                content = st.text_area(
                    "📝 내용만 입력하세요", 
                    height=250,
                    placeholder="AI가 자동으로 업무와 유형을 판단합니다..."
                )
                selected_menu = None
                note_type = None
                alarm_time = None
            
            st.markdown("---")
            
            st.markdown("**🖼️ 이미지 첨부 (선택)**")
            
            uploaded_files = st.file_uploader(
                "이미지 선택",
                type=['png', 'jpg', 'jpeg'],
                accept_multiple_files=True,
                key="image_uploader",
                label_visibility="collapsed"
            )
            
            if uploaded_files:
                for f in uploaded_files:
                    if f.name not in [img["name"] for img in st.session_state.uploaded_images]:
                        st.session_state.uploaded_images.append({
                            "name": f.name,
                            "data": f
                        })
            
            if st.session_state.uploaded_images:
                st.info(f"📸 {len(st.session_state.uploaded_images)}개 이미지 준비됨")
                cols = st.columns(min(len(st.session_state.uploaded_images), 3))
                for idx, img in enumerate(st.session_state.uploaded_images):
                    with cols[idx % 3]:
                        st.image(img["data"], caption=img["name"], use_container_width=True)
            
            submit = st.form_submit_button("💾 저장하기", type="primary", use_container_width=True)
            
            if submit:
                if content.strip():
                    
                    if ai_mode == "🤖 AI 자동 분류":
                        if "GEMINI_API_KEY" not in st.secrets:
                            st.error("❌ AI 모드는 API 키가 필요합니다")
                            st.stop()
                        
                        with st.spinner("🤖 AI 분석 중..."):
                            selected_menu, note_type, alarm_time = ai_classify_note(content, menu_list, config_df)
                        
                        if selected_menu and note_type:
                            st.success(f"✅ AI 분류: **{selected_menu}** / **{note_type}**")
                        else:
                            st.error("❌ AI 분류 실패")
                            st.stop()
                    
                    image_url = None
                    if st.session_state.uploaded_images:
                        with st.spinner("📤 이미지 업로드 중..."):
                            first_img = st.session_state.uploaded_images[0]
                            timestamp = now_kst().strftime("%Y%m%d_%H%M%S")
                            filename = f"{timestamp}_{first_img['name']}"
                            image_url = upload_to_drive(first_img["data"], filename)
                    
                    notes_df = load_sheet("notes")
                    new_row = pd.DataFrame([{
                        "날짜": today_kst_str(),
                        "시간": now_kst().strftime("%H:%M:%S"),
                        "메뉴": selected_menu,
                        "유형": note_type,
                        "내용": content,
                        "이미지": image_url if image_url else "",
                        "알림시간": alarm_time if alarm_time else "",
                        "완료": ""
                    }])
                    
                    updated_df = pd.concat([notes_df, new_row], ignore_index=True)
                    
                    if save_sheet(updated_df, "notes"):
                        st.success("✅ 저장 완료!")
                        st.session_state.uploaded_images = []
                        st.rerun()
                    else:
                        st.error("❌ 저장 실패")
                else:
                    st.warning("⚠️ 내용을 입력하세요")
        
        if st.session_state.uploaded_images:
            st.markdown("**업로드된 이미지 관리**")
            for idx, img in enumerate(st.session_state.uploaded_images):
                col1, col2 = st.columns([5, 1])
                with col1:
                    st.text(f"📷 {img['name']}")
                with col2:
                    if st.button("🗑️", key=f"del_img_{idx}"):
                        st.session_state.uploaded_images.pop(idx)
                        st.rerun()
    
    with col_right:
        st.markdown("## 📚 최근 기록")
        
        notes_df = load_sheet("notes")
        if not notes_df.empty:
            recent_notes = notes_df.iloc[::-1].head(5)
            
            for idx, row in recent_notes.iterrows():
                badge_class = {
                    "아이디어": "badge-idea",
                    "할일": "badge-todo",
                    "업데이트": "badge-update",
                    "문제점": "badge-issue"
                }.get(row['유형'], "badge-update")
                
                alarm_info = ""
                if str(row.get("알림시간", "")).strip() and str(row.get("알림시간", "")) != "nan":
                    alarm_info = f"⏰ {row['알림시간']}"
                
                done_mark = ""
                if str(row.get("완료", "")).strip().lower() in ["o", "완료", "done", "x"]:
                    done_mark = "✅"
                
                with st.expander(f"**{row['메뉴']}** - {row['날짜']} {row['시간']} {done_mark}"):
                    st.markdown(f"<span class='badge {badge_class}'>{row['유형']}</span>", unsafe_allow_html=True)
                    if alarm_info:
                        st.caption(alarm_info)
                    st.markdown(row['내용'])
                    if row['이미지'] and str(row['이미지']) != 'nan' and str(row['이미지']).strip():
                        st.image(row['이미지'], use_container_width=True)
        else:
            st.info("📭 아직 기록이 없습니다")

# ================== 모드 2: 전체 히스토리 ==================
elif mode == "전체 히스토리":
    st.markdown("## 📋 전체 업무 히스토리")
    
    notes_df = load_sheet("notes")
    config_df = load_sheet("config")
    
    if not notes_df.empty and not config_df.empty:
        menu_list = config_df["메뉴명"].tolist()
        
        col1, col2, col3 = st.columns(3)
        with col1:
            filter_menu = st.selectbox("📁 업무", ["전체"] + menu_list)
        with col2:
            filter_type = st.selectbox("🏷️ 유형", ["전체", "아이디어", "할일", "업데이트", "문제점"])
        with col3:
            filter_date = st.selectbox("📅 기간", ["전체", "오늘", "이번 주", "이번 달"])
        
        filtered_df = notes_df.copy()
        
        if filter_menu != "전체":
            filtered_df = filtered_df[filtered_df["메뉴"] == filter_menu]
        
        if filter_type != "전체":
            filtered_df = filtered_df[filtered_df["유형"] == filter_type]
        
        if filter_date == "오늘":
            filtered_df = filtered_df[filtered_df["날짜"] == today_kst_str()]
        elif filter_date == "이번 주":
            week_ago = (now_kst() - timedelta(days=7)).strftime("%Y-%m-%d")
            filtered_df = filtered_df[filtered_df["날짜"] >= week_ago]
        elif filter_date == "이번 달":
            this_month = now_kst().strftime("%Y-%m")
            filtered_df = filtered_df[filtered_df["날짜"].astype(str).str.startswith(this_month)]
        
        st.info(f"📊 총 **{len(filtered_df)}건**의 기록")
        
        if not filtered_df.empty:
            for idx, row in filtered_df.iloc[::-1].iterrows():
                col1, col2 = st.columns([6, 1])
                
                with col1:
                    badge_class = {
                        "아이디어": "badge-idea",
                        "할일": "badge-todo",
                        "업데이트": "badge-update",
                        "문제점": "badge-issue"
                    }.get(row['유형'], "badge-update")
                    
                    alarm_info = ""
                    if str(row.get("알림시간", "")).strip() and str(row.get("알림시간", "")) != "nan":
                        alarm_info = f"⏰ {row['알림시간']}"
                    
                    done_mark = ""
                    is_done = str(row.get("완료", "")).strip().lower() in ["o", "완료", "done", "x"]
                    if is_done:
                        done_mark = "✅"
                    
                    with st.expander(f"**{row['메뉴']}** - {row['날짜']} {row['시간']} {done_mark}"):
                        st.markdown(f"<span class='badge {badge_class}'>{row['유형']}</span>", unsafe_allow_html=True)
                        if alarm_info:
                            st.caption(alarm_info)
                        st.markdown(row['내용'])
                        if row['이미지'] and str(row['이미지']) != 'nan' and str(row['이미지']).strip():
                            st.image(row['이미지'], use_container_width=True)
                        
                        if row['유형'] == "할일" and not is_done:
                            if st.button("✅ 완료", key=f"complete_{idx}"):
                                notes_df.loc[idx, '완료'] = 'O'
                                if save_sheet(notes_df, "notes"):
                                    st.success("완료!")
                                    st.rerun()
                
                with col2:
                    if st.button("🗑️", key=f"del_{idx}", help="삭제"):
                        notes_df = notes_df.drop(idx)
                        if save_sheet(notes_df, "notes"):
                            st.success("삭제 완료!")
                            st.rerun()
        else:
            st.info("📭 조건에 맞는 기록이 없습니다")
    elif notes_df.empty:
        st.info("📭 아직 기록이 없습니다")
    else:
        st.error("⚠️ config 설정을 먼저 확인하세요")

# ================== 모드 3: 대화 이력 ==================
elif mode == "대화 이력":
    st.markdown("## 💬 대화 이력")
    
    with st.expander("📥 대화 내용 가져오기", expanded=True):
        tab1, tab2 = st.tabs(["📝 직접 입력", "📂 파일 업로드"])
        
        with tab1:
            with st.form(key="chat_form_manual", clear_on_submit=True):
                chat_topic = st.text_input("📌 주제/제목")
                chat_content = st.text_area("📝 대화 내용", height=300, placeholder="대화 내용을 붙여넣으세요...")
                
                submit_manual = st.form_submit_button("💾 저장", type="primary", use_container_width=True)
                
                if submit_manual:
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
                            st.rerun()
                        else:
                            st.error("❌ 저장 실패")
                    else:
                        st.warning("⚠️ 주제와 내용을 모두 입력하세요")
        
        with tab2:
            uploaded_file = st.file_uploader(
                "📂 파일 업로드 (.txt, .md)", 
                type=["txt", "md"],
                help="대화 내용이 저장된 텍스트 파일"
            )
            
            if uploaded_file is not None:
                try:
                    file_content = uploaded_file.getvalue().decode("utf-8")
                    
                    st.success(f"✅ 파일 로드: {uploaded_file.name}")
                    st.info(f"📊 전체 길이: {len(file_content):,} 자")
                    
                    with st.form(key="chat_form_file", clear_on_submit=False):
                        default_topic = uploaded_file.name.replace('.txt', '').replace('.md', '')
                        
                        file_topic = st.text_input("📌 주제/제목", value=default_topic)
                        
                        preview_length = min(2000, len(file_content))
                        st.text_area(
                            "📝 파일 내용 미리보기", 
                            value=file_content[:preview_length] + ("..." if len(file_content) > preview_length else ""),
                            height=200,
                            disabled=True
                        )
                        
                        col1, col2 = st.columns(2)
                        
                        with col1:
                            submit_file = st.form_submit_button("💾 전체 저장", type="primary")
                        
                        with col2:
                            submit_ai = st.form_submit_button("🤖 AI 요약 후 저장")
                        
                        if submit_file:
                            if file_topic.strip():
                                chats_df = load_sheet("chats")
                                new_row = pd.DataFrame([{
                                    "날짜": today_kst_str(),
                                    "시간": now_kst().strftime("%H:%M:%S"),
                                    "주제": file_topic,
                                    "전체내용": file_content
                                }])
                                
                                updated_df = pd.concat([chats_df, new_row], ignore_index=True)
                                
                                if save_sheet(updated_df, "chats"):
                                    st.success("✅ 전체 내용 저장 완료!")
                                    st.rerun()
                            else:
                                st.warning("⚠️ 주제를 입력하세요")
                        
                        if submit_ai:
                            if "GEMINI_API_KEY" not in st.secrets:
                                st.error("❌ AI 기능은 API 키가 필요합니다")
                            elif file_topic.strip():
                                with st.spinner("🤖 AI 요약 중..."):
                                    try:
                                        model = genai.GenerativeModel('gemini-2.5-flash')
                                        
                                        content_to_analyze = file_content[:50000]
                                        
                                        prompt = f"""다음 대화를 분석해서 정리해줘:

## 📌 주요 주제
(핵심 주제 3줄 요약)

## 💬 주요 대화 내용
- 주요 질문과 답변 요약
- 핵심 포인트만 정리

## 📝 코드/파일 변경사항
(변경된 파일과 주요 수정 내용)

## 🎯 결론 및 다음 단계
(최종 결과와 남은 작업)

[대화 내용]
{content_to_analyze}
"""
                                        
                                        response = model.generate_content(prompt)
                                        summary = response.text
                                        
                                        chats_df = load_sheet("chats")
                                        new_row = pd.DataFrame([{
                                            "날짜": today_kst_str(),
                                            "시간": now_kst().strftime("%H:%M:%S"),
                                            "주제": f"[AI 요약] {file_topic}",
                                            "전체내용": summary
                                        }])
                                        
                                        updated_df = pd.concat([chats_df, new_row], ignore_index=True)
                                        
                                        if save_sheet(updated_df, "chats"):
                                            st.success("✅ AI 요약 저장 완료!")
                                            st.markdown("### 📄 요약 결과")
                                            st.markdown(summary)
                                            st.rerun()
                                    
                                    except Exception as e:
                                        st.error(f"❌ AI 요약 실패: {e}")
                            else:
                                st.warning("⚠️ 주제를 입력하세요")
                
                except Exception as e:
                    st.error(f"❌ 파일 읽기 실패: {e}")
    
    st.divider()
    st.markdown("## 📚 저장된 대화 이력")
    
    chats_df = load_sheet("chats")
    
    if not chats_df.empty:
        for idx, row in chats_df.iloc[::-1].iterrows():
            with st.expander(f"**{row['주제']}** - {row['날짜']} {row['시간']}"):
                st.markdown(row['전체내용'])
                
                col1, col2 = st.columns([5, 1])
                with col2:
                    if st.button("🗑️", key=f"del_chat_{idx}", help="삭제"):
                        chats_df = chats_df.drop(idx)
                        if save_sheet(chats_df, "chats"):
                            st.success("삭제 완료!")
                            st.rerun()
    else:
        st.info("📭 아직 대화 기록이 없습니다")

# ================== 모드 4: 일일 리포트 ==================
elif mode == "일일 리포트":
    st.markdown("## 📊 일일 리포트")
    
    notes_df = load_sheet("notes")
    
    if not notes_df.empty:
        today_str = today_kst_str()
        today_notes = notes_df[notes_df["날짜"] == today_str]
        
        if not today_notes.empty:
            st.success(f"📅 **{today_str}** 오늘의 기록: **{len(today_notes)}건**")
            
            # 통계
            col1, col2, col3, col4 = st.columns(4)
            
            with col1:
                idea_count = len(today_notes[today_notes["유형"] == "아이디어"])
                st.metric("💡 아이디어", idea_count)
            
            with col2:
                todo_count = len(today_notes[today_notes["유형"] == "할일"])
                st.metric("✅ 할일", todo_count)
            
            with col3:
                update_count = len(today_notes[today_notes["유형"] == "업데이트"])
                st.metric("📝 업데이트", update_count)
            
            with col4:
                issue_count = len(today_notes[today_notes["유형"] == "문제점"])
                st.metric("🔥 문제점", issue_count)
            
            st.divider()
            
            # 업무별 분류
            if "메뉴" in today_notes.columns:
                st.markdown("### 📁 업무별 요약")
                
                for menu in today_notes["메뉴"].unique():
                    menu_notes = today_notes[today_notes["메뉴"] == menu]
                    
                    with st.expander(f"**{menu}** ({len(menu_notes)}건)"):
                        for idx, row in menu_notes.iterrows():
                            badge_class = {
                                "아이디어": "badge-idea",
                                "할일": "badge-todo",
                                "업데이트": "badge-update",
                                "문제점": "badge-issue"
                            }.get(row['유형'], "badge-update")
                            
                            st.markdown(f"<span class='badge {badge_class}'>{row['유형']}</span> {row['시간']} - {row['내용'][:100]}...", unsafe_allow_html=True)
            
            st.divider()
            
            # AI 요약
            if "GEMINI_API_KEY" in st.secrets:
                st.markdown("### 🤖 AI 일일 요약")
                
                if st.button("📋 오늘 업무 AI 요약하기", type="primary"):
                    all_content = "\n\n".join([f"[{row['메뉴']} - {row['유형']}] {row['내용']}" for _, row in today_notes.iterrows()])
                    
                    with st.spinner("🤖 AI 요약 중..."):
                        try:
                            model = genai.GenerativeModel('gemini-2.5-flash')
                            
                            prompt = f"""오늘({today_str}) 업무 기록을 요약해줘:

## 📌 주요 업무
(오늘 한 주요 업무 정리)

## ✅ 완료한 일
(완료된 작업들)

## 🎯 진행 중
(진행 중인 작업들)

## 💡 아이디어 및 개선사항
(새로운 아이디어나 개선안)

## 🔥 해결 필요
(문제점이나 이슈)

오늘의 기록:
{all_content[:10000]}
"""
                            
                            response = model.generate_content(prompt)
                            summary = response.text
                            
                            st.markdown("---")
                            st.markdown(summary)
                            
                            if st.button("💾 이 요약을 대화 이력에 저장"):
                                chats_df = load_sheet("chats")
                                new_row = pd.DataFrame([{
                                    "날짜": today_kst_str(),
                                    "시간": now_kst().strftime("%H:%M:%S"),
                                    "주제": f"{today_str} 일일 업무 요약",
                                    "전체내용": summary
                                }])
                                
                                updated_df = pd.concat([chats_df, new_row], ignore_index=True)
                                
                                if save_sheet(updated_df, "chats"):
                                    st.success("✅ 대화 이력에 저장 완료!")
                        
                        except Exception as e:
                            st.error(f"❌ AI 요약 실패: {e}")
            else:
                st.warning("🔴 AI 요약 기능을 사용하려면 API 키가 필요합니다")
        
        else:
            st.info("📭 오늘 아직 기록이 없습니다")
    else:
        st.info("📭 전체 기록이 없습니다")

# ================== 모드 5: 메뉴/설정 관리 ==================
elif mode == "메뉴/설정 관리":
    st.markdown("## ⚙️ 메뉴/설정 관리")
    
    config_df = load_sheet("config")
    
    st.markdown("### 📁 등록된 업무 목록")
    
    if not config_df.empty:
        st.dataframe(config_df, use_container_width=True)
        
        st.divider()
        st.markdown("### ➕ 새 업무 추가")
        
        with st.form(key="add_menu_form", clear_on_submit=True):
            new_menu = st.text_input("업무명")
            new_desc = st.text_area("업무 설명 (선택)", height=100, placeholder="AI가 자동 분류할 때 참고합니다")
            
            submit_new = st.form_submit_button("➕ 추가", type="primary")
            
            if submit_new:
                if new_menu.strip():
                    new_row = pd.DataFrame([{
                        "메뉴명": new_menu,
                        "시트정보": "",
                        "트리거정보": "",
                        "업무설명": new_desc if new_desc.strip() else "",
                        "메일발송설정": ""
                    }])
                    
                    updated_df = pd.concat([config_df, new_row], ignore_index=True)
                    
                    if save_sheet(updated_df, "config"):
                        st.success(f"✅ '{new_menu}' 추가 완료!")
                        st.rerun()
                    else:
                        st.error("❌ 추가 실패")
                else:
                    st.warning("⚠️ 업무명을 입력하세요")
        
        st.divider()
        st.markdown("### 🗑️ 업무 삭제")
        
        menu_to_delete = st.selectbox("삭제할 업무 선택", config_df["메뉴명"].tolist())
        
        if st.button("🗑️ 삭제", type="secondary"):
            config_df = config_df[config_df["메뉴명"] != menu_to_delete]
            if save_sheet(config_df, "config"):
                st.success(f"✅ '{menu_to_delete}' 삭제 완료!")
                st.rerun()
            else:
                st.error("❌ 삭제 실패")
    
    else:
        st.warning("⚠️ 등록된 업무가 없습니다")
        
        st.markdown("### ➕ 첫 업무 추가")
        
        with st.form(key="first_menu_form", clear_on_submit=True):
            first_menu = st.text_input("업무명")
            first_desc = st.text_area("업무 설명", height=100)
            
            submit_first = st.form_submit_button("➕ 추가", type="primary")
            
            if submit_first:
                if first_menu.strip():
                    new_df = pd.DataFrame([{
                        "메뉴명": first_menu,
                        "시트정보": "",
                        "트리거정보": "",
                        "업무설명": first_desc,
                        "메일발송설정": ""
                    }])
                    
                    if save_sheet(new_df, "config"):
                        st.success(f"✅ '{first_menu}' 추가 완료!")
                        st.rerun()
                    else:
                        st.error("❌ 추가 실패")
                else:
                    st.warning("⚠️ 업무명을 입력하세요")
