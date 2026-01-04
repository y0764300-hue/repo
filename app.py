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

@st.cache_data(ttl=60)  # 🆕 추가!
def load_sheet(worksheet):
    """시트 로드 - 캐시 적용"""
    try:
        df = conn.read(worksheet=worksheet, ttl=300)  # 🆕 ttl=300으로 변경
        
        if df is None or len(df) == 0:
            if worksheet == "notes":
                return pd.DataFrame(columns=['날짜', '시간', '메뉴', '유형', '내용', '이미지', '알림시간', '완료'])
            elif worksheet == "chats":
                return pd.DataFrame(columns=['날짜', '시간', '주제', '전체내용'])
            elif worksheet == "config":
                return pd.DataFrame(columns=["메뉴명", "시트정보", "트리거정보", "업무설명", "메일발송설정"])
        
        df = df.fillna("")
        
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
        load_sheet.clear()  # 🆕 캐시 초기화
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
    
def create_calendar_event(title, description, start_datetime_str, menu=""):
    """구글 캘린더에 일정 등록"""
    try:
        credentials = service_account.Credentials.from_service_account_info(
            st.secrets["gcp_service_account"],
            scopes=['https://www.googleapis.com/auth/calendar']
        )
        service = build('calendar', 'v3', credentials=credentials)
        
        # 시작 시간 파싱
        start_dt = datetime.strptime(start_datetime_str, "%Y-%m-%d %H:%M")
        start_dt = TZ_KST.localize(start_dt)
        
        # 종료 시간 (1시간 후)
        end_dt = start_dt + timedelta(hours=1)
        
        # 이벤트 생성
        event = {
            'summary': f"[{menu}] {title[:50]}...",
            'description': description,
            'start': {
                'dateTime': start_dt.isoformat(),
                'timeZone': 'Asia/Seoul',
            },
            'end': {
                'dateTime': end_dt.isoformat(),
                'timeZone': 'Asia/Seoul',
            },
            'reminders': {
                'useDefault': False,
                'overrides': [
                    {'method': 'popup', 'minutes': 30},
                    {'method': 'popup', 'minutes': 10},
                ],
            },
        }
        
        # 캘린더 ID (서비스 계정에 공유한 캘린더)
        calendar_id = 'wldydxo09@gmail.com'  # 또는 본인 Gmail 주소
        
        event_result = service.events().insert(
            calendarId=calendar_id,
            body=event
        ).execute()
        
        return event_result.get('htmlLink')
        
    except Exception as e:
        st.error(f"캘린더 등록 실패: {e}")
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
        
        # 현재 시간 정보 추가
        now = now_kst()
        today = now.strftime("%Y-%m-%d")
        current_time = now.strftime("%H:%M")
        
        prompt = f"""다음 메모를 분석해서 업무와 유형을 판단해줘.

**현재 시각: {today} {current_time}**

등록된 업무:
{menu_info}

유형 설명:
- 아이디어: 새로운 제안, 개선안, 창의적 생각
- 할일: 앞으로 해야 할 작업, 처리 필요한 업무
- 업데이트: 진행 상황, 완료 보고, 현황
- 문제점: 발생한 이슈, 해결 필요한 문제

메모 내용:
{content}

시간 추출 규칙:
- "오늘 9시" → {today} 09:00
- "오늘 오후 9시" → {today} 21:00
- "내일 3시" → {(now + timedelta(days=1)).strftime("%Y-%m-%d")} 15:00
- "1월 5일 오후 2시" → 2026-01-05 14:00
- 시간 언급 없으면 → 없음

아래 형식으로 **정확히** 답변해줘:
업무번호: [1~{len(menu_list)} 중 하나]
유형: [아이디어/할일/업데이트/문제점 중 하나]
시간: [YYYY-MM-DD HH:MM 형식 또는 없음]

예시:
업무번호: 1
유형: 할일
시간: {today} 21:00"""

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
                    # 시간 패턴 찾기
                    time_pattern = r'\d{4}-\d{2}-\d{2}\s+\d{2}:\d{2}'
                    matches = re.findall(time_pattern, time_str)
                    if matches:
                        alarm_time = matches[0]
        
        if not menu and menu_list:
            menu = menu_list[0]
        
        if not note_type:
            note_type = '업데이트'
        
        # 디버깅용 출력
        if note_type == "할일":
            st.info(f"🤖 AI 분석 결과: {menu} / {note_type} / 시간: {alarm_time if alarm_time else '없음'}")
        
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
    initial_sidebar_state="collapsed"
)

# ============ 모바일 최적화 CSS ============
st.markdown("""
<style>
    /* 전체 배경 및 기본 설정 */
    .block-container {
        padding-top: 1rem !important;
        padding-bottom: 1rem !important;
        padding-left: 1rem !important;
        padding-right: 1rem !important;
        max-width: 1400px !important;
    }
    
    /* 모바일 최적화 */
    @media (max-width: 768px) {
        .block-container {
            padding-left: 0.5rem !important;
            padding-right: 0.5rem !important;
        }
        
        h1 {
            font-size: 1.5rem !important;
        }
        
        h2 {
            font-size: 1.2rem !important;
            white-space: nowrap !important;
            overflow: visible !important;
        }
        
        h3 {
            font-size: 1.1rem !important;
        }
        
        .stButton > button {
            min-height: 44px !important;
            font-size: 1rem !important;
            padding: 0.75rem 1rem !important;
        }
        
        .stTextInput > div > div > input,
        .stSelectbox > div > div > select {
            min-height: 44px !important;
            font-size: 16px !important;
        }
        
        .stTextArea > div > div > textarea {
            font-size: 16px !important;
        }
        
        .stRadio [role="radiogroup"] {
            flex-direction: row !important;
        }
        
        .stRadio [role="radiogroup"] label {
            width: auto !important;
            flex: 1 !important;
            margin-bottom: 0.5rem !important;
        }
        
        .streamlit-expanderHeader {
            padding: 0.75rem !important;
            font-size: 0.9rem !important;
        }
        
        .badge {
            font-size: 0.75rem !important;
            padding: 0.25rem 0.6rem !important;
        }
        
        [data-testid="column"] {
            padding: 0.25rem !important;
        }
        
        [data-testid="stSidebar"] [role="radiogroup"] label {
            font-size: 0.95rem !important;
            padding: 0.6rem 0.8rem !important;
        }
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
        white-space: nowrap !important;
        overflow: visible !important;
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
        margin: 1.5rem 0 !important;
        border: none !important;
        height: 2px !important;
        background: linear-gradient(90deg, transparent, #e5e7eb, transparent) !important;
    }
    
    /* 라디오 버튼 */
    .stRadio [role="radiogroup"] {
        gap: 0.5rem !important;
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
        padding: 1.5rem !important;
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
    
    @media (max-width: 768px) {
        [data-testid="stDataFrame"] {
            font-size: 0.85rem !important;
        }
    }
</style>
""", unsafe_allow_html=True)


# ============ 헤더 ============
st.markdown("# 스마트 업무 비서")
st.caption("🤖 AI 기반 업무 기록 및 관리")
st.divider()

# ============ 할 일 알림 ============
pending_tasks = check_pending_tasks()
if pending_tasks:
    st.warning(f"⏰ **{len(pending_tasks)}개의 할 일 알림**")
    for task in pending_tasks:
        with st.expander(f"{task['상태']} - {task['내용'][:20]}..."):
            st.markdown(f"**메뉴:** {task['메뉴']}")
            st.markdown(f"**시간:** {task['알림시간']}")
            st.markdown(f"**내용:** {task['내용']}")
            
            if st.button("✅ 완료", key=f"done_{task['idx']}", use_container_width=True):
                notes_df = load_sheet("notes")
                notes_df.loc[task['idx'], '완료'] = 'O'
                if save_sheet(notes_df, "notes"):
                    st.success("완료!")
                    st.rerun()

# ============ 사이드바: 모드 선택 ============
with st.sidebar:
    st.markdown("## 📱 메뉴")
    
    mode = st.radio(
        "선택",
        ["업무 기록하기", "전체 히스토리", "대화 이력", "일일 리포트", "업무 포트폴리오", "메뉴/설정 관리"],
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
        st.warning("⚠️ 등록된 업무가 없습니다.")
        st.stop()
    
    st.markdown("## 📝 업무 기록")  # 제목 짧게 수정
    
    # AI 자동/수동 선택 - 가로 배치 강제
    col_mode1, col_mode2 = st.columns(2)
    with col_mode1:
        ai_auto = st.button("🤖 AI 자동", key="btn_ai", use_container_width=True, 
                           type="primary" if st.session_state.get("input_mode", "ai") == "ai" else "secondary")
    with col_mode2:
        manual = st.button("✋ 수동", key="btn_manual", use_container_width=True,
                          type="primary" if st.session_state.get("input_mode", "ai") == "manual" else "secondary")
    
    # 모드 상태 저장
    if ai_auto:
        st.session_state.input_mode = "ai"
    if manual:
        st.session_state.input_mode = "manual"
    
    if "input_mode" not in st.session_state:
        st.session_state.input_mode = "ai"
    
    ai_mode = "🤖 AI 자동" if st.session_state.input_mode == "ai" else "✋ 수동"
    
    if "uploaded_images" not in st.session_state:
        st.session_state.uploaded_images = []
    
    with st.form(key="note_form", clear_on_submit=True):
        
        if ai_mode == "✋ 수동":
            selected_menu = st.selectbox("📁 업무", menu_list)
            
            # 유형 선택 - 가로 배치
            col1, col2, col3, col4 = st.columns(4)
            with col1:
                type_idea = st.checkbox("💡 아이디어", key="type_idea")
            with col2:
                type_todo = st.checkbox("✅ 할일", key="type_todo")
            with col3:
                type_update = st.checkbox("📝 업데이트", key="type_update", value=True)
            with col4:
                type_issue = st.checkbox("🔥 문제점", key="type_issue")
            
            # 선택된 유형 결정
            if type_idea:
                note_type = "아이디어"
            elif type_todo:
                note_type = "할일"
            elif type_issue:
                note_type = "문제점"
            else:
                note_type = "업데이트"
            
            # 내용 입력란과 저장 버튼을 나란히 배치
            col_content, col_save = st.columns([5, 1])
            
            with col_content:
                content = st.text_area(
                    "📝 내용", 
                    height=150,
                    placeholder="내용을 입력하세요...",
                    label_visibility="collapsed"
                )
            
            with col_save:
                # 저장 버튼을 세로로 중앙 배치
                st.write("")  # 여백
                st.write("")
                submit = st.form_submit_button("💾\n저장", type="primary", use_container_width=True)
            
            alarm_time = None
            if note_type == "할일":
                st.markdown("**⏰ 알림 (구글 캘린더 자동 등록)**")
                st.caption("📅 시간을 입력하면 구글 캘린더에 자동으로 등록됩니다")
                col_date, col_time = st.columns(2)

                with col_date:
                    alarm_date = st.date_input("날짜", value=None, label_visibility="collapsed")
                with col_time:
                    alarm_time_input = st.time_input("시간", value=None, label_visibility="collapsed")
                
                if alarm_date and alarm_time_input:
                    alarm_time = f"{alarm_date.strftime('%Y-%m-%d')} {alarm_time_input.strftime('%H:%M')}"
        else:
            # AI 자동 모드
            col_content, col_save = st.columns([5, 1])
            
            with col_content:
                content = st.text_area(
                    "📝 AI가 자동 분류", 
                    height=200,
                    placeholder="내용만 입력하면 AI가 알아서 분류합니다...",
                    label_visibility="collapsed"
                )
            
            with col_save:
                st.write("")
                st.write("")
                st.write("")
                submit = st.form_submit_button("💾\n저장", type="primary", use_container_width=True)
            
            selected_menu = None
            note_type = None
            alarm_time = None
        
        st.markdown("---")
        
        st.markdown("**🖼️ 이미지 (선택)**")
        
        uploaded_files = st.file_uploader(
            "이미지",
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
            st.info(f"📸 {len(st.session_state.uploaded_images)}개")
            for idx, img in enumerate(st.session_state.uploaded_images):
                col_img, col_del = st.columns([4, 1])
                with col_img:
                    st.image(img["data"], use_container_width=True)
                with col_del:
                    if st.form_submit_button("🗑️", key=f"del_img_form_{idx}"):
                        st.session_state.uploaded_images.pop(idx)
                        st.rerun()
        
        # submit 처리는 폼 내에서
        if submit:
            if content.strip():
                
                if ai_mode == "🤖 AI 자동":
                    if "GEMINI_API_KEY" not in st.secrets:
                        st.error("❌ AI 모드는 API 키 필요")
                        st.stop()
                    
                    with st.spinner("🤖 분석중..."):
                        selected_menu, note_type, alarm_time = ai_classify_note(content, menu_list, config_df)
                    
                    if selected_menu and note_type:
                        st.success(f"✅ {selected_menu} / {note_type}")
                    else:
                        st.error("❌ 분류 실패")
                        st.stop()
                
                image_url = None
                if st.session_state.uploaded_images:
                    with st.spinner("📤 업로드중..."):
                        first_img = st.session_state.uploaded_images[0]
                        timestamp = now_kst().strftime("%Y%m%d_%H%M%S")
                        filename = f"{timestamp}_{first_img['name']}"
                        image_url = upload_to_drive(first_img["data"], filename)
                
                # 🆕 캘린더 등록 (할일이고 알림시간이 있으면)
                calendar_link = None
                if note_type == "할일" and alarm_time:
                    with st.spinner("📅 캘린더 등록중..."):
                        calendar_link = create_calendar_event(
                            title=content[:100],
                            description=content,
                            start_datetime_str=alarm_time,
                            menu=selected_menu
                        )
                    if calendar_link:
                        st.info(f"🔗 [캘린더에서 확인]({calendar_link})")
                
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
                    if calendar_link:
                        st.success("📅 캘린더 등록 완료!")
                    st.session_state.uploaded_images = []
                    st.rerun()

                else:
                    st.error("❌ 저장 실패")
            else:
                st.warning("⚠️ 내용 입력 필요")
    
    st.divider()
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
            
            with st.expander(f"{row['메뉴']} - {row['날짜']} {row['시간']}"):
                st.markdown(f"<span class='badge {badge_class}'>{row['유형']}</span>", unsafe_allow_html=True)
                st.markdown(row['내용'])
                if row['이미지'] and str(row['이미지']) != 'nan' and str(row['이미지']).strip():
                    st.image(row['이미지'], use_container_width=True)
    else:
        st.info("📭 기록 없음")


# ================== 모드 2: 전체 히스토리 ==================
elif mode == "전체 히스토리":
    st.markdown("## 📋 전체 히스토리")
    
    notes_df = load_sheet("notes")
    config_df = load_sheet("config")
    
    if not notes_df.empty and not config_df.empty:
        menu_list = config_df["메뉴명"].tolist()
        
        filter_menu = st.selectbox("📁 업무", ["전체"] + menu_list)
        filter_type = st.selectbox("🏷️ 유형", ["전체", "아이디어", "할일", "업데이트", "문제점"])
        filter_date = st.selectbox("📅 기간", ["전체", "오늘", "이번 주", "이번 달"])
        
        filtered_df = notes_df.copy()
        
        # 업무 필터
        if filter_menu != "전체":
            filtered_df = filtered_df[filtered_df["메뉴"] == filter_menu]
        
        # 유형 필터 (수정됨)
        if filter_type != "전체":
            filtered_df = filtered_df[filtered_df["유형"] == filter_type]
        
        # 날짜 필터
        if filter_date == "오늘":
            filtered_df = filtered_df[filtered_df["날짜"] == today_kst_str()]
        elif filter_date == "이번 주":
            week_ago = (now_kst() - timedelta(days=7)).strftime("%Y-%m-%d")
            filtered_df = filtered_df[filtered_df["날짜"] >= week_ago]
        elif filter_date == "이번 달":
            this_month = now_kst().strftime("%Y-%m")
            filtered_df = filtered_df[filtered_df["날짜"].astype(str).str.startswith(this_month)]
        
        st.info(f"📊 총 **{len(filtered_df)}건**")
        
        if not filtered_df.empty:
            for idx, row in filtered_df.iloc[::-1].iterrows():
                badge_class = {
                    "아이디어": "badge-idea",
                    "할일": "badge-todo",
                    "업데이트": "badge-update",
                    "문제점": "badge-issue"
                }.get(row['유형'], "badge-update")
                
                is_done = str(row.get("완료", "")).strip().lower() in ["o", "완료", "done", "x"]
                done_mark = "✅" if is_done else ""
                
                with st.expander(f"{row['메뉴']} - {row['날짜']} {done_mark}"):
                    st.markdown(f"<span class='badge {badge_class}'>{row['유형']}</span>", unsafe_allow_html=True)
                    st.markdown(row['내용'])
                    if row['이미지'] and str(row['이미지']) != 'nan' and str(row['이미지']).strip():
                        st.image(row['이미지'], use_container_width=True)
                    
                    col1, col2 = st.columns(2)
                    with col1:
                        if row['유형'] == "할일" and not is_done:
                            if st.button("✅ 완료", key=f"complete_{idx}", use_container_width=True):
                                notes_df.loc[idx, '완료'] = 'O'
                                if save_sheet(notes_df, "notes"):
                                    st.success("완료!")
                                    st.rerun()
                    with col2:
                        if st.button("🗑️ 삭제", key=f"del_{idx}", use_container_width=True):
                            notes_df = notes_df.drop(idx)
                            if save_sheet(notes_df, "notes"):
                                st.success("삭제!")
                                st.rerun()
        else:
            st.info("📭 기록 없음")
    elif notes_df.empty:
        st.info("📭 기록 없음")
    else:
        st.error("⚠️ 설정 확인 필요")


# ================== 모드 3: 대화 이력 ==================
elif mode == "대화 이력":
    st.markdown("## 💬 대화 이력")
    
    with st.expander("📥 대화 가져오기", expanded=True):
        tab1, tab2 = st.tabs(["📝 직접 입력", "📂 파일"])
        
        with tab1:
            with st.form(key="chat_form_manual", clear_on_submit=True):
                chat_topic = st.text_input("📌 주제")
                chat_content = st.text_area("📝 내용", height=250)
                
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
                            st.success("✅ 저장!")
                            st.rerun()
                    else:
                        st.warning("⚠️ 입력 필요")
        
        with tab2:
            uploaded_file = st.file_uploader("📂 파일", type=["txt", "md"])
            
            if uploaded_file is not None:
                try:
                    file_content = uploaded_file.getvalue().decode("utf-8")
                    
                    st.success(f"✅ {uploaded_file.name}")
                    st.info(f"📊 {len(file_content):,} 자")
                    
                    with st.form(key="chat_form_file"):
                        file_topic = st.text_input("📌 주제", value=uploaded_file.name.replace('.txt', ''))
                        
                        col1, col2 = st.columns(2)
                        
                        with col1:
                            submit_file = st.form_submit_button("💾 저장", type="primary")
                        
                        with col2:
                            submit_ai = st.form_submit_button("🤖 요약")
                        
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
                                    st.success("✅ 저장!")
                                    st.rerun()
                        
                        if submit_ai:
                            if "GEMINI_API_KEY" not in st.secrets:
                                st.error("❌ API 키 필요")
                            elif file_topic.strip():
                                with st.spinner("🤖 요약중..."):
                                    try:
                                        model = genai.GenerativeModel('gemini-2.5-flash')
                                        
                                        prompt = f"""다음 대화를 요약해줘:

## 주요 내용
## 핵심 포인트
## 결론

[대화]
{file_content[:50000]}
"""
                                        
                                        response = model.generate_content(prompt)
                                        summary = response.text
                                        
                                        chats_df = load_sheet("chats")
                                        new_row = pd.DataFrame([{
                                            "날짜": today_kst_str(),
                                            "시간": now_kst().strftime("%H:%M:%S"),
                                            "주제": f"[AI] {file_topic}",
                                            "전체내용": summary
                                        }])
                                        
                                        updated_df = pd.concat([chats_df, new_row], ignore_index=True)
                                        
                                        if save_sheet(updated_df, "chats"):
                                            st.success("✅ 요약 저장!")
                                            st.markdown(summary)
                                            st.rerun()
                                    
                                    except Exception as e:
                                        st.error(f"❌ 실패: {e}")
                
                except Exception as e:
                    st.error(f"❌ 파일 읽기 실패: {e}")
    
    st.divider()
    st.markdown("## 📚 저장된 대화")
    
    chats_df = load_sheet("chats")
    
    if not chats_df.empty:
        for idx, row in chats_df.iloc[::-1].iterrows():
            with st.expander(f"{row['주제']} - {row['날짜']}"):
                st.markdown(row['전체내용'])
                
                if st.button("🗑️ 삭제", key=f"del_chat_{idx}", use_container_width=True):
                    chats_df = chats_df.drop(idx)
                    if save_sheet(chats_df, "chats"):
                        st.success("삭제!")
                        st.rerun()
    else:
        st.info("📭 기록 없음")

# ================== 모드 4: 일일 리포트 ==================
elif mode == "일일 리포트":
    st.markdown("## 📊 일일 리포트")
    
    notes_df = load_sheet("notes")
    
    if not notes_df.empty:
        today_str = today_kst_str()
        today_notes = notes_df[notes_df["날짜"] == today_str]
        
        if not today_notes.empty:
            st.success(f"📅 **{today_str}**\n\n총 **{len(today_notes)}건**")
            
            col1, col2 = st.columns(2)
            
            with col1:
                idea_count = len(today_notes[today_notes["유형"] == "아이디어"])
                st.metric("💡 아이디어", idea_count)
                
                update_count = len(today_notes[today_notes["유형"] == "업데이트"])
                st.metric("📝 업데이트", update_count)
            
            with col2:
                todo_count = len(today_notes[today_notes["유형"] == "할일"])
                st.metric("✅ 할일", todo_count)
                
                issue_count = len(today_notes[today_notes["유형"] == "문제점"])
                st.metric("🔥 문제점", issue_count)
            
            st.divider()
            
            if "메뉴" in today_notes.columns:
                st.markdown("### 📁 업무별")
                
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
                            
                            st.markdown(f"<span class='badge {badge_class}'>{row['유형']}</span> {row['내용'][:50]}...", unsafe_allow_html=True)
            
            st.divider()
            
            if "GEMINI_API_KEY" in st.secrets:
                st.markdown("### 🤖 AI 요약")
                
                if st.button("📋 오늘 업무 요약", type="primary", use_container_width=True):
                    all_content = "\n\n".join([f"[{row['메뉴']} - {row['유형']}] {row['내용']}" for _, row in today_notes.iterrows()])
                    
                    with st.spinner("🤖 요약중..."):
                        try:
                            model = genai.GenerativeModel('gemini-2.5-flash')
                            
                            prompt = f"""오늘({today_str}) 업무 요약:

## 주요 업무
## 완료한 일
## 진행 중
## 아이디어
## 이슈

기록:
{all_content[:10000]}
"""
                            
                            response = model.generate_content(prompt)
                            summary = response.text
                            
                            st.markdown("---")
                            st.markdown(summary)
                            
                            if st.button("💾 대화 이력에 저장", use_container_width=True):
                                chats_df = load_sheet("chats")
                                new_row = pd.DataFrame([{
                                    "날짜": today_kst_str(),
                                    "시간": now_kst().strftime("%H:%M:%S"),
                                    "주제": f"{today_str} 일일 요약",
                                    "전체내용": summary
                                }])
                                
                                updated_df = pd.concat([chats_df, new_row], ignore_index=True)
                                
                                if save_sheet(updated_df, "chats"):
                                    st.success("✅ 저장!")
                        
                        except Exception as e:
                            st.error(f"❌ 실패: {e}")
        
        else:
            st.info("📭 오늘 기록 없음")
    else:
        st.info("📭 전체 기록 없음")

# ================== 모드 5: 업무 포트폴리오 ==================
elif mode == "업무 포트폴리오":
    st.markdown("## 📊 업무 포트폴리오")
    st.caption("개발한 업무들의 통계와 성과를 한눈에 확인하세요")
    
    config_df = load_sheet("config")
    notes_df = load_sheet("notes")
    
    if config_df.empty:
        st.warning("⚠️ 등록된 업무가 없습니다")
        st.stop()
    
    # ========== 1. 전체 요약 ==========
    st.markdown("### 📈 전체 요약")
    
    col1, col2, col3 = st.columns(3)
    
    with col1:
        total_menus = len(config_df)
        st.metric("📁 총 업무 수", f"{total_menus}개")
    
    with col2:
        total_records = len(notes_df) if not notes_df.empty else 0
        st.metric("📝 총 기록 수", f"{total_records}건")
    
    with col3:
        if not notes_df.empty and "날짜" in notes_df.columns:
            dates = pd.to_datetime(notes_df["날짜"])
            if len(dates) > 1:
                days_active = (dates.max() - dates.min()).days + 1
                st.metric("📅 활동 기간", f"{days_active}일")
            else:
                st.metric("📅 활동 기간", "1일")
        else:
            st.metric("📅 활동 기간", "0일")
    
    st.divider()
    
    # ========== 2. 업무별 상세 통계 ==========
    st.markdown("### 📁 업무별 상세")
    
    for idx, row in config_df.iterrows():
        menu_name = row['메뉴명']
        menu_desc = row.get('업무설명', '')
        
        # 이 업무의 기록 개수
        if not notes_df.empty and "메뉴" in notes_df.columns:
            menu_records = notes_df[notes_df["메뉴"] == menu_name]
            record_count = len(menu_records)
            
            # 유형별 개수
            type_counts = {}
            if not menu_records.empty and "유형" in menu_records.columns:
                type_counts = menu_records["유형"].value_counts().to_dict()
            
            # 최근 활동일
            last_activity = ""
            if not menu_records.empty and "날짜" in menu_records.columns:
                last_date = menu_records["날짜"].max()
                last_activity = f"최근: {last_date}"
        else:
            record_count = 0
            type_counts = {}
            last_activity = "활동 없음"
        
        # Expander로 표시
        with st.expander(f"**{menu_name}** ({record_count}건) {last_activity}"):
            
            # 업무 설명
            if menu_desc:
                st.info(f"📝 **설명:** {menu_desc}")
            else:
                st.caption("설명이 없습니다")
            
            # 유형별 통계
            if type_counts:
                st.markdown("**📊 유형별 통계**")
                
                col1, col2, col3, col4 = st.columns(4)
                
                with col1:
                    idea_count = type_counts.get("아이디어", 0)
                    st.metric("💡 아이디어", idea_count)
                
                with col2:
                    todo_count = type_counts.get("할일", 0)
                    st.metric("✅ 할일", todo_count)
                
                with col3:
                    update_count = type_counts.get("업데이트", 0)
                    st.metric("📝 업데이트", update_count)
                
                with col4:
                    issue_count = type_counts.get("문제점", 0)
                    st.metric("🔥 문제점", issue_count)
            
            # 최근 5개 기록 미리보기
            if not menu_records.empty:
                st.markdown("**📋 최근 기록 미리보기**")
                recent_5 = menu_records.iloc[::-1].head(5)
                
                for r_idx, r_row in recent_5.iterrows():
                    badge_class = {
                        "아이디어": "badge-idea",
                        "할일": "badge-todo",
                        "업데이트": "badge-update",
                        "문제점": "badge-issue"
                    }.get(r_row['유형'], "badge-update")
                    
                    content_preview = r_row['내용'][:50] + "..." if len(r_row['내용']) > 50 else r_row['내용']
                    
                    st.markdown(
                        f"<span class='badge {badge_class}'>{r_row['유형']}</span> "
                        f"{r_row['날짜']} - {content_preview}", 
                        unsafe_allow_html=True
                    )
            else:
                st.caption("📭 아직 기록이 없습니다")
            
            # 이 업무만 보기 버튼
            if st.button(f"🔍 '{menu_name}' 전체 보기", key=f"view_{idx}", use_container_width=True):
                st.info(f"💡 **팁:** '전체 히스토리' 메뉴에서 업무를 '{menu_name}'로 필터링하면 볼 수 있어요!")
    
    st.divider()
    
    # ========== 3. 시간별 활동 분석 ==========
    if not notes_df.empty and "날짜" in notes_df.columns:
        st.markdown("### 📅 시간별 활동 분석")
        
        # 날짜별 기록 수
        date_counts = notes_df["날짜"].value_counts().sort_index()
        
        # 최근 7일 활동
        recent_7days = date_counts.tail(7)
        
        st.markdown("**📊 최근 7일 활동량**")
        
        if len(recent_7days) > 0:
            for date, count in recent_7days.items():
                # 간단한 막대 그래프 표현
                bar = "█" * min(int(count), 20)
                st.markdown(f"`{date}` {bar} **{count}건**")
        else:
            st.caption("최근 7일 활동이 없습니다")
        
        # 가장 활발한 날
        most_active_date = date_counts.idxmax()
        most_active_count = date_counts.max()
        
        st.success(f"🏆 **가장 활발했던 날:** {most_active_date} ({most_active_count}건)")
    
    st.divider()
    
    # ========== 4. AI 포트폴리오 생성 ==========
    if "GEMINI_API_KEY" in st.secrets:
        st.markdown("### 🤖 AI 포트폴리오 자동 생성")
        st.caption("AI가 당신의 업무 활동을 분석해서 포트폴리오 문서를 만들어줍니다")
        
        if st.button("📝 AI 포트폴리오 생성", type="primary", use_container_width=True):
            
            with st.spinner("🤖 포트폴리오 생성 중..."):
                try:
                    # 모든 업무 정보 수집
                    portfolio_data = ""
                    
                    for idx, row in config_df.iterrows():
                        menu_name = row['메뉴명']
                        menu_desc = row.get('업무설명', '설명 없음')
                        
                        if not notes_df.empty and "메뉴" in notes_df.columns:
                            menu_records = notes_df[notes_df["메뉴"] == menu_name]
                            record_count = len(menu_records)
                            
                            # 최근 기록 3개
                            recent_records = menu_records.iloc[::-1].head(3)
                            records_text = "\n".join([f"- [{r['유형']}] {r['내용'][:100]}" for _, r in recent_records.iterrows()])
                        else:
                            record_count = 0
                            records_text = "기록 없음"
                        
                        portfolio_data += f"""
## {menu_name}
- 설명: {menu_desc}
- 총 기록: {record_count}건
- 최근 활동:
{records_text}

"""
                    
                    model = genai.GenerativeModel('gemini-2.5-flash')
                    
                    prompt = f"""다음은 한 사용자가 개발하고 관리한 업무들의 기록입니다.
이를 바탕으로 전문적인 업무 포트폴리오를 작성해주세요.

## 요구사항
1. 전체 업무 개요 (무슨 일을 해왔는지)
2. 각 업무별 주요 성과와 역할
3. 전체적인 업무 능력 요약
4. 특히 잘한 점

## 업무 데이터
{portfolio_data[:5000]}

---
위 내용을 바탕으로 전문적이고 읽기 쉬운 포트폴리오를 작성해주세요.
마크다운 형식으로 작성하되, 한국어로 작성해주세요."""

                    response = model.generate_content(prompt)
                    portfolio_text = response.text
                    
                    st.success("✅ 포트폴리오 생성 완료!")
                    
                    st.divider()
                    st.markdown("### 📄 생성된 포트폴리오")
                    st.markdown(portfolio_text)
                    
                    st.divider()
                    
                    # 저장 옵션
                    col1, col2 = st.columns(2)
                    
                    with col1:
                        if st.button("💾 대화 이력에 저장", use_container_width=True):
                            chats_df = load_sheet("chats")
                            new_row = pd.DataFrame([{
                                "날짜": today_kst_str(),
                                "시간": now_kst().strftime("%H:%M:%S"),
                                "주제": "업무 포트폴리오",
                                "전체내용": portfolio_text
                            }])
                            
                            updated_df = pd.concat([chats_df, new_row], ignore_index=True)
                            
                            if save_sheet(updated_df, "chats"):
                                st.success("✅ 저장 완료!")
                    
                    with col2:
                        # 다운로드 버튼
                        st.download_button(
                            label="📥 텍스트 파일로 다운로드",
                            data=portfolio_text,
                            file_name=f"업무포트폴리오_{today_kst_str()}.md",
                            mime="text/markdown",
                            use_container_width=True
                        )
                
                except Exception as e:
                    st.error(f"❌ 포트폴리오 생성 실패: {e}")
    else:
        st.info("💡 API 키를 설정하면 AI 포트폴리오 생성 기능을 사용할 수 있어요")

# ================== 모드 6: 메뉴/설정 관리 ==================
elif mode == "메뉴/설정 관리":
    st.markdown("## ⚙️ 설정")
    
    config_df = load_sheet("config")
    
    st.markdown("### 📁 등록된 업무")
    
    if not config_df.empty:
        for idx, row in config_df.iterrows():
            with st.expander(f"**{row['메뉴명']}**"):
                st.text(f"설명: {row.get('업무설명', '없음')}")
                
                if st.button("🗑️ 삭제", key=f"del_menu_{idx}", use_container_width=True):
                    config_df = config_df.drop(idx)
                    if save_sheet(config_df, "config"):
                        st.success("삭제!")
                        st.rerun()
        
        st.divider()
        st.markdown("### ➕ 업무 추가")
        
        with st.form(key="add_menu_form", clear_on_submit=True):
            new_menu = st.text_input("업무명")
            new_desc = st.text_area("설명 (선택)", height=100)
            
            submit_new = st.form_submit_button("➕ 추가", type="primary", use_container_width=True)
            
            if submit_new:
                if new_menu.strip():
                    new_row = pd.DataFrame([{
                        "메뉴명": new_menu,
                        "시트정보": "",
                        "트리거정보": "",
                        "업무설명": new_desc,
                        "메일발송설정": ""
                    }])
                    
                    updated_df = pd.concat([config_df, new_row], ignore_index=True)
                    
                    if save_sheet(updated_df, "config"):
                        st.success(f"✅ '{new_menu}' 추가!")
                        st.rerun()
                else:
                    st.warning("⚠️ 업무명 필요")
    
    else:
        st.warning("⚠️ 업무 없음")
        
        with st.form(key="first_menu_form", clear_on_submit=True):
            first_menu = st.text_input("첫 업무명")
            first_desc = st.text_area("설명", height=100)
            
            submit_first = st.form_submit_button("➕ 추가", type="primary", use_container_width=True)
            
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
                        st.success(f"✅ '{first_menu}' 추가!")
                        st.rerun()
