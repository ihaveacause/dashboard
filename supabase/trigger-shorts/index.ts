// Supabase Edge Function: trigger-shorts
// ---------------------------------------------------------------
// ONE trigger for the whole Shorts pipeline. The dashboard sends:
//     { step, episode_number, short_id, language }
//   step = "scripts" | "images" | "video" | "upload"
//   "scripts" operates on an episode (episode_number) and creates 1-3 rows.
//   "images" / "video" / "upload" operate on a single short (short_id).
// and this dispatches the matching GitHub Actions workflow.
//
// Modelled exactly on your proven trigger-idea function:
//   • same GitHub repo + dispatch format
//   • same GH_PAT secret (already configured in your project)
// ---------------------------------------------------------------

const GITHUB_OWNER = "ihaveacause";
const GITHUB_REPO  = "dashboard";

const WORKFLOWS: Record<string, string> = {
  scripts: "shorts_generate_scripts.yml",
  images:  "shorts_generate_images.yml",
  video:   "shorts_render_video.yml",
  upload:  "shorts_upload_youtube.yml",
};

const CORS = {
  "Access-Control-Allow-Origin": "*",
  "Access-Control-Allow-Methods": "POST, OPTIONS",
  "Access-Control-Allow-Headers": "Content-Type, Authorization, apikey",
};

Deno.serve(async (req) => {
  if (req.method === "OPTIONS") {
    return new Response(null, { headers: CORS });
  }

  try {
    const { step, episode_number, short_id, language } = await req.json();

    const workflow = WORKFLOWS[step];
    if (!workflow) {
      return new Response(
        JSON.stringify({ error: `unknown step '${step}' (expected scripts/images/video/upload)` }),
        { status: 400, headers: { ...CORS, "Content-Type": "application/json" } }
      );
    }

    const inputs: Record<string, string> = { language: String(language || "ta") };

    if (step === "scripts") {
      if (episode_number === undefined || episode_number === null || episode_number === "") {
        return new Response(
          JSON.stringify({ error: "episode_number is required for step 'scripts'" }),
          { status: 400, headers: { ...CORS, "Content-Type": "application/json" } }
        );
      }
      inputs.episode_number = String(episode_number);
    } else {
      if (!short_id) {
        return new Response(
          JSON.stringify({ error: `short_id is required for step '${step}'` }),
          { status: 400, headers: { ...CORS, "Content-Type": "application/json" } }
        );
      }
      inputs.short_id = String(short_id);
    }

    const GH_PAT = Deno.env.get("GH_PAT");
    if (!GH_PAT) {
      return new Response(
        JSON.stringify({ error: "GH_PAT secret not configured" }),
        { status: 500, headers: { ...CORS, "Content-Type": "application/json" } }
      );
    }

    const response = await fetch(
      `https://api.github.com/repos/${GITHUB_OWNER}/${GITHUB_REPO}/actions/workflows/${workflow}/dispatches`,
      {
        method: "POST",
        headers: {
          "Authorization": `Bearer ${GH_PAT}`,
          "Accept":        "application/vnd.github.v3+json",
          "Content-Type":  "application/json",
        },
        body: JSON.stringify({ ref: "main", inputs }),
      }
    );

    if (response.status === 204) {
      return new Response(
        JSON.stringify({ success: true, message: `Shorts '${step}' started` }),
        { status: 200, headers: { ...CORS, "Content-Type": "application/json" } }
      );
    } else {
      const errorText = await response.text();
      return new Response(
        JSON.stringify({ error: `GitHub API error ${response.status}`, details: errorText }),
        { status: 500, headers: { ...CORS, "Content-Type": "application/json" } }
      );
    }

  } catch (err) {
    return new Response(
      JSON.stringify({ error: err.message }),
      { status: 500, headers: { ...CORS, "Content-Type": "application/json" } }
    );
  }
});
