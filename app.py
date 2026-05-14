import streamlit as st
import os
import tempfile
import json
import re
import subprocess
import math
from pathlib import Path
from datetime import datetime

# ── Page config ────────────────────────────────────────────────────────────────
st.set_page_config(
    page_title="Transcritor de Mídia",
    page_icon="🎙️",
    layout="centered",
)

# Allow uploads up to 1 GB
# This is also set in .streamlit/config.toml — both are needed
try:
    from streamlit import config as _st_config
    _st_config.set_option("server.maxUploadSize", 1024)
except Exception:
    pass

# ── Custom CSS ─────────────────────────────────────────────────────────────────
st.markdown("""
<style>
@import url('https://fonts.googleapis.com/css2?family=Space+Mono:wght@400;700&family=Inter:wght@300;400;500;600&display=swap');

html, body, [class*="css"] { font-family: 'Inter', sans-serif; }
h1, h2, h3 { font-family: 'Space Mono', monospace !important; }

.stApp { background: #0f0f11; color: #e8e8e8; }

section[data-testid="stSidebar"] {
    background: #16161a;
    border-right: 1px solid #2a2a35;
}

.block-container { padding-top: 2rem; max-width: 780px; }

.stButton > button {
    background: #7c3aed;
    color: white;
    border: none;
    border-radius: 8px;
    padding: 0.6rem 1.4rem;
    font-family: 'Space Mono', monospace;
    font-size: 0.85rem;
    letter-spacing: 0.03em;
    transition: all 0.2s;
    width: 100%;
}
.stButton > button:hover {
    background: #6d28d9;
    transform: translateY(-1px);
    box-shadow: 0 4px 20px rgba(124,58,237,0.4);
}

.stSelectbox label, .stFileUploader label, .stTextInput label {
    color: #a0a0b0 !important;
    font-size: 0.8rem !important;
    letter-spacing: 0.05em !important;
    text-transform: uppercase !important;
}

.stTextInput input {
    background: #1e1e26 !important;
    border: 1px solid #2a2a35 !important;
    color: #e8e8e8 !important;
    border-radius: 8px !important;
}
.stTextInput input:focus {
    border-color: #7c3aed !important;
    box-shadow: 0 0 0 2px rgba(124,58,237,0.2) !important;
}

div[data-testid="stSelectbox"] > div {
    background: #1e1e26 !important;
    border: 1px solid #2a2a35 !important;
    border-radius: 8px !important;
    color: #e8e8e8 !important;
}

div[data-testid="stFileUploaderDropzone"] {
    background: #1e1e26 !important;
    border: 2px dashed #2a2a35 !important;
    border-radius: 12px !important;
    transition: border-color 0.2s;
}
div[data-testid="stFileUploaderDropzone"]:hover {
    border-color: #7c3aed !important;
}

.stTextArea textarea {
    background: #1e1e26 !important;
    border: 1px solid #2a2a35 !important;
    color: #e8e8e8 !important;
    border-radius: 8px !important;
    font-family: 'Inter', sans-serif !important;
    line-height: 1.7 !important;
}

.status-box {
    background: #1e1e26;
    border: 1px solid #2a2a35;
    border-radius: 12px;
    padding: 1.2rem 1.5rem;
    margin: 1rem 0;
}
.status-success { border-left: 3px solid #10b981; }
.status-info    { border-left: 3px solid #7c3aed; }
.status-warning { border-left: 3px solid #f59e0b; }
.status-box a   { color: #a78bfa; }

.header-tag {
    display: inline-block;
    background: rgba(124,58,237,0.15);
    border: 1px solid rgba(124,58,237,0.3);
    color: #a78bfa;
    font-family: 'Space Mono', monospace;
    font-size: 0.7rem;
    padding: 0.2rem 0.6rem;
    border-radius: 4px;
    margin-bottom: 0.5rem;
}

.chunk-progress {
    background: #1e1e26;
    border: 1px solid #2a2a35;
    border-radius: 8px;
    padding: 0.8rem 1rem;
    margin: 0.4rem 0;
    font-size: 0.85rem;
    color: #a0a0b0;
}

hr { border-color: #2a2a35 !important; }
</style>
""", unsafe_allow_html=True)


# ── ffmpeg path resolution ────────────────────────────────────────────────────
def get_ffmpeg_path() -> str:
    """
    Returns the ffmpeg binary path.
    Priority: system ffmpeg → imageio-ffmpeg bundled binary.
    """
    import shutil
    system_ff = shutil.which("ffmpeg")
    if system_ff:
        return system_ff
    try:
        import imageio_ffmpeg
        return imageio_ffmpeg.get_ffmpeg_exe()
    except Exception:
        pass
    raise RuntimeError(
        "ffmpeg não encontrado. Instale ffmpeg no sistema ou adicione "
        "'imageio-ffmpeg' ao requirements.txt."
    )

def get_ffprobe_path() -> str:
    """Returns ffprobe path (system only — imageio-ffmpeg doesn't bundle it)."""
    import shutil
    p = shutil.which("ffprobe")
    if p:
        return p
    # ffprobe is usually alongside ffmpeg; try same directory
    import os
    ff = shutil.which("ffmpeg")
    if ff:
        candidate = os.path.join(os.path.dirname(ff), "ffprobe")
        if os.path.isfile(candidate):
            return candidate
    raise RuntimeError("ffprobe não encontrado no sistema.")


# ── Secret helper ─────────────────────────────────────────────────────────────
def get_secret(key: str) -> str | None:
    """Read from st.secrets (local) or os.environ (Railway/cloud), whichever exists."""
    try:
        val = st.secrets.get(key, None)
        if val:
            return val
    except Exception:
        pass
    return os.environ.get(key, None)


# ── Constants ──────────────────────────────────────────────────────────────────
CHUNK_LIMIT_MB    = 23          # OpenAI limit is 25 MB; keep margin
AUDIO_BITRATE     = "64k"       # mono mp3 64kbps — perfect for speech
# At 64kbps mono: 1 s ≈ 8 000 bytes → chunk size in seconds
SECONDS_PER_CHUNK = int((CHUNK_LIMIT_MB * 1024 * 1024) / 8000)  # ≈ 3010 s


# ── Helpers: Drive ─────────────────────────────────────────────────────────────

def extract_folder_id(url_or_id: str) -> str:
    url_or_id = url_or_id.strip()
    m = re.search(r'/folders/([a-zA-Z0-9_-]+)', url_or_id)
    if m:
        return m.group(1)
    m = re.search(r'[?&]id=([a-zA-Z0-9_-]+)', url_or_id)
    if m:
        return m.group(1)
    return url_or_id


@st.cache_resource(show_spinner=False)
def get_drive_service():
    try:
        from google.oauth2 import service_account
        from googleapiclient.discovery import build

        creds_json = get_secret("GOOGLE_SERVICE_ACCOUNT_JSON")
        if not creds_json:
            return None, "Credencial `GOOGLE_SERVICE_ACCOUNT_JSON` não encontrada nos secrets nem nas variáveis de ambiente."

        creds_info = json.loads(creds_json) if isinstance(creds_json, str) else dict(creds_json)
        scopes = ["https://www.googleapis.com/auth/drive"]
        creds  = service_account.Credentials.from_service_account_info(creds_info, scopes=scopes)
        return build("drive", "v3", credentials=creds), None
    except Exception as e:
        return None, str(e)


def list_subfolders(service, parent_id: str):
    """Return direct subfolders. Supports both personal and Shared Drives."""
    query = (
        f"'{parent_id}' in parents "
        "and mimeType='application/vnd.google-apps.folder' "
        "and trashed=false"
    )
    result = service.files().list(
        q=query,
        fields="files(id, name)",
        orderBy="name",
        includeItemsFromAllDrives=True,
        supportsAllDrives=True,
        corpora="allDrives",
    ).execute()
    return [(f["name"], f["id"]) for f in result.get("files", [])]


def list_all_folders_flat(service, parent_id: str, prefix: str = "") -> list:
    """Recursively list all folders. Returns [(label, id)] with hierarchy in label."""
    results = []
    for name, fid in list_subfolders(service, parent_id):
        label = f"{prefix} / {name}" if prefix else name
        results.append((label, fid))
        results.extend(list_all_folders_flat(service, fid, prefix=label))
    return results


def upload_to_drive(service, file_bytes: bytes, filename: str, folder_id: str, mime_type: str = "text/plain"):
    from googleapiclient.http import MediaIoBaseUpload
    import io
    media    = MediaIoBaseUpload(io.BytesIO(file_bytes), mimetype=mime_type, resumable=True)
    metadata = {"name": filename, "parents": [folder_id]}
    uploaded = service.files().create(
        body=metadata,
        media_body=media,
        fields="id, webViewLink",
        supportsAllDrives=True,
    ).execute()
    link = uploaded.get("webViewLink", "")
    # If no webViewLink (common with service accounts on personal drives),
    # build the URL manually from the file id
    if not link and uploaded.get("id"):
        link = f"https://drive.google.com/file/d/{uploaded['id']}/view"
    return link


# ── Helpers: ffmpeg + OpenAI Whisper API ───────────────────────────────────────

def get_audio_duration(path: str) -> float:
    """Return total duration in seconds via ffprobe."""
    result = subprocess.run(
        [
            get_ffprobe_path(), "-v", "error",
            "-show_entries", "format=duration",
            "-of", "default=noprint_wrappers=1:nokey=1",
            path,
        ],
        capture_output=True, text=True, check=True,
    )
    return float(result.stdout.strip())


def extract_audio_segment(input_path: str, output_path: str,
                           start_sec: float, duration_sec: float | None = None):
    """
    Extract a segment from any media file as mono mp3 at 64 kbps / 16 kHz.
    These settings are transparent for speech: Whisper was trained at 16 kHz
    and mono channels carry all voice information.
    """
    cmd = [get_ffmpeg_path(), "-y", "-ss", str(start_sec)]
    if duration_sec is not None:
        cmd += ["-t", str(duration_sec)]
    cmd += [
        "-i", input_path,
        "-vn",           # strip video track
        "-ac", "1",      # mono
        "-ar", "16000",  # 16 kHz — Whisper's native sample rate
        "-ab", AUDIO_BITRATE,
        "-f", "mp3",
        output_path,
    ]
    subprocess.run(cmd, capture_output=True, check=True)


def transcribe_chunk_api(audio_path: str, language: str | None, client) -> str:
    """Send one audio file to the Whisper API; return transcript as plain text."""
    with open(audio_path, "rb") as f:
        kwargs = {"model": "whisper-1", "file": f, "response_format": "text"}
        if language:
            kwargs["language"] = language
        result = client.audio.transcriptions.create(**kwargs)
    return result.strip() if isinstance(result, str) else result.text.strip()


def build_openai_client():
    try:
        from openai import OpenAI
        api_key = get_secret("OPENAI_API_KEY")
        if not api_key:
            return None, "Chave `OPENAI_API_KEY` não encontrada nos secrets nem nas variáveis de ambiente."
        return OpenAI(api_key=api_key), None
    except ImportError:
        return None, "Pacote `openai` não instalado. Rode: pip install openai"
    except Exception as e:
        return None, str(e)


def transcribe_file(input_path: str, language: str | None, client, status_ph) -> str:
    """
    Full pipeline:
      1. Extract mono 16 kHz mp3 via ffmpeg (drops video, massively shrinks size)
      2. If still > CHUNK_LIMIT_MB, split into ~50-min chunks
      3. Transcribe each chunk via Whisper API and concatenate
    """
    tmp_dir    = tempfile.mkdtemp()
    full_audio = os.path.join(tmp_dir, "audio.mp3")

    # Step 1 — extract audio
    status_ph.markdown(
        '<div class="chunk-progress">🎵 Extraindo e comprimindo áudio…</div>',
        unsafe_allow_html=True,
    )
    extract_audio_segment(input_path, full_audio, start_sec=0)
    audio_mb = os.path.getsize(full_audio) / (1024 * 1024)

    # Step 2 — single-chunk path (most files)
    if audio_mb <= CHUNK_LIMIT_MB:
        status_ph.markdown(
            f'<div class="chunk-progress">📤 Enviando para Whisper API ({audio_mb:.1f} MB)…</div>',
            unsafe_allow_html=True,
        )
        text = transcribe_chunk_api(full_audio, language, client)
        os.unlink(full_audio)
        os.rmdir(tmp_dir)
        return text

    # Step 3 — multi-chunk path (very long files)
    duration = get_audio_duration(full_audio)
    n_chunks = math.ceil(duration / SECONDS_PER_CHUNK)
    os.unlink(full_audio)

    status_ph.markdown(
        f'<div class="chunk-progress">✂️ Arquivo longo ({audio_mb:.1f} MB) — '
        f'dividindo em {n_chunks} partes de ~{SECONDS_PER_CHUNK//60} min…</div>',
        unsafe_allow_html=True,
    )

    parts = []
    for i in range(n_chunks):
        start      = i * SECONDS_PER_CHUNK
        chunk_path = os.path.join(tmp_dir, f"chunk_{i:03d}.mp3")
        extract_audio_segment(input_path, chunk_path,
                              start_sec=start, duration_sec=SECONDS_PER_CHUNK)

        chunk_mb = os.path.getsize(chunk_path) / (1024 * 1024)
        status_ph.markdown(
            f'<div class="chunk-progress">'
            f'📤 Transcrevendo parte {i+1} de {n_chunks} ({chunk_mb:.1f} MB)…'
            f'</div>',
            unsafe_allow_html=True,
        )
        parts.append(transcribe_chunk_api(chunk_path, language, client))
        os.unlink(chunk_path)

    os.rmdir(tmp_dir)
    return " ".join(parts)


# ── UI ─────────────────────────────────────────────────────────────────────────

st.markdown('<span class="header-tag">BETA · v2.0</span>', unsafe_allow_html=True)
st.title("🎙️ Transcritor de Mídia")
st.caption("Transcreva vídeos e áudios automaticamente e salve no Google Drive.")

st.divider()

# ── SECTION 1: Google Drive ────────────────────────────────────────────────────
st.subheader("📁 Google Drive")

drive_url = st.text_input(
    "Link ou ID da pasta raiz no Drive",
    placeholder="https://drive.google.com/drive/folders/XXXX  ou  ID direto",
)

drive_service, drive_error = get_drive_service()
selected_folder_id   = None
selected_folder_name = None

if drive_error:
    st.markdown(
        f'<div class="status-box status-warning">🔑 <strong>Drive:</strong> {drive_error}</div>',
        unsafe_allow_html=True,
    )

if drive_url and drive_service:
    root_id = extract_folder_id(drive_url)
    with st.spinner("Buscando pastas…"):
        try:
            all_folders = list_all_folders_flat(drive_service, root_id)
        except Exception as e:
            st.error(f"Erro ao listar pastas: {e}")
            all_folders = []

    # Always include the root folder as first option
    folder_options = {"📁 (pasta raiz)": root_id}
    for name, fid in all_folders:
        folder_options[name] = fid

    folder_choice        = st.selectbox(
        "Salvar na pasta",
        options=list(folder_options.keys()),
        help="Escolha qualquer pasta ou subpasta dentro da pasta raiz.",
    )
    selected_folder_id   = folder_options[folder_choice]
    selected_folder_name = folder_choice
    st.markdown(
        f'<div class="status-box status-info">📂 Destino: <strong>{folder_choice}</strong></div>',
        unsafe_allow_html=True,
    )

elif drive_url and not drive_service:
    st.info("Configure as credenciais do Google Drive nos secrets para habilitar o salvamento.")

st.divider()

# ── SECTION 2: Arquivo de mídia ────────────────────────────────────────────────
st.subheader("📎 Arquivo de Mídia")

ALLOWED_EXTENSIONS = [
    "mp3", "mp4", "wav", "m4a", "ogg", "flac", "aac",
    "wma", "webm", "mkv", "avi", "mov", "mpeg", "mpg",
    "opus", "aiff", "aif",
]

uploaded_file = st.file_uploader(
    "Selecione o arquivo de vídeo ou áudio",
    type=ALLOWED_EXTENSIONS,
    help=f"Formatos aceitos: {', '.join(ALLOWED_EXTENSIONS)} · Tamanho máximo: 1 GB",
)

if uploaded_file:
    col1, col2 = st.columns(2)
    with col1:
        st.markdown(f"**Nome:** `{uploaded_file.name}`")
    with col2:
        size_mb = uploaded_file.size / (1024 * 1024)
        size_str = f"{size_mb / 1024:.2f} GB" if size_mb > 1024 else f"{size_mb:.1f} MB"
        st.markdown(f"**Tamanho:** `{size_str}`")

st.divider()

# ── SECTION 3: Configurações ───────────────────────────────────────────────────
st.subheader("⚙️ Configurações de Transcrição")

col_a, col_b = st.columns(2)

LANG_MAP = {
    "Automático": None, "Português": "pt", "Inglês": "en",
    "Espanhol":   "es", "Francês":   "fr", "Alemão": "de",
    "Italiano":   "it", "Japonês":   "ja", "Chinês": "zh",
}

with col_a:
    language_label = st.selectbox(
        "Idioma do áudio",
        options=list(LANG_MAP.keys()),
        index=1,
    )

with col_b:
    output_format = st.selectbox(
        "Formato do arquivo de saída",
        options=["Texto simples (.txt)", "Markdown (.md)", "JSON (.json)"],
    )

custom_filename = st.text_input(
    "Nome do arquivo (opcional)",
    placeholder="deixe em branco para usar o nome original",
)

st.divider()

# ── SECTION 4: Transcrever ─────────────────────────────────────────────────────
transcription_result   = st.session_state.get("transcription_result", None)
transcription_filename = st.session_state.get("transcription_filename", None)

if st.button("▶  Iniciar Transcrição", disabled=uploaded_file is None):
    client, err = build_openai_client()
    if err:
        st.error(f"OpenAI: {err}")
        st.stop()

    status_ph = st.empty()

    try:
        suffix = Path(uploaded_file.name).suffix
        with tempfile.NamedTemporaryFile(delete=False, suffix=suffix) as tmp:
            tmp.write(uploaded_file.read())
            tmp_path = tmp.name

        text = transcribe_file(tmp_path, LANG_MAP[language_label], client, status_ph)
        os.unlink(tmp_path)

        st.session_state["transcription_result"]   = text
        st.session_state["transcription_filename"] = uploaded_file.name

    except subprocess.CalledProcessError as e:
        stderr = e.stderr.decode() if e.stderr else str(e)
        st.error(f"Erro no ffmpeg: {stderr}")
        st.stop()
    except Exception as e:
        st.error(f"Erro durante a transcrição: {e}")
        st.stop()
    finally:
        status_ph.empty()

    st.success("✅ Transcrição concluída!")
    transcription_result   = st.session_state["transcription_result"]
    transcription_filename = st.session_state["transcription_filename"]

# ── Show result ────────────────────────────────────────────────────────────────
if transcription_result:
    st.subheader("📝 Resultado")
    st.text_area("Transcrição", value=transcription_result, height=300)

    base_name       = Path(custom_filename or transcription_filename or "transcricao").stem
    ts              = datetime.now().strftime("%Y%m%d_%H%M%S")
    final_name_base = f"{base_name}_{ts}"

    if output_format.startswith("Texto"):
        file_content = transcription_result.encode("utf-8")
        final_name   = final_name_base + ".txt"
        mime         = "text/plain"
    elif output_format.startswith("Markdown"):
        md = (
            f"# Transcrição — {base_name}\n\n"
            f"_{datetime.now().strftime('%d/%m/%Y %H:%M')}_\n\n"
            f"---\n\n{transcription_result}\n"
        )
        file_content = md.encode("utf-8")
        final_name   = final_name_base + ".md"
        mime         = "text/markdown"
    else:
        data = {
            "arquivo_original": transcription_filename,
            "data":             datetime.now().isoformat(),
            "idioma":           language_label,
            "transcricao":      transcription_result,
        }
        file_content = json.dumps(data, ensure_ascii=False, indent=2).encode("utf-8")
        final_name   = final_name_base + ".json"
        mime         = "application/json"

    st.download_button(
        label="⬇️  Baixar transcrição",
        data=file_content,
        file_name=final_name,
        mime=mime,
    )

    can_save = drive_service is not None and selected_folder_id is not None
    if can_save:
        if st.button(f"☁️  Salvar no Drive → {selected_folder_name}"):
            with st.spinner("Enviando para o Google Drive…"):
                try:
                    link = upload_to_drive(drive_service, file_content, final_name, selected_folder_id, mime)
                    st.markdown(
                        f'<div class="status-box status-success">✅ Salvo! '
                        f'<a href="{link}" target="_blank">Abrir no Drive →</a></div>',
                        unsafe_allow_html=True,
                    )
                except Exception as e:
                    st.error(f"Erro ao enviar para o Drive: {e}")
    elif not drive_service:
        st.info("Configure as credenciais do Drive nos secrets para habilitar o envio direto.")
    elif not drive_url:
        st.info("Informe o link da pasta raiz do Drive para habilitar o salvamento.")


    # ── SECTION 5: Indexação no banco vetorial ─────────────────────────────────
    db_url = get_secret("DATABASE_URL")
    if db_url:
        st.divider()
        st.subheader("🧠 Indexação no Banco Vetorial")

        import hashlib
        import time as _time
        import psycopg2
        import psycopg2.extras as _pg_extras
        import tiktoken

        def _get_pg_conn():
            conn = psycopg2.connect(db_url)
            conn.autocommit = True
            return conn

        def _chunk_text(text, max_tokens=500, overlap=50):
            enc = tiktoken.get_encoding("cl100k_base")
            tokens = enc.encode(text)
            chunks = []
            i = 0
            while i < len(tokens):
                chunk = enc.decode(tokens[i:i + max_tokens])
                if chunk.strip():
                    chunks.append(chunk)
                i += max_tokens - overlap
            return chunks

        def _embed(text, oai_client):
            resp = oai_client.embeddings.create(model="text-embedding-ada-002", input=text)
            return resp.data[0].embedding

        def _insert_chunks(cur, chunks, filename, folder, category, oai_client):
            for i, chunk in enumerate(chunks):
                emb  = _embed(chunk, oai_client)
                meta = {
                    "arquivo":      filename,
                    "pasta":        folder,
                    "category":     category,
                    "chunk":        i,
                    "total":        len(chunks),
                    "content_hash": hashlib.md5(chunk.encode()).hexdigest(),
                }
                cur.execute(
                    "INSERT INTO documentos (conteudo, metadata, embedding) VALUES (%s, %s, %s::vector)",
                    (chunk, _pg_extras.Json(meta), str(emb)),
                )
                _time.sleep(0.05)

        def _category_from_folder(folder):
            if not folder:
                return "transcricoes"
            first = folder.strip("/").split("/")[0].lower()
            mapping = {
                "brand": "brand", "marca": "brand",
                "modulo": "modulos", "módulo": "modulos",
                "persona": "personas", "case": "cases",
                "conteudo": "conteudos", "conteúdo": "conteudos",
                "transcri": "transcricoes",
            }
            for key, val in mapping.items():
                if key in first:
                    return val
            return "geral"

        def _ingest_drive(drive_svc, folder_id, oai_client, status_ph, folder_name=""):
            import io as _io
            from googleapiclient.http import MediaIoBaseDownload
            try:
                import docx as _docx
            except ImportError:
                _docx = None
            try:
                from pypdf import PdfReader
            except ImportError:
                PdfReader = None

            SUPPORTED = {
                "application/vnd.google-apps.document",
                "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
                "application/pdf", "text/plain", "text/markdown",
            }

            def _list(fid, fname):
                resp = drive_svc.files().list(
                    q=f"\'{fid}\' in parents and trashed=false",
                    fields="files(id,name,mimeType)",
                    pageSize=200,
                    includeItemsFromAllDrives=True,
                    supportsAllDrives=True,
                    corpora="allDrives",
                ).execute()
                result = []
                for f in resp.get("files", []):
                    if f["mimeType"] == "application/vnd.google-apps.folder":
                        result += _list(f["id"], fname + "/" + f["name"])
                    elif f["mimeType"] in SUPPORTED:
                        result.append({**f, "folder": fname})
                return result

            def _download(file_id, mime_type):
                if mime_type == "application/vnd.google-apps.document":
                    resp = drive_svc.files().export(fileId=file_id, mimeType="text/plain").execute()
                    return resp.decode("utf-8") if isinstance(resp, bytes) else resp
                req = drive_svc.files().get_media(fileId=file_id, supportsAllDrives=True)
                buf = _io.BytesIO()
                dl  = MediaIoBaseDownload(buf, req)
                done = False
                while not done:
                    _, done = dl.next_chunk()
                buf.seek(0)
                return buf.read()

            def _extract(content, mime_type, name):
                try:
                    if mime_type in ("application/vnd.google-apps.document", "text/plain", "text/markdown"):
                        return content if isinstance(content, str) else content.decode("utf-8", errors="ignore")
                    if mime_type == "application/vnd.openxmlformats-officedocument.wordprocessingml.document":
                        if _docx:
                            doc = _docx.Document(_io.BytesIO(content))
                            return "\n".join(p.text for p in doc.paragraphs if p.text.strip())
                    if mime_type == "application/pdf":
                        if PdfReader:
                            reader = PdfReader(_io.BytesIO(content))
                            return "\n\n".join(page.extract_text() or "" for page in reader.pages)
                except Exception as e:
                    status_ph.warning(f"⚠️ {name}: {e}")
                return ""

            files = _list(folder_id, folder_name)
            status_ph.markdown(
                f'<div class="chunk-progress">📁 {len(files)} arquivo(s) encontrado(s)…</div>',
                unsafe_allow_html=True,
            )
            conn = _get_pg_conn()
            cur  = conn.cursor()
            total = 0
            for f in files:
                try:
                    status_ph.markdown(
                        f'<div class="chunk-progress">📄 {f["folder"]}/{f["name"]}</div>',
                        unsafe_allow_html=True,
                    )
                    raw  = _download(f["id"], f["mimeType"])
                    text = _extract(raw, f["mimeType"], f["name"])
                    if not text.strip():
                        continue
                    chunks   = _chunk_text(text)
                    category = _category_from_folder(f["folder"])
                    _insert_chunks(cur, chunks, f["name"], f["folder"], category, oai_client)
                    total += len(chunks)
                except Exception as e:
                    status_ph.warning(f"❌ {f['name']}: {e}")
            cur.close()
            conn.close()
            return total

        # UI
        index_mode = st.radio(
            "Modo de indexação",
            options=[
                "1 — Reindexar todo o repositório (limpa o banco e reindexa tudo do Drive)",
                "2 — Indexar apenas esta transcrição (adiciona ao banco sem limpar)",
            ],
            index=1,
        )

        if index_mode.startswith("1"):
            st.markdown(
                '<div class="status-box status-warning">'
                '⚠️ <strong>Atenção:</strong> apaga todos os registros da tabela '
                '<code>documentos</code> antes de reinserir. Operação irreversível.'
                '</div>',
                unsafe_allow_html=True,
            )

        btn_label = "🔄  Reindexar repositório completo" if index_mode.startswith("1") else "➕  Indexar esta transcrição"
        btn_disabled = index_mode.startswith("2") and not transcription_result

        if not transcription_result and index_mode.startswith("2"):
            st.info("Faça uma transcrição primeiro para habilitar a indexação individual.")

        if st.button(btn_label, disabled=btn_disabled):
            oai_client, oai_err = build_openai_client()
            if oai_err:
                st.error(f"OpenAI: {oai_err}")
                st.stop()

            idx_ph = st.empty()
            try:
                if index_mode.startswith("1"):
                    if not drive_service:
                        st.error("Drive não autenticado.")
                        st.stop()
                    if not drive_url:
                        st.error("Informe o link da pasta raiz do Drive.")
                        st.stop()
                    conn = _get_pg_conn()
                    cur  = conn.cursor()
                    idx_ph.markdown('<div class="chunk-progress">🗑️ Limpando tabela…</div>', unsafe_allow_html=True)
                    cur.execute("DELETE FROM documentos;")
                    cur.close()
                    conn.close()
                    total = _ingest_drive(drive_service, extract_folder_id(drive_url), oai_client, idx_ph)
                    idx_ph.empty()
                    st.markdown(
                        f'<div class="status-box status-success">✅ Reindexação concluída — <strong>{total}</strong> chunks inseridos.</div>',
                        unsafe_allow_html=True,
                    )
                else:
                    filename = transcription_filename or "transcricao.txt"
                    folder   = selected_folder_name or ""
                    category = _category_from_folder(folder)
                    chunks   = _chunk_text(transcription_result)
                    idx_ph.markdown(
                        f'<div class="chunk-progress">⚙️ Gerando embeddings para {len(chunks)} chunk(s)…</div>',
                        unsafe_allow_html=True,
                    )
                    conn = _get_pg_conn()
                    cur  = conn.cursor()
                    _insert_chunks(cur, chunks, filename, folder, category, oai_client)
                    cur.close()
                    conn.close()
                    idx_ph.empty()
                    st.markdown(
                        f'<div class="status-box status-success">✅ Indexado — <strong>{len(chunks)}</strong> chunk(s) na categoria <strong>{category}</strong>.</div>',
                        unsafe_allow_html=True,
                    )
            except Exception as e:
                idx_ph.empty()
                st.error(f"Erro na indexação: {e}")
