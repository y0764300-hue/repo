import gradio as gr
import pandas as pd
from datetime import datetime, timedelta
import pytz
import google.generativeai as genai
import gspread
from google.oauth2.service_account import Credentials
from googleapiclient.discovery import build
from googleapiclient.http import MediaIoBaseUpload
import io
import json
import re

# 한국 시간대 설정
TZ_KST = pytz.timezone("Asia/Seoul")

def now_kst():
    return datetime.now(TZ_KST)

def today_kst_str():
    return now_kst().strftime("%Y-%m-%d")

# ================ Google Sheets 연결 ================
def connect_sheets():
    try:
        with open('secrets.json', 'r', encoding='utf-8') as f:
            secrets = json.load(f)
        
        creds = Credentials.from_service_account_info(
            secrets['gcp_service_account'],
            scopes=[
                'https://www.googleapis.com/auth/spreadsheets',
                'https://www.googleapis.com/auth/drive'
            ]
        )
        
        client = gspread.authorize(creds)
        spreadsheet = client.open_by_url(secrets['SPREADSHEET_URL'])
        return spreadsheet, secrets
    except Exception as e:
        print(f"❌ Sheets 연결 실패: {e}")
        return None, None

def load_sheet(worksheet_name):
    try:
        spreadsheet, _ = connect_sheets()
        if not spreadsheet:
            return pd.DataFrame()
        
        worksheet = spreadsheet.worksheet(worksheet_name)
        data = worksheet.get_all_records()
        df = pd.DataFrame(data)
        df = df.fillna("")
        
        if worksheet_name == "notes":
            if "알림시간" not in df.columns:
                df["알림시간"] = ""
            if "완료" not in df.columns:
                df["완료"] = ""
        
        return df
    except Exception as e:
        print(f"❌ 시트 로드 실패: {e}")
        if worksheet_name == "notes":
            return pd.DataFrame(columns=['날짜', '시간', '메뉴', '유형', '내용', '이미지', '알림시간', '완료'])
        elif worksheet_name == "config":
            return pd.DataFrame(columns=["메뉴명", "시트정보", "트리거정보", "업무설명", "메일발송설정"])
        elif worksheet_name == "chats":
            return pd.DataFrame(columns=['날짜', '시간', '주제', '전체내용'])
        return pd.DataFrame()

def save_to_sheet(df, worksheet_name):
    try:
        spreadsheet, _ = connect_sheets()
        if not spreadsheet:
            return False
        
        worksheet = spreadsheet.worksheet(worksheet_name)
        worksheet.clear()
        data = [df.columns.values.tolist()] + df.fillna("").values.tolist()
        worksheet.update('A1', data)
        return True
    except Exception as e:
        print(f"❌ 시트 저장 실패: {e}")
        return False

# ================ Google Drive 업로드 ================
def upload_to_drive(image, filename):
    try:
        _, secrets = connect_sheets()
        if not secrets:
            return None
        
        creds = Credentials.from_service_account_info(
            secrets['gcp_service_account'],
            scopes=['https://www.googleapis.com/auth/drive.file']
        )
        
        service = build('drive', 'v3', credentials=creds)
        
        img_byte_arr = io.BytesIO()
        image.save(img_byte_arr, format='PNG')
        img_byte_arr.seek(0)
        
        file_metadata = {
            'name': filename,
            'parents': [secrets['GOOGLE_DRIVE_FOLDER_ID']]
        }
        
        media = MediaIoBaseUpload(img_byte_arr, mimetype='image/png')
        
        file = service.files().create(
            body=file_metadata,
            media_body=media,
            fields='id'
        ).execute()
        
        service.permissions().create(
            fileId=file['id'],
            body={'type': 'anyone', 'role': 'reader'}
        ).execute()
        
        return f"https://drive.google.com/uc?export=view&id={file['id']}"
    
    except Exception as e:
        print(f"❌ 이미지 업로드 실패: {e}")
        return None

# ================ AI 분류 ================
def ai_classify_note(content, menu_list, config_df):
    try:
        with open('secrets.json', 'r', encoding='utf-8') as f:
            secrets = json.load(f)
        
        if not secrets.get('GEMINI_API_KEY'):
            return menu_list[0], '📝 업데이트', None, "API 키 없음"
        
        genai.configure(api_key=secrets['GEMINI_API_KEY'])
        model = genai.GenerativeModel('gemini-2.0-flash-exp')
        
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
                
                if '아이디어' in type_str:
                    note_type = '💡 아이디어'
                elif '할' in type_str and '일' in type_str:
                    note_type = '✅ 할 일'
                elif '업데이트' in type_str:
                    note_type = '📝 업데이트'
                elif '문제' in type_str:
                    note_type = '🔥 문제점'
            
            elif '시간' in line and ':' in line:
                time_str = line.split(':', 1)[1].strip()
                if '없음' not in time_str and len(time_str) > 5:
                    time_pattern = r'\d{4}-\d{2}-\d{2}\s+\d{2}:\d{2}'
                    matches = re.findall(time_pattern, time_str)
                    if matches:
                        alarm_time = matches[0]
        
        if not menu:
            menu = menu_list[0]
        if not note_type:
            note_type = '📝 업데이트'
        
        return menu, note_type, alarm_time, result
    
    except Exception as e:
        print(f"❌ AI 분류 실패: {e}")
        return menu_list[0] if menu_list else None, '📝 업데이트', None, str(e)

# ================ 메인 함수들 ================
def save_note(content, image, mode, manual_menu, manual_type, manual_date, manual_time):
    """업무 기록 저장"""
    if not content or not content.strip():
        return "⚠️ 내용을 입력하세요"
    
    try:
        config_df = load_sheet("config")
        if config_df.empty:
            return "❌ config 시트를 불러올 수 없습니다"
        
        menu_list = config_df["메뉴명"].tolist()
        
        if mode == "🤖 AI 자동 분류":
            menu, note_type, alarm_time, ai_log = ai_classify_note(content, menu_list, config_df)
            result_msg = f"🤖 **AI 분류 완료**\n\n- 업무: **{menu}**\n- 유형: **{note_type}**\n"
            if alarm_time:
                result_msg += f"- 알림: **{alarm_time}**\n"
            result_msg += f"\n### AI 분석:\n```\n{ai_log}\n```\n\n---\n\n"
        else:
            menu = manual_menu
            note_type = manual_type
            alarm_time = None
            
            if note_type == "✅ 할 일" and manual_date and manual_time:
                alarm_time = f"{manual_date} {manual_time}"
            
            result_msg = f"✋ **수동 저장**\n\n- 업무: **{menu}**\n- 유형: **{note_type}**\n"
            if alarm_time:
                result_msg += f"- 알림: **{alarm_time}**\n"
            result_msg += "\n---\n\n"
        
        image_url = ""
        if image is not None:
            timestamp = now_kst().strftime("%Y%m%d_%H%M%S")
            filename = f"{timestamp}.png"
            image_url = upload_to_drive(image, filename)
            if image_url:
                result_msg += "📸 이미지 업로드 완료\n\n"
        
        notes_df = load_sheet("notes")
        new_row = pd.DataFrame([{
            "날짜": today_kst_str(),
            "시간": now_kst().strftime("%H:%M:%S"),
            "메뉴": menu,
            "유형": note_type,
            "내용": content,
            "이미지": image_url,
            "알림시간": alarm_time or "",
            "완료": ""
        }])
        
        updated_df = pd.concat([notes_df, new_row], ignore_index=True)
        
        if save_to_sheet(updated_df, "notes"):
            result_msg += f"✅ **저장 완료!**\n\n**내용:** {content[:100]}{'...' if len(content) > 100 else ''}"
            return result_msg
        else:
            return "❌ 저장 실패"
    
    except Exception as e:
        return f"❌ 오류 발생: {str(e)}"

def get_recent_notes():
    """최근 기록 5개"""
    try:
        notes_df = load_sheet("notes")
        if notes_df.empty:
            return "📭 아직 기록이 없습니다"
        
        recent = notes_df.tail(5).iloc[::-1]
        
        result = "## 📚 최근 기록 (최신 5개)\n\n"
        for _, row in recent.iterrows():
            alarm_info = ""
            if str(row.get("알림시간", "")).strip():
                alarm_info = f" ⏰ {row['알림시간']}"
            
            done_mark = ""
            if str(row.get("완료", "")).strip().lower() in ["o", "완료", "done", "x"]:
                done_mark = " ✅"
            
            result += f"**{row['유형']}** [{row['메뉴']}] {row['날짜']} {row['시간']}{alarm_info}{done_mark}\n\n"
            result += f"{row['내용']}\n\n"
            
            if row.get('이미지') and str(row['이미지']).strip():
                result += f"[🖼️ 이미지 보기]({row['이미지']})\n\n"
            
            result += "---\n\n"
        
        return result
    except Exception as e:
        return f"❌ 오류: {str(e)}"

def load_all_notes(menu_filter, type_filter, date_filter):
    """전체 히스토리 로드 (필터링)"""
    try:
        notes_df = load_sheet("notes")
        if notes_df.empty:
            return pd.DataFrame()
        
        filtered_df = notes_df.copy()
        
        if menu_filter != "전체 보기":
            filtered_df = filtered_df[filtered_df["메뉴"] == menu_filter]
        
        if type_filter != "전체":
            filtered_df = filtered_df[filtered_df["유형"] == type_filter]
        
        if date_filter == "오늘":
            filtered_df = filtered_df[filtered_df["날짜"] == today_kst_str()]
        elif date_filter == "이번 주":
            week_ago = (now_kst() - timedelta(days=7)).strftime("%Y-%m-%d")
            filtered_df = filtered_df[filtered_df["날짜"] >= week_ago]
        elif date_filter == "이번 달":
            this_month = now_kst().strftime("%Y-%m")
            filtered_df = filtered_df[filtered_df["날짜"].astype(str).str.startswith(this_month)]
        
        filtered_df = filtered_df.iloc[::-1]
        
        display_df = filtered_df[['날짜', '시간', '메뉴', '유형', '내용', '알림시간', '완료']].copy()
        display_df['내용'] = display_df['내용'].str[:50] + '...'
        
        return display_df
    except Exception as e:
        return pd.DataFrame()

def generate_daily_report():
    """일일 리포트 생성"""
    try:
        notes_df = load_sheet("notes")
        today_str = today_kst_str()
        today_notes = notes_df[notes_df["날짜"] == today_str]
        
        if today_notes.empty:
            return f"📅 {today_str}에 작성된 업무 기록이 없습니다"
        
        report = f"# 📊 {today_str} 일일 리포트\n\n"
        
        for menu in today_notes["메뉴"].unique():
            report += f"## 📌 {menu}\n\n"
            menu_notes = today_notes[today_notes["메뉴"] == menu]
            
            for _, row in menu_notes.iterrows():
                report += f"**{row['유형']}** {row['시간']}\n\n"
                report += f"{row['내용']}\n\n"
                
                if row.get('이미지') and str(row['이미지']).strip():
                    report += f"[🖼️ 이미지]({row['이미지']})\n\n"
                
                report += "---\n\n"
        
        return report
    except Exception as e:
        return f"❌ 오류: {str(e)}"

# ================ Gradio UI ================
with gr.Blocks(title="📝 스마트 업무 비서", theme=gr.themes.Soft()) as demo:
    
    gr.Markdown("# 📝 스마트 업무 비서")
    gr.Markdown("*AI 기반 업무 기록 및 관리 시스템 (Gradio 버전)*")
    
    # 탭 1: 업무 기록하기
    with gr.Tab("📝 업무 기록하기"):
        
        with gr.Row():
            mode = gr.Radio(
                ["🤖 AI 자동 분류", "✋ 수동 선택"],
                value="🤖 AI 자동 분류",
                label="입력 모드",
                info="AI 모드: 내용만 입력하면 자동 분류"
            )
        
        # 수동 선택 옵션
        with gr.Row(visible=False) as manual_options:
            config_df = load_sheet("config")
            menu_list = config_df["메뉴명"].tolist() if not config_df.empty else ["업무1"]
            
            with gr.Column():
                manual_menu = gr.Dropdown(
                    choices=menu_list,
                    value=menu_list[0] if menu_list else None,
                    label="📁 업무 선택"
                )
                manual_type = gr.Radio(
                    ["💡 아이디어", "✅ 할 일", "📝 업데이트", "🔥 문제점"],
                    value="📝 업데이트",
                    label="🏷️ 유형"
                )
            
            with gr.Column(visible=False) as alarm_options:
                manual_date = gr.Textbox(
                    label="📅 알림 날짜 (YYYY-MM-DD)",
                    placeholder="2026-01-04"
                )
                manual_time = gr.Textbox(
                    label="⏰ 알림 시간 (HH:MM)",
                    placeholder="15:00"
                )
        
        # 모드 변경 시 수동 옵션 표시/숨김
        def toggle_manual(mode):
            return gr.update(visible=(mode == "✋ 수동 선택"))
        
        def toggle_alarm(note_type):
            return gr.update(visible=(note_type == "✅ 할 일"))
        
        mode.change(toggle_manual, mode, manual_options)
        manual_type.change(toggle_alarm, manual_type, alarm_options)
        
        with gr.Row():
            with gr.Column(scale=2):
                content = gr.Textbox(
                    label="📝 내용 입력",
                    placeholder="여기에 업무 내용을 입력하세요...\n예: 내일 오후 3시에 회의",
                    lines=7
                )
            
            with gr.Column(scale=1):
                image = gr.Image(
                    label="🖼️ 이미지 (드래그 앤 드롭!)",
                    type="pil",
                    height=300
                )
        
        submit_btn = gr.Button("💾 저장", variant="primary", size="lg")
        output = gr.Markdown(label="결과")
        
        submit_btn.click(
            fn=save_note,
            inputs=[content, image, mode, manual_menu, manual_type, manual_date, manual_time],
            outputs=output
        )
        
        gr.Markdown("---")
        
        recent_btn = gr.Button("🔄 최근 기록 보기", size="sm")
        recent_output = gr.Markdown()
        
        recent_btn.click(fn=get_recent_notes, outputs=recent_output)
    
    # 탭 2: 전체 히스토리
    with gr.Tab("📋 전체 히스토리"):
        gr.Markdown("## 📋 전체 업무 히스토리")
        
        with gr.Row():
            config_df = load_sheet("config")
            menu_list = config_df["메뉴명"].tolist() if not config_df.empty else []
            
            menu_filter = gr.Dropdown(
                choices=["전체 보기"] + menu_list,
                value="전체 보기",
                label="📁 업무 필터"
            )
            type_filter = gr.Dropdown(
                choices=["전체", "💡 아이디어", "✅ 할 일", "📝 업데이트", "🔥 문제점"],
                value="전체",
                label="🏷️ 유형 필터"
            )
            date_filter = gr.Dropdown(
                choices=["전체 기간", "오늘", "이번 주", "이번 달"],
                value="전체 기간",
                label="📅 기간 필터"
            )
        
        load_btn = gr.Button("🔄 불러오기", variant="primary")
        notes_table = gr.Dataframe(label="전체 기록", wrap=True)
        
        load_btn.click(
            fn=load_all_notes,
            inputs=[menu_filter, type_filter, date_filter],
            outputs=notes_table
        )
    
    # 탭 3: 일일 리포트
    with gr.Tab("📊 일일 리포트"):
        gr.Markdown("## 📊 오늘의 업무 리포트")
        
        generate_btn = gr.Button("📋 리포트 생성", variant="primary", size="lg")
        report_output = gr.Markdown()
        
        generate_btn.click(fn=generate_daily_report, outputs=report_output)

# 실행
if __name__ == "__main__":
    demo.launch(
        share=False,
        server_name="0.0.0.0",
        server_port=7860,
        show_error=True
    )
###