// components/EpisodeCard.tsx
// Episode card shown in the dashboard with status and Approve to YouTube button.

"use client";

import { useState } from "react";

interface Episode {
  id: string;
  episode_number: number;
  title?: string;
  episode_title?: string;
  module_name?: string;
  status: string;
  video_url?: string;
  youtube_url?: string;
  youtube_video_id?: string;
  scheduled_at?: string;
}

interface EpisodeCardProps {
  episode: Episode;
  language: "tamil" | "english";
  onStatusChange?: () => void;
}

const STATUS_LABELS: Record<string, { label: string; colour: string }> = {
  script_ready:   { label: "Script Ready",   colour: "bg-blue-500" },
  audio_uploaded: { label: "Audio Uploaded", colour: "bg-purple-500" },
  rendering:      { label: "Rendering",      colour: "bg-yellow-500" },
  done:           { label: "Video Ready",    colour: "bg-green-500" },
  approved:       { label: "Uploading...",   colour: "bg-orange-500" },
  published:      { label: "Published",      colour: "bg-pink-600" },
};

export default function EpisodeCard({
  episode,
  language,
  onStatusChange,
}: EpisodeCardProps) {
  const [loading, setLoading]   = useState(false);
  const [message, setMessage]   = useState("");
  const [error,   setError]     = useState("");

  const title      = episode.title || episode.episode_title || "Untitled";
  const statusInfo = STATUS_LABELS[episode.status] ?? {
    label:  episode.status,
    colour: "bg-gray-500",
  };

  const canApprove =
    episode.status === "done" &&
    !episode.youtube_video_id &&
    !!episode.video_url;

  const scheduledDate = episode.scheduled_at
    ? new Date(episode.scheduled_at).toLocaleString("en-IN", {
        timeZone: "Asia/Kolkata",
        dateStyle: "medium",
        timeStyle: "short",
      })
    : null;

  async function handleApprove() {
    if (!confirm(`Approve EP ${episode.episode_number} — "${title}" for YouTube upload?`))
      return;

    setLoading(true);
    setMessage("");
    setError("");

    try {
      const res = await fetch("/api/approve-youtube", {
        method:  "POST",
        headers: { "Content-Type": "application/json" },
        body:    JSON.stringify({ episode_id: episode.id, language }),
      });

      const data = await res.json();

      if (!res.ok) {
        setError(data.error || "Something went wrong");
      } else {
        setMessage(data.message || "Upload started!");
        onStatusChange?.();
      }
    } catch (err) {
      setError("Network error. Please try again.");
    } finally {
      setLoading(false);
    }
  }

  return (
    <div className="bg-gray-900 border border-gray-700 rounded-xl p-4 flex flex-col gap-3 hover:border-pink-500 transition-colors">
      {/* Header */}
      <div className="flex items-start justify-between gap-2">
        <div className="flex items-center gap-2">
          {/* Episode badge */}
          <span className="bg-pink-700 text-white text-xs font-bold px-2 py-1 rounded-lg whitespace-nowrap">
            EP {String(episode.episode_number).padStart(2, "0")}
          </span>
          {/* Status badge */}
          <span className={`${statusInfo.colour} text-white text-xs px-2 py-1 rounded-lg`}>
            {statusInfo.label}
          </span>
        </div>
        {/* Language tag */}
        <span className="text-xs text-gray-400 uppercase tracking-wide">
          {language}
        </span>
      </div>

      {/* Title */}
      <p className="text-white font-semibold text-sm leading-snug">{title}</p>

      {/* Module */}
      {episode.module_name && (
        <p className="text-gray-400 text-xs">{episode.module_name}</p>
      )}

      {/* YouTube link if published */}
      {episode.youtube_url && (
        <a
          href={episode.youtube_url}
          target="_blank"
          rel="noopener noreferrer"
          className="text-pink-400 text-xs underline hover:text-pink-300 truncate"
        >
          ▶ Watch on YouTube
        </a>
      )}

      {/* Scheduled time */}
      {scheduledDate && (
        <p className="text-gray-500 text-xs">📅 Scheduled: {scheduledDate} IST</p>
      )}

      {/* Approve button */}
      {canApprove && (
        <button
          onClick={handleApprove}
          disabled={loading}
          className="mt-1 w-full bg-pink-600 hover:bg-pink-700 disabled:bg-gray-600 
                     text-white font-semibold text-sm py-2 px-4 rounded-lg 
                     transition-colors flex items-center justify-center gap-2"
        >
          {loading ? (
            <>
              <span className="animate-spin">⏳</span> Uploading...
            </>
          ) : (
            <>
              ▶ Approve to YouTube
            </>
          )}
        </button>
      )}

      {/* Already uploaded state */}
      {episode.status === "approved" && !episode.youtube_video_id && (
        <div className="text-orange-400 text-xs text-center py-1">
          ⏳ Upload in progress — check back in a few minutes
        </div>
      )}

      {/* Messages */}
      {message && (
        <p className="text-green-400 text-xs text-center">{message}</p>
      )}
      {error && (
        <p className="text-red-400 text-xs text-center">{error}</p>
      )}
    </div>
  );
}
