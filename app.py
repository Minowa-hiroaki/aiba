import streamlit as st
from streamlit_gsheets import GSheetsConnection
import pandas as pd
from datetime import datetime, timedelta
from google.cloud import storage
from google.oauth2 import service_account
import uuid
from PIL import Image
import io
import requests
import urllib.parse
import streamlit.components.v1 as components
import json

# --- 1. ページ基本設定 ---
st.set_page_config(
    page_title="AIBA Memorial Party",
    page_icon="🎧",
    layout="centered"
)

# --- 2. スプレッドシート接続設定（オプショナル） ---
# Google Sheetsを使う場合は .streamlit/secrets.toml の設定が必要です
try:
    conn = st.connection("gsheets", type=GSheetsConnection)
    USE_GSHEETS = True
except Exception:
    USE_GSHEETS = False
    st.warning("⚠️ Google Sheets未設定: データは一時的にセッションに保存されます")

# --- 2-2. Google Cloud Storage接続設定 ---
USE_GCS = False
try:
    if "connections" in st.secrets and "gsheets" in st.secrets["connections"]:
        gcs_credentials = service_account.Credentials.from_service_account_info(
            {
                "type": st.secrets["connections"]["gsheets"]["type"],
                "project_id": st.secrets["connections"]["gsheets"]["project_id"],
                "private_key_id": st.secrets["connections"]["gsheets"]["private_key_id"],
                "private_key": st.secrets["connections"]["gsheets"]["private_key"],
                "client_email": st.secrets["connections"]["gsheets"]["client_email"],
                "client_id": st.secrets["connections"]["gsheets"]["client_id"],
                "auth_uri": st.secrets["connections"]["gsheets"]["auth_uri"],
                "token_uri": st.secrets["connections"]["gsheets"]["token_uri"],
                "auth_provider_x509_cert_url": st.secrets["connections"]["gsheets"]["auth_provider_x509_cert_url"],
                "client_x509_cert_url": st.secrets["connections"]["gsheets"]["client_x509_cert_url"],
            }
        )
        gcs_client = storage.Client(credentials=gcs_credentials, project=st.secrets["connections"]["gsheets"]["project_id"])
        bucket_name = st.secrets["gcs"]["bucket_name"]
        gcs_bucket = gcs_client.bucket(bucket_name)
        # バケットの存在確認
        if gcs_bucket.exists():
            USE_GCS = True
        else:
            USE_GCS = False
except Exception as e:
    USE_GCS = False

def get_data(worksheet_name):
    """データを読み込む（Google Sheets または セッション状態から）"""
    if USE_GSHEETS:
        try:
            return conn.read(worksheet=worksheet_name, ttl="1m")
        except Exception:
            pass
    
    # セッション状態から取得
    if f'{worksheet_name}_data' not in st.session_state:
        st.session_state[f'{worksheet_name}_data'] = pd.DataFrame()
    return st.session_state[f'{worksheet_name}_data']

def save_data(worksheet_name, data):
    """データを保存（Google Sheets または セッション状態へ）"""
    if USE_GSHEETS:
        try:
            result = conn.update(worksheet=worksheet_name, data=data)
            return True
        except Exception as e:
            import traceback
            error_details = traceback.format_exc()
            st.error(f"Google Sheets保存エラー: {type(e).__name__}: {str(e)}")
            with st.expander("詳細なエラー情報"):
                st.code(error_details)
            return False
    else:
        # セッション状態に保存
        st.session_state[f'{worksheet_name}_data'] = data
        return True

def upload_image_to_gcs(uploaded_file):
    """画像をGCSにアップロードして公開URLを返す"""
    if not USE_GCS or uploaded_file is None:
        return None
    
    try:
        # 画像を開く
        image = Image.open(uploaded_file)
        
        # EXIF情報を元に画像の向きを自動修正（物理的に回転）
        from PIL import ImageOps
        image = ImageOps.exif_transpose(image)
        
        # 画像をリサイズ（アップロード速度を改善）
        # 最大サイズを1920x1080に制限（アスペクト比維持）
        max_width = 1920
        max_height = 1080
        
        # アスペクト比を維持しながらリサイズ
        if image.width > max_width or image.height > max_height:
            image.thumbnail((max_width, max_height), Image.Resampling.LANCZOS)
        
        # RGBAモードの場合はRGBに変換（JPEG保存用）
        if image.mode in ('RGBA', 'P', 'LA'):
            rgb_image = Image.new('RGB', image.size, (255, 255, 255))
            if image.mode == 'P':
                image = image.convert('RGBA')
            rgb_image.paste(image, mask=image.split()[-1] if image.mode in ('RGBA', 'LA') else None)
            image = rgb_image
        elif image.mode != 'RGB':
            image = image.convert('RGB')
        
        # 修正した画像をバイトストリームに変換
        img_byte_arr = io.BytesIO()
        # JPEGフォーマットで保存（品質85%に最適化 - 十分綺麗で高速）
        image.save(img_byte_arr, format='JPEG', quality=85, optimize=True)
        img_byte_arr.seek(0)
        
        # ユニークなファイル名を生成
        unique_filename = f"photos/{uuid.uuid4()}.jpg"
        
        # GCSにアップロード
        blob = gcs_bucket.blob(unique_filename)
        blob.upload_from_file(img_byte_arr, content_type='image/jpeg')
        # バケットレベルで公開されているため、make_public()は不要
        
        # 公開URLを返す
        return blob.public_url
    except Exception as e:
        st.warning(f"画像アップロード失敗: {str(e)}")
        return None

@st.cache_data(ttl=3600)  # 1時間キャッシュ
def get_album_artwork(song_name, artist_name):
    """iTunes Search APIを使ってアルバムアートワークを取得"""
    try:
        # iTunes Search API
        search_term = f"{song_name} {artist_name}"
        encoded_term = urllib.parse.quote(search_term)
        api_url = f"https://itunes.apple.com/search?term={encoded_term}&entity=song&limit=1"
        
        # タイムアウトを2秒に短縮して高速化
        response = requests.get(api_url, timeout=2)
        if response.status_code == 200:
            data = response.json()
            if data.get('resultCount', 0) > 0:
                # アートワークURLを取得（100x100を600x600に変更）
                artwork_url = data['results'][0].get('artworkUrl100', '').replace('100x100', '600x600')
                return artwork_url
        return None
    except:
        return None

def clean_youtube_url(url):
    """YouTube URLをクリーンアップ（全角文字や不要な文字を削除）"""
    if not url or pd.isna(url):
        return ""
    
    # 文字列に変換
    url = str(url).strip()
    
    # 全角文字を半角に変換
    url = url.replace('：', ':').replace('／', '/').replace('？', '?').replace('＝', '=').replace('＆', '&')
    url = url.replace('）', '').replace('（', '').replace('）', '').replace('(', '').replace(')', '')
    
    # 末尾の不要な文字を削除
    url = url.rstrip('）（)').strip()
    
    # YouTubeのURLかチェック
    if 'youtube.com' in url or 'youtu.be' in url:
        return url
    
    return ""

def convert_gdrive_to_embed(url):
    """Google Drive URLを埋め込み用URLに変換"""
    if not url or pd.isna(url):
        return None
    
    url = str(url).strip()
    
    # Google DriveのURLかチェック
    if 'drive.google.com' not in url:
        return None
    
    # ファイルIDを抽出
    import re
    
    # https://drive.google.com/file/d/FILE_ID/view?usp=sharing 形式
    match = re.search(r'/file/d/([a-zA-Z0-9_-]+)', url)
    if match:
        file_id = match.group(1)
        return f"https://drive.google.com/file/d/{file_id}/preview"
    
    # https://drive.google.com/open?id=FILE_ID 形式
    match = re.search(r'[?&]id=([a-zA-Z0-9_-]+)', url)
    if match:
        file_id = match.group(1)
        return f"https://drive.google.com/file/d/{file_id}/preview"
    
    return None

def generate_upload_signed_url(filename, content_type):
    """GCSへの直接アップロード用のSigned URLを生成"""
    if not USE_GCS:
        return None, None
    
    try:
        # ファイル拡張子を取得
        file_extension = filename.split('.')[-1] if '.' in filename else 'tmp'
        # ユニークなファイル名を生成
        unique_filename = f"memory/{uuid.uuid4()}.{file_extension}"
        
        # Blobオブジェクトを作成
        blob = gcs_bucket.blob(unique_filename)
        
        # Signed URLを生成（30分有効、PUTメソッド）
        upload_url = blob.generate_signed_url(
            version="v4",
            expiration=timedelta(minutes=30),
            method="PUT",
            content_type=content_type
        )
        
        # 公開URLを返す
        public_url = blob.public_url
        
        return upload_url, public_url
    except Exception as e:
        st.error(f"Signed URL生成エラー: {str(e)}")
        return None, None

def render_large_file_uploader(key="large_uploader", signed_url=None, public_url=None):
    """大容量ファイル用のカスタムアップローダー（Signed URL方式）"""
    
    # Signed URLが渡されている場合、それを埋め込む
    signed_url_js = f"'{signed_url}'" if signed_url else 'null'
    public_url_js = f"'{public_url}'" if public_url else 'null'
    
    html_code = f"""
    <div style="padding: 20px; background-color: #0f172a; border: 2px dashed #334155; border-radius: 10px; text-align: center;">
        <input type="file" id="fileInput_{key}" accept=".mp4,.mov,.avi,.mp3,.wav,.m4a" 
               style="display: none;" onchange="handleFileSelect_{key}(event)">
        <button onclick="document.getElementById('fileInput_{key}').click()" 
                style="background-color: #10b981; color: white; padding: 12px 24px; border: none; border-radius: 8px; font-size: 16px; cursor: pointer; font-weight: 600;">
            📁 ファイルを選択
        </button>
        <div id="fileInfo_{key}" style="margin-top: 15px; color: #94a3b8;"></div>
        <div id="uploadProgress_{key}" style="margin-top: 15px; display: none;">
            <div style="background-color: #1e293b; border-radius: 10px; height: 30px; overflow: hidden;">
                <div id="progressBar_{key}" style="background-color: #10b981; height: 100%; width: 0%; transition: width 0.3s; display: flex; align-items: center; justify-content: center; color: white; font-weight: 600;"></div>
            </div>
            <p id="uploadStatus_{key}" style="margin-top: 10px; color: #10b981; font-weight: 600;"></p>
        </div>
    </div>
    
    <script>
    (function() {{
        let selectedFile_{key} = null;
        const SIGNED_URL = {signed_url_js};
        const PUBLIC_URL = {public_url_js};
        
        // Signed URLが既にある場合、自動的にアップロード開始
        if (SIGNED_URL && PUBLIC_URL) {{
            // ローカルストレージからファイル情報を取得
            const fileInfo = localStorage.getItem('pending_upload_{key}');
            if (fileInfo) {{
                document.getElementById('uploadProgress_{key}').style.display = 'block';
                document.getElementById('uploadStatus_{key}').textContent = 'アップロード準備中...';
                setTimeout(() => {{
                    restoreAndUpload_{key}(SIGNED_URL, PUBLIC_URL);
                }}, 100);
            }}
        }}
        
        window.handleFileSelect_{key} = function(event) {{
            selectedFile_{key} = event.target.files[0];
            if (selectedFile_{key}) {{
                const sizeMB = (selectedFile_{key}.size / 1024 / 1024).toFixed(2);
                document.getElementById('fileInfo_{key}').innerHTML = 
                    `<p style="color: #fbbf24; font-weight: 600;">✅ ${{selectedFile_{key}.name}} (${{sizeMB}} MB)</p>
                     <button onclick="startUpload_{key}()" style="background-color: #10b981; color: white; padding: 10px 20px; border: none; border-radius: 8px; margin-top: 10px; cursor: pointer; font-weight: 600;">🚀 アップロード開始</button>`;
            }}
        }};
        
        window.startUpload_{key} = function() {{
            if (!selectedFile_{key}) {{
                alert('ファイルを選択してください');
                return;
            }}
            
            // ファイル情報をローカルストレージに保存
            const fileInfo = {{
                name: selectedFile_{key}.name,
                size: selectedFile_{key}.size,
                type: selectedFile_{key}.type
            }};
            localStorage.setItem('pending_upload_{key}', JSON.stringify(fileInfo));
            
            // Streamlitに情報を送信（ページリロード用）
            const fileExtension = selectedFile_{key}.name.split('.').pop();
            const mimeTypes = {{
                'mp4': 'video/mp4',
                'mov': 'video/quicktime',
                'avi': 'video/x-msvideo',
                'mp3': 'audio/mpeg',
                'wav': 'audio/wav',
                'm4a': 'audio/mp4'
            }};
            const contentType = mimeTypes[fileExtension.toLowerCase()] || 'application/octet-stream';
            
            window.parent.postMessage({{
                type: 'streamlit:setComponentValue',
                key: '{key}',
                value: JSON.stringify({{
                    action: 'request_signed_url',
                    filename: selectedFile_{key}.name,
                    contentType: contentType,
                    size: selectedFile_{key}.size
                }})
            }}, '*');
            
            document.getElementById('uploadProgress_{key}').style.display = 'block';
            document.getElementById('uploadStatus_{key}').textContent = 'Signed URLを取得中...';
        }};
        
        function restoreAndUpload_{key}(signedUrl, publicUrl) {{
            const fileInfo = JSON.parse(localStorage.getItem('pending_upload_{key}'));
            if (!fileInfo) return;
            
            // ファイル選択ダイアログを再度開く
            const input = document.getElementById('fileInput_{key}');
            input.onchange = function(e) {{
                const file = e.target.files[0];
                if (file && file.name === fileInfo.name) {{
                    uploadFile_{key}(file, signedUrl, publicUrl);
                }} else {{
                    document.getElementById('uploadStatus_{key}').textContent = '❌ ファイルが一致しません';
                    document.getElementById('uploadStatus_{key}').style.color = '#ef4444';
                    localStorage.removeItem('pending_upload_{key}');
                }}
            }};
            input.click();
        }}
        
        function uploadFile_{key}(file, signedUrl, publicUrl) {{
            const xhr = new XMLHttpRequest();
            
            xhr.upload.addEventListener('progress', (e) => {{
                if (e.lengthComputable) {{
                    const percentComplete = Math.round((e.loaded / e.total) * 100);
                    document.getElementById('progressBar_{key}').style.width = percentComplete + '%';
                    document.getElementById('progressBar_{key}').textContent = percentComplete + '%';
                    document.getElementById('uploadStatus_{key}').textContent = `アップロード中... ${{percentComplete}}%`;
                }}
            }});
            
            xhr.addEventListener('load', () => {{
                if (xhr.status === 200) {{
                    document.getElementById('uploadStatus_{key}').textContent = '✅ アップロード完了！';
                    localStorage.removeItem('pending_upload_{key}');
                    
                    // Streamlitに完了を通知
                    window.parent.postMessage({{
                        type: 'streamlit:setComponentValue',
                        key: '{key}',
                        value: JSON.stringify({{
                            action: 'upload_complete',
                            url: publicUrl,
                            filename: file.name
                        }})
                    }}, '*');
                }} else {{
                    document.getElementById('uploadStatus_{key}').textContent = '❌ アップロード失敗: ' + xhr.status;
                    document.getElementById('uploadStatus_{key}').style.color = '#ef4444';
                    localStorage.removeItem('pending_upload_{key}');
                }}
            }});
            
            xhr.addEventListener('error', () => {{
                document.getElementById('uploadStatus_{key}').textContent = '❌ ネットワークエラー';
                document.getElementById('uploadStatus_{key}').style.color = '#ef4444';
                localStorage.removeItem('pending_upload_{key}');
            }});
            
            const fileExtension = file.name.split('.').pop();
            const mimeTypes = {{
                'mp4': 'video/mp4',
                'mov': 'video/quicktime',
                'avi': 'video/x-msvideo',
                'mp3': 'audio/mpeg',
                'wav': 'audio/wav',
                'm4a': 'audio/mp4'
            }};
            const contentType = mimeTypes[fileExtension.toLowerCase()] || 'application/octet-stream';
            
            xhr.open('PUT', signedUrl, true);
            xhr.setRequestHeader('Content-Type', contentType);
            xhr.send(file);
        }}
    }})();
    </script>
    """
    
    # HTMLコンポーネントを表示
    component_value = components.html(html_code, height=250)
    
    return component_value

# --- 3. デザインCSS ---
st.markdown("""
    <style>
    .stApp { 
        background: linear-gradient(135deg, #a8daff 0%, #7cb3e9 50%, #4a90e2 100%) !important;
        color: #1e3a5f !important;
    }
    h1 { margin-bottom: 5px !important; text-align: center; color: #1e3a5f !important; }
    .sub-text { margin-bottom: 25px !important; color: #2c5282; font-size: 1.1rem; text-align: center; }
    
    /* 入力フィールドのスタイル - 白い背景、暗い文字 */
    input, textarea, 
    div[data-baseweb="input"] input,
    div[data-baseweb="textarea"] textarea,
    input[type="text"], input[type="email"], input[type="number"],
    .stTextInput input, .stTextArea textarea,
    .stTextInput > div > div > input,
    .stTextArea > div > div > textarea {
        background-color: #ffffff !important; 
        color: #1e3a5f !important;
        caret-color: #1e3a5f !important;
        -webkit-text-fill-color: #1e3a5f !important;
        border: 2px solid #7cb3e9 !important; 
        border-radius: 10px !important;
        font-weight: 500 !important;
    }
    
    /* より具体的な入力フィールドの色設定 */
    [data-testid="stTextInput"] input,
    [data-testid="stTextArea"] textarea {
        color: #1e3a5f !important;
        -webkit-text-fill-color: #1e3a5f !important;
        background-color: #ffffff !important;
    }
    
    /* フォーカス時のスタイル */
    [data-testid="stTextInput"] input:focus,
    [data-testid="stTextArea"] textarea:focus,
    input:focus, textarea:focus {
        color: #1e3a5f !important;
        -webkit-text-fill-color: #1e3a5f !important;
        background-color: #ffffff !important;
        border-color: #4a90e2 !important;
    }
    
    /* オートフィル時のスタイル */
    input:-webkit-autofill,
    input:-webkit-autofill:hover,
    input:-webkit-autofill:focus,
    textarea:-webkit-autofill,
    textarea:-webkit-autofill:hover,
    textarea:-webkit-autofill:focus {
        -webkit-text-fill-color: #1e3a5f !important;
        -webkit-box-shadow: 0 0 0px 1000px #ffffff inset !important;
        background-color: #ffffff !important;
        color: #1e3a5f !important;
    }
    
    /* プレースホルダーのスタイル */
    input::placeholder, textarea::placeholder {
        color: #94a3b8 !important;
        opacity: 1 !important;
    }
    
    /* セレクトボックスのスタイル */
    div[data-baseweb="select"] > div {
        background-color: #ffffff !important;
        color: #1e3a5f !important;
        border: 2px solid #7cb3e9 !important;
    }
    
    /* ラジオボタンのラベル */
    div[role="radiogroup"] label {
        color: #1e3a5f !important;
    }
    
    /* File Uploaderのスタイル */
    [data-testid="stFileUploader"] label,
    [data-testid="stFileUploader"] > div {
        color: #1e3a5f !important;
    }
    
    div.stButton > button, button[kind="primary"], button[type="submit"] {
        background-color: #10b981 !important; color: #ffffff !important;
        font-weight: 800 !important; border-radius: 10px !important;
        height: 50px !important; width: 100% !important; border: none !important;
    }
    button[kind="secondary"] {
        background-color: #e6f4ff !important; color: #1e3a5f !important;
        border: 2px solid #7cb3e9 !important;
    }
    .post-card {
        background-color: #ffffff; padding: 20px; border-radius: 15px;
        border: 1px solid #b3d9ff; margin-bottom: 15px;
    }
    .stTabs [data-baseweb="tab-list"] { background-color: #ffffff; padding: 5px; border-radius: 10px; }
    .stTabs [data-baseweb="tab"][aria-selected="true"] { color: #10b981 !important; background-color: #e6f4ff !important; }
    
    /* Expanderのスタイル */
    div[data-testid="stExpander"] {
        background-color: #ffffff !important;
        border: 1px solid #7cb3e9 !important;
        border-radius: 10px !important;
    }
    div[data-testid="stExpander"] summary {
        color: #1e3a5f !important;
        font-weight: 600 !important;
    }
    </style>
    """, unsafe_allow_html=True)

# --- 4. ログイン管理 ---
if 'user_name' not in st.session_state:
    st.session_state.user_name = ""

# ページロードカウント（スクロール制御用）
if 'page_load_count' not in st.session_state:
    st.session_state.page_load_count = 0

# ファイルアップローダーのリセット用キー
if 'photo_uploader_key' not in st.session_state:
    st.session_state.photo_uploader_key = 0
if 'memory_uploader_key' not in st.session_state:
    st.session_state.memory_uploader_key = 0
if 'music_form_key' not in st.session_state:
    st.session_state.music_form_key = 0
if 'message_form_key' not in st.session_state:
    st.session_state.message_form_key = 0
if 'uploaded_file_url' not in st.session_state:
    st.session_state.uploaded_file_url = {}

if not st.session_state.user_name:
    st.markdown("<div style='height: 20px;'></div>", unsafe_allow_html=True)
    st.markdown("<h1>🎧 愛波恒平 Memorial</h1>", unsafe_allow_html=True)
    st.markdown("<p class='sub-text'>KOHEI AIBAを愛する仲間たちのための場所です。</p>", unsafe_allow_html=True)
    
    # スライドショー
    slideshow_html = """
    <style>
        .slideshow-container {
            position: relative;
            max-width: 600px;
            height: 400px;
            margin: 20px auto;
            border-radius: 12px;
            overflow: hidden;
            box-shadow: 0 4px 20px rgba(16, 185, 129, 0.3);
            background-color: #0f172a;
            display: flex;
            align-items: center;
            justify-content: center;
        }
        
        .slide {
            display: none;
            width: 100%;
            height: 100%;
            animation: fadeIn 1s;
            position: absolute;
            top: 0;
            left: 0;
        }
        
        .slide.active {
            display: flex;
            align-items: center;
            justify-content: center;
        }
        
        .slide img {
            max-width: 100%;
            max-height: 100%;
            width: auto;
            height: auto;
            object-fit: contain;
            display: block;
        }
        
        @keyframes fadeIn {
            from { opacity: 0; }
            to { opacity: 1; }
        }
    </style>
    
    <div class="slideshow-container">
        <div class="slide active">
            <img src="https://storage.googleapis.com/aiba-memorial-pthotos/images/login1.jpg" alt="KOHEI AIBA 1">
        </div>
        <div class="slide">
            <img src="https://storage.googleapis.com/aiba-memorial-pthotos/images/login2.jpg" alt="KOHEI AIBA 2">
        </div>
        <div class="slide">
            <img src="https://storage.googleapis.com/aiba-memorial-pthotos/images/login3.jpg" alt="KOHEI AIBA 3">
        </div>
    </div>
    
    <script>
        let currentSlide = 0;
        const slides = document.querySelectorAll('.slide');
        
        function showNextSlide() {
            slides[currentSlide].classList.remove('active');
            currentSlide = (currentSlide + 1) % slides.length;
            slides[currentSlide].classList.add('active');
        }
        
        // 4秒ごとに次のスライドを表示
        setInterval(showNextSlide, 4000);
    </script>
    """
    
    components.html(slideshow_html, height=450)
    
    with st.form("login_form"):
        name_input = st.text_input("お名前を入力してください (Your Name)", placeholder="Enter your name here...")
        submitted = st.form_submit_button("ENTER SITE", type="primary")
        
        if submitted and name_input:
            st.session_state.user_name = name_input
            st.session_state.page_load_count = 0  # ログイン時はスクロール実行
            st.rerun()
    st.stop()

# --- 5. メインレイアウト ---
# ページトップのアンカー
st.markdown('<div id="page-top"></div>', unsafe_allow_html=True)

st.markdown(f"<h3 style='text-align: center; margin-bottom: 5px;'>🎧 愛波恒平 Memorial</h3>", unsafe_allow_html=True)
st.markdown(f"<p style='text-align: center; color: #10b981; font-size: 0.9rem;'>User: {st.session_state.user_name}</p>", unsafe_allow_html=True)

# ログイン直後のスクロール制御（components.htmlを使用）
if st.session_state.page_load_count == 0:
    scroll_html = """
    <script>
    // ページロード直後にスクロールを最上部に固定
    (function() {
        console.log('[SCROLL] Forcing page to top');
        
        function scrollToTop() {
            try {
                // window スクロール
                if (window.scrollTo) window.scrollTo(0, 0);
                if (window.parent && window.parent.scrollTo) {
                    try { window.parent.scrollTo(0, 0); } catch(e) {}
                }
                
                // document 要素
                if (document.documentElement) {
                    document.documentElement.scrollTop = 0;
                }
                if (document.body) {
                    document.body.scrollTop = 0;
                }
                
                // Streamlit特有の要素
                var main = document.querySelector('section.main');
                if (main) main.scrollTop = 0;
                
                var app = document.querySelector('[data-testid="stAppViewContainer"]');
                if (app) app.scrollTop = 0;
                
                var stApp = document.querySelector('[data-testid="stApp"]');
                if (stApp) stApp.scrollTop = 0;
            } catch(e) {
                // エラーは無視（nullチェック済み）
            }
        }
        
        // 即座に実行
        scrollToTop();
        
        // 繰り返し実行（5秒間）
        var count = 0;
        var interval = setInterval(function() {
            scrollToTop();
            count++;
            if (count > 500) {
                clearInterval(interval);
                console.log('[SCROLL] Complete after', count, 'attempts');
            }
        }, 10);
    })();
    </script>
    """
    components.html(scroll_html, height=0)
    st.session_state.page_load_count += 1

tab_info, tab_photo, tab_music, tab_memory, tab_live, tab_message, tab_fund = st.tabs(["Info", "Photo/Story", "Music", "Memory", "Live", "Message", "Fund"])

# --- 5-1. Info ---
with tab_info:
    st.header("Event Info")
    
    # 訃報
    st.markdown("""
    ### 💙 Celebration Of Kohei
    
    2025年2月10日、愛波恒平が New York にて旅立ちました。
    
    2月25日にNYで葬儀・埋葬を行ってまいりました。
    
    日本でも恒平と親しかった皆さん、恒平が大切にしていた皆さんと、改めて集い、恒平のことを語り合う時間を持てたらと思い、家族・仲間で会を企画しています。
    
    この会は「お葬式」ではなく、**恒平の人生をみんなで祝い、思い出を持ち寄り、つながり直すための時間**です。
    
    恒平が愛した、ワイワイとあたたかい雰囲気の中で、気軽にお越しいただけたら嬉しいです。
    
    **FROM RIEKO AIBA**
    """)
    
    st.divider()
    
    # 開催概要
    st.markdown("""
    ### 🗓️ お別れ会【愛波恒平 Memorial Party】
    
    """)
    
    st.info("""
    **📍 日時:** 2025年3月21日（土）16:30 - 20:00（出入り自由）  
    ご都合のよい時間にお立ち寄りください。途中退席も問題ございません。
    
    **📍 場所:** [Bar Dunbar](https://maps.app.goo.gl/z5aMtCkMAv2z1wre9)  
    目黒駅から徒歩約5〜6分
    
    **📍 会費:** 5,000円程度（人数次第で変動。決まり次第ご連絡します）
    """)
    
    st.markdown("""
    - 軽食とドリンクをご用意する、カジュアルな会です。
    - お食事や飲み物はお持ち込みいただいても構いません。
    - バーでは本格的なドリンクやカクテルもご注文いただけます。
    - ご家族連れも歓迎です。少し顔を出すだけでも、ゆっくり語り合うのでも、自由にお過ごしください。
    - 参加人数が多くなった場合は、混雑を避けるため、出入りしながら近隣のお店へ移動していただくこともあるかもしれません。
    """)
    
    st.divider()
    
    # 参加登録
    st.markdown("""
    ### ✅ 参加登録（アンケート）
    
    人数把握と準備のため、以下のフォームにご回答ください。
    """)
    
    st.link_button("📝 参加フォームに回答する", "https://forms.gle/7JngRqNhoskGkVoL6", use_container_width=True)
    st.caption("なるべく早めのご回答にご協力ください。")
    
    st.divider()
    
    # 写真・動画・メッセージの共有（このサイトの使い方と統合）
    st.markdown("""
    ### 📷 写真・動画・メッセージの共有
    
    恒平の人生をお祝いしてみんなで共有するため、**このサイト**を用意しました。
    
    恒平との思い出の写真、動画、音楽、エピソード、メッセージなどをお寄せいただければ幸いです。
    
    この会に参加できない方からの投稿も歓迎しております。**当日、会場で共有させていただく予定です。**
    
    ---
    
    #### 📢 各タブの使い方
    
    """)
    
    # Photo/Story タブへのリンク
    col1, col2 = st.columns([4, 1])
    with col1:
        st.markdown("""
        **📸 Photo/Story** - 思い出の写真とエピソードを投稿  
        あなたの思い出を写真や文章で共有してください。写真なしでもOK！
        """)
    with col2:
        st.markdown("<div style='height: 40px;'></div>", unsafe_allow_html=True)
    
    # Music タブへのリンク
    col1, col2 = st.columns([4, 1])
    with col1:
        st.markdown("""
        **🎵 Music** - KOHEI AIBAが愛した音楽、あなたとの思い出の曲  
        曲とエピソードを投稿して、みんなで共有しましょう。
        """)
    with col2:
        st.markdown("<div style='height: 40px;'></div>", unsafe_allow_html=True)
    
    # Memory タブへのリンク
    col1, col2 = st.columns([4, 1])
    with col1:
        st.markdown("""
        **🎬 Memory** - KOHEI AIBAの映像・音源を共有  
        - **YouTube URL** ⭐推奨: YouTube動画（限定公開OK）を埋め込み再生
        - **Google Drive URL**: 大容量ファイルはリンクから視聴
        - **ファイルアップロード**: 小容量ファイル（200MB以下推奨）を直接アップロード
        
        💡 **大容量動画の推奨方法**: YouTube（限定公開）にアップロード → URLを投稿  
        （Streamlit上で直接再生できます）
        """)
    with col2:
        st.markdown("<div style='height: 40px;'></div>", unsafe_allow_html=True)
    
    # Live タブへのリンク
    col1, col2 = st.columns([4, 1])
    with col1:
        st.markdown("""
        **📺 Live** - イベント当日のライブ配信  
        当日来られない方も、会場の様子をリアルタイムで視聴できます。
        """)
    with col2:
        st.markdown("<div style='height: 40px;'></div>", unsafe_allow_html=True)
    
    # Message タブへのリンク
    col1, col2 = st.columns([4, 1])
    with col1:
        st.markdown("""
        **💌 Message** - 恒平へのメッセージ  
        KOHEI AIBAへ、動画やメッセージを送ることができます。
        """)
    with col2:
        st.markdown("<div style='height: 40px;'></div>", unsafe_allow_html=True)
    
    st.markdown("""
    ---
    
    ### 💡 みんなで参加しよう！
    
    **👍 Likeボタンで共感を伝えよう！**  
    写真、ストーリー、音楽の投稿に「いいね」をつけて、みんなの思い出を応援してください。
    
    一緒にKOHEI AIBAの記憶を祝いましょう 🎧✨
    """)
    
    st.divider()
    
    # 投稿統計
    st.header("📊 投稿統計")
    
    # 各カテゴリーのデータを取得
    photo_df = get_data("Photo")
    music_df = get_data("Music")
    memory_df = get_data("Memory")
    message_df = get_data("Message")
    
    # 統計情報を表示
    col1, col2, col3, col4 = st.columns(4)
    
    with col1:
        photo_count = len(photo_df) if not photo_df.empty else 0
        photo_likes = int(photo_df['likes'].sum()) if not photo_df.empty and 'likes' in photo_df.columns else 0
        st.metric("📸 Photo/Story", f"{photo_count}件", f"👍 {photo_likes}")
    
    with col2:
        music_count = len(music_df) if not music_df.empty else 0
        music_likes = int(music_df['likes'].sum()) if not music_df.empty and 'likes' in music_df.columns else 0
        st.metric("🎵 Music", f"{music_count}件", f"👍 {music_likes}")
    
    with col3:
        memory_count = len(memory_df) if not memory_df.empty else 0
        memory_likes = int(memory_df['likes'].sum()) if not memory_df.empty and 'likes' in memory_df.columns else 0
        st.metric("🎬 Memory", f"{memory_count}件", f"👍 {memory_likes}")
    
    with col4:
        message_count = len(message_df) if not message_df.empty else 0
        st.metric("💌 Message", f"{message_count}件", "")
    
    st.caption("みんなの投稿で、KOHEI AIBAの記憶がより豊かになります ✨")
    
    st.divider()
    
    # 愛波 Family Fund について
    st.markdown("""
    ### 💝 愛波 Family Fund について
    
    今回はお香典という形ではいただいておりません。
    
    もしお気持ちをいただける場合は、恒平が愛した子どもたち2人のためのドネーションサイト（**愛波 Family Fund**）がございます。
    
    このファンドは、KOHEI AIBAが愛し、大切にしていた子供たちの未来のために使われます。  
    皆様の温かいご支援をお待ちしています。
    
    **🔗 ドネーションは、Fundタブからお願いいたします。**
    """)
    
    st.divider()
    
    # 納骨について
    st.markdown("""
    ### 🌸 納骨について
    
    お別れ会の後、**3月23日（月）に家族で富士山麓の富士霊園に納骨**を予定しています。
    """)
    
    st.divider()
    
    # 当日参加できない方へ
    st.markdown("""
    ### 📺 当日参加できない方へ
    
    当日現地に来られない方も、**Liveタブ**からリアルタイムで会場の様子をご覧いただけます。  
    世界中どこからでも、KOHEI AIBAへの想いを共有しましょう 🌍✨
    """)
    
    st.divider()
    
    # 投稿内容の修正・削除について
    st.markdown("""
    ### ⚠️ 投稿内容の修正・削除について
    
    投稿した内容の修正や削除を行いたい場合は、直接、運営サイドにご連絡ください。
    """)
    
    # 技術情報（管理者向け）
    with st.expander("🔧 技術情報（管理者向け）"):
        if USE_GCS:
            try:
                # バケット情報を再取得
                gcs_bucket.reload()
                bucket_location = gcs_bucket.location
                
                if bucket_location is None or bucket_location == "None":
                    st.warning("⚠️ バケットリージョン情報を取得できませんでした。")
                    st.info("**考えられる原因:**")
                    st.markdown("""
                    - バケットが存在しない、または権限が不足している
                    - マルチリージョンバケット（自動的に最適なリージョンを選択）
                    - バケット設定の問題
                    
                    **確認方法:**
                    1. Google Cloud Consoleにアクセス
                    2. Cloud Storage → バケット一覧を確認
                    3. バケット名をクリック → 「構成」タブでリージョンを確認
                    """)
                else:
                    st.info(f"📍 GCS バケットリージョン: **{bucket_location}**")
                    
                    # リージョン別の推奨メッセージ
                    location_upper = bucket_location.upper()
                    if location_upper in ['ASIA-NORTHEAST1', 'ASIA-NORTHEAST2']:
                        st.success("✅ 日本リージョンです。アップロード速度は最適です。")
                    elif location_upper.startswith('ASIA'):
                        st.warning("⚠️ アジアリージョンですが、日本ではありません。日本リージョン（ASIA-NORTHEAST1）への移行を検討してください。")
                    elif location_upper.startswith('US'):
                        st.error("❌ 米国リージョンです。日本からのアップロードが遅い可能性があります。日本リージョン（ASIA-NORTHEAST1）への移行を強く推奨します。")
                    elif location_upper.startswith('EU'):
                        st.error("❌ 欧州リージョンです。日本からのアップロードが遅い可能性があります。日本リージョン（ASIA-NORTHEAST1）への移行を強く推奨します。")
                    else:
                        st.info(f"リージョン: {bucket_location}")
                    
                    st.caption("💡 リージョンが日本以外の場合、新しい日本リージョンのバケットを作成することでアップロード速度が大幅に改善されます。")
            except Exception as e:
                st.error(f"❌ バケット情報の取得エラー: {str(e)}")
                st.info("Google Cloud Consoleで直接バケット設定を確認してください。")
        else:
            st.info("GCSは使用されていません。")

# --- 5-2. Memory ---
with tab_memory:
    st.header("🎬 Memory - 彼の記憶")
    
    # アップロード完了メッセージを表示
    if 'upload_message' in st.session_state:
        msg_type, msg_text = st.session_state.upload_message
        if msg_type == "success":
            st.success(msg_text)
        else:
            st.warning(msg_text)
        del st.session_state.upload_message
    
    memory_df = get_data("Memory")
    
    st.subheader("📤 映像・音源をアップロード")
    # カテゴリー選択
    category = st.selectbox(
        "カテゴリー",
        ["映像", "音源"],
        key=f"memory_category_{st.session_state.memory_uploader_key}"
    )
    
    # 説明
    mem_description = st.text_area(
        "説明・タイトル",
        placeholder="例：2023年のライブ映像、スタジオ録音など",
        key=f"memory_description_{st.session_state.memory_uploader_key}"
    )
    
    # YouTube/Google Drive URLまたはファイルアップロード
    st.write("**アップロード方法を選択**")
    upload_type = st.radio(
        "アップロード方法", 
        ["YouTube URL", "Google Drive URL", "ファイルをアップロード (小容量のみ)"], 
        horizontal=True, 
        label_visibility="collapsed",
        key=f"memory_upload_type_{st.session_state.memory_uploader_key}"
    )
    
    youtube_url_mem = ""
    gdrive_url_mem = ""
    file_url_mem = ""
    
    if upload_type == "YouTube URL":
        youtube_url_mem = st.text_input(
            "YouTube URL",
            placeholder="https://www.youtube.com/watch?v=...",
            key=f"memory_youtube_{st.session_state.memory_uploader_key}"
        )
        if youtube_url_mem:
            try:
                st.video(youtube_url_mem)
            except:
                st.error("有効なYouTube URLを入力してください")
    
    elif upload_type == "Google Drive URL":
        st.info("💡 **大容量ファイル（1GB以上）はGoogle Driveをご利用ください**")
        st.warning("⚠️ **注意**: 動画は埋め込みではなく、リンクからGoogle Driveで再生されます。埋め込み再生したい場合は、**YouTube（限定公開）**のご利用を推奨します。")
        with st.expander("📖 Google Driveでの共有方法"):
            st.markdown("""
            1. Google Driveにファイルをアップロード
            2. ファイルを右クリック → 「共有」
            3. 「一般的なアクセス」を「リンクを知っている全員」に変更
            4. 「リンクをコピー」をクリック
            5. 下の入力欄にURLを貼り付け
            
            **または、YouTube（限定公開）を推奨**:
            - YouTube Studioで「限定公開」としてアップロード
            - URLをコピーして「YouTube URL」を選択して投稿
            - Streamlit上で直接再生できます
            """)
        
        gdrive_url_mem = st.text_input(
            "Google Drive 共有リンク",
            placeholder="https://drive.google.com/file/d/...",
            key=f"memory_gdrive_{st.session_state.memory_uploader_key}"
        )
        
        if gdrive_url_mem:
            st.success("✅ Google DriveのURLを受け付けました")
            st.caption("投稿後、リンクボタンからGoogle Driveで視聴できます")
    
    else:  # ファイルをアップロード
        st.warning("⚠️ ファイルアップロードは200MB以下のファイル推奨です。大容量ファイルはGoogle Driveをご利用ください。")
        
        # 標準ファイルアップローダー
        uploaded_file_mem = st.file_uploader(
            "ファイルを選択",
            type=['mp4', 'mov', 'avi', 'mp3', 'wav', 'm4a'],
            key=f"memory_file_{st.session_state.memory_uploader_key}"
        )
        
        if uploaded_file_mem:
            file_size_mb = uploaded_file_mem.size / 1024 / 1024
            st.info(f"📁 {uploaded_file_mem.name} ({file_size_mb:.2f} MB)")
            
            if file_size_mb > 200:
                st.error("⚠️ 200MBを超えるファイルはGoogle Driveのご利用を推奨します")
    
    st.info("📌 注意：アップロード後、Info画面に移動することがありますが、問題なくアップロードできていることが多いです。Memoryタブで投稿前のアップロードを確認して、投稿するボタンを押してください。")
    
    if st.button("投稿する", key="post_memory", type="primary"):
        if mem_description:
            # YouTube/Google Drive URLを確認
            if upload_type == "YouTube URL" and not youtube_url_mem:
                st.error("⚠️ YouTube URLを入力してください")
                st.stop()
            elif upload_type == "Google Drive URL" and not gdrive_url_mem:
                st.error("⚠️ Google Drive URLを入力してください")
                st.stop()
            elif upload_type == "ファイルをアップロード (小容量のみ)":
                # ファイルアップロード処理
                if 'uploaded_file_mem' in locals() and uploaded_file_mem and USE_GCS:
                    with st.spinner("ファイルをアップロード中..."):
                        # ファイル拡張子を取得
                        file_extension = uploaded_file_mem.name.split('.')[-1]
                        unique_filename = f"memory/{uuid.uuid4()}.{file_extension}"
                        
                        # GCSにアップロード
                        blob = gcs_bucket.blob(unique_filename)
                        blob.upload_from_file(uploaded_file_mem, content_type=uploaded_file_mem.type)
                        file_url_mem = blob.public_url
                        st.success("✅ アップロード完了")
                elif not USE_GCS:
                    st.error("⚠️ ファイルアップロード機能が利用できません")
                    st.stop()
                elif 'uploaded_file_mem' not in locals() or not uploaded_file_mem:
                    st.error("⚠️ ファイルを選択してください")
                    st.stop()
            
            # URLのクリーンアップ
            youtube_url_cleaned = clean_youtube_url(youtube_url_mem) if youtube_url_mem else ""
            gdrive_url_cleaned = gdrive_url_mem.strip() if gdrive_url_mem else ""
            
            # データ保存
            new_row = pd.DataFrame([{
                "user": st.session_state.user_name,
                "category": category,
                "description": mem_description,
                "youtube_url": youtube_url_cleaned,
                "gdrive_url": gdrive_url_cleaned,
                "file_url": file_url_mem if file_url_mem else "",
                "likes": 0
            }])
            
            updated_df = pd.concat([memory_df, new_row], ignore_index=True)
            
            st.session_state.memory_uploader_key += 1
            
            # 保存
            if save_data("Memory", updated_df):
                st.session_state.upload_message = ("success", "投稿が完了しました！")
            else:
                st.session_state.upload_message = ("warning", "投稿の保存に失敗しました")
            
            st.cache_data.clear()
            st.rerun()
        else:
            st.error("説明を入力してください")
    
    st.divider()
    
    filter_category = st.selectbox(
        "カテゴリーで絞り込み",
        ["すべて", "映像", "音源"]
    )
    
    if not memory_df.empty:
        # フィルタリング
        if filter_category != "すべて":
            filtered_df = memory_df[memory_df['category'] == filter_category]
        else:
            filtered_df = memory_df
        
        if not filtered_df.empty:
            for idx, row in filtered_df.iloc[::-1].iterrows():
                with st.container():
                    # カテゴリーとLike
                    col_title, col_like = st.columns([5, 1])
                    with col_title:
                        category_icon = {
                            "映像": "🎬",
                            "音源": "🎵"
                        }
                        icon = category_icon.get(row.get('category', ''), '📁')
                        st.markdown(f"### {icon} {row.get('category', '不明')}")
                        st.caption(f"投稿者: {row.get('user', '不明')}")
                    with col_like:
                        if st.button(f"👍 {row.get('likes', 0)}", key=f"like_memory_{idx}", type="secondary"):
                            memory_df.loc[idx, 'likes'] = int(row.get('likes', 0)) + 1
                            
                            if save_data("Memory", memory_df):
                                pass
                            
                            st.rerun()
                    
                    # 説明
                    st.write(row.get('description', ''))
                    
                    # YouTube動画、Google Drive、またはファイル表示
                    youtube_url = row.get('youtube_url', '')
                    gdrive_url = row.get('gdrive_url', '')
                    # file_url と file_uri の両方に対応
                    file_url = row.get('file_url', '') or row.get('file_uri', '')
                    
                    # YouTube動画を優先表示
                    if pd.notna(youtube_url) and str(youtube_url).strip() != '':
                        st.video(str(youtube_url))
                    
                    # Google Drive動画を表示
                    elif pd.notna(gdrive_url) and str(gdrive_url).strip() != '':
                        gdrive_url_str = str(gdrive_url)
                        
                        # 大容量ファイルは埋め込みではなく、リンクボタンで対応
                        st.info("📺 Google Driveで動画を再生")
                        st.link_button("▶️ Google Driveで視聴する", gdrive_url_str, use_container_width=True)
                    
                    # アップロードされたファイルを表示
                    elif pd.notna(file_url) and str(file_url).strip() != '':
                        # ファイルタイプで表示方法を変える
                        file_url_str = str(file_url)
                        
                        try:
                            if any(ext in file_url_str.lower() for ext in ['.mp4', '.mov', '.avi']):
                                # Streamlitのst.video()を使用
                                st.video(file_url_str)
                            elif any(ext in file_url_str.lower() for ext in ['.mp3', '.wav', '.m4a']):
                                st.audio(file_url_str)
                            else:
                                st.link_button("📥 ファイルをダウンロード", file_url_str)
                        except Exception as e:
                            st.error(f"再生エラー: {str(e)}")
                            st.write("URLを直接開く:")
                            st.code(file_url_str)
                            st.link_button("📥 ファイルをダウンロード", file_url_str)
                    
                    st.divider()
        else:
            st.info(f"「{filter_category}」の投稿はまだありません")
    else:
        st.info("まだ投稿がありません。最初の記憶を共有しましょう")

# --- 5-3. Photo/Story ---
with tab_photo:
    st.subheader("📸 Share Photos & Stories")
    
    # アップロード完了メッセージを表示
    if 'upload_message' in st.session_state:
        msg_type, msg_text = st.session_state.upload_message
        if msg_type == "success":
            st.success(msg_text)
        else:
            st.warning(msg_text)
        del st.session_state.upload_message
    
    photo_df = get_data("Photo")
    
    st.subheader("📝 思い出を投稿する (写真なしでもOK)")
    # ファイル選択ボタンをここに配置
    uploaded_file = st.file_uploader(
        "写真をえらぶ (Select Photo)", 
        type=['jpg', 'png', 'jpeg'],
        key=f"photo_uploader_{st.session_state.photo_uploader_key}"
    )
    
    # アップロードされた画像のプレビュー
    if uploaded_file is not None:
        # EXIF情報を元に正しい向きで表示
        try:
            from PIL import ImageOps
            preview_image = Image.open(uploaded_file)
            preview_image = ImageOps.exif_transpose(preview_image)
            st.image(preview_image, caption="アップロード予定の写真", use_container_width=True)
            uploaded_file.seek(0)  # ファイルポインタを先頭に戻す
        except:
            st.image(uploaded_file, caption="アップロード予定の写真", use_container_width=True)
            uploaded_file.seek(0)
        
        if USE_GCS:
            st.success("✅ 画像は永続的に保存されます")
        else:
            st.info("📌 注意：現在、画像はプレビューのみで、投稿後は保存されません。コメントのみが保存されます。")
    
    p_comment = st.text_area(
        "KOHEI AIBAとの思い出 (Story)",
        key=f"photo_comment_{st.session_state.photo_uploader_key}"
    )
    
    st.info("📌 注意：アップロード後、Info画面に移動することがありますが、問題なくアップロードできていることが多いです。Photo/Storyタブで投稿前のアップロードを確認して、Post to Galleryしてください。")
    
    if st.button("Post to Gallery", type="primary"):
        if p_comment or uploaded_file:
            # 画像をGCSにアップロード
            image_url = ""
            if uploaded_file is not None and USE_GCS:
                with st.spinner("画像をアップロード中..."):
                    image_url = upload_image_to_gcs(uploaded_file)
                    if image_url:
                        st.success("画像のアップロードが完了しました！")
                    else:
                        st.warning("画像のアップロードに失敗しました。コメントのみ投稿します。")
            elif uploaded_file is not None and not USE_GCS:
                st.info("📌 GCS未設定のため、画像は保存されません。コメントのみ投稿します。")
            
            new_row = pd.DataFrame([{
                "user": st.session_state.user_name, 
                "image_url": image_url if image_url else "",
                "comment": p_comment if p_comment else "(画像のみ)", 
                "likes": 0
            }])
            
            # データを更新
            updated_df = pd.concat([photo_df, new_row], ignore_index=True)
            
            st.session_state.photo_uploader_key += 1
            
            # 保存を試みる
            save_result = save_data("Photo", updated_df)
            
            # メッセージをsession_stateに保存
            if save_result:
                st.session_state.upload_message = ("success", "投稿が完了しました！")
            else:
                st.session_state.upload_message = ("warning", "投稿の保存に失敗しました")
            
            st.cache_data.clear()
            st.rerun()
        else:
            st.error("エピソードを入力するか、写真を選んでください。")

    st.divider()
    st.write("**投稿一覧**")
    if not photo_df.empty:
        # データをクリーン化（Noneや空文字を除外）
        photo_df_clean = photo_df[photo_df['comment'].notna() & (photo_df['comment'] != '') & (photo_df['comment'] != 'None')]
        
        if not photo_df_clean.empty:
            for idx, row in photo_df_clean.iloc[::-1].iterrows():
                with st.container():
                    # ユーザー名を表示
                    st.markdown(f"**{row.get('user', '不明')}**")
                    
                    # 画像を表示（URLがある場合）
                    image_url = row.get('image_url', '')
                    if image_url and image_url != '' and image_url != 'なし' and image_url != 'あり':
                        try:
                            st.image(image_url, use_container_width=True)
                        except:
                            st.caption("📸 (画像の読み込みに失敗しました)")
                    elif image_url == 'あり':
                        st.caption("📸 (画像あり - 旧データ)")
                    
                    # コメントを表示
                    comment_text = str(row.get('comment', '')).strip()
                    if comment_text and comment_text != 'None' and comment_text != '(画像のみ)':
                        st.write(comment_text)
                    elif comment_text == '(画像のみ)':
                        st.caption("(写真のみの投稿)")
                    else:
                        st.caption("(コメントなし)")
                    
                    # Likeボタン
                    col1, col2 = st.columns([1, 5])
                    with col1:
                        if st.button(f"👍 {row.get('likes', 0)}", key=f"like_photo_{idx}", type="secondary"):
                            # Likesを増やす
                            photo_df.loc[idx, 'likes'] = int(row.get('likes', 0)) + 1
                            
                            if save_data("Photo", photo_df):
                                pass
                            
                            st.rerun()
                    
                    st.divider()
        else:
            st.info("コメント付きの投稿がまだありません。最初の投稿をしてみましょう！")
    else:
        st.info("まだ投稿がありません。最初の投稿をしてみましょう！")

# --- 5-4. Music ---
with tab_music:
    st.subheader("🎵 Memorial Playlist")
    
    # アップロード完了メッセージを表示
    if 'upload_message' in st.session_state:
        msg_type, msg_text = st.session_state.upload_message
        if msg_type == "success":
            st.success(msg_text)
        else:
            st.warning(msg_text)
        del st.session_state.upload_message
    
    music_df = get_data("Music")
    
    # 曲名とアーティスト名の入力
    col1, col2 = st.columns(2)
    with col1:
        song_in = st.text_input(
            "曲名 (Song Title)",
            key=f"music_song_{st.session_state.music_form_key}"
        )
    with col2:
        artist_in = st.text_input(
            "アーティスト (Artist)",
            key=f"music_artist_{st.session_state.music_form_key}"
        )
    
    if song_in and artist_in:
        # 既存の曲を検索（曲名で検索）
        duplicate = music_df[music_df['song'].str.lower() == song_in.lower()] if not music_df.empty else pd.DataFrame()
        
        if not duplicate.empty:
            # 【登録済みの曲の場合】
            st.warning(f"⚠️ すでに「{song_in}」が登録されています！")
            
            # ジャケット画像を表示
            first_entry = duplicate.iloc[0]
            artwork_url = first_entry.get('artwork_url', '')
            
            if pd.notna(artwork_url) and str(artwork_url).strip() != '':
                st.image(str(artwork_url), width=300)
            else:
                st.caption("🎵 (ジャケット画像なし)")
            
            # 既存のエピソードを表示
            st.write("**これまでのエピソード:**")
            for idx, entry in duplicate.iterrows():
                with st.container():
                    st.markdown(f"**{entry['user']}** さんの想い:")
                    st.write(entry.get('comment', ''))
                    st.caption(f"👍 {entry.get('likes', 0)} Likes")
                    st.divider()
            
            # 追加エピソードの投稿
            st.subheader("➕ この曲にあなたのエピソードを追加")
            new_comment = st.text_area(
                "KOHEI AIBAとの思い出を共有してください",
                key=f"music_new_comment_{st.session_state.music_form_key}"
            )
            st.info("📌 注意：入力後、Info画面に移動することがありますが、問題なく入力できていることが多いです。Musicタブで入力内容を確認して、エピソードを追加してください。")
            if st.button("エピソードを追加", key=f"add_episode_{st.session_state.music_form_key}", type="primary"):
                # 既存の曲情報を使って新しいエピソードを追加
                new_row = pd.DataFrame([{
                    "user": st.session_state.user_name,
                    "song": song_in,
                    "artist": artist_in,
                    "youtube_url": "",
                    "artwork_url": first_entry.get('artwork_url', ''),
                    "comment": new_comment if new_comment else "",
                    "likes": 0
                }])
                updated_df = pd.concat([music_df, new_row], ignore_index=True)
                st.session_state.music_form_key += 1
                
                if save_data("Music", updated_df):
                    st.session_state.upload_message = ("success", "エピソードを追加しました！")
                else:
                    st.session_state.upload_message = ("warning", "保存に失敗しました")
                
                st.cache_data.clear()
                st.rerun()
        else:
            # 【未登録の曲の場合】
            st.subheader("🎵 新しい曲をプレイリストに追加")
            # ジャケット画像を自動取得して表示
            with st.spinner("ジャケット画像を検索中..."):
                artwork_url = get_album_artwork(song_in, artist_in)
            
            if artwork_url:
                st.image(artwork_url, caption=f"{song_in} - {artist_in}", width=300)
                st.caption("✅ ジャケット画像が見つかりました")
            else:
                st.caption("ℹ️ ジャケット画像が見つかりませんでした")
            
            # エピソード入力
            m_comment = st.text_area(
                "この曲にまつわるKOHEI AIBAとのエピソード",
                key=f"music_comment_{st.session_state.music_form_key}"
            )
            
            st.info("📌 注意：入力後、Info画面に移動することがありますが、問題なく入力できていることが多いです。Musicタブで入力内容を確認して、プレイリストに追加してください。")
            
            if st.button("プレイリストに追加", key=f"add_new_song_{st.session_state.music_form_key}", type="primary"):
                new_row = pd.DataFrame([{
                    "user": st.session_state.user_name,
                    "song": song_in,
                    "artist": artist_in,
                    "youtube_url": "",
                    "artwork_url": artwork_url if artwork_url else "",
                    "comment": m_comment if m_comment else "",
                    "likes": 0
                }])
                updated_df = pd.concat([music_df, new_row], ignore_index=True)
                st.session_state.music_form_key += 1
                
                if save_data("Music", updated_df):
                    st.session_state.upload_message = ("success", "プレイリストに追加しました！")
                else:
                    st.session_state.upload_message = ("warning", "保存に失敗しました")
                
                st.cache_data.clear()
                st.rerun()
    
    # プレイリスト表示
    st.divider()
    st.write("**🎵 Memorial Playlist**")
    
    if not music_df.empty:
        # 曲ごとにグループ化して表示
        unique_songs = music_df.drop_duplicates(subset=['song'], keep='first')
        
        for idx, song_row in unique_songs.iloc[::-1].iterrows():
            song_name = song_row.get('song', '不明')
            artist_name = song_row.get('artist', '不明')
            
            # この曲の全エピソードを取得
            song_episodes = music_df[music_df['song'] == song_name]
            total_likes = song_episodes.get('likes', pd.Series([0])).sum()
            
            with st.container():
                # 曲名とLikeボタンを同じ行に表示
                col_title, col_like = st.columns([5, 1])
                with col_title:
                    st.markdown(f"### 🎵 {song_name} / {artist_name}")
                with col_like:
                    # 曲全体のLikeボタン
                    if st.button(f"👍 {total_likes}", key=f"like_song_{song_name}", type="secondary"):
                        # この曲の最初のエピソードのLikesを増やす
                        first_idx = song_episodes.index[0]
                        music_df.loc[first_idx, 'likes'] = int(song_episodes.iloc[0].get('likes', 0)) + 1
                        
                        if save_data("Music", music_df):
                            pass
                        
                        st.rerun()
                
                # ジャケット画像とエピソードを表示
                artwork_url = song_row.get('artwork_url', '')
                
                col_left, col_right = st.columns([1, 2])
                
                with col_left:
                    # ジャケット画像表示
                    if pd.notna(artwork_url) and str(artwork_url).strip() != '':
                        st.image(str(artwork_url), use_container_width=True)
                    else:
                        st.markdown("### 🎵")
                        st.caption("(ジャケット画像なし)")
                
                with col_right:
                    # エピソード表示
                    st.write(f"**エピソード ({len(song_episodes)}件)**")
                    for ep_idx, episode in song_episodes.iterrows():
                        st.markdown(f"**{episode.get('user', '不明')}**: {episode.get('comment', '')}")
                
                st.divider()
    else:
        st.info("まだ曲が登録されていません。最初の曲を追加しましょう！")

# --- 5-5. Live ---
with tab_live:
    st.header("🎧 Live DJ & Streaming")
    st.info("当日はストリーミング配信を予定しています。DJ写真は準備中です。")
    st.markdown("### 📺 配信はイベント当日にこちらで公開されます")

# --- 5-6. Message ---
with tab_message:
    st.subheader("💌 Messages to Kids")
    
    # アップロード完了メッセージを表示
    if 'upload_message' in st.session_state:
        msg_type, msg_text = st.session_state.upload_message
        if msg_type == "success":
            st.success(msg_text)
        else:
            st.warning(msg_text)
        del st.session_state.upload_message
    
    message_df = get_data("Message")
    
    st.info("💝 KOHEI AIBAへの想いやメッセージを送ることができます。投稿されたメッセージは運営が大切に保管します。")
    
    st.subheader("📝 恒平へのメッセージを送る")
    
    # 名前入力
    msg_name = st.text_input(
        "あなたの名前（From）",
        key=f"message_name_{st.session_state.message_form_key}"
    )
    
    # YouTube URL入力（オプション）
    msg_video_url = st.text_input(
        "YouTube URL（オプション - 動画があれば）",
        placeholder="https://www.youtube.com/watch?v=...",
        key=f"message_video_{st.session_state.message_form_key}"
    )
    
    # ビデオプレビュー
    if msg_video_url:
        try:
            st.video(msg_video_url)
        except:
            st.error("有効なYouTube URLを入力してください")
    
    # メッセージ入力
    msg_text = st.text_area(
        "恒平へのメッセージ",
        placeholder="恒平への想いやメッセージを自由に綴ってください...",
        height=150,
        key=f"message_text_{st.session_state.message_form_key}"
    )
    
    st.info("📌 注意：入力後、Info画面に移動することがありますが、問題なく入力できていることが多いです。Messageタブで入力内容を確認して、メッセージを送るボタンを押してください。")
    
    if st.button("メッセージを送る", key=f"post_message_{st.session_state.message_form_key}", type="primary"):
        if msg_name and msg_text:
            # YouTube URLのクリーンアップ
            cleaned_video_url = clean_youtube_url(msg_video_url) if msg_video_url else ""
            
            new_row = pd.DataFrame([{
                "user": st.session_state.user_name,
                "name": msg_name,
                "video_url": cleaned_video_url,
                "message": msg_text
            }])
            
            updated_df = pd.concat([message_df, new_row], ignore_index=True)
            st.session_state.message_form_key += 1
            
            if save_data("Message", updated_df):
                st.session_state.upload_message = ("success", "✅ メッセージを受け付けました。大切に保管します。温かいメッセージをありがとうございます。")
            else:
                st.session_state.upload_message = ("warning", "保存に失敗しました")
            
            st.cache_data.clear()
            st.rerun()
        else:
            st.error("名前とメッセージを入力してください")

# --- 5-7. Fund ---
with tab_fund:
    st.header("💝 Aiba Family Fund")
    
    # Google Driveの画像を表示（ファイルID: 1JpfJnMS3G01bTbZEvsbIdW40uIP2Xknf）
    st.image("https://lh3.googleusercontent.com/d/1JpfJnMS3G01bTbZEvsbIdW40uIP2Xknf", use_container_width=True)
    
    st.markdown("""  
    KOHEI AIBAが愛し、大切にしていた子供たちの未来のために。  
    皆様の温かいご支援をお待ちしています。
    """)
    
    st.link_button("Donate to Aiba Family Fund", "https://gofund.me/979e2078d")

st.divider()
st.caption("© 2026 愛波恒平 Memorial Project Team")