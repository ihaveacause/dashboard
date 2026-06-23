"""
generate_tamil_voice_samples.py — one-time (re-runnable) helper
===============================================================
Mirrors generate_voice_samples.py, but for the Tamil (ta-IN) Chirp 3: HD
shortlist (4 female + 4 male). Synthesizes a short Tamil preview clip for each
voice and uploads it to the Supabase public bucket `voice-samples`, so the
dashboard's voice picker can play a ▶ Preview for each Tamil option.

Place at:  new_youtube/scripts/generate_tamil_voice_samples.py

Run once (and again whenever you change the catalog):

    SUPABASE_URL=... SUPABASE_KEY=... \
    GOOGLE_APPLICATION_CREDENTIALS_JSON='{...}' \
    python new_youtube/scripts/generate_tamil_voice_samples.py

Requirements: the same public `voice-samples` bucket the English samples use.
Keep TA_VOICES in sync with VOICE_CATALOG_TA in index.html.
"""
import os, json, base64, requests

SUPABASE_URL   = os.environ["SUPABASE_URL"]
# Service-role key bypasses Storage RLS; fall back to SUPABASE_KEY.
SUPABASE_KEY   = os.environ.get("SUPABASE_SERVICE_KEY") or os.environ["SUPABASE_KEY"]
GCP_CREDS_JSON = os.environ["GOOGLE_APPLICATION_CREDENTIALS_JSON"]
BUCKET         = "voice-samples"

# A short, reflective Tamil line so each preview shows real Tamil pronunciation.
SAMPLE_TEXT = ("ஒவ்வொரு மனிதனின் உள்ளத்திலும் உண்மையை நோக்கிய "
               "அமைதியான ஏக்கம் உள்ளது — அதைத் தெளிவாகக் காணும் ஞானமும்.")

# The 8 Tamil shortlist voices — must match VOICE_CATALOG_TA in index.html.
TA_VOICES = [
    # — female —
    "ta-IN-Chirp3-HD-Callirrhoe",  # default
    "ta-IN-Chirp3-HD-Leda",
    "ta-IN-Chirp3-HD-Aoede",
    "ta-IN-Chirp3-HD-Kore",
    # — male —
    "ta-IN-Chirp3-HD-Charon",
    "ta-IN-Chirp3-HD-Schedar",
    "ta-IN-Chirp3-HD-Orus",
    "ta-IN-Chirp3-HD-Iapetus",
]

def google_token():
    from google.oauth2 import service_account
    import google.auth.transport.requests as gr
    creds = service_account.Credentials.from_service_account_info(
        json.loads(GCP_CREDS_JSON), scopes=["https://www.googleapis.com/auth/cloud-platform"])
    creds.refresh(gr.Request())
    return creds.token

def synth(voice, token):
    lang = "-".join(voice.split("-")[:2])  # -> "ta-IN"
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
    print(f"🎙  Generating {len(TA_VOICES)} Tamil voice samples...")
    token = google_token()
    ok = 0
    for v in TA_VOICES:
        audio = synth(v, token)
        if audio and upload(v, audio):
            ok += 1
    print(f"\nDone — {ok}/{len(TA_VOICES)} Tamil samples ready.")

if __name__ == "__main__":
    main()
