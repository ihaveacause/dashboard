import { serve } from "https://deno.land/std@0.177.0/http/server.ts";

const CORS = {
  "Access-Control-Allow-Origin": "*",
  "Access-Control-Allow-Headers": "authorization, x-client-info, apikey, content-type",
};

serve(async (req) => {
  if (req.method === "OPTIONS") {
    return new Response("ok", { headers: CORS });
  }

  try {
    const { episode_number, idea_id, language = "ta" } = await req.json();

    if (!episode_number && !idea_id) {
      return new Response(
        JSON.stringify({ error: "episode_number or idea_id required" }),
        { status: 400, headers: { ...CORS, "Content-Type": "application/json" } }
      );
    }

    const GITHUB_TOKEN = Deno.env.get("GITHUB_TOKEN")!;
    const GITHUB_OWNER = Deno.env.get("GITHUB_OWNER")!;
    const GITHUB_REPO  = Deno.env.get("GITHUB_REPO")!;

    const inputs: Record<string, string> = { language };
    if (episode_number) inputs.episode_number = String(episode_number);
    if (idea_id)        inputs.idea_id = String(idea_id);

    const response = await fetch(
      `https://api.github.com/repos/${GITHUB_OWNER}/${GITHUB_REPO}/actions/workflows/generate_shorts_video.yml/dispatches`,
      {
        method: "POST",
        headers: {
          Authorization: `token ${GITHUB_TOKEN}`,
          Accept: "application/vnd.github.v3+json",
          "Content-Type": "application/json",
        },
        body: JSON.stringify({ ref: "main", inputs }),
      }
    );

    if (response.status === 204) {
      return new Response(
        JSON.stringify({ success: true, message: "Shorts video generation triggered" }),
        { headers: { ...CORS, "Content-Type": "application/json" } }
      );
    }

    const errorText = await response.text();
    return new Response(
      JSON.stringify({ error: `GitHub API error ${response.status}`, details: errorText }),
      { status: 500, headers: { ...CORS, "Content-Type": "application/json" } }
    );
  } catch (err) {
    return new Response(
      JSON.stringify({ error: err.message }),
      { status: 500, headers: { ...CORS, "Content-Type": "application/json" } }
    );
  }
});
