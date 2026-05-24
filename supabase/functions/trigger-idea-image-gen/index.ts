// Supabase Edge Function: trigger-idea-image-gen
// Mirrors trigger-script-gen pattern exactly

const GITHUB_OWNER  = "ihaveacause";
const GITHUB_REPO   = "dashboard";
const WORKFLOW_FILE = "generate_idea_images.yml";

const CORS = {
  "Access-Control-Allow-Origin": "*",
  "Access-Control-Allow-Methods": "POST, OPTIONS",
  "Access-Control-Allow-Headers": "Content-Type, Authorization, apikey",
};

Deno.serve(async (req) => {
  if (req.method === "OPTIONS") return new Response(null, { headers: CORS });
  try {
    const { idea_id } = await req.json();
    if (!idea_id) return new Response(JSON.stringify({ error: "idea_id is required" }), { status: 400, headers: { ...CORS, "Content-Type": "application/json" } });

    const GH_PAT = Deno.env.get("GH_PAT");
    if (!GH_PAT) return new Response(JSON.stringify({ error: "GH_PAT not configured" }), { status: 500, headers: { ...CORS, "Content-Type": "application/json" } });

    const response = await fetch(
      `https://api.github.com/repos/${GITHUB_OWNER}/${GITHUB_REPO}/actions/workflows/${WORKFLOW_FILE}/dispatches`,
      {
        method: "POST",
        headers: { "Authorization": `Bearer ${GH_PAT}`, "Accept": "application/vnd.github.v3+json", "Content-Type": "application/json" },
        body: JSON.stringify({ ref: "main", inputs: { idea_id: String(idea_id) } })
      }
    );

    if (response.status === 204) {
      return new Response(JSON.stringify({ success: true }), { status: 200, headers: { ...CORS, "Content-Type": "application/json" } });
    }
    const errorText = await response.text();
    return new Response(JSON.stringify({ error: `GitHub API error ${response.status}`, details: errorText }), { status: 500, headers: { ...CORS, "Content-Type": "application/json" } });
  } catch (err) {
    return new Response(JSON.stringify({ error: err.message }), { status: 500, headers: { ...CORS, "Content-Type": "application/json" } });
  }
});
