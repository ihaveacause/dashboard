// app/api/approve-youtube/route.ts
// Called by the "Approve to YouTube" button in the dashboard.
// Triggers the GitHub Actions workflow for the given episode.

import { createClient } from "@supabase/supabase-js";
import { NextRequest, NextResponse } from "next/server";

const SUPABASE_URL           = process.env.NEXT_PUBLIC_SUPABASE_URL!;
const SUPABASE_SERVICE_KEY   = process.env.SUPABASE_SERVICE_ROLE_KEY!;
const GITHUB_TOKEN           = process.env.GH_YOUTUBE_TOKEN!;
const GITHUB_REPO            = process.env.GITHUB_REPO!;   // e.g. "yourname/ihaveacause"

export async function POST(req: NextRequest) {
  try {
    const { episode_id, language } = await req.json();

    if (!episode_id || !language) {
      return NextResponse.json(
        { error: "episode_id and language are required" },
        { status: 400 }
      );
    }

    const sb = createClient(SUPABASE_URL, SUPABASE_SERVICE_KEY);
    const table = language === "tamil" ? "tamil_episodes" : "english_episodes";

    // 1. Check episode has a video_url
    const { data: episode, error } = await sb
      .from(table)
      .select("id, episode_number, status, video_url, youtube_video_id")
      .eq("id", episode_id)
      .single();

    if (error || !episode) {
      return NextResponse.json({ error: "Episode not found" }, { status: 404 });
    }

    if (!episode.video_url) {
      return NextResponse.json(
        { error: "Episode has no video file yet. Render the video first." },
        { status: 400 }
      );
    }

    if (episode.youtube_video_id) {
      return NextResponse.json(
        { error: "Episode already uploaded to YouTube." },
        { status: 400 }
      );
    }

    // 2. Mark as 'approved' in Supabase
    await sb
      .from(table)
      .update({
        status:      "approved",
        approved_at: new Date().toISOString(),
      })
      .eq("id", episode_id);

    // 3. Trigger GitHub Actions workflow
    const ghResponse = await fetch(
      `https://api.github.com/repos/${GITHUB_REPO}/actions/workflows/upload_to_youtube.yml/dispatches`,
      {
        method: "POST",
        headers: {
          Authorization: `Bearer ${GITHUB_TOKEN}`,
          Accept:        "application/vnd.github+json",
          "Content-Type": "application/json",
        },
        body: JSON.stringify({
          ref: "main",
          inputs: {
            episode_id,
            language,
          },
        }),
      }
    );

    if (!ghResponse.ok) {
      const errText = await ghResponse.text();
      console.error("GitHub dispatch error:", errText);
      // Revert status
      await sb.from(table).update({ status: "done" }).eq("id", episode_id);
      return NextResponse.json(
        { error: "Failed to trigger GitHub Actions", detail: errText },
        { status: 500 }
      );
    }

    return NextResponse.json({
      success: true,
      message: `Episode ${episode.episode_number} approved and upload started.`,
    });

  } catch (err) {
    console.error("approve-youtube error:", err);
    return NextResponse.json({ error: "Internal server error" }, { status: 500 });
  }
}
