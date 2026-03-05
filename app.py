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
            conn.update(worksheet=worksheet_name, data=data)
            return True
        except Exception as e:
            st.error(f"Google Sheets保存エラー: {e}")
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
        # JPEGフォーマットで保存（EXIF情報なし、物理的に回転済み）
        image.save(img_byte_arr, format='JPEG', quality=95, optimize=True)
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

def get_album_artwork(song_name, artist_name):
    """iTunes Search APIを使ってアルバムアートワークを取得"""
    try:
        # iTunes Search API
        search_term = f"{song_name} {artist_name}"
        encoded_term = urllib.parse.quote(search_term)
        api_url = f"https://itunes.apple.com/search?term={encoded_term}&entity=song&limit=1"
        
        response = requests.get(api_url, timeout=5)
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

# --- 3. デザインCSS ---
st.markdown("""
    <style>
    .stApp { background-color: #020617 !important; color: #f1f5f9 !important; }
    h1 { margin-bottom: 5px !important; text-align: center; }
    .sub-text { margin-bottom: 25px !important; color: #94a3b8; font-size: 1.1rem; text-align: center; }
    
    /* 入力フィールドのスタイル - 背景を暗く、文字を明るく */
    input, textarea, 
    div[data-baseweb="input"] input,
    div[data-baseweb="textarea"] textarea,
    input[type="text"], input[type="email"], input[type="number"],
    .stTextInput input, .stTextArea textarea,
    .stTextInput > div > div > input,
    .stTextArea > div > div > textarea {
        background-color: #0f172a !important; 
        color: #fbbf24 !important;
        caret-color: #fbbf24 !important;
        -webkit-text-fill-color: #fbbf24 !important;
        border: 2px solid #334155 !important; 
        border-radius: 10px !important;
        font-weight: 500 !important;
    }
    
    /* より具体的な入力フィールドの色設定 */
    [data-testid="stTextInput"] input,
    [data-testid="stTextArea"] textarea {
        color: #fbbf24 !important;
        -webkit-text-fill-color: #fbbf24 !important;
        background-color: #0f172a !important;
    }
    
    /* フォーカス時のスタイル */
    [data-testid="stTextInput"] input:focus,
    [data-testid="stTextArea"] textarea:focus,
    input:focus, textarea:focus {
        color: #fbbf24 !important;
        -webkit-text-fill-color: #fbbf24 !important;
        background-color: #0f172a !important;
        border-color: #10b981 !important;
    }
    
    /* オートフィル時のスタイル */
    input:-webkit-autofill,
    input:-webkit-autofill:hover,
    input:-webkit-autofill:focus,
    textarea:-webkit-autofill,
    textarea:-webkit-autofill:hover,
    textarea:-webkit-autofill:focus {
        -webkit-text-fill-color: #fbbf24 !important;
        -webkit-box-shadow: 0 0 0px 1000px #0f172a inset !important;
        background-color: #0f172a !important;
        color: #fbbf24 !important;
    }
    
    /* プレースホルダーのスタイル */
    input::placeholder, textarea::placeholder {
        color: #64748b !important;
        opacity: 1 !important;
    }
    
    /* セレクトボックスのスタイル */
    div[data-baseweb="select"] > div {
        background-color: #0f172a !important;
        color: #fbbf24 !important;
        border: 2px solid #334155 !important;
    }
    
    /* ラジオボタンのラベル */
    div[role="radiogroup"] label {
        color: #f1f5f9 !important;
    }
    
    /* File Uploaderのスタイル */
    [data-testid="stFileUploader"] label,
    [data-testid="stFileUploader"] > div {
        color: #f1f5f9 !important;
    }
    
    div.stButton > button, button[kind="primary"], button[type="submit"] {
        background-color: #10b981 !important; color: #ffffff !important;
        font-weight: 800 !important; border-radius: 10px !important;
        height: 50px !important; width: 100% !important; border: none !important;
    }
    button[kind="secondary"] {
        background-color: #1e293b !important; color: #ffffff !important;
        border: 2px solid #334155 !important;
    }
    .post-card {
        background-color: #0f172a; padding: 20px; border-radius: 15px;
        border: 1px solid #1e293b; margin-bottom: 15px;
    }
    .stTabs [data-baseweb="tab-list"] { background-color: #0f172a; padding: 5px; border-radius: 10px; }
    .stTabs [data-baseweb="tab"][aria-selected="true"] { color: #10b981 !important; }
    
    /* Expanderのスタイル */
    div[data-testid="stExpander"] {
        background-color: #0f172a !important;
        border: 1px solid #334155 !important;
        border-radius: 10px !important;
    }
    div[data-testid="stExpander"] summary {
        color: #fbbf24 !important;
        font-weight: 600 !important;
    }
    </style>
    """, unsafe_allow_html=True)

# --- 4. ログイン管理 ---
if 'user_name' not in st.session_state:
    st.session_state.user_name = ""

# ファイルアップローダーのリセット用キー
if 'photo_uploader_key' not in st.session_state:
    st.session_state.photo_uploader_key = 0
if 'memory_uploader_key' not in st.session_state:
    st.session_state.memory_uploader_key = 0
if 'music_form_key' not in st.session_state:
    st.session_state.music_form_key = 0
if 'message_form_key' not in st.session_state:
    st.session_state.message_form_key = 0

if not st.session_state.user_name:
    st.markdown("<div style='height: 80px;'></div>", unsafe_allow_html=True)
    st.markdown("<h1>🎧 AIBA Memorial</h1>", unsafe_allow_html=True)
    st.markdown("<p class='sub-text'>KOHEI AIBAを愛する仲間たちのための場所です。</p>", unsafe_allow_html=True)
    
    with st.form("login_form"):
        name_input = st.text_input("お名前を入力してください (Your Name)", placeholder="Enter your name here...")
        submitted = st.form_submit_button("ENTER SITE", type="primary")
        
        if submitted and name_input:
            st.session_state.user_name = name_input
            st.rerun()
    st.stop()

# --- 5. メインレイアウト ---
st.markdown(f"<h3 style='text-align: center; margin-bottom: 5px;'>🎧 AIBA Memorial</h3>", unsafe_allow_html=True)
st.markdown(f"<p style='text-align: center; color: #10b981; font-size: 0.9rem;'>User: {st.session_state.user_name}</p>", unsafe_allow_html=True)

tab_info, tab_photo, tab_music, tab_memory, tab_live, tab_message, tab_fund = st.tabs(["Info", "Photo/Story", "Music", "Memory", "Live", "Message", "Fund"])

# --- 5-1. Info ---
with tab_info:
    st.header("Event Info")
    st.markdown("""
    ### **AIBA Memorial Party - The Final Set**
    * **Date:** 2026.03.20 (Fri) or 21 (Sat)
    * **Place:** 渋谷 WOMB (調整中)
    
    収益はすべて「Aiba Family Fund」へ贈られます。
    
    ---
    
    ### 📺 当日参加できない方へ
    
    当日現地に来られない方も、**Liveタブ**からリアルタイムで会場の様子をご覧いただけます。  
    世界中どこからでも、KOHEI AIBAへの想いを共有しましょう 🌍✨
    """)
    
    st.divider()
    
    st.markdown("""
    ### 📢 このサイトの使い方
    
    **📸 Photo/Story** - 思い出の写真とエピソードを投稿  
    あなたの思い出を写真や文章で共有してください。写真なしでもOK！
    
    **🎵 Music** - KOHEI AIBAが愛した音楽、あなたとの思い出の曲  
    曲とエピソードを投稿して、みんなで共有しましょう。
    
    **🎬 Memory** - KOHEI AIBAの映像・音源を共有  
    YouTubeリンクやファイルをアップロードして、彼の記憶を残しましょう。
    
    **📺 Live** - イベント当日のライブ配信  
    当日来られない方も、会場の様子をリアルタイムで視聴できます。
    
    **💌 Message** - 子供たちへのメッセージ  
    KOHEI AIBAの子供たちへ、動画やメッセージを送ることができます。
    
    ---
    
    ### 💡 みんなで参加しよう！
    
    **👍 Likeボタンで共感を伝えよう！**  
    写真、ストーリー、音楽の投稿に「いいね」をつけて、  
    みんなの思い出を応援してください。
    
    一緒にKOHEI AIBAの記憶を祝いましょう 🎧✨
    """)

# --- 5-2. Memory ---
with tab_memory:
    st.header("🎬 Memory - 彼の記憶")
    memory_df = get_data("Memory")
    
    st.subheader("📤 映像・音源をアップロード")
    # カテゴリー選択
    category = st.selectbox(
        "カテゴリー",
        ["映像", "音源"],
        key=f"memory_category_{st.session_state.memory_uploader_key}"
    )
    
    # タイトルと説明
    mem_title = st.text_input(
        "タイトル",
        key=f"memory_title_{st.session_state.memory_uploader_key}"
    )
    mem_description = st.text_area(
        "説明・エピソード",
        key=f"memory_description_{st.session_state.memory_uploader_key}"
    )
    
    # YouTube URLまたはファイルアップロード
    st.write("**アップロード方法を選択**")
    upload_type = st.radio(
        "アップロード方法", 
        ["YouTube URLを入力", "ファイルをアップロード"], 
        horizontal=True, 
        label_visibility="collapsed",
        key=f"memory_upload_type_{st.session_state.memory_uploader_key}"
    )
    
    youtube_url_mem = ""
    file_url_mem = ""
    
    if upload_type == "YouTube URLを入力":
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
    else:
        uploaded_mem_file = st.file_uploader(
            "ファイルを選択（動画・音声）",
            type=['mp4', 'mov', 'avi', 'mp3', 'wav', 'm4a'],
            key=f"memory_uploader_{st.session_state.memory_uploader_key}"
        )
        if uploaded_mem_file is not None:
            st.info(f"📁 {uploaded_mem_file.name} ({uploaded_mem_file.size / 1024 / 1024:.2f} MB)")
            if USE_GCS:
                st.success("✅ アップロード準備完了")
            else:
                st.warning("⚠️ GCS未設定。ファイルは保存されません。")
    
    if st.button("投稿する", key="post_memory", type="primary"):
        if mem_title and mem_description:
            # 変数の初期化
            file_url_mem = ""
            
            # ファイルアップロード処理
            if upload_type == "ファイルをアップロード" and uploaded_mem_file is not None and USE_GCS:
                st.info(f"🔄 アップロード開始: {uploaded_mem_file.name}")
                with st.spinner("ファイルをアップロード中..."):
                    try:
                        # ファイルをGCSにアップロード
                        file_extension = uploaded_mem_file.name.split('.')[-1]
                        unique_filename = f"memory/{uuid.uuid4()}.{file_extension}"
                        blob = gcs_bucket.blob(unique_filename)
                        uploaded_mem_file.seek(0)
                        
                        # Content-Typeを明示的に設定
                        content_type = uploaded_mem_file.type
                        if not content_type:
                            # MIMEタイプが設定されていない場合、拡張子から判断
                            mime_types = {
                                'mp4': 'video/mp4',
                                'mov': 'video/quicktime',
                                'avi': 'video/x-msvideo',
                                'mp3': 'audio/mpeg',
                                'wav': 'audio/wav',
                                'm4a': 'audio/mp4'
                            }
                            content_type = mime_types.get(file_extension.lower(), 'application/octet-stream')
                        
                        blob.upload_from_file(uploaded_mem_file, content_type=content_type)
                        # バケットレベルで公開されているため、make_public()は不要
                        
                        # Cache-Controlヘッダーを設定
                        blob.cache_control = 'public, max-age=31536000'
                        blob.patch()
                        
                        # 公開URLを取得
                        file_url_mem = blob.public_url
                        st.success("ファイルのアップロードが完了しました！")
                        st.info(f"📎 保存URL: {file_url_mem}")
                    except Exception as e:
                        st.error(f"アップロードエラー: {str(e)}")
                        import traceback
                        st.error(f"詳細: {traceback.format_exc()}")
                        file_url_mem = ""  # エラー時は空文字列に設定
            elif upload_type == "ファイルをアップロード" and uploaded_mem_file is not None and not USE_GCS:
                st.error("⚠️ GCS未設定のため、ファイルはアップロードされません")
            elif upload_type == "ファイルをアップロード" and uploaded_mem_file is None:
                st.error("⚠️ ファイルが選択されていません")
            
            # YouTube URLのクリーンアップ
            if youtube_url_mem:
                youtube_url_mem = clean_youtube_url(youtube_url_mem)
            
            # デバッグ情報
            with st.expander("🔍 保存データの確認"):
                st.write(f"YouTube URL: {youtube_url_mem if youtube_url_mem else '(なし)'}")
                st.write(f"File URL: {file_url_mem if file_url_mem else '(なし)'}")
            
            # データ保存
            new_row = pd.DataFrame([{
                "user": st.session_state.user_name,
                "category": category,
                "title": mem_title,
                "description": mem_description,
                "youtube_url": youtube_url_mem if youtube_url_mem else "",
                "file_url": file_url_mem if file_url_mem else "",
                "likes": 0
            }])
            
            updated_df = pd.concat([memory_df, new_row], ignore_index=True)
            
            if save_data("Memory", updated_df):
                st.success("投稿が完了しました！")
                if file_url_mem:
                    st.info(f"✅ 動画URL: {file_url_mem}")
                st.session_state.memory_uploader_key += 1
                st.cache_data.clear()
                st.rerun()
        else:
            st.error("タイトルと説明を入力してください")
    
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
                    # タイトルとカテゴリー
                    col_title, col_like = st.columns([5, 1])
                    with col_title:
                        category_icon = {
                            "映像": "🎬",
                            "音源": "🎵"
                        }
                        icon = category_icon.get(row.get('category', ''), '📁')
                        st.markdown(f"### {icon} {row.get('title', '無題')}")
                        st.caption(f"投稿者: {row.get('user', '不明')} | カテゴリー: {row.get('category', '不明')}")
                    with col_like:
                        if st.button(f"👍 {row.get('likes', 0)}", key=f"like_memory_{idx}", type="secondary"):
                            memory_df.loc[idx, 'likes'] = int(row.get('likes', 0)) + 1
                            if save_data("Memory", memory_df):
                                st.cache_data.clear()
                                st.rerun()
                    
                    # 説明
                    st.write(row.get('description', ''))
                    
                    # YouTube動画またはファイル表示
                    youtube_url = row.get('youtube_url', '')
                    # file_url と file_uri の両方に対応
                    file_url = row.get('file_url', '') or row.get('file_uri', '')
                    
                    if pd.notna(youtube_url) and str(youtube_url).strip() != '':
                        st.video(str(youtube_url))
                    elif pd.notna(file_url) and str(file_url).strip() != '':
                        # ファイルタイプで表示方法を変える
                        file_url_str = str(file_url)
                        
                        # デバッグ情報を表示
                        with st.expander("🔍 デバッグ情報（開発中）"):
                            st.code(f"URL: {file_url_str}")
                        
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
            st.image(preview_image, caption="アップロード予定の写真", width='stretch')
            uploaded_file.seek(0)  # ファイルポインタを先頭に戻す
        except:
            st.image(uploaded_file, caption="アップロード予定の写真", width='stretch')
            uploaded_file.seek(0)
        
        if USE_GCS:
            st.success("✅ 画像は永続的に保存されます")
        else:
            st.info("📌 注意：現在、画像はプレビューのみで、投稿後は保存されません。コメントのみが保存されます。")
    
    p_comment = st.text_area(
        "KOHEI AIBAとの思い出 (Story)",
        key=f"photo_comment_{st.session_state.photo_uploader_key}"
    )
    
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
            
            # 保存を試みる
            if save_data("Photo", updated_df):
                st.success("投稿が完了しました！")
                st.session_state.photo_uploader_key += 1
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
                            st.image(image_url, width='stretch')
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
                                st.cache_data.clear()
                                st.rerun()
                    
                    st.divider()
        else:
            st.info("コメント付きの投稿がまだありません。最初の投稿をしてみましょう！")
    else:
        st.info("まだ投稿がありません。最初の投稿をしてみましょう！")

# --- 5-4. Music ---
with tab_music:
    st.subheader("🎵 Memorial Playlist")
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
            if st.button("エピソードを追加", key=f"add_episode_{st.session_state.music_form_key}", type="primary"):
                if new_comment:
                    # 既存の曲情報を使って新しいエピソードを追加
                    new_row = pd.DataFrame([{
                        "user": st.session_state.user_name,
                        "song": song_in,
                        "artist": artist_in,
                        "youtube_url": "",
                        "artwork_url": first_entry.get('artwork_url', ''),
                        "comment": new_comment,
                        "likes": 0
                    }])
                    updated_df = pd.concat([music_df, new_row], ignore_index=True)
                    
                    if save_data("Music", updated_df):
                        st.success("エピソードを追加しました！")
                        st.session_state.music_form_key += 1
                        st.cache_data.clear()
                        st.rerun()
                else:
                    st.error("エピソードを入力してください。")
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
            
            if st.button("プレイリストに追加", key=f"add_new_song_{st.session_state.music_form_key}", type="primary"):
                if m_comment:
                    new_row = pd.DataFrame([{
                        "user": st.session_state.user_name,
                        "song": song_in,
                        "artist": artist_in,
                        "youtube_url": "",
                        "artwork_url": artwork_url if artwork_url else "",
                        "comment": m_comment,
                        "likes": 0
                    }])
                    updated_df = pd.concat([music_df, new_row], ignore_index=True)
                    
                    if save_data("Music", updated_df):
                        st.success("プレイリストに追加しました！")
                        st.session_state.music_form_key += 1
                        st.cache_data.clear()
                        st.rerun()
                else:
                    st.error("エピソードを入力してください。")
    
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
                            st.cache_data.clear()
                            st.rerun()
                
                # ジャケット画像とエピソードを表示
                artwork_url = song_row.get('artwork_url', '')
                
                col_left, col_right = st.columns([1, 2])
                
                with col_left:
                    # ジャケット画像表示
                    if pd.notna(artwork_url) and str(artwork_url).strip() != '':
                        st.image(str(artwork_url), width='stretch')
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
    st.video("https://www.youtube.com/watch?v=dQw4w9WgXcQ")

# --- 5-6. Message ---
with tab_message:
    st.subheader("💌 Messages to Kids")
    message_df = get_data("Message")
    
    st.info("💝 KOHEI AIBAの子供たちへの想いやメッセージを送ることができます。投稿されたメッセージは運営が大切に保管し、子供たちへ届けます。")
    
    st.subheader("📝 子供たちへのメッセージを送る")
    
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
        "子供たちへのメッセージ",
        placeholder="子供たちへの想いやメッセージを自由に綴ってください...",
        height=150,
        key=f"message_text_{st.session_state.message_form_key}"
    )
    
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
            
            if save_data("Message", updated_df):
                st.success("✅ メッセージを受け付けました。大切に保管し、子供たちへ届けます。温かいメッセージをありがとうございます。")
                st.session_state.message_form_key += 1
                st.cache_data.clear()
                st.rerun()
        else:
            st.error("名前とメッセージを入力してください")

# --- 5-7. Fund ---
with tab_fund:
    st.link_button("Donate to Aiba Family Fund", "https://congrant.com/project/89m98/14101", width='stretch')

st.divider()
st.caption("© 2026 AIBA Memorial Project Team")