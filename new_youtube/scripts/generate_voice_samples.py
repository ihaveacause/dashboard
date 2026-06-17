"""
generate_voice_samples.py — one-time (re-runnable) helper
=========================================================
Synthesizes a short preview clip for each Chirp 3: HD voice in the catalog and
uploads them to the Supabase public bucket `voice-samples`, so the dashboard's
voice picker can play a ▶ Preview for each option.

Run it once (and again whenever you change the catalog):

    SUPABASE_URL=... SUPABASE_KEY=... \
    GOOGLE_APPLICATION_CREDENTIALS_JSON='{...}' \
    python new_youtube/scripts/generate_voice_samples.py

Requirements: the `voice-samples` bucket must exist and be public in Supabase
Storage. Keep VOICE_CATALOG in sync with index.html.
"""
import os, json, base64, requests

SUPABASE_URL   = os.environ["SUPABASE_URL"]
# Use the service-role key if provided (bypasses Storage row-level security);
# fall back to SUPABASE_KEY. The anon key will 403 unless the bucket has an
# INSERT policy, so the service-role key is the simplest path for this job.
SUPABASE_KEY   = os.environ.get("SUPABASE_SERVICE_KEY") or os.environ["SUPABASE_KEY"]
GCP_CREDS_JSON = os.environ["GOOGLE_APPLICATION_CREDENTIALS_JSON"]
BUCKET         = "voice-samples"

SAMPLE_TEXT = ("In every human heart there is a quiet longing for truth — "
               "and the wisdom to see it clearly.")

# Keep in sync with VOICE_CATALOG in index.html
VOICES = [
    "en-GB-Chirp3-HD-Charon", "en-GB-Chirp3-HD-Schedar",
    "en-US-Chirp3-HD-Orus",   "en-US-Chirp3-HD-Iapetus",
    "en-GB-Chirp3-HD-Kore",   "en-GB-Chirp3-HD-Leda", "en-GB-Chirp3-HD-Aoede",
    "en-US-Chirp3-HD-Vindemiatrix", "en-US-Chirp3-HD-Autonoe",
]

def google_token():
    from google.oauth2 import service_account
    import google.auth.transport.requests as gr
    creds = service_account.Credentials.from_service_account_info(
        json.loads(GCP_CREDS_JSON), scopes=["https://www.googleapis.com/auth/cloud-platform"])
    creds.refresh(gr.Request())
    return creds.token

def synth(voice, token):
    lang = "-".join(voice.split("-")[:2])
    r = requests.post(
        "https://texttospeech.googleapis.com/v1/text:synthesize",
        headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"},
        json={"input": {"text": SAMPLE_TEXT},
              "voice": {"languageCode": lang, "name": voice},
              "audioConfig": {"audioEncoding": "MP3"}}, timeout=120)
    if r.status_code != 200:
        print(f"  ❌ {voice} synth failed {r.status_code}: {r.text[:160]}")
        return None
    return base64.b64decode(r.json()["audioContent"])

def upload(voice, audio):
    path = f"{voice}.mp3"
    r = requests.post(
        f"{SUPABASE_URL}/storage/v1/object/{BUCKET}/{path}",
        headers={"apikey": SUPABASE_KEY, "Authorization": f"Bearer {SUPABASE_KEY}",
                 "Content-Type": "audio/mpeg", "x-upsert": "true"},
        data=audio, timeout=60)
    if r.status_code not in (200, 201):
        print(f"  ❌ {voice} upload failed {r.status_code}: {r.text[:160]}")
        return False
    print(f"  ✅ {voice} → {SUPABASE_URL}/storage/v1/object/public/{BUCKET}/{path}")
    return True

def main():
    print(f"🎙  Generating {len(VOICES)} voice samples...")
    token = google_token()
    ok = 0
    for v in VOICES:
        audio = synth(v, token)
        if audio and upload(v, audio):
            ok += 1
    print(f"\nDone — {ok}/{len(VOICES)} samples ready.")

if __name__ == "__main__":
    main()
