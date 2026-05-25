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
    const { idea_id, language = "ta" } = await req.json();

    if (!idea_id) {
      return new Response(
        JSON.stringify({ error: "idea_id required" }),
        { status: 400, headers: { ...CORS, "Content-Type": "application/json" } }
      );
    }

    const GITHUB_TOKEN = Deno.env.get("GITHUB_TOKEN")!;
    const GITHUB_OWNER = Deno.env.get("GITHUB_OWNER")!;
    const GITHUB_REPO  = Deno.env.get("GITHUB_REPO")!;

    const response = await fetch(
      `https://api.github.com/repos/${GITHUB_OWNER}/${GITHUB_REPO}/actions/workflows/upload_idea_to_youtube.yml/dispatches`,
      {
        method: "POST",
        headers: {
          Authorization: `token ${GITHUB_TOKEN}`,
          Accept: "application/vnd.github.v3+json",
          "Content-Type": "application/json",
        },
        body: JSON.stringify({
          ref: "main",
          inputs: { idea_id: String(idea_id), language },
        }),
      }
    );

    if (response.status === 204) {
      return new Response(
        JSON.stringify({ success: true, message: "Idea YouTube upload triggered" }),
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
