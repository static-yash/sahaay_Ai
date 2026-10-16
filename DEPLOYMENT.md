# 🚀 Deployment Guide — Solarpunk AI (Sahaay)

This application is a real-time voice and text AI assistant powered by **FastAPI**, **WebSockets**, and **Google Gemini 2.5 Flash**.

This guide covers deployment across the most popular cloud platforms and container environments.

---

## 📋 Required Environment Variables

Configure these in your hosting provider's dashboard or secrets manager:

| Variable | Required | Default | Description |
|---|---|---|---|
| `GEMINI_API_KEY` | **Yes** | — | Google Gemini API key from [Google AI Studio](https://aistudio.google.com/) |
| `GEMINI_MODEL` | No | `gemini-2.5-flash` | Gemini model name (e.g., `gemini-2.5-flash`) |
| `PORT` | No | `8000` | Port automatically provided by Render, Railway, Cloud Run, etc. |
| `HOST` | No | `0.0.0.0` | Server host binding |
| `ENVIRONMENT` | No | `production` | Set to `development` to enable automatic code reloading |

---

## Option 1: Render (Recommended — Quickest)

Render provides native WebSocket support and automatic TLS (`https://` and `wss://`).

1. Fork or push this repository to GitHub/GitLab.
2. Sign in to [Render](https://dashboard.render.com/).
3. Click **New +** → **Blueprint** and connect your repository (Render will automatically read [`render.yaml`](render.yaml)).
   - *Alternatively, click **New +** → **Web Service** manually*:
     - **Runtime**: `Python`
     - **Build Command**: `pip install -r requirements.txt`
     - **Start Command**: `uvicorn main:app --host 0.0.0.0 --port $PORT`
     - **Health Check Path**: `/health`
4. Under **Environment Variables**, add:
   - `GEMINI_API_KEY`: `<your_gemini_api_key>`
5. Click **Create Web Service**. Once deployed, open your Render URL (e.g. `https://sahaay-voice-ai.onrender.com`).

---

## Option 2: Railway

Railway offers one-click GitHub deployment with automatic Dockerfile detection.

1. Go to [Railway.app](https://railway.app/).
2. Click **New Project** → **Deploy from GitHub repo**.
3. Select this repository. Railway will detect the [`Dockerfile`](Dockerfile) or [`Procfile`](Procfile) and build it.
4. Go to the project **Settings** → **Variables** and add:
   - `GEMINI_API_KEY`: `<your_gemini_api_key>`
5. Go to **Settings** → **Networking** → Click **Generate Domain**.
6. Visit your generated `.up.railway.app` URL.

---

## Option 3: Fly.io

Fly.io is ideal for real-time WebSocket applications with low-latency edge routing.

1. Install the Fly CLI:
   ```bash
   # macOS / Linux
   curl -L https://fly.io/install.sh | sh
   ```
2. Log in and launch:
   ```bash
   fly auth login
   fly launch --no-deploy
   ```
3. Set your secret Gemini API key:
   ```bash
   fly secrets set GEMINI_API_KEY="your-api-key-here"
   ```
4. Deploy the app:
   ```bash
   fly deploy
   ```
5. Open your app:
   ```bash
   fly open
   ```

---

## Option 4: Google Cloud Run (Serverless Container)

1. Ensure the `gcloud` CLI is installed and authenticated:
   ```bash
   gcloud auth login
   gcloud config set project <YOUR_PROJECT_ID>
   ```
2. Build and deploy directly to Cloud Run:
   ```bash
   gcloud run deploy sahaay-voice-ai \
     --source . \
     --platform managed \
     --region us-central1 \
     --allow-unauthenticated \
     --set-env-vars GEMINI_API_KEY="your-gemini-api-key",GEMINI_MODEL="gemini-2.5-flash"
   ```
3. Cloud Run will provide an HTTPS endpoint supporting WebSockets out of the box with zero idle server cost.

---

## Option 5: Self-Hosted / VPS with Docker Compose

To deploy on your own server (DigitalOcean Droplet, AWS EC2, Hetzner, etc.):

1. Clone your repo on the server:
   ```bash
   git clone <your-repo-url>
   cd sahaay
   ```
2. Create a `.env` file with your credentials:
   ```bash
   cp .env.example .env
   nano .env
   # Set GEMINI_API_KEY=your_key
   ```
3. Start the container in detached mode:
   ```bash
   docker compose up -d --build
   ```
4. Check running status and logs:
   ```bash
   docker compose ps
   docker compose logs -f
   ```
5. The application will be live at `http://<your-server-ip>:8000`.
   *(Tip: Use Caddy or Nginx with Let's Encrypt for automatic HTTPS/WSS).*

---

## 🔍 Verification & Health Check

After deployment, check the `/health` endpoint:

```bash
curl https://<your-deployed-domain>/health
```

Expected JSON response:
```json
{
  "status": "ok",
  "gemini_configured": true,
  "model": "gemini-2.5-flash",
  "sdk": "google-genai"
}
```

If `gemini_configured` is `false`, make sure your `GEMINI_API_KEY` environment variable is set in your cloud provider's dashboard and restart the service.
