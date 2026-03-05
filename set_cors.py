"""
GCSバケットにCORS設定を適用するスクリプト
"""
from google.cloud import storage
from google.oauth2 import service_account
import streamlit as st

# Streamlit secretsから認証情報を取得
credentials = service_account.Credentials.from_service_account_info(
    st.secrets["connections"]["gsheets"]
)

# Storage クライアントを作成
client = storage.Client(credentials=credentials, project=st.secrets["connections"]["gsheets"]["project_id"])
bucket_name = st.secrets["gcs"]["bucket_name"]
bucket = client.bucket(bucket_name)

# CORS設定
cors_configuration = [
    {
        "origin": ["*"],
        "method": ["GET", "HEAD"],
        "responseHeader": ["Content-Type", "Content-Length", "Range", "Accept-Ranges"],
        "maxAgeSeconds": 3600
    }
]

bucket.cors = cors_configuration
bucket.patch()

print(f"CORS設定を {bucket_name} に適用しました")
print(f"現在のCORS設定: {bucket.cors}")
