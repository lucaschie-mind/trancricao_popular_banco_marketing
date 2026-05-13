# Setup — Transcritor de Mídia v2.0

## 1. Dependências Python

```bash
pip install -r requirements.txt
```

## 2. ffmpeg (obrigatório)

O ffmpeg é usado para extrair e comprimir o áudio antes de enviar ao Whisper.

```bash
# macOS
brew install ffmpeg

# Ubuntu/Debian
sudo apt install ffmpeg

# Windows
# Baixe em https://ffmpeg.org/download.html e adicione ao PATH
```

No Railway, adicione no `nixpacks.toml`:
```toml
[phases.setup]
nixPkgs = ["ffmpeg"]
```

---

## 3. Secrets necessários

Crie `.streamlit/secrets.toml`:

```toml
# Chave da OpenAI (Whisper API)
OPENAI_API_KEY = "sk-..."

# JSON da Service Account do Google (para o Drive)
GOOGLE_SERVICE_ACCOUNT_JSON = """
{
  "type": "service_account",
  "project_id": "...",
  "private_key_id": "...",
  "private_key": "-----BEGIN RSA PRIVATE KEY-----\n...\n-----END RSA PRIVATE KEY-----\n",
  "client_email": "conta@projeto.iam.gserviceaccount.com",
  ...
}
"""
```

> No Railway/Streamlit Cloud: adicione via painel de variáveis de ambiente.

---

## 4. Configurar Google Drive (Service Account)

1. Acesse https://console.cloud.google.com
2. Ative a **Google Drive API**
3. Crie uma **Service Account** → gere uma chave JSON
4. Compartilhe a pasta raiz do Drive com o e-mail da Service Account (permissão de Editor)

---

## 5. Rodar localmente

```bash
streamlit run app.py
```

---

## 6. Deploy no Railway

```bash
railway init
railway up
```

Variáveis de ambiente no painel do Railway:
- `OPENAI_API_KEY`
- `GOOGLE_SERVICE_ACCOUNT_JSON`

`nixpacks.toml` na raiz para instalar ffmpeg:
```toml
[phases.setup]
nixPkgs = ["ffmpeg"]
```

---

## Como funciona o processamento de áudio

| Etapa | O que acontece |
|---|---|
| Upload | Arquivo salvo em temp (qualquer formato) |
| Extração | ffmpeg converte para mp3 mono 16kHz 64kbps |
| Verificação | Se < 23 MB → envia direto para a API |
| Chunking | Se ≥ 23 MB → divide em partes de ~50 min e transcreve em sequência |
| Resultado | Textos concatenados e exibidos na tela |

**Por que 64kbps mono não afeta a qualidade?**
O Whisper foi treinado com áudio a 16 kHz. Voz humana ocupa a faixa de 300 Hz–3.4 kHz.
64kbps mono captura tudo isso com folga — a única perda é em música de alta fidelidade,
que não interfere na transcrição de fala.

---

## Custo estimado (Whisper API)

| Duração | Custo aproximado |
|---|---|
| 10 min | US$ 0,06 |
| 1 hora | US$ 0,36 |
| 10 horas | US$ 3,60 |
