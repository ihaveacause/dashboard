// Supabase Edge Function: trigger-idea
// ---------------------------------------------------------------
// ONE trigger for the whole Ideas pipeline. The dashboard sends:
//     { step, idea_number, language }
//   step = "script" | "images" | "video" | "thumbnail" | "upload"
// and this dispatches the matching GitHub Actions workflow.
//
// Modelled exactly on your proven trigger-idea-gen function:
//   • same GitHub repo + dispatch format
//   • same GH_PAT secret (already configured in your project)
// ---------------------------------------------------------------

const GITHUB_OWNER = "ihaveacause";
const GITHUB_REPO  = "dashboard";

// step -> workflow file
const WORKFLOWS: Record<string, string> = {
  script:    "generate_idea_script.yml",
  images:    "generate_idea_images.yml",
  video:     "generate_idea_video.yml",
  thumbnail: "generate_idea_thumbnail.yml",
  upload:    "upload_idea_to_youtube.yml",
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
    const { step, idea_number, language } = await req.json();

    const workflow = WORKFLOWS[step];
    if (!workflow) {
      return new Response(
        JSON.stringify({ error: `unknown step '${step}' (expected script/images/video/thumbnail/upload)` }),
        { status: 400, headers: { ...CORS, "Content-Type": "application/json" } }
      );
    }

    if (idea_number === undefined || idea_number === null || idea_number === "") {
      return new Response(
        JSON.stringify({ error: "idea_number is required" }),
        { status: 400, headers: { ...CORS, "Content-Type": "application/json" } }
      );
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
        body: JSON.stringify({
          ref: "main",
          inputs: {
            idea_number: String(idea_number),
            language:    String(language || "ta"),
          },
        }),
      }
    );

    if (response.status === 204) {
      return new Response(
        JSON.stringify({ success: true, message: `Idea '${step}' started for #${idea_number}` }),
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
